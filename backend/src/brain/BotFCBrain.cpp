#include "BotFCBrain.h"

#include <chrono>
#include <cmath>
#include <iostream>

#include <boost/asio/connect.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/websocket.hpp>
#include <qi/log.hpp>

// Include ALBroker
#include <alcommon/albroker.h>

namespace beast = boost::beast;
namespace http = beast::http;
namespace websocket = beast::websocket;
namespace net = boost::asio;
using tcp = boost::asio::ip::tcp;

const float MAX_FIELD_RADIUS = 2.5f;
const float COMBAT_DISTANCE = 0.40f;
const float MOTOR_TEMP_LIMIT = 60.0f;

static float getTime() {
  auto now = std::chrono::system_clock::now();
  return std::chrono::duration<float>(now.time_since_epoch()).count();
}

BotFCBrain::BotFCBrain(boost::shared_ptr<AL::ALBroker> broker,
                       const std::string &traitStr, const std::string &serverIP,
                       int serverPort)
    : parentBroker(broker), isRunning(false), kickCount(0), overheatCount(0),
      breakRemaining(0), originX(0.f), originY(0.f), originTheta(0.f),
      lastBallTime(0.f), lastManOnTime(0.f), lastOverheatTime(0.f),
      searchYaw(0.f), searchYawDir(1.0f) {

  std::string trait = "balanced";
  if (traitStr == "offense") {
    currentRole = BHumanBot::PlayerRole::STRIKER;
    trait = "offense";
  } else if (traitStr == "defense") {
    currentRole = BHumanBot::PlayerRole::DEFENDER;
    trait = "defense";
  } else {
    currentRole = BHumanBot::PlayerRole::BALANCED;
  }

  currentState = BHumanBot::SkillState::INIT;
  telemetryClient = std::unique_ptr<TelemetryClient>(
      new TelemetryClient(serverIP, serverPort));
}

BotFCBrain::~BotFCBrain() { stop(); }

void BotFCBrain::start() {
  if (isRunning)
    return;

  try {
    motion = boost::shared_ptr<AL::ALMotionProxy>(
        new AL::ALMotionProxy(parentBroker));
    posture = boost::shared_ptr<AL::ALRobotPostureProxy>(
        new AL::ALRobotPostureProxy(parentBroker));
    memory = boost::shared_ptr<AL::ALMemoryProxy>(
        new AL::ALMemoryProxy(parentBroker));
    tts = boost::shared_ptr<AL::ALTextToSpeechProxy>(
        new AL::ALTextToSpeechProxy(parentBroker));
    leds =
        boost::shared_ptr<AL::ALLedsProxy>(new AL::ALLedsProxy(parentBroker));
    ball_det = boost::shared_ptr<AL::ALRedBallDetectionProxy>(
        new AL::ALRedBallDetectionProxy(parentBroker));
    sonar_p =
        boost::shared_ptr<AL::ALSonarProxy>(new AL::ALSonarProxy(parentBroker));

    fastAccess =
        boost::shared_ptr<AL::ALMemoryFastAccess>(new AL::ALMemoryFastAccess());
    std::vector<std::string> keys;
    keys.push_back("Device/SubDeviceList/LHipPitch/Temperature/Sensor/Value");
    keys.push_back("Device/SubDeviceList/RHipPitch/Temperature/Sensor/Value");
    keys.push_back("Device/SubDeviceList/LKneePitch/Temperature/Sensor/Value");
    keys.push_back("Device/SubDeviceList/RKneePitch/Temperature/Sensor/Value");
    fastAccess->ConnectToVariables(parentBroker, keys);

    // Spin up ML Data Logger using loopback
    dataLogger =
        std::unique_ptr<MLDataLogger>(new MLDataLogger("127.0.0.1", 9559));

  } catch (const std::exception &e) {
    qiLogError("BotFC") << "Failed to init proxies: " << e.what();
    return;
  }

  motion->setStiffnesses("Body", 1.0f);
  try {
    ball_det->subscribe("BotFCBrain", 33, 0.0f);
  } catch (...) {
  }
  try {
    sonar_p->subscribe("BotFCBrain");
  } catch (...) {
  }

  posture->goToPosture("StandInit", 1.0f);
  tts->post("say", "Pure standalone C++ Brain online.");

  try {
    std::vector<float> p = motion->getRobotPosition(true);
    originX = p[0];
    originY = p[1];
    originTheta = p[2];
  } catch (...) {
  }

  lastBallTime = getTime() - 100.0f;
  currentState = BHumanBot::SkillState::SEARCH;
  isRunning = true;

  dataLogger->start();

  std::string traitStr =
      (currentRole == BHumanBot::PlayerRole::STRIKER
           ? "offense"
           : (currentRole == BHumanBot::PlayerRole::DEFENDER ? "defense"
                                                             : "balanced"));
  telemetryClient->start(traitStr);

  // Spawn FSM Subsystem
  fsmThread = std::thread(&BotFCBrain::run, this);
}

