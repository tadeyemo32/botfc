#!/usr/bin/env python3
"""
simulate.py – BotFC 2-D Physics Simulation

Generates synthetic game_log.csv rows that mirror the structure produced
by MLDataLogger on the real robot. Use this to pre-train the model before
real match data is available, or to augment a small real-data set.

Field (top-down) coordinate system
───────────────────────────────────
  x : −3.0 … +3.0  m   (positive = toward opponent goal)
  y : −2.0 … +2.0  m   (positive = left side)
  heading : −π … π rad  (0 = facing +x / opponent goal)

Ball model
──────────
  constant-velocity with friction drag, elastic wall bounces,
  random kicks from simulated robot contacts.

Robot model
───────────
  Simple unicycle: always faces the ball; walks forward when aligned;
  kicks when within KICK_RADIUS and roughly aligned.

CSV output matches the 17-column schema used by MLDataLogger:
  timestamp, state, ball_valid, ball_bx, ball_by, ball_bsz, ball_dist,
  ball_vx, ball_vy, ball_pred_bx, ball_pred_by, ball_confidence,
  head_yaw, inertial_roll, inertial_pitch, kicks, battery_pct

Usage:
  pip install -r requirements.txt
  python3 simulate.py --episodes 50 --steps 3000 --out sim_data.csv
"""

import argparse
import csv
import math
import os
import random
import time

# ── constants ─────────────────────────────────────────────────────────────────
FIELD_HALF_X = 3.0
FIELD_HALF_Y = 2.0
FRICTION      = 0.96       # ball speed multiplier per step
BOUNCE_DAMP   = 0.7
DT            = 0.05       # seconds per simulation step
KICK_RADIUS   = 0.35       # m – robot must be within this to kick
KICK_FORCE    = 2.5        # m/s kick impulse
WALK_SPEED    = 0.25       # m/s
HEAD_TRACK_GAIN = 0.7
SENSOR_NOISE    = 0.01     # Gaussian noise std for sensor readings

# Approximate ball-size-in-frame formula (inverse of dist^2 in px space)
BALL_K_CONST = 0.06

FSM_STATES = ["SEARCH", "APPROACH", "ALIGN", "KICK"]


# ── helpers ───────────────────────────────────────────────────────────────────

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def angle_diff(a, b):
    d = a - b
    while d > math.pi:  d -= 2 * math.pi
    while d < -math.pi: d += 2 * math.pi
    return d

def noisy(v, std=SENSOR_NOISE):
    return v + random.gauss(0, std)


# ── simulation ────────────────────────────────────────────────────────────────

class Ball:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x  = random.uniform(-1.0, 1.0)
        self.y  = random.uniform(-0.8, 0.8)
        self.vx = random.uniform(-0.5, 0.5)
        self.vy = random.uniform(-0.5, 0.5)

    def step(self):
        self.vx *= FRICTION
        self.vy *= FRICTION
        self.x  += self.vx * DT
        self.y  += self.vy * DT
        # Wall bounces
        if self.x >  FIELD_HALF_X: self.x =  FIELD_HALF_X; self.vx *= -BOUNCE_DAMP
        if self.x < -FIELD_HALF_X: self.x = -FIELD_HALF_X; self.vx *= -BOUNCE_DAMP
        if self.y >  FIELD_HALF_Y: self.y =  FIELD_HALF_Y; self.vy *= -BOUNCE_DAMP
        if self.y < -FIELD_HALF_Y: self.y = -FIELD_HALF_Y; self.vy *= -BOUNCE_DAMP


class Robot:
    def __init__(self):
        self.x       = -1.5
        self.y       = 0.0
        self.heading = 0.0   # radians
        self.head_yaw = 0.0
        self.state   = "SEARCH"
        self.kicks   = 0
        self.search_dir = 1.0

    def observe_ball(self, ball):
        dx = ball.x - self.x
        dy = ball.y - self.y
        dist = math.hypot(dx, dy)
        if dist > 5.0:
            return None, None  # not visible

        ball_angle_world = math.atan2(dy, dx)
        # angle relative to robot body
        rel_angle = angle_diff(ball_angle_world, self.heading)
        # bx ≈ horizontal offset in camera frame
        bx = clamp(rel_angle / math.pi, -0.5, 0.5)
        by = clamp(-0.1 / max(dist, 0.1), -0.5, 0.0)  # ball below centre approx
        bsz = clamp(BALL_K_CONST / (dist * dist), 0.0001, 0.5)

        return (noisy(bx), noisy(by), noisy(bsz), noisy(dist)), rel_angle

    def step(self, ball):
        obs, rel_angle = self.observe_ball(ball)

        if obs is None:
            self.state = "SEARCH"
            self.head_yaw += self.search_dir * 0.05
            if abs(self.head_yaw) > 1.0:
                self.search_dir *= -1
            kicked = False
        else:
            bx, by, bsz, dist = obs
            self.head_yaw = clamp(self.head_yaw - bx * HEAD_TRACK_GAIN * 0.4, -1.0, 1.0)

            if dist > 0.8:
                self.state = "APPROACH"
                # Turn toward ball
                self.heading += clamp(rel_angle * 0.4, -0.3, 0.3)
                self.x += math.cos(self.heading) * WALK_SPEED * DT
                self.y += math.sin(self.heading) * WALK_SPEED * DT
            elif abs(rel_angle) > 0.15:
                self.state = "ALIGN"
                self.heading += clamp(rel_angle * 0.6, -0.4, 0.4)
            else:
                self.state = "KICK"

            kicked = self.state == "KICK" and dist < KICK_RADIUS

        if kicked:
            ball.vx = math.cos(self.heading) * KICK_FORCE
            ball.vy = math.sin(self.heading) * KICK_FORCE
            self.kicks += 1
            self.state = "SEARCH"

        # Keep robot inside field
        self.x = clamp(self.x, -FIELD_HALF_X, FIELD_HALF_X)
        self.y = clamp(self.y, -FIELD_HALF_Y, FIELD_HALF_Y)

        return obs


