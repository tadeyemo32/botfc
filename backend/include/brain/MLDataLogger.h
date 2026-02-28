#ifndef MLDATALOGGER_H
#define MLDATALOGGER_H

#include <alproxies/alvideodeviceproxy.h>
#include <atomic>
#include <boost/shared_ptr.hpp>
#include <map>
#include <mutex>
#include <string>
#include <thread>

// Snapshot context for JSON
struct TelemetrySnapshot {
  float headYaw;
  float headPitch;
  float sonarLeft;
  float sonarRight;
  bool ballFound;
  float ballBx;
  float ballBy;
  float ballBsz;
};

class MLDataLogger {
public:
  MLDataLogger(const std::string &robotIp, int robotPort);
  ~MLDataLogger();

  void start();
  void stop();

  // The FSM calls this to update the current truth
  void updateTelemetry(const TelemetrySnapshot &t);

private:
  void loop();
  void saveJson(const std::string &path, const TelemetrySnapshot &t);

  boost::shared_ptr<AL::ALVideoDeviceProxy> videoDevice;
  std::string videoClientName;
  std::thread loggerThread;
  std::atomic<bool> isRunning;

  std::mutex mtx;
  TelemetrySnapshot currentTelemetry;

  std::string outDir;
  int frameIndex;
};

#endif // MLDATALOGGER_H
