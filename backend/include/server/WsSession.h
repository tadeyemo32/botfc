#ifndef WSSESSION_H
#define WSSESSION_H

#include "HttpServer.h"
#include <boost/asio.hpp>
#include <boost/beast.hpp>
#include <memory>
#include <string>

class WsSession : public std::enable_shared_from_this<WsSession> {
  boost::beast::websocket::stream<boost::beast::tcp_stream> ws_;
  boost::beast::flat_buffer buffer_;
  HttpServer &server_;
  std::string role_; // "frontend" | "bot" | "bot_camera" | "camera_feed"

public:
  WsSession(boost::asio::ip::tcp::socket &&socket, HttpServer &server,
            const std::string &role);

  template <class Body, class Allocator>
  void doAccept(boost::beast::http::request<
                Body, boost::beast::http::basic_fields<Allocator>>
                    req) {
    ws_.set_option(boost::beast::websocket::stream_base::timeout::suggested(
        boost::beast::role_type::server));
    ws_.accept(req);

    if (role_ == "frontend") {
      server_.addFrontendSession(shared_from_this());
      this->write(server_.getTelemetry().dump());
    } else if (role_ == "camera_feed") {
      server_.addCameraViewerSession(shared_from_this());
    }

    doRead();
  }

  void write(const std::string &msg);

private:
  void doRead();
  void onRead(boost::beast::error_code ec, std::size_t bytes_transferred);
};

#endif