# ── CSV writer ────────────────────────────────────────────────────────────────

HEADER = [
    "timestamp", "state", "ball_valid",
    "ball_bx", "ball_by", "ball_bsz", "ball_dist",
    "ball_vx", "ball_vy", "ball_pred_bx", "ball_pred_by",
    "ball_confidence", "head_yaw",
    "inertial_roll", "inertial_pitch",
    "kicks", "battery_pct",
]

PRED_HORIZON = 0.45


def run_episode(writer, episode_id, n_steps, base_ts):
    ball  = Ball()
    robot = Robot()
    prev_bx = prev_by = 0.0
    prev_t  = 0.0
    vbx = vby = 0.0
    conf = 0.0
    tracking_since = None

    for step in range(n_steps):
        t = base_ts + step * DT
        ball.step()
        obs = robot.step(ball)

        if obs is not None:
            bx, by, bsz, dist = obs
            ball_valid = True
            if tracking_since is None:
                tracking_since = t
                vbx = vby = 0.0
            dt_track = t - prev_t
            if 0.01 < dt_track < 0.5:
                raw_vbx = clamp((bx - prev_bx) / dt_track, -4, 4)
                raw_vby = clamp((by - prev_by) / dt_track, -4, 4)
                vbx = 0.35 * raw_vbx + 0.65 * vbx
                vby = 0.35 * raw_vby + 0.65 * vby
            conf = min(1.0, (t - tracking_since) / 1.5)
            pred_bx = clamp(bx + vbx * PRED_HORIZON, -0.5, 0.5)
            pred_by = clamp(by + vby * PRED_HORIZON, -0.5, 0.5)
            prev_bx, prev_by, prev_t = bx, by, t
        else:
            ball_valid = False
            bx = by = bsz = dist = 0.0
            vbx = vby = pred_bx = pred_by = 0.0
            conf = 0.0
            tracking_since = None

        # Simulate mild inertial noise (no actual falls in simulation)
        roll  = noisy(0.0, 0.02)
        pitch = noisy(0.0, 0.02)

        writer.writerow({
            "timestamp":      round(t, 3),
            "state":          robot.state,
            "ball_valid":     int(ball_valid),
            "ball_bx":        round(bx, 4),
            "ball_by":        round(by, 4),
            "ball_bsz":       round(bsz, 6) if ball_valid else 0,
            "ball_dist":      round(dist, 3),
            "ball_vx":        round(vbx, 4),
            "ball_vy":        round(vby, 4),
            "ball_pred_bx":   round(pred_bx, 4),
            "ball_pred_by":   round(pred_by, 4),
            "ball_confidence":round(conf, 3),
            "head_yaw":       round(robot.head_yaw, 4),
            "inertial_roll":  round(roll, 4),
            "inertial_pitch": round(pitch, 4),
            "kicks":          robot.kicks,
            "battery_pct":    80,  # constant in sim
        })

    return base_ts + n_steps * DT


def main():
    parser = argparse.ArgumentParser(description="BotFC 2-D physics simulator")
    parser.add_argument("--episodes", type=int, default=50,   help="Number of game episodes")
    parser.add_argument("--steps",    type=int, default=3000, help="Steps per episode (~DT sec each)")
    parser.add_argument("--out",      default="sim_data.csv", help="Output CSV file")
    args = parser.parse_args()

    out_path = args.out
    write_header = not os.path.exists(out_path)

    total_rows = args.episodes * args.steps
    print("[simulate] Generating {} episodes × {} steps = {:,} rows → {}".format(
        args.episodes, args.steps, total_rows, out_path))

    base_ts = time.time()
    with open(out_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        if write_header:
            writer.writeheader()
        for ep in range(1, args.episodes + 1):
            base_ts = run_episode(writer, ep, args.steps, base_ts)
            print("[simulate] Episode {:3d}/{} done".format(ep, args.episodes), end="\r", flush=True)

    print("\n[simulate] Done. {:,} rows written to {}".format(total_rows, out_path))


if __name__ == "__main__":
    main()
