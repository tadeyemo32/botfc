#include "Config.h"
#include <fstream>
#include <iostream>
#include <sstream>

Config &Config::getInstance() {
  static Config instance;
  return instance;
}

static std::string trim(const std::string &s) {
  size_t first = s.find_first_not_of(" \t\r\n\"");
  if (first == std::string::npos)
    return "";
  size_t last = s.find_last_not_of(" \t\r\n\"");
  return s.substr(first, (last - first + 1));
}

bool Config::load(const std::string &filename) {
  std::ifstream file(filename);
  if (!file.is_open()) {
    std::cerr << "[Config] Could not open config file: " << filename
              << ". Using defaults." << std::endl;
    return false;
  }

  std::string line;
  while (std::getline(file, line)) {
    size_t sep = line.find(':');
    if (sep == std::string::npos)
      continue;

    std::string key = trim(line.substr(0, sep));
    std::string value = trim(line.substr(sep + 1));

    if (key == "ip")
      robot_ip = value;
    else if (key == "username")
      username = value;
    else if (key == "password")
      password = value;
    else if (key == "port") {
      try {
        server_port = std::stoi(value);
      } catch (...) {
      }
    }
  }

  std::cout << "[Config] Loaded configuration from " << filename << std::endl;
  std::cout << "[Config] Robot IP: " << robot_ip << ", User: " << username
            << std::endl;
  return true;
}
