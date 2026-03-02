#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <map>
#include <random>
#include <sstream>
#include <vector>

using namespace std;

struct MLP {
  int Din = 12, Dh = 32, Dout = 4;
  vector<vector<double>> W1, W2, vW1, vW2;
  vector<double> b1, b2, vb1, vb2;

  MLP() {
    W1.assign(Din, vector<double>(Dh));
    vW1.assign(Din, vector<double>(Dh, 0.0));
    b1.assign(Dh, 0.0);
    vb1.assign(Dh, 0.0);
    W2.assign(Dh, vector<double>(Dout));
    vW2.assign(Dh, vector<double>(Dout, 0.0));
    b2.assign(Dout, 0.0);
    vb2.assign(Dout, 0.0);
    mt19937 gen(42);
    normal_distribution<> d1(0, sqrt(2.0 / Din));
    for (int i = 0; i < Din; ++i)
      for (int j = 0; j < Dh; ++j)
        W1[i][j] = d1(gen);
    normal_distribution<> d2(0, sqrt(2.0 / Dh));
    for (int i = 0; i < Dh; ++i)
      for (int j = 0; j < Dout; ++j)
        W2[i][j] = d2(gen);
  }

  int predict(const vector<double> &x, vector<double> &prob) {
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
    prob = out;
    int best_c = 0;
    for (int j = 0; j < Dout; ++j) {
      prob[j] /= sum;
      if (prob[j] > prob[best_c])
        best_c = j;
    }
    return best_c;
  }

  void train(const vector<vector<double>> &X, const vector<int> &Y,
             int epochs = 30, double lr = 0.01, double momentum = 0.9) {
    int N = X.size();
    for (int ep = 0; ep < epochs; ++ep) {
      double total_loss = 0.0;
      int correct = 0;
      for (int r = 0; r < N; ++r) {
        // Forward
        vector<double> h(Dh, 0.0);
        for (int i = 0; i < Din; ++i)
          for (int j = 0; j < Dh; ++j)
            h[j] += X[r][i] * W1[i][j];
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
        for (int j = 0; j < Dout; ++j)
          out[j] /= sum;

        total_loss -= log(out[Y[r]]);
        int best_c = 0;
        for (int j = 0; j < Dout; ++j)
          if (out[j] > out[best_c])
            best_c = j;
        if (best_c == Y[r])
          correct++;

        // Backward
        vector<double> d_out = out;
        d_out[Y[r]] -= 1.0;

        vector<double> d_h(Dh, 0.0);
        for (int j = 0; j < Dout; ++j) {
          for (int i = 0; i < Dh; ++i) {
            d_h[i] += W2[i][j] * d_out[j];
            double gradW2 = h[i] * d_out[j];
            vW2[i][j] = momentum * vW2[i][j] - lr * gradW2;
            W2[i][j] += vW2[i][j];
          }
          vb2[j] = momentum * vb2[j] - lr * d_out[j];
          b2[j] += vb2[j];
        }

        for (int j = 0; j < Dh; ++j) {
          if (h[j] <= 0)
            d_h[j] = 0; // ReLU derivative
          for (int i = 0; i < Din; ++i) {
            double gradW1 = X[r][i] * d_h[j];
            vW1[i][j] = momentum * vW1[i][j] - lr * gradW1;
            W1[i][j] += vW1[i][j];
          }
          vb1[j] = momentum * vb1[j] - lr * d_h[j];
          b1[j] += vb1[j];
        }
      }
      cout << "[train_model] Epoch " << ep + 1 << "/" << epochs
           << " - loss: " << total_loss / N << " - acc: " << (double)correct / N
           << endl;
    }
  }

  void save(const string &path) {
    ofstream out(path, ios::binary);
    for (int i = 0; i < Din; ++i)
      out.write((char *)W1[i].data(), Dh * sizeof(double));
    out.write((char *)b1.data(), Dh * sizeof(double));
    for (int i = 0; i < Dh; ++i)
      out.write((char *)W2[i].data(), Dout * sizeof(double));
    out.write((char *)b2.data(), Dout * sizeof(double));
    out.close();
  }

  void load(const string &path) {
    ifstream in(path, ios::binary);
    if (!in)
      return;
    for (int i = 0; i < Din; ++i)
      in.read((char *)W1[i].data(), Dh * sizeof(double));
    in.read((char *)b1.data(), Dh * sizeof(double));
    for (int i = 0; i < Dh; ++i)
      in.read((char *)W2[i].data(), Dout * sizeof(double));
    in.read((char *)b2.data(), Dout * sizeof(double));
  }
};

int main(int argc, char **argv) {
  string csv_file = "sim_data.csv";
  if (argc > 1)
    csv_file = argv[1];

  ifstream in(csv_file);
  if (!in) {
    cerr << "[train_model] Cannot open " << csv_file << endl;
    return 1; // It's okay, simulation data covers this usually
  }

  string line;
  getline(in, line); // Header

  // Feature extract and scale globally
  vector<vector<double>> features;
  vector<int> labels;
  map<string, int> class_map = {
      {"SEARCH", 0}, {"APPROACH", 1}, {"ALIGN", 2}, {"KICK", 3}};

  while (getline(in, line)) {
    stringstream ss(line);
    string v;
    vector<string> cols;
    while (getline(ss, v, ','))
      cols.push_back(v);
    if (cols.size() < 17)
      continue;

    string state = cols[1];
    if (class_map.find(state) == class_map.end())
      continue;

    // cols: 2..11 and 12..14 (matching the 12 features from Python model)
    // Feature mapping: "ball_valid", "ball_bx", "ball_by", "ball_bsz",
    // "ball_dist", "ball_vx", "ball_vy", "ball_pred_bx", "ball_pred_by",
    // "ball_confidence", "head_yaw", "inertial_roll"
    int idxs[] = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13};
    vector<double> f(12);
    for (int i = 0; i < 12; i++)
      f[i] = stod(cols[idxs[i]]);

    features.push_back(f);
    labels.push_back(class_map[state]);
  }

  int N = features.size();
  if (N == 0) {
    cout << "No data" << endl;
    return 0;
  }

  // MinMax Scaling
  vector<double> fmin(12, 1e9), fmax(12, -1e9);
  for (int r = 0; r < N; ++r) {
    for (int i = 0; i < 12; ++i) {
      fmin[i] = min(fmin[i], features[r][i]);
      fmax[i] = max(fmax[i], features[r][i]);
    }
  }
  for (int r = 0; r < N; ++r) {
    for (int i = 0; i < 12; ++i) {
      if (fmax[i] > fmin[i])
        features[r][i] = (features[r][i] - fmin[i]) / (fmax[i] - fmin[i]);
      else
        features[r][i] = 0.0;
    }
  }

  cout << "[train_model] Loaded " << N << " rows." << endl;

  MLP model;
  model.train(features, labels, 20, 0.005, 0.9);

  model.save("output/botfc_weights.bin");

  ofstream map_file("output/class_mapping.txt");
  map_file << "0: SEARCH\n1: APPROACH\n2: ALIGN\n3: KICK\n";
  map_file.close();

  ofstream scale_file("output/scaler.bin", ios::binary);
  scale_file.write((char *)fmin.data(), 12 * sizeof(double));
  scale_file.write((char *)fmax.data(), 12 * sizeof(double));
  scale_file.close();

  cout << "[train_model] Saved Custom C++ Model to output/botfc_weights.bin"
       << endl;
  return 0;
}
