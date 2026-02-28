#include "SshService.h"
#include <arpa/inet.h>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

SshService::SshService(const std::string &host, const std::string &user,
                       const std::string &password)
    : host_(host), user_(user), password_(password) {}

SshService::~SshService() { disconnect(); }

bool SshService::connect() {
  session_ = ssh_new();
  if (!session_)
    return false;

  ssh_options_set(session_, SSH_OPTIONS_HOST, host_.c_str());
  ssh_options_set(session_, SSH_OPTIONS_USER, user_.c_str());
  long timeout = 5;
  ssh_options_set(session_, SSH_OPTIONS_TIMEOUT, &timeout);

  if (ssh_connect(session_) != SSH_OK) {
    std::cerr << "[SSH] Connection error: " << ssh_get_error(session_)
              << std::endl;
    return false;
  }

  if (!authenticate()) {
    std::cerr << "[SSH] Authentication failed." << std::endl;
    return false;
  }

  return true;
}

void SshService::disconnect() {
  if (session_) {
    ssh_disconnect(session_);
    ssh_free(session_);
    session_ = nullptr;
  }
}

bool SshService::authenticate() {
  ssh_userauth_none(session_, NULL);
  int methods = ssh_userauth_list(session_, NULL);

  // Try provided password
  if (methods & SSH_AUTH_METHOD_PASSWORD) {
    if (ssh_userauth_password(session_, NULL, password_.c_str()) ==
        SSH_AUTH_SUCCESS)
      return true;
    // Fallbacks
    if (ssh_userauth_password(session_, NULL, "Na0Na0") == SSH_AUTH_SUCCESS)
      return true;
    if (ssh_userauth_password(session_, NULL, "nao") == SSH_AUTH_SUCCESS)
      return true;
  }

  // Try interactive
  if (methods & SSH_AUTH_METHOD_INTERACTIVE) {
    const char *passwords[] = {password_.c_str(), "Na0Na0", "nao"};
    for (int i = 0; i < 3; i++) {
      int rc = ssh_userauth_kbdint(session_, NULL, NULL);
      while (rc == SSH_AUTH_INFO) {
        int nprompts = ssh_userauth_kbdint_getnprompts(session_);
        for (int p = 0; p < nprompts; p++) {
          ssh_userauth_kbdint_setanswer(session_, p, passwords[i]);
        }
        rc = ssh_userauth_kbdint(session_, NULL, NULL);
      }
      if (rc == SSH_AUTH_SUCCESS)
        return true;
    }
  }

  return false;
}

bool SshService::executeCommand(const std::string &command) {
  if (!session_)
    return false;
  ssh_channel channel = ssh_channel_new(session_);
  if (!channel)
    return false;

  if (ssh_channel_open_session(channel) != SSH_OK) {
    ssh_channel_free(channel);
    return false;
  }

  if (ssh_channel_request_exec(channel, command.c_str()) != SSH_OK) {
    ssh_channel_close(channel);
    ssh_channel_free(channel);
    return false;
  }

  ssh_channel_close(channel);
  ssh_channel_free(channel);
  return true;
}

bool SshService::uploadFile(const std::string &localPath,
                            const std::string &remotePath) {
  if (!session_)
    return false;
  sftp_session sftp = sftp_new(session_);
  if (!sftp || sftp_init(sftp) != SSH_OK) {
    if (sftp)
      sftp_free(sftp);
    return false;
  }

  FILE *file = fopen(localPath.c_str(), "rb");
  if (!file) {
    sftp_free(sftp);
    return false;
  }

  sftp_file remoteFile =
      sftp_open(sftp, remotePath.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0777);
  if (!remoteFile) {
    fclose(file);
    sftp_free(sftp);
    return false;
  }

  char buffer[16384];
  size_t nread;
  while ((nread = fread(buffer, 1, sizeof(buffer), file)) > 0) {
    sftp_write(remoteFile, buffer, nread);
  }

  sftp_close(remoteFile);
  fclose(file);
  sftp_free(sftp);
  return true;
}

std::string SshService::getLocalIp(const std::string &targetHost) {
  int sock = socket(AF_INET, SOCK_DGRAM, 0);
  if (sock < 0)
    return "127.0.0.1";

  struct sockaddr_in serv;
  memset(&serv, 0, sizeof(serv));
  serv.sin_family = AF_INET;
  serv.sin_addr.s_addr = inet_addr(targetHost.c_str());
  serv.sin_port = htons(9);

  if (::connect(sock, (const struct sockaddr *)&serv, sizeof(serv)) < 0) {
    close(sock);
    return "127.0.0.1";
  }

  struct sockaddr_in name;
  socklen_t namelen = sizeof(name);
  if (getsockname(sock, (struct sockaddr *)&name, &namelen) < 0) {
    close(sock);
    return "127.0.0.1";
  }

  char buffer[100];
  const char *p = inet_ntop(AF_INET, &name.sin_addr, buffer, sizeof(buffer));
  std::string localIp = p ? buffer : "127.0.0.1";
  close(sock);
  return localIp;
}