void BotFCBrain::stop() {
  if (!isRunning)
    return;
  isRunning = false;

  if (dataLogger)
    dataLogger->stop();
  if (telemetryClient)
    telemetryClient->stop();

  if (fsmThread.joinable())
    fsmThread.join();

  try {
    motion->stopMove();
  } catch (...) {
  }
  try {
    ball_det->unsubscribe("BotFCBrain");
  } catch (...) {
  }
  try {
    sonar_p->unsubscribe("BotFCBrain");
  } catch (...) {
  }
  try {
    posture->goToPosture("Crouch", 0.8f);
    motion->setStiffnesses("Body", 0.0f);
  } catch (...) {
  }

  qiLogInfo("BotFC") << "Standalone Executable exited safely.";
}

void BotFCBrain::run() {
  while (isRunning) {
    safetyCheck();

    BHumanBot::SkillState s;
    int k_count, b_rem;
    float l_ball_time;
    {
      std::lock_guard<std::mutex> lock(stateMutex);
      s = currentState;
      k_count = kickCount;
      b_rem = breakRemaining;
      l_ball_time = lastBallTime;
    }

    // Update Telemetry
    TelemetryData t;
    t.state = BHumanBot::stateToString(s);
    t.kicks = k_count;
    t.breakRemaining = b_rem;
    t.ballAge = (l_ball_time > 0.0f) ? (getTime() - l_ball_time) : -1.0f;
    telemetryClient->update(t);

    if (s != BHumanBot::SkillState::HALFTIME) {
      enforceBounds();

      switch (s) {
      case BHumanBot::SkillState::SEARCH:
        doSearch();
        break;
      case BHumanBot::SkillState::APPROACH:
        doApproach();
        break;
      case BHumanBot::SkillState::ALIGN:
        doAlign();
        break;
      case BHumanBot::SkillState::KICK:
        doKick();
        break;
      case BHumanBot::SkillState::TACKLE:
        doTackle();
        break;
      default:
        std::lock_guard<std::mutex> lock(stateMutex);
        currentState = BHumanBot::SkillState::SEARCH;
        break;
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }
}

void BotFCBrain::updateLocalMap() {
  try {
    float sl =
        (float)memory->getData("Device/SubDeviceList/US/Left/Sensor/Value");
    float sr =
        (float)memory->getData("Device/SubDeviceList/US/Right/Sensor/Value");
    std::vector<float> p = motion->getRobotPosition(true);
    float left_angle = (p[2] + 0.5f) * (180.0f / M_PI);
    float right_angle = (p[2] - 0.5f) * (180.0f / M_PI);
    int sec_l = ((int)(left_angle / 30.0f)) * 30;
    int sec_r = ((int)(right_angle / 30.0f)) * 30;

    {
      std::lock_guard<std::mutex> lock(stateMutex);
      if (fieldMap.find(sec_l) == fieldMap.end() || sl < fieldMap[sec_l])
        fieldMap[sec_l] = sl;
      if (fieldMap.find(sec_r) == fieldMap.end() || sr < fieldMap[sec_r])
        fieldMap[sec_r] = sr;
    }

    // Build Telemetry snapshot for ML Logging
    TelemetrySnapshot t;
    t.headYaw = motion->getAngles("HeadYaw", false)[0];
    t.headPitch = motion->getAngles("HeadPitch", false)[0];
    t.sonarLeft = sl;
    t.sonarRight = sr;

    AL::ALValue data = memory->getData("redBallDetected");
    if (data.isValid() && data.getSize() >= 2) {
      t.ballFound = true;
      AL::ALValue info = data[1];
      t.ballBx = (float)info[0];
      t.ballBy = (float)info[1];
      t.ballBsz = (float)info[2];
    } else {
      t.ballFound = false;
      t.ballBx = 0.f;
      t.ballBy = 0.f;
      t.ballBsz = 0.f;
    }
    if (dataLogger)
      dataLogger->updateTelemetry(t);

  } catch (...) {
  }
}

bool BotFCBrain::isInBounds(float x, float y) {
  float dx, dy;
  std::map<int, float> l_map;
  {
    std::lock_guard<std::mutex> lock(stateMutex);
    dx = x - originX;
    dy = y - originY;
    l_map = fieldMap;
  }
  float dist = std::sqrt(dx * dx + dy * dy);
  if (dist > MAX_FIELD_RADIUS)
    return false;
  float angle = std::atan2(dy, dx) * (180.0f / M_PI);
  int sector = ((int)(angle / 30.0f)) * 30;
  if (l_map.find(sector) != l_map.end() && dist > l_map[sector] * 0.85f)
    return false;
  return true;
}

void BotFCBrain::enforceBounds() {
  try {
    std::vector<float> p = motion->getRobotPosition(true);
    if (!isInBounds(p[0], p[1])) {
      motion->stopMove();
      leds->fadeRGB("AllLeds", 1.0f, 0.0f, 1.0f, 0.15f);
      float target_angle = std::atan2(originY - p[1], originX - p[0]);
      float turn = target_angle - p[2];
      while (turn > M_PI)
        turn -= 2.0f * M_PI;
      while (turn < -M_PI)
        turn += 2.0f * M_PI;
      motion->moveTo(0.0f, 0.0f, turn);
      motion->moveTo(0.3f, 0.0f, 0.0f);
    }
  } catch (...) {
  }
}

void BotFCBrain::safetyCheck() {
  std::vector<float> temps;
  fastAccess->GetValues(temps);
  float max_t = 0.0f;
  for (float t : temps) {
    if (t > max_t)
      max_t = t;
  }

  if (max_t > MOTOR_TEMP_LIMIT) {
    motion->stopMove();
    float now = getTime();
    if (now - lastOverheatTime < 180.0f)
      overheatCount++;
    else
      overheatCount = 0;

    lastOverheatTime = now;
    int cd = 60 + (overheatCount * 30);
    int mins = cd / 60;
    int secs = cd % 60;

    std::string phrase = "Motors at " + std::to_string((int)max_t) +
                         ". I need a " + std::to_string(secs) +
                         " second break.";
    if (mins > 0)
      phrase = "Motors at " + std::to_string((int)max_t) + ". I need a " +
               std::to_string(mins) + " minute break.";

    tts->post("say", phrase);
    leds->fadeRGB("AllLeds", 1.0f, 0.64f, 0.0f, 0.15f);
    posture->goToPosture("Crouch", 0.8f);
    motion->setStiffnesses("Body", 0.0f);

    {
      std::lock_guard<std::mutex> lock(stateMutex);
      currentState = BHumanBot::SkillState::HALFTIME;
    }

    for (int r = cd; r > 0; --r) {
      if (!isRunning)
        break;
      {
        std::lock_guard<std::mutex> lock(stateMutex);
        breakRemaining = r;
      }
      if (r % 30 == 0)
        tts->post("say", std::to_string(r / 60) + " minutes remaining.");
      std::this_thread::sleep_for(std::chrono::seconds(1));
    }

    {
      std::lock_guard<std::mutex> lock(stateMutex);
      breakRemaining = 0;
    }

    if (isRunning) {
      tts->post("say", "Cooling complete.");
      motion->setStiffnesses("Body", 1.0f);
      posture->goToPosture("StandInit", 1.0f);
      {
        std::lock_guard<std::mutex> lock(stateMutex);
        currentState = BHumanBot::SkillState::SEARCH;
      }
    }
  }
}

void BotFCBrain::doSearch() {
  updateLocalMap();
  leds->fadeRGB("AllLeds", 1.0f, 0.2f, 0.0f, 0.15f);

  try {
    AL::ALValue data = memory->getData("redBallDetected");
    if (data.isValid() && data.getSize() >= 2) {
      motion->stopMove();
      {
        std::lock_guard<std::mutex> lock(stateMutex);
        lastBallTime = getTime();
        currentState = BHumanBot::SkillState::APPROACH;
      }
      motion->setAngles("HeadPitch", 0.0f, 0.2f);
      motion->setAngles("HeadYaw", 0.0f, 0.2f);
      tts->post("say", "Ball found!");
      return;
    }
  } catch (...) {
  }

  searchYaw += searchYawDir * 0.15f;
  if (searchYaw > 1.0f) {
    searchYaw = 1.0f;
    searchYawDir = -1.0f;
  } else if (searchYaw < -1.0f) {
    searchYaw = -1.0f;
    searchYawDir = 1.0f;
  }

  motion->setAngles("HeadYaw", searchYaw, 0.2f);
  motion->setAngles("HeadPitch", 0.15f, 0.2f);

  float ltime;
  {
    std::lock_guard<std::mutex> lock(stateMutex);
    ltime = lastBallTime;
  }
  if (getTime() - ltime > 15.0f)
    motion->moveToward(0.0f, 0.0f, 0.2f);
  else
    motion->stopMove();
}

void BotFCBrain::doApproach() {
  updateLocalMap();
  leds->fadeRGB("AllLeds", 0.0f, 1.0f, 0.0f, 0.15f);
  motion->setAngles("HeadPitch", 0.25f, 0.3f);

  float now = getTime();
  if (now - lastManOnTime > 4.0f) {
    tts->post("say", "Man on, man on");
    lastManOnTime = now;
  }

  bool found = false;
  float bx = 0, by = 0, bsz = 0;
  try {
    AL::ALValue data = memory->getData("redBallDetected");
    if (data.isValid() && data.getSize() >= 2) {
      found = true;
      AL::ALValue info = data[1];
      bx = (float)info[0];
      by = (float)info[1];
      bsz = (float)info[2];
    }
  } catch (...) {
  }

  if (!found) {
    {
      std::lock_guard<std::mutex> lock(stateMutex);
      currentState = BHumanBot::SkillState::SEARCH;
    }
    return;
  }

  {
    std::lock_guard<std::mutex> lock(stateMutex);
    lastBallTime = now;
  }

  float sl = 9.f, sr = 9.f;
  try {
    sl = (float)memory->getData("Device/SubDeviceList/US/Left/Sensor/Value");
    sr = (float)memory->getData("Device/SubDeviceList/US/Right/Sensor/Value");
  } catch (...) {
  }
  float min_sonar = std::min(sl, sr);

  if (min_sonar <= COMBAT_DISTANCE && min_sonar < bsz * 5.0f + 0.1f) {
    motion->stopMove();
    {
      std::lock_guard<std::mutex> lock(stateMutex);
      currentState = BHumanBot::SkillState::TACKLE;
    }
    return;
  }

  if (bsz > 0.08f) {
    motion->stopMove();
    {
      std::lock_guard<std::mutex> lock(stateMutex);
      currentState = BHumanBot::SkillState::ALIGN;
    }
    return;
  }

  float turn = std::max(-0.6f, std::min(0.6f, -bx * 3.0f));
  float speed = std::max(
      0.2f, std::min(0.7f, 0.6f * (1.0f - std::min(bsz / 0.08f, 0.8f))));
  motion->moveToward(speed, 0.0f, turn);
}

void BotFCBrain::doAlign() {
  updateLocalMap();
  leds->fadeRGB("AllLeds", 0.0f, 1.0f, 0.0f, 0.15f);
  motion->setAngles("HeadPitch", 0.3f, 0.3f);

  float now = getTime();
  if (now - lastManOnTime > 4.0f) {
    tts->post("say", "Man on, man on");
    lastManOnTime = now;
  }

  bool found = false;
  float bx = 0, bsz = 0;
  try {
    AL::ALValue data = memory->getData("redBallDetected");
    if (data.isValid() && data.getSize() >= 2) {
      found = true;
      AL::ALValue info = data[1];
      bx = (float)info[0];
      bsz = (float)info[2];
    }
  } catch (...) {
  }

  if (!found) {
    motion->stopMove();
    {
      std::lock_guard<std::mutex> lock(stateMutex);
      currentState = BHumanBot::SkillState::SEARCH;
    }
    return;
  }

  {
    std::lock_guard<std::mutex> lock(stateMutex);
    lastBallTime = now;
  }

  float sl = 9.f, sr = 9.f;
  try {
    sl = (float)memory->getData("Device/SubDeviceList/US/Left/Sensor/Value");
    sr = (float)memory->getData("Device/SubDeviceList/US/Right/Sensor/Value");
  } catch (...) {
  }
  float min_sonar = std::min(sl, sr);

  if (min_sonar <= COMBAT_DISTANCE) {
    motion->stopMove();
    {
      std::lock_guard<std::mutex> lock(stateMutex);
      currentState = BHumanBot::SkillState::TACKLE;
    }
    return;
  }

  if (std::abs(bx) < 0.05f && bsz > 0.10f) {
    motion->stopMove();
    tts->post("say", "I see the goal");
    {
      std::lock_guard<std::mutex> lock(stateMutex);
      currentState = BHumanBot::SkillState::KICK;
    }
    return;
  }

  if (std::abs(bx) > 0.03f) {
    float lateral = -bx * 0.15f;
    float turn = -bx * 0.8f;
    motion->moveToward(0.1f, lateral, turn);
  } else {
    motion->moveToward(0.15f, 0.0f, 0.0f);
  }
}

void BotFCBrain::doTackle() {
  leds->fadeRGB("AllLeds", 1.0f, 0.0f, 0.0f, 0.15f);
  try {
    tts->post("say", "Pushing!");
    motion->setStiffnesses("Body", 1.0f);
    posture->goToPosture("StandInit", 0.8f);
    motion->wbEnable(true);

    AL::ALValue joints = AL::ALValue::array("LShoulderPitch", "RShoulderPitch");
    AL::ALValue angles = AL::ALValue::array(0.0f, 0.0f);
    motion->setAngles(joints, angles, 0.3f);

    joints = AL::ALValue::array("LKneePitch", "RKneePitch");
    angles = AL::ALValue::array(0.4f, 0.4f);
    motion->setAngles(joints, angles, 0.3f);

    std::this_thread::sleep_for(std::chrono::milliseconds(500));

    motion->moveToward(1.0f, 0.0f, 0.0f);
    std::this_thread::sleep_for(std::chrono::milliseconds(2000));
    motion->stopMove();
    motion->wbEnable(false);

    joints = AL::ALValue::array("LShoulderPitch", "RShoulderPitch");
    angles = AL::ALValue::array(1.5f, 1.5f);
    motion->setAngles(joints, angles, 0.4f);
    posture->goToPosture("StandInit", 0.8f);

  } catch (...) {
  }
  {
    std::lock_guard<std::mutex> lock(stateMutex);
    currentState = BHumanBot::SkillState::SEARCH;
  }
}

void BotFCBrain::doKick() {
  leds->fadeRGB("AllLeds", 0.0f, 0.0f, 1.0f, 0.15f);
  motion->stopMove();
  motion->setAngles("HeadPitch", 0.35f, 0.5f);
  std::this_thread::sleep_for(std::chrono::milliseconds(200));

  bool found = false;
  float bx = 0;
  try {
    AL::ALValue data = memory->getData("redBallDetected");
    if (data.isValid() && data.getSize() >= 2) {
      AL::ALValue info = data[1];
      found = true;
      bx = (float)info[0];
    }
  } catch (...) {
  }

  if (!found) {
    {
      std::lock_guard<std::mutex> lock(stateMutex);
      currentState = BHumanBot::SkillState::SEARCH;
    }
    return;
  }

  float side_step_y = (bx < -0.02f) ? -0.04f : 0.04f;
  std::string kick_leg = (bx < -0.02f) ? "L" : "R";

  tts->post("say", "Kick!");

  try {
    posture->goToPosture("Stand", 0.8f);
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    motion->moveTo(0.0f, side_step_y, 0.0f);
    std::this_thread::sleep_for(std::chrono::milliseconds(200));

    std::string hip, knee, support_roll;
    if (kick_leg == "R") {
      hip = "RHipPitch";
      knee = "RKneePitch";
      support_roll = "LHipRoll";
    } else {
      hip = "LHipPitch";
      knee = "LKneePitch";
      support_roll = "RHipRoll";
    }

    motion->setAngles(support_roll, 0.15f, 0.4f);
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
    motion->setAngles(hip, -0.4f, 0.5f);
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    motion->setAngles(hip, 0.8f, 1.0f);
    motion->setAngles(knee, -0.7f, 1.0f);
    std::this_thread::sleep_for(std::chrono::milliseconds(300));

    posture->goToPosture("Stand", 0.8f);
    {
      std::lock_guard<std::mutex> lock(stateMutex);
      kickCount++;
      currentState = BHumanBot::SkillState::SEARCH;
    }
  } catch (...) {
    posture->goToPosture("Stand", 0.8f);
    {
      std::lock_guard<std::mutex> lock(stateMutex);
      currentState = BHumanBot::SkillState::SEARCH;
    }
  }
}
