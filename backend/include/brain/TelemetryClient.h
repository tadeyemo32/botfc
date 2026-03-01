#ifndef TELEMETRYCLIENT_H
#define TELEMETRYCLIENT_H

#include <atomic>
#include <boost/asio.hpp>
#include <boost/beast.hpp>
#include <mutex>
#include <string>
#include <thread>

struct TelemetryData {
  std::string state;
  int kicks;
  std::string trait;
  float ballAge;
  int breakRemaining;
};

class TelemetryClient {
public:
  TelemetryClient(const std::string &host, int port);
  ~TelemetryClient();

  void start(const std::string &trait);
  void stop();
  void update(const TelemetryData &data);

private:
  void loop();
  std::string buildJsonPayload();

  std::string host_;
  int port_;
  std::string trait_;
  std::atomic<bool> isRunning;
  std::thread thread_;

  std::mutex mutex_;
  TelemetryData currentData_;
};

#endif
