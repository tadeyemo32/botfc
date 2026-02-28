#include <chrono>
#include <iostream>
#include <signal.h>
#include <thread>

#include <alcommon/albroker.h>
#include <alcommon/albrokermanager.h>
#include <qi/log.hpp>

#include "BotFCBrain.h"

BotFCBrain *g_brain = nullptr;

void signalHandler(int /*signum*/) {
  if (g_brain) {
    qiLogInfo("BotFCBrain") << "Signal caught. Stopping standalone executor.";
    g_brain->stop();
  }
  exit(0);
}

int main(int argc, char *argv[]) {
  std::string robotIp = "127.0.0.1";
  int robotPort = 9559;
  std::string trait = "balanced";
  std::string serverIp = "127.0.0.1";
  int serverPort = 5050;

  // Read trait if passed argument (e.g. ./botfc_brain --trait=offense)
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (arg.find("--trait=") == 0) {
      trait = arg.substr(8);
    } else if (arg.find("--ip=") == 0) {
      robotIp = arg.substr(5);
    } else if (arg.find("--pip=") == 0) {
      robotIp = arg.substr(6);
    } else if (arg.find("--pport=") == 0) {
      robotPort = std::stoi(arg.substr(8));
    } else if (arg.find("--server-ip=") == 0) {
      serverIp = arg.substr(12);
    } else if (arg.find("--server-port=") == 0) {
      serverPort = std::stoi(arg.substr(14));
    }
  }

  signal(SIGINT, signalHandler);
  signal(SIGTERM, signalHandler);

  qiLogInfo("BotFCBrain") << "Booting Bot FC Standalone Executable...";

  try {
    // Create an ALBroker targeting the Naoqi Core locally
    boost::shared_ptr<AL::ALBroker> broker = AL::ALBroker::createBroker(
        "BotFCBroker", "0.0.0.0", 0, robotIp, robotPort);
    AL::ALBrokerManager::setInstance(broker->fBrokerManager.lock());
    AL::ALBrokerManager::getInstance()->addBroker(broker);

    BotFCBrain brain(broker, trait, serverIp, serverPort);
    g_brain = &brain;

    qiLogInfo("BotFCBrain")
        << "BotFCBrain initialized successfully. Starting AI Loops...";
    brain.start();

    // Blocking sleep thread to keep FSM subsystems alive inside the C++
    // application
    while (true) {
      std::this_thread::sleep_for(std::chrono::seconds(1));
    }

  } catch (const std::exception &e) {
    qiLogError("BotFCBrain") << "FATAL Initialization Error: " << e.what();
    return 1;
  }

  return 0;
}
