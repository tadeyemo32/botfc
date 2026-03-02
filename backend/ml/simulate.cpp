#include <iostream>
#include <fstream>
#include <vector>
#include <cmath>
#include <random>
#include <string>
#include <iomanip>

using namespace std;

const double FIELD_HALF_X = 3.0;
const double FIELD_HALF_Y = 2.0;
const double FRICTION = 0.96;
const double BOUNCE_DAMP = 0.7;
const double DT = 0.05;
const double KICK_RADIUS = 0.35;
const double KICK_FORCE = 2.5;
const double WALK_SPEED = 0.25;
const double HEAD_TRACK_GAIN = 0.7;
const double SENSOR_NOISE = 0.01;
const double BALL_K_CONST = 0.06;

double clamp(double v, double lo, double hi) { return max(lo, min(hi, v)); }
double angle_diff(double a, double b) {
    double d = a - b;
    while (d > M_PI) d -= 2 * M_PI;
    while (d < -M_PI) d += 2 * M_PI;
    return d;
}

mt19937 gen(42);
normal_distribution<> noise_dist(0, SENSOR_NOISE);

double noisy(double v) { return v + noise_dist(gen); }

struct Ball {
    double x, y, vx, vy;
    void reset() {
        uniform_real_distribution<> ux(-1.0, 1.0);
        uniform_real_distribution<> uy(-0.8, 0.8);
        uniform_real_distribution<> uv(-0.5, 0.5);
        x = ux(gen); y = uy(gen); vx = uv(gen); vy = uv(gen);
    }
    Ball() { reset(); }
    void step() {
        vx *= FRICTION; vy *= FRICTION;
        x += vx * DT; y += vy * DT;
        if (x > FIELD_HALF_X) { x = FIELD_HALF_X; vx *= -BOUNCE_DAMP; }
        if (x < -FIELD_HALF_X) { x = -FIELD_HALF_X; vx *= -BOUNCE_DAMP; }
        if (y > FIELD_HALF_Y) { y = FIELD_HALF_Y; vy *= -BOUNCE_DAMP; }
        if (y < -FIELD_HALF_Y) { y = -FIELD_HALF_Y; vy *= -BOUNCE_DAMP; }
    }
};

struct Robot {
    double x = -1.5, y = 0.0, heading = 0.0, head_yaw = 0.0;
    string state = "SEARCH";
    int kicks = 0;
    double search_dir = 1.0;

    bool observe_ball(Ball& ball, double& bx, double& by, double& bsz, double& dist, double& rel_angle) {
        double dx = ball.x - x;
        double dy = ball.y - y;
        dist = hypot(dx, dy);
        if (dist > 5.0) return false;
        double ball_angle_world = atan2(dy, dx);
        rel_angle = angle_diff(ball_angle_world, heading);
        bx = clamp(rel_angle / M_PI, -0.5, 0.5);
        by = clamp(-0.1 / max(dist, 0.1), -0.5, 0.0);
        bsz = clamp(BALL_K_CONST / (dist * dist), 0.0001, 0.5);
        bx = noisy(bx); by = noisy(by); bsz = noisy(bsz); dist = noisy(dist);
        return true;
    }

    void step(Ball& ball, bool& ball_valid, double& out_bx, double& out_by, double& out_bsz, double& out_dist) {
        double rel_angle;
        ball_valid = observe_ball(ball, out_bx, out_by, out_bsz, out_dist, rel_angle);
        bool kicked = false;

        if (!ball_valid) {
            state = "SEARCH";
            head_yaw += search_dir * 0.05;
            if (abs(head_yaw) > 1.0) search_dir *= -1;
        } else {
            head_yaw = clamp(head_yaw - out_bx * HEAD_TRACK_GAIN * 0.4, -1.0, 1.0);
            if (out_dist > 0.8) {
                state = "APPROACH";
                heading += clamp(rel_angle * 0.4, -0.3, 0.3);
                x += cos(heading) * WALK_SPEED * DT;
                y += sin(heading) * WALK_SPEED * DT;
            } else if (abs(rel_angle) > 0.15) {
                state = "ALIGN";
                heading += clamp(rel_angle * 0.6, -0.4, 0.4);
            } else {
                state = "KICK";
                kicked = (out_dist < KICK_RADIUS);
            }
        }

        if (kicked) {
            ball.vx = cos(heading) * KICK_FORCE;
            ball.vy = sin(heading) * KICK_FORCE;
            kicks++;
            state = "SEARCH";
        }
        x = clamp(x, -FIELD_HALF_X, FIELD_HALF_X);
        y = clamp(y, -FIELD_HALF_Y, FIELD_HALF_Y);
    }
};

int main() {
    ofstream out("sim_data.csv");
    out << "timestamp,state,ball_valid,ball_bx,ball_by,ball_bsz,ball_dist,ball_vx,ball_vy,ball_pred_bx,ball_pred_by,ball_confidence,head_yaw,inertial_roll,inertial_pitch,kicks,battery_pct\n";
    
    double base_ts = 0.0;
    int EPISODES = 50, STEPS = 3000;
    
    for (int ep = 0; ep < EPISODES; ++ep) {
        Ball ball;
        Robot robot;
        double prev_bx = 0, prev_by = 0, prev_t = 0;
        double vbx = 0, vby = 0;
        double tracking_since = -1.0;

        for (int step = 0; step < STEPS; ++step) {
            double t = base_ts + step * DT;
            ball.step();
            double bx=0, by=0, bsz=0, dist=0;
            bool valid = false;
            robot.step(ball, valid, bx, by, bsz, dist);

            double pred_bx = 0, pred_by = 0, conf = 0;
            if (valid) {
                if (tracking_since < 0) { tracking_since = t; vbx = vby = 0; }
                double dt_track = t - prev_t;
                if (dt_track > 0.01 && dt_track < 0.5) {
                    double raw_vbx = clamp((bx - prev_bx)/dt_track, -4, 4);
                    double raw_vby = clamp((by - prev_by)/dt_track, -4, 4);
                    vbx = 0.35 * raw_vbx + 0.65 * vbx;
                    vby = 0.35 * raw_vby + 0.65 * vby;
                }
                conf = min(1.0, (t - tracking_since)/1.5);
                pred_bx = clamp(bx + vbx * 0.45, -0.5, 0.5);
                pred_by = clamp(by + vby * 0.45, -0.5, 0.5);
                prev_bx = bx; prev_by = by; prev_t = t;
            } else {
                tracking_since = -1.0;
                vbx = vby = 0;
            }

            double roll = noisy(0.0), pitch = noisy(0.0);
            
            out << fixed << setprecision(3) << t << ","
                << robot.state << "," << (valid?1:0) << ","
                << setprecision(4) << bx << "," << by << ","
                << setprecision(6) << bsz << ","
                << setprecision(3) << dist << ","
                << setprecision(4) << vbx << "," << vby << ","
                << pred_bx << "," << pred_by << ","
                << setprecision(3) << conf << ","
                << setprecision(4) << robot.head_yaw << ","
                << roll << "," << pitch << ","
                << robot.kicks << ",80\n";
        }
        base_ts += STEPS * DT;
    }
    cout << "Generated sim_data.csv" << endl;
    return 0;
}
