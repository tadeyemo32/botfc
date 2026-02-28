#include "TelemetryClient.h"
#include <boost/asio/connect.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/websocket.hpp>
#include <chrono>
#include <iostream>
#include <qi/log.hpp>

namespace beast = boost::beast;
namespace http = beast::http;
namespace websocket = beast::websocket;
namespace net = boost::asio;
using tcp = net::ip::tcp;

TelemetryClient::TelemetryClient(const std::string &host, int port)
    : host_(host), port_(port), isRunning(false) {}

TelemetryClient::~TelemetryClient() { stop(); }

void TelemetryClient::start(const std::string &trait) {
  if (isRunning)
    return;
  trait_ = trait;
  isRunning = true;
  thread_ = std::thread(&TelemetryClient::loop, this);
}

void TelemetryClient::stop() {
  if (!isRunning)
    return;
  isRunning = false;
  if (thread_.joinable())
    thread_.join();
}

void TelemetryClient::update(const TelemetryData &data) {
  std::lock_guard<std::mutex> lock(mutex_);
  currentData_ = data;
}

std::string TelemetryClient::buildJsonPayload() {
  std::lock_guard<std::mutex> lock(mutex_);
  // Simple but clean string-based JSON generation since Naoqi env might not
  // have Nlohmann
  std::string payload = "{";
  payload += "\"state\":\"" + currentData_.state + "\", ";
  payload += "\"kicks\":" + std::to_string(currentData_.kicks) + ", ";
  payload += "\"trait\":\"" + trait_ + "\", ";
  payload += "\"ball_age\":" + std::to_string(currentData_.ballAge) + ", ";
  payload +=
      "\"break_remaining\":" + std::to_string(currentData_.breakRemaining);
  payload += "}";
  return payload;
}

void TelemetryClient::loop() {
  while (isRunning) {
    try {
      net::io_context ioc;
      tcp::resolver resolver{ioc};
      websocket::stream<tcp::socket> ws{ioc};

      auto const results = resolver.resolve(host_, std::to_string(port_));
      net::connect(ws.next_layer(), results.begin(), results.end());

      ws.set_option(
          websocket::stream_base::decorator([](websocket::request_type &req) {
            req.set(http::field::user_agent, "BotFC-CppBrain-Binary-v2");
          }));

      ws.handshake(host_, "/api/ws/bot");
      ws.binary(false);

      while (isRunning) {
        std::string payload = buildJsonPayload();
        ws.write(net::buffer(payload));
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
      }

      ws.close(websocket::close_code::normal);
    } catch (const std::exception &e) {
      qiLogError("Telemetry")
          << "Connection Error: " << e.what() << ". Retrying in 2 seconds...";
      std::this_thread::sleep_for(std::chrono::seconds(2));
    }
  }
}
