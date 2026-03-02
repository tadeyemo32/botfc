#include <cmath>
#include <fstream>
#include <iostream>
#include <vector>

extern "C" {
void *botfc_load_model(const char *model_path);
void botfc_free_model(void *handle);
float *botfc_infer(void *handle, float *input_data, int input_size);
}

struct MLP {
  int Din = 12, Dh = 32, Dout = 4;
  std::vector<std::vector<double>> W1, W2;
  std::vector<double> b1, b2;

  bool load(const char *path) {
    std::ifstream in(path, std::ios::binary);
    if (!in)
      return false;
    W1.assign(Din, std::vector<double>(Dh, 0.0));
    W2.assign(Dh, std::vector<double>(Dout, 0.0));
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

  std::vector<float> predict(const float *x) {
    std::vector<double> h(Dh, 0.0);
    for (int i = 0; i < Din; ++i)
      for (int j = 0; j < Dh; ++j)
        h[j] += x[i] * W1[i][j];
    for (int j = 0; j < Dh; ++j)
      h[j] = std::max(0.0, h[j] + b1[j]);

    std::vector<double> out(Dout, 0.0);
    double max_val = -1e9;
    for (int i = 0; i < Dh; ++i)
      for (int j = 0; j < Dout; ++j)
        out[j] += h[i] * W2[i][j];
    for (int j = 0; j < Dout; ++j) {
      out[j] += b2[j];
      max_val = std::max(max_val, out[j]);
    }

    double sum = 0.0;
    for (int j = 0; j < Dout; ++j) {
      out[j] = exp(out[j] - max_val);
      sum += out[j];
    }

    std::vector<float> prob(7, 0.0f); // legacy output size was 7
    for (int j = 0; j < Dout; ++j)
      prob[j] = (float)(out[j] / sum);
    // Fill remaining with 0 or safe values
    return prob;
  }
};

struct Context {
  MLP mlp;
  float output_cache[7];
};

void *botfc_load_model(const char *model_path) {
  Context *ctx = new Context();
  if (!ctx->mlp.load(model_path)) {
    std::cerr << "Failed to load model weights\n";
    delete ctx;
    return nullptr;
  }
  return static_cast<void *>(ctx);
}

void botfc_free_model(void *handle) {
  if (handle)
    delete static_cast<Context *>(handle);
}

float *botfc_infer(void *handle, float *input_data, int input_size) {
  if (!handle || input_size < 12)
    return nullptr;
  Context *ctx = static_cast<Context *>(handle);
  std::vector<float> prob = ctx->mlp.predict(input_data);
  for (int i = 0; i < 7; i++)
    ctx->output_cache[i] = prob[i];
  return ctx->output_cache;
}
