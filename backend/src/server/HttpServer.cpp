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
