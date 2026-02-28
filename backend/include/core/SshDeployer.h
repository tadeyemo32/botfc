#ifndef SSHDEPLOYER_H
#define SSHDEPLOYER_H

#include <string>

class SshDeployer {
public:
  static bool deployAndRun(const std::string &host, const std::string &trait);
  static bool stopMatch(const std::string &host);
};

#endif
