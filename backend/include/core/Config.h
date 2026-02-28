#ifndef CONFIG_H
#define CONFIG_H

#include <string>

class Config {
public:
  static Config &getInstance();

  bool load(const std::string &filename);

  std::string getRobotIp() const { return robot_ip; }
  std::string getUsername() const { return username; }
  std::string getPassword() const { return password; }
  int getServerPort() const { return server_port; }

  void setRobotIp(const std::string &ip) { robot_ip = ip; }

private:
  Config() = default;

  std::string robot_ip = "10.85.8.57";
  std::string username = "nao";
  std::string password = "nao";
  int server_port = 5050;
};

#endif
