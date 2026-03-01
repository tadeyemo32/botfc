#include "HttpSession.h"
#include "Config.h"
#include "SshDeployer.h"
#include "SshService.h"
#include "WsSession.h"
#include <thread>

namespace beast = boost::beast;
namespace http = beast::http;
namespace websocket = beast::websocket;
namespace net = boost::asio;
using tcp = net::ip::tcp;

HttpSession::HttpSession(tcp::socket &&socket, HttpServer &server)
    : stream_(std::move(socket)), server_(server) {}

void HttpSession::run() { doRead(); }

void HttpSession::doRead() {
  http::async_read(
      stream_, buffer_, req_,
      beast::bind_front_handler(&HttpSession::onRead, shared_from_this()));
}

void HttpSession::onRead(beast::error_code ec, std::size_t bytes_transferred) {
  boost::ignore_unused(bytes_transferred);
  if (ec == http::error::end_of_stream) {
    stream_.socket().shutdown(tcp::socket::shutdown_send, ec);
    return;
  }
  if (ec)
    return;

  if (websocket::is_upgrade(req_)) {
    auto target = std::string(req_.target());
    std::string role = "bot";
    if (target == "/api/ws/frontend")
      role = "frontend";
    else if (target == "/api/ws/bot_camera")
      role = "bot_camera";
    else if (target == "/api/ws/camera_feed")
      role = "camera_feed";

    std::make_shared<WsSession>(stream_.release_socket(), server_, role)
        ->doAccept(std::move(req_));
    return;
  }

  handleRequest();
}

void HttpSession::handleRequest() {
  http::response<http::string_body> res{http::status::ok, req_.version()};
  res.set(http::field::server, "botfc-server/2.0");
  res.set(http::field::content_type, "application/json");
  res.set(http::field::access_control_allow_origin, "*");
  res.set(http::field::access_control_allow_headers, "Content-Type");

  auto target = req_.target();
  auto method = req_.method();

  if (method == http::verb::options) {
    res.prepare_payload();
  } else if (method == http::verb::get && target == "/api/health") {
    res.body() = "{\"status\": \"ok\"}";
  } else if (method == http::verb::get && target == "/api/status") {
    res.body() = server_.getTelemetry().dump();
  } else if (method == http::verb::get && target == "/api/robot/config") {
    nlohmann::json j;
    j["ip"] = server_.getRobotIp();
    res.body() = j.dump();
  } else if (method == http::verb::post && target == "/api/robot/config") {
    auto j = nlohmann::json::parse(req_.body(), nullptr, false);
    if (!j.is_discarded() && j.contains("ip")) {
      server_.setRobotIp(j["ip"]);
    }
    res.body() = "{\"status\": \"ok\"}";
  } else if (method == http::verb::post && target == "/api/robot/test") {
    std::string ip = server_.getRobotIp();
    std::thread([ip, &server = this->server_]() {
      auto &config = Config::getInstance();
      SshService ssh(ip, config.getUsername(), config.getPassword());
      bool ok = ssh.connect();
      server.updateTelemetry({{"robot_connected", ok}});
      server.broadcastTelemetry(server.getTelemetry().dump());
    }).detach();
    res.body() = "{\"status\": \"testing\"}";
  } else if (method == http::verb::post && target == "/api/start_match") {
    auto req_json = nlohmann::json::parse(req_.body(), nullptr, false);
    std::string trait = "balanced";
    if (!req_json.is_discarded()) {
      if (req_json.contains("player1") && req_json["player1"].contains("mode"))
        trait = req_json["player1"]["mode"];
      else if (req_json.contains("player2") &&
               req_json["player2"].contains("mode"))
        trait = req_json["player2"]["mode"];
    }

    std::string robot_ip = server_.getRobotIp();
    std::thread([trait, robot_ip, &server = this->server_]() {
      if (SshDeployer::deployAndRun(robot_ip, trait)) {
        server.updateTelemetry({{"running", true}, {"state", "INIT"}});
        server.broadcastTelemetry(server.getTelemetry().dump());
      }
    }).detach();
    res.body() = "{\"status\": \"started\"}";
  } else if (method == http::verb::post && target == "/api/stop_match") {
    std::string robot_ip = server_.getRobotIp();
    std::thread([robot_ip, &server = this->server_]() {
      if (SshDeployer::stopMatch(robot_ip)) {
        server.updateTelemetry({{"running", false}, {"state", "IDLE"}});
        server.broadcastTelemetry(server.getTelemetry().dump());
      }
    }).detach();
    res.body() = "{\"status\": \"stopped\"}";
  } else if (method == http::verb::get && target == "/api/camera/frame") {
    // Returns the latest JPEG frame JSON sent by the robot's CameraStreamer.
    // Returns {"frame": null} when no frame has arrived yet.
    std::string f = server_.getLatestFrame();
    if (f.empty()) {
      res.body() = "{\"frame\": null}";
    } else {
      res.body() = f;
    }
  } else if (method == http::verb::post && target == "/api/vision/ball") {
    // host_vision.py posts detected ball coordinates from the host machine.
    auto j = nlohmann::json::parse(req_.body(), nullptr, false);
    if (!j.is_discarded()) {
      nlohmann::json patch;
      patch["host_ball_valid"] = true;
      if (j.contains("bx"))  patch["host_ball_bx"]  = j["bx"];
      if (j.contains("by"))  patch["host_ball_by"]  = j["by"];
      if (j.contains("bsz")) patch["host_ball_bsz"] = j["bsz"];
      server_.updateTelemetry(patch);
      server_.broadcastTelemetry(server_.getTelemetry().dump());
    }
    res.body() = "{\"status\": \"ok\"}";
  } else {
    res.result(http::status::not_found);
  }

  res.prepare_payload();
  http::write(stream_, res);
  doRead();
}
