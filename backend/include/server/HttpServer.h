#ifndef HTTPSERVER_H
#define HTTPSERVER_H

#include <boost/asio.hpp>
#include <boost/beast.hpp>
#include <memory>
#include <mutex>
#include <nlohmann/json.hpp>
#include <set>
#include <string>

class WsSession;

class HttpServer {
public:
  HttpServer(boost::asio::io_context &ioc, unsigned short port);
  void start();

  // Broadcast message to all connected frontend websockets
  void broadcastTelemetry(const std::string &msg);
  void updateTelemetry(const nlohmann::json &payload);
  nlohmann::json getTelemetry();

  void addFrontendSession(std::shared_ptr<WsSession> session);
  void removeFrontendSession(std::shared_ptr<WsSession> session);

  void setRobotIp(const std::string &ip);
  std::string getRobotIp();

private:
  void doAccept();

  boost::asio::io_context &ioc_;
  boost::asio::ip::tcp::acceptor acceptor_;

  std::mutex mutex_;
  std::set<std::shared_ptr<WsSession>> frontend_sessions_;

  nlohmann::json latest_telemetry_;
  std::string robot_ip_;

  friend class HttpSession;
};

#endif
