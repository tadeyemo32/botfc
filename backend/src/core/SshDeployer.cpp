#include "SshDeployer.h"
#include "Config.h"
#include "SshService.h"
#include <iostream>

bool SshDeployer::deployAndRun(const std::string &host,
                               const std::string &trait) {
  auto &config = Config::getInstance();
  SshService ssh(host, config.getUsername(), config.getPassword());

  if (!ssh.connect()) {
    std::cerr << "[SshDeployer] Failed to connect to " << host << std::endl;
    return false;
  }

  std::cout << "[SshDeployer] Connected to " << host
            << ". Uploading Python brain..." << std::endl;

  // Upload Python brain script (relative to where server runs from bin/)
  std::string localScript = "../backend/src/brain/botfc_brain.py";
  std::string remoteScript = "/home/nao/botfc_brain.py";

  if (!ssh.uploadFile(localScript, remoteScript)) {
    std::cerr << "[SshDeployer] Failed to upload Python brain." << std::endl;
    return false;
  }

  std::cout << "[SshDeployer] Upload complete. Launching brain..." << std::endl;

  // Detect our IP so the robot can connect back to this server
  std::string serverIp = SshService::getLocalIp(host);
  std::cout << "[SshDeployer] Detected Server IP: " << serverIp << std::endl;

  // Kill any existing brain, then launch the new one
  ssh.executeCommand("pkill -f botfc_brain.py 2>/dev/null; sleep 1");

  std::string cmd =
      "export PYTHONPATH=/opt/aldebaran/lib/python2.7/site-packages && "
      "nohup python " +
      remoteScript +
      " --ip=127.0.0.1"
      " --pport=9559"
      " --trait=" +
      trait + " --server-ip=" + serverIp +
      " --server-port=5050"
      " > /home/nao/botfc_brain.log 2>&1 &";

  bool result = ssh.executeCommand(cmd);
  std::cout << "[SshDeployer] Execution triggered: "
            << (result ? "SUCCESS" : "FAILED") << std::endl;

  return result;
}

bool SshDeployer::stopMatch(const std::string &host) {
  auto &config = Config::getInstance();
  SshService ssh(host, config.getUsername(), config.getPassword());

  if (!ssh.connect()) {
    std::cerr << "[SshDeployer] Failed to connect to " << host
              << " to stop match." << std::endl;
    return false;
  }

  std::cout << "[SshDeployer] Killing remote Python brain..." << std::endl;
  return ssh.executeCommand("pkill -f botfc_brain.py");
}
