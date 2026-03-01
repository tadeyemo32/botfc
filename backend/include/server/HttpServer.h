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

  // Telemetry (bot → server → frontend)
  void broadcastTelemetry(const std::string &msg);
  void updateTelemetry(const nlohmann::json &payload);
  nlohmann::json getTelemetry();

  void addFrontendSession(std::shared_ptr<WsSession> session);
  void removeFrontendSession(std::shared_ptr<WsSession> session);

  // Camera feed (bot_camera → server → camera_feed browsers)
  void addCameraViewerSession(std::shared_ptr<WsSession> session);
  void removeCameraViewerSession(std::shared_ptr<WsSession> session);
  void setLatestFrame(const std::string &frame_json);
  std::string getLatestFrame();
  void broadcastFrame(const std::string &frame_json);

  void setRobotIp(const std::string &ip);
  std::string getRobotIp();

  // Local C++ decision engine – computes recommended FSM action from telemetry
  std::string computeDecision();

  // Command channel (frontend/server → bot)
  void queueBotCommand(const nlohmann::json &cmd);
  nlohmann::json popBotCommand(); // returns {"action":null} if empty

private:
  void doAccept();

  boost::asio::io_context &ioc_;
  boost::asio::ip::tcp::acceptor acceptor_;

  std::mutex mutex_;
  std::set<std::shared_ptr<WsSession>> frontend_sessions_;
  std::set<std::shared_ptr<WsSession>> camera_sessions_;

  nlohmann::json latest_telemetry_;
  std::string latest_frame_;
  std::string robot_ip_;

  // Pending command for the robot (server-computed or frontend-issued)
  nlohmann::json pending_bot_command_;
  bool has_pending_command_ = false;

  friend class HttpSession;
};

#endif
