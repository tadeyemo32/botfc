#ifndef HTTPSESSION_H
#define HTTPSESSION_H

#include "HttpServer.h"
#include <boost/asio.hpp>
#include <boost/beast.hpp>
#include <memory>

class HttpSession : public std::enable_shared_from_this<HttpSession> {
  boost::beast::tcp_stream stream_;
  boost::beast::flat_buffer buffer_;
  boost::beast::http::request<boost::beast::http::string_body> req_;
  HttpServer &server_;

public:
  HttpSession(boost::asio::ip::tcp::socket &&socket, HttpServer &server);
  void run();

private:
  void doRead();
  void onRead(boost::beast::error_code ec, std::size_t bytes_transferred);
  void handleRequest();
};

#endif
