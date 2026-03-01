#include "WsSession.h"
#include <iostream>

namespace beast = boost::beast;
namespace websocket = beast::websocket;
namespace net = boost::asio;

WsSession::WsSession(net::ip::tcp::socket &&socket, HttpServer &server,
                     const std::string &role)
    : ws_(std::move(socket)), server_(server), role_(role) {}

void WsSession::write(const std::string &msg) {
  auto self = shared_from_this();
  net::post(ws_.get_executor(), [this, self, msg]() {
    ws_.text(true);
    beast::error_code ec;
    ws_.write(net::buffer(msg), ec);
  });
}

void WsSession::doRead() {
  ws_.async_read(buffer_, beast::bind_front_handler(&WsSession::onRead,
                                                    shared_from_this()));
}

void WsSession::onRead(beast::error_code ec, std::size_t bytes_transferred) {
  boost::ignore_unused(bytes_transferred);

  auto cleanup = [&]() {
    if (role_ == "frontend")
      server_.removeFrontendSession(shared_from_this());
    else if (role_ == "camera_feed")
      server_.removeCameraViewerSession(shared_from_this());
  };

  if (ec == websocket::error::closed) {
    cleanup();
    return;
  }
  if (ec) {
    cleanup();
    return;
  }

  std::string msg = beast::buffers_to_string(buffer_.data());
  buffer_.consume(buffer_.size());

  if (role_ == "bot") {
    // Bot telemetry JSON → store and fan-out to frontend sessions
    try {
      auto j = nlohmann::json::parse(msg);
      server_.updateTelemetry(j);
      server_.broadcastTelemetry(msg);
    } catch (...) {
    }
  } else if (role_ == "bot_camera") {
    // Camera frame JSON from robot → store + relay to camera viewer sessions
    server_.setLatestFrame(msg);
    server_.broadcastFrame(msg);
  }
  // "frontend" and "camera_feed" sessions only receive; ignore their messages.

  doRead();
}
