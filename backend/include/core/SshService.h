#ifndef SSHSERVICE_H
#define SSHSERVICE_H

#include <libssh/libssh.h>
#include <libssh/sftp.h>
#include <memory>
#include <string>

class SshService {
public:
  SshService(const std::string &host, const std::string &user,
             const std::string &password);
  ~SshService();

  bool connect();
  void disconnect();

  bool executeCommand(const std::string &command);
  bool uploadFile(const std::string &localPath, const std::string &remotePath);

  static std::string getLocalIp(const std::string &targetHost);

private:
  std::string host_;
  std::string user_;
  std::string password_;
  ssh_session session_ = nullptr;

  bool authenticate();
};

#endif
