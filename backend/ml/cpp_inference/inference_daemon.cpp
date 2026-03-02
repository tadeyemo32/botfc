#include <arpa/inet.h>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iostream>
#include <netinet/in.h>
#include <string>
#include <sys/socket.h>
#include <unistd.h>
#include <vector>

#define PORT 8081

using namespace std;

struct MLP {
  int Din = 12, Dh = 32, Dout = 4;
  vector<vector<double>> W1, W2;
  vector<double> b1, b2;

  bool load(const char *path) {
    ifstream in(path, ios::binary);
    if (!in)
      return false;
    W1.assign(Din, vector<double>(Dh, 0.0));
    W2.assign(Dh, vector<double>(Dout, 0.0));
    b1.assign(Dh, 0.0);
    b2.assign(Dout, 0.0);

    for (int i = 0; i < Din; ++i)
      in.read((char *)W1[i].data(), Dh * sizeof(double));
    in.read((char *)b1.data(), Dh * sizeof(double));
    for (int i = 0; i < Dh; ++i)
      in.read((char *)W2[i].data(), Dout * sizeof(double));
    in.read((char *)b2.data(), Dout * sizeof(double));
    return true;
  }

  void predict(const float *x, float *out_arr) {
    vector<double> h(Dh, 0.0);
    for (int i = 0; i < Din; ++i)
      for (int j = 0; j < Dh; ++j)
        h[j] += x[i] * W1[i][j];
    for (int j = 0; j < Dh; ++j)
      h[j] = max(0.0, h[j] + b1[j]);

    vector<double> out(Dout, 0.0);
    double max_val = -1e9;
    for (int i = 0; i < Dh; ++i)
      for (int j = 0; j < Dout; ++j)
        out[j] += h[i] * W2[i][j];
    for (int j = 0; j < Dout; ++j) {
      out[j] += b2[j];
      max_val = max(max_val, out[j]);
    }

    double sum = 0.0;
    for (int j = 0; j < Dout; ++j) {
      out[j] = exp(out[j] - max_val);
      sum += out[j];
    }

    for (int j = 0; j < 7; ++j)
      out_arr[j] = 0.0f;
    for (int j = 0; j < Dout; ++j)
      out_arr[j] = (float)(out[j] / sum);
  }
};

int main(int argc, char *argv[]) {
  if (argc < 2) {
    cerr << "Usage: " << argv[0] << " <path_to_model.bin>\n";
    return 1;
  }

  MLP mlp;
  if (!mlp.load(argv[1])) {
    cerr << "Failed to load custom C++ MLP model: " << argv[1] << "\n";
    return 1;
  }
  cout << "[ML Daemon] Loaded custom model " << argv[1] << "\n";

  int sockfd = socket(AF_INET, SOCK_DGRAM, 0);
  if (sockfd < 0) {
    cerr << "Error opening socket\n";
    return 1;
  }

  struct sockaddr_in serv_addr, cli_addr;
  memset(&serv_addr, 0, sizeof(serv_addr));
  serv_addr.sin_family = AF_INET;
  serv_addr.sin_addr.s_addr = INADDR_ANY;
  serv_addr.sin_port = htons(PORT);

  if (::bind(sockfd, (struct sockaddr *)&serv_addr, sizeof(serv_addr)) < 0) {
    cerr << "Error binding to port " << PORT << "\n";
    return 1;
  }

  cout << "[ML Daemon] Listening on UDP " << PORT << " for telemetry...\n";

  int expected_elements = 12;
  int expected_bytes = expected_elements * sizeof(float);
  vector<float> input_buffer(expected_elements);
  vector<float> output_buffer(7);

  while (true) {
    socklen_t cli_len = sizeof(cli_addr);
    int n = recvfrom(sockfd, input_buffer.data(), expected_bytes, 0,
                     (struct sockaddr *)&cli_addr, &cli_len);
    if (n != expected_bytes)
      continue;

    mlp.predict(input_buffer.data(), output_buffer.data());

    sendto(sockfd, output_buffer.data(), 7 * sizeof(float), 0,
           (struct sockaddr *)&cli_addr, cli_len);
  }

  close(sockfd);
  return 0;
}
