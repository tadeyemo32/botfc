#ifndef BOTFC_BRAIN_H
#define BOTFC_BRAIN_H

#include <atomic>
#include <map>
#include <mutex>
#include <string>
#include <thread>

// Boost
#include <boost/shared_ptr.hpp>

// ALProxies
#include <almemoryfastaccess/almemoryfastaccess.h>
#include <alproxies/alledsproxy.h>
#include <alproxies/almemoryproxy.h>
#include <alproxies/almotionproxy.h>
#include <alproxies/alredballdetectionproxy.h>
#include <alproxies/alrobotpostureproxy.h>
#include <alproxies/alsonarproxy.h>
#include <alproxies/altexttospeechproxy.h>

#include "MLDataLogger.h"
#include "Roles.h"
#include "TelemetryClient.h"

// ALBroker
namespace AL {
class ALBroker;
}

class BotFCBrain {
public:
  BotFCBrain(boost::shared_ptr<AL::ALBroker> broker, const std::string &trait,
             const std::string &serverIP = "127.0.0.1", int serverPort = 5050);
  ~BotFCBrain();

  void start();
  void stop();

private:
  void run();

  void updateLocalMap();
  bool isInBounds(float x, float y);
  void enforceBounds();
  void safetyCheck();

  void doSearch();
  void doApproach();
  void doAlign();
  void doKick();
  void doTackle();

  boost::shared_ptr<AL::ALBroker> parentBroker;

  // WebSockets Subsystem
  std::unique_ptr<TelemetryClient> telemetryClient;

  // ML Subsystem
  std::unique_ptr<MLDataLogger> dataLogger;

  // Proxies
  boost::shared_ptr<AL::ALMotionProxy> motion;
  boost::shared_ptr<AL::ALRobotPostureProxy> posture;
  boost::shared_ptr<AL::ALMemoryProxy> memory;
  boost::shared_ptr<AL::ALTextToSpeechProxy> tts;
  boost::shared_ptr<AL::ALLedsProxy> leds;
  boost::shared_ptr<AL::ALRedBallDetectionProxy> ball_det;
  boost::shared_ptr<AL::ALSonarProxy> sonar_p;
  boost::shared_ptr<AL::ALMemoryFastAccess> fastAccess;

  std::thread fsmThread;
  std::atomic<bool> isRunning;

  // B-Human Logic
  BHumanBot::PlayerRole currentRole;
  BHumanBot::SkillState currentState;
  std::mutex stateMutex;

  int kickCount;
  int overheatCount;
  int breakRemaining;

  float originX, originY, originTheta;
  std::map<int, float> fieldMap;

  float lastBallTime;
  float lastManOnTime;
  float lastOverheatTime;

  float searchYaw;
  float searchYawDir;
};

#endif // BOTFC_BRAIN_H
