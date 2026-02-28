#include "MLDataLogger.h"
#include <chrono>
#include <dirent.h>
#include <fstream>
#include <iostream>
#include <sys/stat.h>

#include <alvision/alimage.h>
#include <alvision/alvisiondefinitions.h>
#include <qi/log.hpp>

MLDataLogger::MLDataLogger(const std::string &robotIp, int robotPort)
    : isRunning(false), frameIndex(0), outDir("/home/nao/ml_data/") {
  // Create output directory if it doesn't exist natively on the NAO
  mkdir(outDir.c_str(), 0777);

  try {
    videoDevice = boost::shared_ptr<AL::ALVideoDeviceProxy>(
        new AL::ALVideoDeviceProxy(robotIp, robotPort));
  } catch (const std::exception &e) {
    qiLogError("MLLogger") << "Failed to init ALVideoDevice: " << e.what();
  }
}

MLDataLogger::~MLDataLogger() { stop(); }

void MLDataLogger::start() {
  if (isRunning)
    return;
  if (!videoDevice)
    return;

  // kTopCamera, kQVGA (320x240), kYUV422ColorSpace, 5 fps
  try {
    videoClientName = videoDevice->subscribeCamera(
        "BotFC_ML_Logger", AL::kTopCamera, AL::kQVGA, AL::kYUV422ColorSpace, 5);
    isRunning = true;
    loggerThread = std::thread(&MLDataLogger::loop, this);
    qiLogInfo("MLLogger") << "ML Data Logger started at 5Hz to " << outDir;
  } catch (const std::exception &e) {
    qiLogError("MLLogger") << "SubscribeCamera failed: " << e.what();
  }
}

void MLDataLogger::stop() {
  if (!isRunning)
    return;
  isRunning = false;
  if (loggerThread.joinable()) {
    loggerThread.join();
  }
  if (videoDevice && !videoClientName.empty()) {
    try {
      videoDevice->unsubscribe(videoClientName);
    } catch (...) {
    }
  }
}

void MLDataLogger::updateTelemetry(const TelemetrySnapshot &t) {
  std::lock_guard<std::mutex> lock(mtx);
  currentTelemetry = t;
}

void MLDataLogger::saveJson(const std::string &path,
                            const TelemetrySnapshot &t) {
  std::ofstream fs(path);
  if (!fs.is_open())
    return;

  // Building simple JSON for TF Data loaders
  fs << "{" << std::endl;
  fs << "  \"headYaw\": " << t.headYaw << "," << std::endl;
  fs << "  \"headPitch\": " << t.headPitch << "," << std::endl;
  fs << "  \"sonarLeft\": " << t.sonarLeft << "," << std::endl;
  fs << "  \"sonarRight\": " << t.sonarRight << "," << std::endl;
  fs << "  \"ballFound\": " << (t.ballFound ? "true" : "false") << ","
     << std::endl;
  fs << "  \"ballBx\": " << t.ballBx << "," << std::endl;
  fs << "  \"ballBy\": " << t.ballBy << "," << std::endl;
  fs << "  \"ballBsz\": " << t.ballBsz << std::endl;
  fs << "}" << std::endl;
  fs.close();
}

void MLDataLogger::loop() {
  while (isRunning) {
    try {
      AL::ALValue imgData = videoDevice->getImageRemote(videoClientName);
      if (imgData.isValid() && imgData.getSize() > 6) {
        // imgData structure: [0]: width, [1]: height, [2]: layers, [3]:
        // colorSpace, [4]: timeStamp (s), [5]: timeStamp (us), [6]: binary data
        int width = (int)imgData[0];
        int height = (int)imgData[1];
        const void *dataPtr = imgData[6].GetBinary();
        int dataSize = imgData[6].getSize();

        // Write raw YUV422 to .raw file since opencv isn't guaranteed on
        // all 2.8 SDKs A companion python script will decode this into JPEGs
        // for TF models
        std::string baseName = outDir + "frame_" + std::to_string(frameIndex);
        std::string imgPath = baseName + ".raw";
        std::string jsonPath = baseName + ".json";

        std::ofstream imgFile(imgPath, std::ios::out | std::ios::binary);
        if (imgFile.is_open()) {
          imgFile.write(static_cast<const char *>(dataPtr), dataSize);
          imgFile.close();
        }

        // Snap the exact FSM labels at this exact frame timestamp
        TelemetrySnapshot t;
        {
          std::lock_guard<std::mutex> lock(mtx);
          t = currentTelemetry;
        }
        saveJson(jsonPath, t);

        frameIndex++;
        videoDevice->releaseImage(videoClientName);
      }
    } catch (const std::exception &e) {
      // Log silent dropped frame
    }

    std::this_thread::sleep_for(
        std::chrono::milliseconds(200)); // Exactly 5Hz loop
  }
}
