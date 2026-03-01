#include "HttpServer.h"
#include "Config.h"
#include "HttpSession.h"
#include "WsSession.h"
#include <iostream>

namespace beast = boost::beast;
namespace net = boost::asio;
using tcp = boost::asio::ip::tcp;

HttpServer::HttpServer(net::io_context &ioc, unsigned short port)
    : ioc_(ioc),
      acceptor_(ioc, tcp::endpoint(net::ip::make_address("0.0.0.0"), port)) {

  latest_telemetry_ = {{"state", "IDLE"},         {"trait", "none"},
                       {"running", false},        {"kicks", 0},
                       {"last_ball_seen", -1},    {"break_remaining", 0},
                       {"robot_connected", false},{"battery_pct", -1}};

  robot_ip_ = Config::getInstance().getRobotIp();
}

void HttpServer::start() {
  std::cout << "[Server] Acceptance loop started." << std::endl;
  doAccept();
}

// ── Telemetry ─────────────────────────────────────────────────────────────

void HttpServer::broadcastTelemetry(const std::string &msg) {
  std::lock_guard<std::mutex> lock(mutex_);
  for (auto &ws : frontend_sessions_) {
    ws->write(msg);
  }
}

void HttpServer::addFrontendSession(std::shared_ptr<WsSession> session) {
  std::lock_guard<std::mutex> lock(mutex_);
  frontend_sessions_.insert(session);
}

void HttpServer::removeFrontendSession(std::shared_ptr<WsSession> session) {
  std::lock_guard<std::mutex> lock(mutex_);
  frontend_sessions_.erase(session);
}

void HttpServer::updateTelemetry(const nlohmann::json &payload) {
  std::lock_guard<std::mutex> lock(mutex_);
  for (auto &el : payload.items()) {
    latest_telemetry_[el.key()] = el.value();
  }
}

nlohmann::json HttpServer::getTelemetry() {
  std::lock_guard<std::mutex> lock(mutex_);
  return latest_telemetry_;
}

// ── Camera feed ───────────────────────────────────────────────────────────

void HttpServer::addCameraViewerSession(std::shared_ptr<WsSession> session) {
  std::lock_guard<std::mutex> lock(mutex_);
  camera_sessions_.insert(session);
  // Push the latest frame immediately so the viewer doesn't wait
  if (!latest_frame_.empty()) {
    session->write(latest_frame_);
  }
}

void HttpServer::removeCameraViewerSession(std::shared_ptr<WsSession> session) {
  std::lock_guard<std::mutex> lock(mutex_);
  camera_sessions_.erase(session);
}

void HttpServer::setLatestFrame(const std::string &frame_json) {
  std::lock_guard<std::mutex> lock(mutex_);
  latest_frame_ = frame_json;
}

std::string HttpServer::getLatestFrame() {
  std::lock_guard<std::mutex> lock(mutex_);
  return latest_frame_;
}

void HttpServer::broadcastFrame(const std::string &frame_json) {
  std::lock_guard<std::mutex> lock(mutex_);
  for (auto &ws : camera_sessions_) {
    ws->write(frame_json);
  }
}

// ── Local C++ decision engine ─────────────────────────────────────────────

std::string HttpServer::computeDecision() {
  // Mirror FSM logic on the server using live telemetry from the robot.
  // This is broadcast to the frontend as "cpp_action" and also available
  // for the robot to poll via GET /api/bot/command.
  auto t = getTelemetry();
  bool ball_valid = t.value("ball_valid", false);
  if (!ball_valid)
    return "SEARCH";
  double bsz  = t.value("ball_bsz",  0.0);
  double bx   = t.value("ball_bx",   0.0);
  double dist = t.value("ball_dist", 99.0);
  if (dist > 0.8 || bsz < 0.04)
    return "APPROACH";
  if (std::abs(bx) > 0.12)
    return "ALIGN";
  return "KICK";
}

// ── Command channel (frontend → server → robot) ───────────────────────────

void HttpServer::queueBotCommand(const nlohmann::json &cmd) {
  std::lock_guard<std::mutex> lock(mutex_);
  pending_bot_command_  = cmd;
  has_pending_command_  = true;
}

nlohmann::json HttpServer::popBotCommand() {
  std::lock_guard<std::mutex> lock(mutex_);
  if (!has_pending_command_)
    return {{"action", nullptr}};
  nlohmann::json cmd = pending_bot_command_;
  has_pending_command_ = false;
  return cmd;
}

// ── Robot IP ──────────────────────────────────────────────────────────────

void HttpServer::setRobotIp(const std::string &ip) {
  std::lock_guard<std::mutex> lock(mutex_);
  robot_ip_ = ip;
}

std::string HttpServer::getRobotIp() {
  std::lock_guard<std::mutex> lock(mutex_);
  return robot_ip_;
}

// ── Accept loop ───────────────────────────────────────────────────────────

void HttpServer::doAccept() {
  acceptor_.async_accept(
      net::make_strand(ioc_),
      beast::bind_front_handler(
          [this](beast::error_code ec, tcp::socket socket) {
            if (!ec) {
              std::make_shared<HttpSession>(std::move(socket), *this)->run();
            }
            doAccept();
          }));
}
