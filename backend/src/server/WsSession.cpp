#include "WsSession.h"
#include <iostream>

namespace beast = boost::beast;
namespace websocket = beast::websocket;
namespace net = boost::asio;

WsSession::WsSession(net::ip::tcp::socket &&socket, HttpServer &server,
                     bool is_frontend)
    : ws_(std::move(socket)), server_(server), is_frontend_(is_frontend) {}

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
  if (ec == websocket::error::closed) {
    if (is_frontend_)
      server_.removeFrontendSession(shared_from_this());
    return;
  }

  if (ec) {
    if (is_frontend_)
      server_.removeFrontendSession(shared_from_this());
    return;
  }

  if (!is_frontend_) {
    // It's the bot! Parse its JSON telemetry
    std::string msg = beast::buffers_to_string(buffer_.data());
    try {
      auto j = nlohmann::json::parse(msg);
      server_.updateTelemetry(j);
      server_.broadcastTelemetry(msg);
    } catch (...) {
    }
  }

  buffer_.consume(buffer_.size());
  doRead();
}
