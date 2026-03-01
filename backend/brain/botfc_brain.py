#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
BotFC Brain – Enhanced Edition
Runs directly on the NAO/Pepper robot using the NAOqi Python SDK.

Key upgrades over the original:
  • BallModel – EMA smoother + heading memory + distance estimate
  • Bottom camera red-blob detector (eliminates the foot blind-spot)
  • Timestamp-based stale detection guard (NAOqi caches last value)
  • Ball persistence: brief occlusions don't abort the approach
  • Search starts facing last known ball heading
  • In-walk approach: body alignment driven by head-yaw encoder (no camera lag)
  • Kick walk-up: walk to < KICK_APPROACH_DIST before planting kick

Usage:
  python botfc_brain.py --trait=balanced --server-ip=192.168.1.100 --server-port=5050
"""

import sys
import os
import time
import math
import json
import signal
import argparse
import threading
import struct

from naoqi import ALBroker, ALProxy

# ─────────────────────────────────────────────
# Safety / field constants
# ─────────────────────────────────────────────
MAX_FIELD_RADIUS = 2.5
COMBAT_DISTANCE  = 0.40
MOTOR_TEMP_LIMIT = 60.0

# ─────────────────────────────────────────────
# Stability constants — adaptive gait controller
# ─────────────────────────────────────────────
STABILITY_TILT_WARN     = 0.20   # rad — start reducing speed
STABILITY_TILT_DANGER   = 0.40   # rad — emergency slow / stop
STABILITY_GYRO_WARN     = 1.5    # rad/s — angular velocity warning
STABILITY_GYRO_DANGER   = 3.0    # rad/s — angular velocity, imminent fall
STABILITY_SPEED_SCALE   = 0.3    # min speed multiplier at max instability
STABILITY_RECOVERY_RATE = 0.05   # how fast speed limit recovers per cycle
STABILITY_EMA_ALPHA     = 0.4    # smoothing for gyro signals

# ─────────────────────────────────────────────
# Ball model constants
# ─────────────────────────────────────────────
# bsz when the ball is exactly 1 m away – calibrate on field!
# Increase if your ball looks small at 1 m; decrease if too large.
BALL_K_CONST      = 0.042
BALL_EMA_ALPHA    = 0.60   # weight on new measurement (0=frozen, 1=raw)
BALL_LOSS_TIME    = 1.8    # seconds before the model is marked invalid
BALL_VEL_EMA      = 0.35   # velocity smoothing (lower = smoother but laggier)
BALL_PRED_HORIZON = 0.45   # seconds ahead to predict ball position
BALL_VEL_THRESH   = 0.18   # camera-space units/sec – "ball is moving fast"
BALL_CONF_RAMP    = 1.5    # seconds of continuous tracking to reach full confidence

# ─────────────────────────────────────────────
# Head-tracking / approach constants
# ─────────────────────────────────────────────
HEAD_TRACK_GAIN       = 1.15   # tighter aimbot tracking (was 0.55)
BODY_FOLLOW_THRESHOLD = 0.12   # |head_yaw| above which we rotate before walking
ALIGN_BODY_DEADBAND   = 0.06   # bx dead-zone in ALIGN to kill oscillation

# ─────────────────────────────────────────────
# Search constants
# ─────────────────────────────────────────────
SEARCH_WALK_DELAY     = 5.0    # seconds of pure scanning before walk+scan
SEARCH_HEAD_SPEED     = 0.08   # rad/step head sweep speed
SEARCH_PITCH_LOW      = 0.35   # pitch when looking at ground nearby (positive = down)
SEARCH_PITCH_HIGH     = 0.15   # pitch when looking further ahead (still slightly down)

# ─────────────────────────────────────────────
# Approach constants
# ─────────────────────────────────────────────
APPROACH_MAX_SPEED    = 0.80   # max forward walk speed
APPROACH_MIN_SPEED    = 0.25   # min forward speed when close
APPROACH_TURN_GAIN    = 1.8    # body turn rate per unit head_yaw offset
APPROACH_HEAD_RECENTER= 0.15   # speed to recenter head yaw during straight walk

# ─────────────────────────────────────────────
# Kick constants
# ─────────────────────────────────────────────
KICK_VERIFY_SAMPLES  = 4
KICK_VERIFY_INTERVAL = 0.05   # s between samples
KICK_BSZ_READY       = 0.10   # ball must appear at least this large to kick
KICK_BX_MAX          = 0.05   # horizontal tolerance before kicking
KICK_APPROACH_DIST   = 0.22   # target distance (m) for kick walk-up

# ─────────────────────────────────────────────
# Goal alignment – bearing to opponent goal (radians from 
# robot origin).  Striker kicks forward, defender kicks 
# sideways-out.  These are overridden per-role in __init__.
# ─────────────────────────────────────────────
DEFAULT_GOAL_BEARING = 0.0    # straight ahead

# ─────────────────────────────────────────────
# Bottom camera (BGR, QVGA=320×240, 10 fps)
# Subscribe as a separate NAOqi client so we can run it alongside the
# top-camera ALRedBallDetection subscription.
# ─────────────────────────────────────────────
BOT_CAM_ID      = 1    # bottom camera
TOP_CAM_ID      = 0    # top camera
BOT_CAM_RES     = 1    # kQVGA  (320 × 240)
BOT_CAM_FORMAT  = 13   # kBGR
BOT_CAM_FPS     = 10
BOT_CAM_STRIDE  = 4    # check every Nth pixel (speed vs accuracy)
TOP_CAM_HFOV    = 1.064  # ~60.9 deg
TOP_CAM_VFOV    = 0.831  # ~47.6 deg
BOT_CAM_HFOV    = 0.831  # ~47.6 deg
BOT_CAM_VFOV    = 0.665  # ~38.1 deg

# Red-ball thresholds in BGR colour space.
# Tune RED_R_MIN downward if the robot misses the ball under warm/dim lighting.
RED_R_MIN      = 135
RED_B_MAX      = 90
RED_G_MAX      = 110
RED_DIFF_MIN   = 55    # red must exceed max(B,G) by this margin
RED_MIN_PX     = 40    # minimum blob pixel count

# Goal-post detection: multiple colour profiles to handle
# different lighting / field setups (bright yellow, dim/warm,
# white SPL posts).  Each profile is (R_min, G_min, B_max, diff_min, label).
# diff_min < 0 means "white" mode (all channels high and close together).
# ─────────────────────────────────────────────
GOAL_POST_PROFILES = [
    # (R_min, G_min, B_max, diff_min, label)
    (150, 140,  80, 40, "yellow_bright"),  # standard SPL, good lighting
    (130, 110,  90, 30, "yellow_dim"),     # indoor warm lighting
    (190, 190, 210, -1, "white"),           # white goal posts
]
GOAL_POST_MIN_PX  = 15
GOAL_POST_MIN_HEIGHT_RATIO = 0.10

# ─────────────────────────────────────────────
# Goal scored detection
# ─────────────────────────────────────────────
GOAL_CHECK_DELAY   = 0.8   # seconds after kick to start checking
GOAL_CHECK_SAMPLES = 6     # number of frames to sample
GOAL_CHECK_INTERVAL= 0.15  # seconds between samples
GOAL_BALL_GONE_THRESH = 4  # if ball missing in this many samples → goal

# ─────────────────────────────────────────────
# Camera streamer constants (JPEG to server)
# ─────────────────────────────────────────────
STREAM_CAM_ID     = 0    # top camera
STREAM_CAM_RES    = 1    # kQVGA (320×240)
STREAM_CAM_FORMAT = 21   # kJpegColorSpace
STREAM_CAM_FPS    = 5    # frames per second sent to server

# ─────────────────────────────────────────────
# States & Roles
# ─────────────────────────────────────────────
ROLE_STRIKER  = "STRIKER"
ROLE_DEFENDER = "DEFENDER"
ROLE_BALANCED = "BALANCED"

STATE_INIT      = "INIT"
STATE_SEARCH    = "SEARCH"
STATE_APPROACH  = "APPROACH"
STATE_ORBIT     = "ORBIT"
STATE_ALIGN     = "ALIGN"
STATE_TACKLE    = "TACKLE"
STATE_KICK      = "KICK"
STATE_CELEBRATE = "CELEBRATE"
STATE_RECOVER   = "RECOVER"
STATE_HALFTIME  = "HALFTIME"


# ─────────────────────────────────────────────
# BallModel – EMA smoother + distance + heading
# ─────────────────────────────────────────────
class BallModel(object):
    """Ball tracker with EMA smoothing, velocity estimation, and prediction.

    Position is smoothed with an EMA filter.  Velocity is estimated from
    consecutive position deltas (also EMA-smoothed) and used to predict
    where the ball will be BALL_PRED_HORIZON seconds ahead so the robot
    can intercept a moving ball rather than chasing its current position.

    confidence (0–1) ramps up over BALL_CONF_RAMP seconds of continuous
    tracking.  The approach FSM only uses predictions when confidence is
    high enough to trust the velocity estimate.

    Call tick() once per FSM cycle to expire stale data.
    """

    def __init__(self):
        self._bx   = 0.0
        self._by   = 0.0
        self._bsz  = 0.0
        self.dist  = 9.9
        self.valid = False
        self.last_seen       = 0.0
        self.last_heading    = 0.0
        # Velocity & prediction
        self._vbx            = 0.0   # camera-space horizontal velocity (units/s)
        self._vby            = 0.0
        self._last_update_t  = None
        self._tracking_since = 0.0
        self.pred_bx         = 0.0   # predicted position BALL_PRED_HORIZON s ahead
        self.pred_by         = 0.0
        self.confidence      = 0.0   # 0–1 tracker confidence

    # ── Read-only properties ──────────────────────────────────────────────
    @property
    def bx(self):  return self._bx
    @property
    def by(self):  return self._by
    @property
    def bsz(self): return self._bsz
    @property
    def vbx(self): return self._vbx
    @property
    def vby(self): return self._vby

    def update(self, raw_bx, raw_by, raw_bsz, head_yaw):
        now = time.time()
        a   = BALL_EMA_ALPHA

        if self.valid:
            # ── Velocity estimation ─────────────────────────────────────
            if self._last_update_t is not None:
                dt = now - self._last_update_t
                if 0.01 < dt < 0.5:
                    raw_vbx = max(-4.0, min(4.0, (raw_bx - self._bx) / dt))
                    raw_vby = max(-4.0, min(4.0, (raw_by - self._by) / dt))
                    v = BALL_VEL_EMA
                    self._vbx = v * raw_vbx + (1.0 - v) * self._vbx
                    self._vby = v * raw_vby + (1.0 - v) * self._vby
            # ── Position EMA ─────────────────────────────────────────────
            self._bx  = a * raw_bx  + (1.0 - a) * self._bx
            self._by  = a * raw_by  + (1.0 - a) * self._by
            self._bsz = a * raw_bsz + (1.0 - a) * self._bsz
        else:
            # First detection after a gap – seed position, zero velocity
            self._bx, self._by, self._bsz = raw_bx, raw_by, raw_bsz
            self._vbx = 0.0
            self._vby = 0.0
            self._tracking_since = now

        self.dist = max(0.1, BALL_K_CONST / max(self._bsz, 0.001))
        # NAO convention: HeadYaw positive=left, bx positive=left.
        # World heading of ball = head_yaw PLUS bx
        self.last_heading = head_yaw + self._bx
        self.valid        = True
        self.last_seen    = now
        self._last_update_t = now

        # ── Prediction ───────────────────────────────────────────────────
        h = BALL_PRED_HORIZON
        self.pred_bx = max(-0.5, min(0.5, self._bx + self._vbx * h))
        self.pred_by = max(-0.5, min(0.5, self._by + self._vby * h))

        # ── Confidence (ramps from 0→1 over BALL_CONF_RAMP seconds) ──────
        elapsed = now - self._tracking_since
        self.confidence = min(1.0, elapsed / BALL_CONF_RAMP)

    def age(self):
        return (time.time() - self.last_seen) if self.valid else -1.0

    def tick(self):
        """Expire stale model; reset velocity when the ball is lost."""
        if self.valid and self.age() > BALL_LOSS_TIME:
            self.valid      = False
            self._vbx       = 0.0
            self._vby       = 0.0
            self.confidence = 0.0
            self._tracking_since = 0.0


# ─────────────────────────────────────────────
# TelemetryClient
# ─────────────────────────────────────────────
class TelemetryClient(object):
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.trait = "balanced"
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        self.current_data = {
            # FSM
            "state": STATE_INIT, "kicks": 0,
            "ball_age": -1.0,    "break_remaining": 0,
            # Battery
            "battery_pct": -1,
            # Ball state
            "ball_valid": False, "ball_bx": 0.0,  "ball_by": 0.0,
            "ball_bsz": 0.0,     "ball_dist": 9.9,
            "ball_vx": 0.0,      "ball_vy": 0.0,
            "ball_pred_bx": 0.0, "ball_pred_by": 0.0,
            "ball_confidence": 0.0,
            # Robot pose
            "head_yaw": 0.0,
            "inertial_roll": 0.0, "inertial_pitch": 0.0,
        }

    def start(self, trait):
        if self.running:
            return
        self.trait = trait
        self.running = True
        self.thread = threading.Thread(target=self._loop)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)

    def update(self, **kw):
        """Merge keyword arguments into the live telemetry dict."""
        with self.lock:
            self.current_data.update(kw)

    def _build_payload(self):
        with self.lock:
            d = self.current_data.copy()
        d["trait"] = self.trait
        return json.dumps(d)

    def _loop(self):
        import socket
        import base64

        while self.running:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect((self.host, self.port))

                ws_key = base64.b64encode(os.urandom(16))
                handshake = (
                    "GET /api/ws/bot HTTP/1.1\r\n"
                    "Host: {host}:{port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Key: {key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    "User-Agent: BotFC-PyBrain-v2\r\n"
                    "\r\n"
                ).format(host=self.host, port=self.port, key=ws_key)
                sock.sendall(handshake.encode("utf-8"))

                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise Exception("Handshake failed")
                    resp += chunk

                if b"101" not in resp.split(b"\r\n")[0]:
                    raise Exception("WebSocket upgrade rejected")

                print("[Telemetry] Connected to ws://{}:{}/api/ws/bot".format(
                    self.host, self.port))

                while self.running:
                    self._ws_send(sock, self._build_payload())
                    time.sleep(0.1)

                self._ws_close(sock)
            except Exception as e:
                err = str(e)
                # Broken pipe = server went down; suppress noise, just reconnect
                if "Broken pipe" not in err and "EPIPE" not in err:
                    print("[Telemetry] {}: reconnecting in 2s...".format(err))
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
            if self.running:
                time.sleep(2)

    @staticmethod
    def _ws_send(sock, message):
        data = message.encode("utf-8")
        length = len(data)
        frame = bytearray()
        frame.append(0x81)
        if length <= 125:
            frame.append(0x80 | length)
        elif length <= 65535:
            frame.append(0x80 | 126)
            frame.extend(struct.pack("!H", length))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack("!Q", length))
        # Python 2 compat: os.urandom returns str, not bytes – XOR needs ints
        mask_bytes = bytearray(os.urandom(4))
        frame.extend(mask_bytes)
        data_bytes = bytearray(data)
        masked = bytearray(data_bytes[i] ^ mask_bytes[i % 4] for i in range(length))
        frame.extend(masked)
        sock.sendall(bytes(frame))

    @staticmethod
    def _ws_close(sock):
        frame = bytearray([0x88, 0x80])
        frame.extend(os.urandom(4))
        try:
            sock.sendall(bytes(frame))
        except Exception:
            pass


# ─────────────────────────────────────────────
# MLDataLogger  (unchanged from original)
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# StabilityController – adaptive gait & balance
# ─────────────────────────────────────────────
class StabilityController(object):
    """Monitors IMU (roll/pitch + gyro) and adapts walk speed to keep
    the robot upright.  Provides a speed_multiplier (0.3-1.0) that the
    FSM should apply to all forward/lateral walk commands.

    Features
    --------
    * Tilt-rate estimation from gyroscope (smoothed with EMA)
    * Adaptive speed scaling: reduces walk speed proportionally to
      instability to prevent falls before they happen
    * Stability score (0-1) for ML training: 1 = perfectly stable
    * Running statistics: total fall count, cumulative instability
    """

    def __init__(self):
        # Smoothed signals
        self.gyro_x      = 0.0   # roll rate  (rad/s)
        self.gyro_y      = 0.0   # pitch rate (rad/s)
        self.tilt_mag    = 0.0   # combined tilt magnitude
        self.gyro_mag    = 0.0   # combined gyro magnitude
        # Outputs
        self.speed_mult  = 1.0   # 0.3-1.0 — multiply walk commands by this
        self.stability   = 1.0   # 0-1 score for ML
        self.is_unstable = False
        # Stats for ML
        self.fall_count        = 0
        self.cumulative_instab = 0.0
        self.cycles            = 0
        self.wobble_events     = 0   # times speed was reduced

    def update(self, roll, pitch, gx, gy):
        """Call once per FSM cycle with IMU readings.

        roll/pitch in radians (from AngleX/AngleY).
        gx/gy in rad/s (from GyroscopeX/GyroscopeY).
        """
        a = STABILITY_EMA_ALPHA
        self.gyro_x   = a * gx + (1.0 - a) * self.gyro_x
        self.gyro_y   = a * gy + (1.0 - a) * self.gyro_y
        self.tilt_mag = math.sqrt(roll * roll + pitch * pitch)
        self.gyro_mag = math.sqrt(self.gyro_x ** 2 + self.gyro_y ** 2)
        self.cycles  += 1

        # ── Stability score (1.0 = perfect, 0.0 = falling) ────────────
        tilt_score = max(0.0, 1.0 - self.tilt_mag / STABILITY_TILT_DANGER)
        gyro_score = max(0.0, 1.0 - self.gyro_mag / STABILITY_GYRO_DANGER)
        self.stability = min(tilt_score, gyro_score)
        self.cumulative_instab += (1.0 - self.stability)

        # ── Adaptive speed multiplier ─────────────────────────────────
        # If tilting or angular velocity is high, reduce walk speed.
        if self.tilt_mag > STABILITY_TILT_WARN or self.gyro_mag > STABILITY_GYRO_WARN:
            # Proportional reduction
            tilt_factor = max(STABILITY_SPEED_SCALE,
                              1.0 - (self.tilt_mag - STABILITY_TILT_WARN) /
                              (STABILITY_TILT_DANGER - STABILITY_TILT_WARN))
            gyro_factor = max(STABILITY_SPEED_SCALE,
                              1.0 - (self.gyro_mag - STABILITY_GYRO_WARN) /
                              (STABILITY_GYRO_DANGER - STABILITY_GYRO_WARN))
            target = max(STABILITY_SPEED_SCALE, min(tilt_factor, gyro_factor))
            self.speed_mult = min(self.speed_mult, target)  # drop fast
            self.is_unstable = True
            self.wobble_events += 1
        else:
            # Slowly recover speed when stable
            self.speed_mult = min(1.0, self.speed_mult + STABILITY_RECOVERY_RATE)
            self.is_unstable = False

    def record_fall(self):
        self.fall_count += 1

    def get_ml_dict(self):
        """Return a dict of stability features for ML logging."""
        return {
            "stab_score":      round(self.stability, 3),
            "stab_speed_mult": round(self.speed_mult, 3),
            "stab_tilt_mag":   round(self.tilt_mag, 4),
            "stab_gyro_mag":   round(self.gyro_mag, 4),
            "stab_gyro_x":     round(self.gyro_x, 4),
            "stab_gyro_y":     round(self.gyro_y, 4),
            "stab_unstable":   1 if self.is_unstable else 0,
            "stab_fall_count": self.fall_count,
            "stab_wobbles":    self.wobble_events,
        }


class MLDataLogger(object):
    """Comprehensive ML data logger — captures 40+ features per tick at ~20Hz.

    Features logged (for TensorFlow training):
    ─────────────────────────────────────────
    FSM:       state, action taken, time in state
    Ball:      position, velocity, size, distance, prediction, confidence
    IMU:       roll, pitch, gyro_x, gyro_y, accel_x, accel_y, accel_z
    Stability: score, speed_mult, tilt_mag, gyro_mag, fall_count
    Joints:    all 26 joint angles (head, arms, hips, knees, ankles)
    Feet:      4x pressure sensors (left front, left back, right front, right back)
    Sonar:     left, right distances
    Walk:      commanded speed_x, speed_y, speed_theta
    System:    battery_pct, max_motor_temp, goal_bearing, goal_confidence
    """

    # Joint names for full body capture
    JOINT_NAMES = [
        "HeadYaw", "HeadPitch",
        "LShoulderPitch", "LShoulderRoll", "LElbowYaw", "LElbowRoll",
        "LWristYaw", "LHand",
        "LHipYawPitch", "LHipRoll", "LHipPitch",
        "LKneePitch", "LAnklePitch", "LAnkleRoll",
        "RHipYawPitch", "RHipRoll", "RHipPitch",
        "RKneePitch", "RAnklePitch", "RAnkleRoll",
        "RShoulderPitch", "RShoulderRoll", "RElbowYaw", "RElbowRoll",
        "RWristYaw", "RHand",
    ]

    # Foot pressure sensor keys
    FOOT_SENSORS = [
        "Device/SubDeviceList/LFoot/FSR/FrontLeft/Sensor/Value",
        "Device/SubDeviceList/LFoot/FSR/RearLeft/Sensor/Value",
        "Device/SubDeviceList/RFoot/FSR/FrontRight/Sensor/Value",
        "Device/SubDeviceList/RFoot/FSR/RearRight/Sensor/Value",
    ]

    def __init__(self, robot_ip, robot_port):
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        self.frame_index = 0
        self.out_dir = "/home/nao/ml_data/"
        self.video_client_name = ""
        self.video_device = None
        self.current_telemetry = {}
        self._csv_initialized = False

        try:
            os.makedirs(self.out_dir)
        except OSError:
            pass

        try:
            self.video_device = ALProxy("ALVideoDevice", robot_ip, robot_port)
        except Exception as e:
            print("[MLLogger] Failed to init ALVideoDevice: {}".format(e))

    def start(self):
        if self.running or not self.video_device:
            return
        try:
            self.video_client_name = self.video_device.subscribeCamera(
                "BotFC_ML_Logger", 0, 1, 9, 5)
            self.running = True
            self.thread = threading.Thread(target=self._loop)
            self.thread.daemon = True
            self.thread.start()
            print("[MLLogger] Started at 5Hz to {}".format(self.out_dir))
        except Exception as e:
            print("[MLLogger] subscribeCamera failed: {}".format(e))

    def stop(self):
        if not self.running:
            return
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        if self.video_device and self.video_client_name:
            try:
                self.video_device.unsubscribe(self.video_client_name)
            except Exception:
                pass

    def update_telemetry(self, snapshot):
        with self.lock:
            self.current_telemetry = snapshot.copy()

    def _get_csv_columns(self):
        """Return ordered list of all CSV column names."""
        cols = [
            # Timestamp + FSM
            "timestamp", "state", "time_in_state",
            # Ball perception
            "ball_valid", "ball_bx", "ball_by", "ball_bsz", "ball_dist",
            "ball_vx", "ball_vy", "ball_pred_bx", "ball_pred_by",
            "ball_confidence",
            # IMU: angles
            "imu_roll", "imu_pitch",
            # IMU: gyroscope
            "gyro_x", "gyro_y",
            # IMU: accelerometer
            "accel_x", "accel_y", "accel_z",
            # Stability controller
            "stab_score", "stab_speed_mult", "stab_tilt_mag",
            "stab_gyro_mag", "stab_gyro_x", "stab_gyro_y",
            "stab_unstable", "stab_fall_count", "stab_wobbles",
            # Walk command
            "walk_speed_x", "walk_speed_y", "walk_speed_theta",
            # Foot pressure
            "foot_lf", "foot_lb", "foot_rf", "foot_rb",
            # Sonar
            "sonar_left", "sonar_right",
            # System
            "battery_pct", "max_motor_temp", "kicks", "goals",
            "goal_bearing", "goal_confidence",
        ]
        # Add all joint angles
        for jn in self.JOINT_NAMES:
            cols.append("j_" + jn)
        return cols

    def _ensure_csv_header(self):
        if self._csv_initialized:
            return
        csv_path = self.out_dir + "game_log.csv"
        try:
            import os as _os
            if not _os.path.exists(csv_path):
                cols = self._get_csv_columns()
                with open(csv_path, "w") as f:
                    f.write(",".join(cols) + "\n")
        except Exception:
            pass
        self._csv_initialized = True

    def log_game_state(self, t):
        """Append one comprehensive row to game_log.csv for ML training.

        t is a merged dict containing telemetry + stability + sensor data.
        Called from BotFCBrain._run() each cycle (~20 Hz).
        """
        try:
            if not self._csv_initialized:
                self._ensure_csv_header()
            csv_path = self.out_dir + "game_log.csv"
            cols = self._get_csv_columns()
            vals = []
            for c in cols:
                v = t.get(c, 0.0)
                if isinstance(v, float):
                    vals.append("{:.5f}".format(v))
                else:
                    vals.append(str(v))
            with open(csv_path, "a") as f:
                f.write(",".join(vals) + "\n")
        except Exception:
            pass

    def _save_json(self, path, t):
        try:
            with open(path, "w") as f:
                json.dump(t, f, indent=2)
        except Exception:
            pass

    def _loop(self):
        self._ensure_csv_header()
        while self.running:
            try:
                img_data = self.video_device.getImageRemote(self.video_client_name)
                if img_data and len(img_data) > 6:
                    raw_bytes = img_data[6]
                    base_name = "{}frame_{}".format(self.out_dir, self.frame_index)
                    with open(base_name + ".raw", "wb") as f:
                        f.write(raw_bytes)
                    with self.lock:
                        t = self.current_telemetry.copy()
                    self._save_json(base_name + ".json", t)
                    self.frame_index += 1
                    self.video_device.releaseImage(self.video_client_name)
            except Exception:
                pass
            time.sleep(0.2)


# ─────────────────────────────────────────────
# CameraStreamer – JPEG frames via WS to server
# ─────────────────────────────────────────────
class CameraStreamer(object):
    """Grabs frames from NAOqi, encodes to JPEG, and streams them to the
    server via WebSocket /api/ws/bot_camera.  The server relays to browser
    clients on /api/ws/camera_feed.

    Strategy
    --------
    1. Subscribe to the camera with BGR colorspace (13) — the most
       reliable raw format across all NAO firmware versions.
    2. Pull frames with getImageRemote().  img[6] is a raw byte string
       of BGR pixels, NOT a standard image file.
    3. Reconstruct the pixels into a NumPy array with
       np.frombuffer(...).reshape(h, w, 3).
    4. Encode to JPEG with cv2.imencode (preferred) or PIL.
    5. Send the base64-encoded JPEG over WebSocket.
    """

    # BGR colorspace is the most reliable for numpy/cv2 reconstruction.
    _CAM_FMT = 13   # kBGRColorSpace
    _CAM_RES = 1    # kQVGA (320x240)
    _CAM_FPS = 5

    def __init__(self, robot_ip, robot_port, server_host, server_port):
        self.robot_ip    = robot_ip
        self.robot_port  = robot_port
        self.server_host = server_host
        self.server_port = server_port
        self.running     = False
        self.thread      = None
        self._vid        = None
        self._cam_client = ""
        self._encoder    = None   # 'cv2', 'pil', or None

    # ── Detect best JPEG encoder available on this system ────────────
    @staticmethod
    def _detect_encoder():
        """Return 'cv2', 'pil', or None."""
        try:
            import cv2 as _cv2            # noqa: F401
            return "cv2"
        except ImportError:
            pass
        try:
            from PIL import Image as _I   # noqa: F401
            return "pil"
        except ImportError:
            pass
        return None

    def start(self):
        if self.running:
            return

        self._encoder = self._detect_encoder()
        print("[CamStream] JPEG encoder: {}".format(self._encoder or "NONE"))

        try:
            self._vid = ALProxy("ALVideoDevice", self.robot_ip, self.robot_port)
        except Exception as e:
            print("[CamStream] ALVideoDevice unavailable: {}".format(e))
            return

        # Clean up any stale subscription from a previous crashed run
        for old in ("BotFC_Stream", "BotFC_Stream_0", "BotFC_Stream_1"):
            try:
                self._vid.unsubscribe(old)
            except Exception:
                pass

        # If we have no encoder, try kJpegColorSpace (21) so NAOqi
        # encodes the frame for us.  Otherwise use BGR (13).
        if self._encoder:
            fmt, label = self._CAM_FMT, "kBGR"
        else:
            fmt, label = 21, "kJpeg (no local encoder)"

        handle = None
        try:
            handle = self._vid.subscribeCamera(
                "BotFC_Stream", STREAM_CAM_ID, self._CAM_RES,
                fmt, self._CAM_FPS)
        except Exception as e1:
            print("[CamStream] subscribeCamera fmt={} failed: {}".format(fmt, e1))
            # Fallback: try the other format
            alt_fmt = 21 if fmt != 21 else 13
            try:
                handle = self._vid.subscribeCamera(
                    "BotFC_Stream", STREAM_CAM_ID, self._CAM_RES,
                    alt_fmt, self._CAM_FPS)
                fmt = alt_fmt
            except Exception as e2:
                print("[CamStream] Fallback subscribeCamera also failed: {}".format(e2))

        if not handle:
            print("[CamStream] Could not subscribe to camera – aborting.")
            return

        self._fmt = fmt
        self._cam_client = handle
        print("[CamStream] subscribed OK: handle={}, fmt={} ({})".format(
            handle, fmt, label))

        self.running = True
        self.thread  = threading.Thread(target=self._loop)
        self.thread.daemon = True
        self.thread.start()
        print("[CamStream] Streaming to {}:{} at {} fps.".format(
            self.server_host, self.server_port, self._CAM_FPS))

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        if self._vid and self._cam_client:
            try:
                self._vid.unsubscribe(self._cam_client)
            except Exception:
                pass

    # ── Frame → JPEG conversion ──────────────────────────────────────
    def _to_jpeg(self, nao_image):
        """Convert a NAOqi getImageRemote() result to JPEG bytes.

        Returns (jpeg_bytes, width, height) or (None, 0, 0) on failure.
        """
        if not nao_image or len(nao_image) <= 6:
            return None, 0, 0

        width    = int(nao_image[0])
        height   = int(nao_image[1])
        raw_data = nao_image[6]

        if not raw_data:
            return None, width, height

        # ── kJpegColorSpace (21): raw_data is already JPEG ───────────
        if self._fmt == 21:
            jpg = bytearray(raw_data)
            if len(jpg) < 3 or jpg[0] != 0xFF or jpg[1] != 0xD8:
                return None, width, height   # corrupt / not JPEG
            return bytes(jpg), width, height

        # ── Raw pixel format (BGR=13 / RGB=11): reconstruct with numpy
        try:
            import numpy as np
            # Convert the raw byte string into a flat uint8 array,
            # then reshape into (height, width, channels).
            arr = np.frombuffer(bytearray(raw_data), dtype=np.uint8)
            expected = width * height * 3
            if arr.size != expected:
                return None, width, height
            arr = arr.reshape((height, width, 3))
        except Exception as e:
            print("[CamStream] numpy reshape failed: {}".format(e))
            return None, width, height

        # ── Encode to JPEG ───────────────────────────────────────────
        # Prefer cv2 (fast, C-level); fall back to PIL.
        if self._encoder == "cv2":
            try:
                import cv2
                # cv2.imencode expects BGR, which is what fmt=13 gives us.
                if self._fmt == 11:          # RGB → BGR for cv2
                    arr = arr[:, :, ::-1]
                ok, buf = cv2.imencode(".jpg", arr,
                                       [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    return bytes(bytearray(buf)), width, height
            except Exception as e:
                print("[CamStream] cv2 encode error: {}".format(e))

        if self._encoder == "pil":
            try:
                from PIL import Image
                import io
                # PIL expects RGB
                if self._fmt == 13:          # BGR → RGB for PIL
                    arr = arr[:, :, ::-1]
                pil_img = Image.fromarray(arr, "RGB")
                buf = io.BytesIO()
                pil_img.save(buf, format="JPEG", quality=70)
                return buf.getvalue(), width, height
            except Exception as e:
                print("[CamStream] PIL encode error: {}".format(e))

        return None, width, height

    # ── WebSocket streaming loop ─────────────────────────────────────
    def _loop(self):
        import socket as _socket
        import base64 as _b64

        bad_frames = 0

        while self.running:
            sock = None
            try:
                sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                sock.settimeout(5)
                print("[CamStream] Connecting to {}:{}...".format(
                    self.server_host, self.server_port))
                sock.connect((self.server_host, self.server_port))

                ws_key = _b64.b64encode(os.urandom(16))
                handshake = (
                    "GET /api/ws/bot_camera HTTP/1.1\r\n"
                    "Host: {host}:{port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Key: {key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    "User-Agent: BotFC-CamStream-v2\r\n"
                    "\r\n"
                ).format(host=self.server_host, port=self.server_port,
                         key=ws_key)
                sock.sendall(handshake.encode("utf-8"))

                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise Exception("Handshake failed: no response")
                    resp += chunk

                first_line = resp.split(b"\r\n")[0] if resp else b""
                if b"101" not in first_line:
                    raise Exception("WS upgrade rejected: " + repr(first_line))

                print("[CamStream] WebSocket connected.")
                sock.settimeout(None)
                bad_frames = 0
                interval = 1.0 / self._CAM_FPS

                while self.running:
                    t0 = time.time()
                    try:
                        nao_img = self._vid.getImageRemote(self._cam_client)
                    except Exception as ge:
                        print("[CamStream] getImageRemote error: {}".format(ge))
                        nao_img = None

                    jpg, w, h = self._to_jpeg(nao_img)

                    if jpg:
                        b64 = _b64.b64encode(jpg)
                        # Python 2: b64 is already str; Python 3: decode
                        if not isinstance(b64, str):
                            b64 = b64.decode("ascii")
                        payload = json.dumps({
                            "type": "frame", "w": w, "h": h, "jpg": b64})
                        TelemetryClient._ws_send(sock, payload)
                        bad_frames = 0
                    else:
                        bad_frames += 1
                        if bad_frames % 20 == 1:
                            print("[CamStream] {} bad/empty frames".format(
                                bad_frames))

                    try:
                        self._vid.releaseImage(self._cam_client)
                    except Exception:
                        pass

                    elapsed = time.time() - t0
                    remaining = interval - elapsed
                    if remaining > 0:
                        time.sleep(remaining)

                try:
                    TelemetryClient._ws_close(sock)
                except Exception:
                    pass
            except Exception as e:
                err = str(e)
                if "Broken pipe" not in err and "EPIPE" not in err:
                    print("[CamStream] Error: {}. Retry in 3s...".format(e))
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
            if self.running:
                time.sleep(3)


# ─────────────────────────────────────────────
# CommandPoller – receives action commands from C++ server
# ─────────────────────────────────────────────
class CommandPoller(object):
    """Polls GET /api/bot/command every 0.5 s.

    The C++ server queues commands issued by the frontend operator or its
    own decision engine.  A pending command is returned once and cleared so
    the robot doesn't repeat it.  Valid actions: SEARCH, APPROACH, ALIGN,
    KICK, STOP.
    """

    def __init__(self, server_host, server_port):
        self.host    = server_host
        self.port    = server_port
        self.running = False
        self.thread  = None
        self._cmd    = None
        self.lock    = threading.Lock()

    def start(self):
        self.running = True
        self.thread  = threading.Thread(target=self._loop)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        import httplib  # Python 2.7
        while self.running:
            try:
                conn = httplib.HTTPConnection(self.host, self.port, timeout=2)
                conn.request("GET", "/api/bot/command")
                r = conn.getresponse()
                if r.status == 200:
                    data = json.loads(r.read())
                    action = data.get("action")
                    if action:
                        with self.lock:
                            self._cmd = action
                conn.close()
            except Exception:
                pass
            time.sleep(0.5)

    def get_and_clear(self):
        """Return pending command string or None."""
        with self.lock:
            cmd = self._cmd
            self._cmd = None
        return cmd


# ─────────────────────────────────────────────
# BotFCBrain
# ─────────────────────────────────────────────
class BotFCBrain(object):

    def __init__(self, robot_ip, robot_port, trait, server_ip, server_port):
        self.robot_ip   = robot_ip
        self.robot_port = robot_port

        if trait == "offense":
            self.role  = ROLE_STRIKER
        elif trait == "defense":
            self.role  = ROLE_DEFENDER
        else:
            self.role  = ROLE_BALANCED
            trait      = "balanced"
        self.trait = trait

        # FSM state
        self.state             = STATE_INIT
        self.lock              = threading.Lock()
        self.running           = False
        self.kick_count        = 0
        self.goal_count        = 0
        self.overheat_count    = 0
        self.break_remaining   = 0
        self.origin_x          = 0.0
        self.origin_y          = 0.0
        self.origin_theta      = 0.0
        self.last_ball_time    = 0.0
        self.last_man_on_time  = 0.0
        self.last_overheat_time= 0.0
        self.search_yaw        = 0.0
        self.search_yaw_dir    = 1.0
        self.search_sweeps     = 0
        self.field_map         = {}
        self.fsm_thread        = None
        self._state_enter_time = 0.0   # when current state was entered
        self._last_walk_cmd    = (0.0, 0.0, 0.0)  # (x, y, theta) for ML logging

        # Goal awareness
        self.goal_bearing      = DEFAULT_GOAL_BEARING
        self.goal_last_seen    = 0.0   # time.time() of last goal detection
        self.goal_confidence   = 0.0   # 0-1, decays when not seen

        # Ball perception
        self._last_ball_ts     = None     # NAOqi timestamp guard (stale detection)
        
        # Load C++ ML Inference Bridge (if available)
        self.ml_inference = None
        try:
            sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'ml'))
            from botfc_ml import BotFCInference
            self.ml_inference = BotFCInference("botfc_model.tflite")
            print("[BotFC] Loaded C++ ML Inference Bridge successfully.")
        except Exception as e:
            print("[BotFC] Could not load C++ ML Bridge (it's okay, will use P-Controllers instead): " + str(e))
        self.ball_model        = BallModel()

        # Bottom camera
        self._vid              = None     # ALVideoDevice proxy
        self._bot_cam_client   = ""       # subscription name
        self._top_cam_client   = ""

        # Stability controller
        self.stability = StabilityController()

        # Subsystems
        self.telemetry_client = TelemetryClient(server_ip, server_port)
        self.data_logger      = MLDataLogger(robot_ip, robot_port)
        self.camera_streamer  = CameraStreamer(robot_ip, robot_port, server_ip, server_port)
        self.command_poller   = CommandPoller(server_ip, server_port)

        # NAOqi proxies (initialised in start())
        self.motion    = None
        self.posture   = None
        self.memory    = None
        self.tts       = None
        self.leds      = None
        self.ball_det  = None
        self.sonar_p   = None
        self.battery   = None

        self.temp_keys = [
            "Device/SubDeviceList/LHipPitch/Temperature/Sensor/Value",
            "Device/SubDeviceList/RHipPitch/Temperature/Sensor/Value",
            "Device/SubDeviceList/LKneePitch/Temperature/Sensor/Value",
            "Device/SubDeviceList/RKneePitch/Temperature/Sensor/Value",
        ]

    # ─── Sensor helpers (for stability + ML) ──────────────

    def _read_inertial(self):
        """Return (roll_rad, pitch_rad) from the inertial sensor unit."""
        try:
            roll  = float(self.memory.getData(
                "Device/SubDeviceList/InertialSensor/AngleX/Sensor/Value"))
            pitch = float(self.memory.getData(
                "Device/SubDeviceList/InertialSensor/AngleY/Sensor/Value"))
            return (roll, pitch)
        except Exception:
            return (0.0, 0.0)

    def _read_gyro(self):
        """Return (gx, gy) angular velocity in rad/s from gyroscope."""
        try:
            gx = float(self.memory.getData(
                "Device/SubDeviceList/InertialSensor/GyroscopeX/Sensor/Value"))
            gy = float(self.memory.getData(
                "Device/SubDeviceList/InertialSensor/GyroscopeY/Sensor/Value"))
            return (gx, gy)
        except Exception:
            return (0.0, 0.0)

    def _read_accel(self):
        """Return (ax, ay, az) acceleration in m/s² from accelerometer."""
        try:
            ax = float(self.memory.getData(
                "Device/SubDeviceList/InertialSensor/AccelerometerX/Sensor/Value"))
            ay = float(self.memory.getData(
                "Device/SubDeviceList/InertialSensor/AccelerometerY/Sensor/Value"))
            az = float(self.memory.getData(
                "Device/SubDeviceList/InertialSensor/AccelerometerZ/Sensor/Value"))
            return (ax, ay, az)
        except Exception:
            return (0.0, 0.0, 9.81)

    def _read_foot_pressure(self):
        """Return (lf, lb, rf, rb) foot pressure sensor values."""
        try:
            vals = [float(self.memory.getData(k))
                    for k in MLDataLogger.FOOT_SENSORS]
            return tuple(vals)
        except Exception:
            return (0.0, 0.0, 0.0, 0.0)

    def _read_joint_angles(self):
        """Return dict of all joint angles {name: radians}."""
        result = {}
        try:
            angles = self.motion.getAngles("Body", False)
            for i, name in enumerate(MLDataLogger.JOINT_NAMES):
                if i < len(angles):
                    result["j_" + name] = round(float(angles[i]), 4)
                else:
                    result["j_" + name] = 0.0
        except Exception:
            for name in MLDataLogger.JOINT_NAMES:
                result["j_" + name] = 0.0
        return result

    def _read_max_motor_temp(self):
        """Return the hottest motor temperature."""
        try:
            temps = [float(self.memory.getData(k)) for k in self.temp_keys]
            return max(temps) if temps else 0.0
        except Exception:
            return 0.0

    def _is_fallen(self):
        """Return True if the inertial sensor shows the robot is no longer upright.

        Uses BOTH angle and gyro rate for faster detection.
        """
        roll, pitch = self._read_inertial()
        gx, gy = self._read_gyro()

        # Angle-based: severe tilt
        if abs(roll) > 0.55 or abs(pitch) > 0.80:
            return True

        # Rate-based: if tilting fast AND already leaning, about to fall
        if (abs(roll) > 0.35 and abs(gx) > 2.5) or \
           (abs(pitch) > 0.50 and abs(gy) > 2.5):
            return True

        return False

    # ─── Sonar helper ───────────────────────────
    def _read_sonar(self):
        """Read left and right sonar distance (metres). Returns (sl, sr)."""
        try:
            sl = float(self.memory.getData(
                "Device/SubDeviceList/US/Left/Sensor/Value"))
            sr = float(self.memory.getData(
                "Device/SubDeviceList/US/Right/Sensor/Value"))
            return sl, sr
        except Exception:
            return 9.0, 9.0

    # ─── Obstacle avoidance constants ─────────
    OBSTACLE_DANGER  = 0.25   # metres — very close, hard brake + curve
    OBSTACLE_CAUTION = 0.50   # metres — start curving around
    OBSTACLE_NOTICE  = 0.80   # metres — mild avoidance bias

    def _stable_walk(self, speed_x, speed_y, speed_theta):
        """Wrapper around moveToward with stability + obstacle avoidance.

        All FSM states should call this instead of self.motion.moveToward()
        to get:
          1. Adaptive speed reduction from StabilityController
          2. Sonar-based obstacle avoidance (curves around walls/robots)
        """
        sm = self.stability.speed_mult

        # ── Obstacle avoidance via sonar ──────────────────────────────
        sl, sr = self._read_sonar()
        self._sonar_left  = sl
        self._sonar_right = sr
        min_dist = min(sl, sr)

        avoid_turn = 0.0
        avoid_lat  = 0.0
        speed_cap  = 1.0      # fraction cap on forward speed

        if min_dist < self.OBSTACLE_DANGER:
            # Very close — strong avoidance
            if sl < sr:
                avoid_turn = -0.5    # hard turn right
                avoid_lat  = -0.08   # strafe right
            else:
                avoid_turn = 0.5     # hard turn left
                avoid_lat  = 0.08    # strafe left
            speed_cap = 0.3          # crawl forward

        elif min_dist < self.OBSTACLE_CAUTION:
            # Moderate distance — gentle curve
            closeness = 1.0 - (min_dist - self.OBSTACLE_DANGER) / \
                        (self.OBSTACLE_CAUTION - self.OBSTACLE_DANGER)
            if sl < sr:
                avoid_turn = -0.30 * closeness
                avoid_lat  = -0.05 * closeness
            else:
                avoid_turn = 0.30 * closeness
                avoid_lat  = 0.05 * closeness
            speed_cap = 0.5 + 0.5 * (1.0 - closeness)

        elif min_dist < self.OBSTACLE_NOTICE:
            # Far but noticed — slight bias
            closeness = 1.0 - (min_dist - self.OBSTACLE_CAUTION) / \
                        (self.OBSTACLE_NOTICE - self.OBSTACLE_CAUTION)
            if sl < sr:
                avoid_turn = -0.12 * closeness
            else:
                avoid_turn = 0.12 * closeness

        # ── Apply stability multiplier ────────────────────────────────
        adj_x     = speed_x * sm * speed_cap
        adj_y     = (speed_y + avoid_lat) * sm
        adj_theta = (speed_theta + avoid_turn) * min(1.0, sm + 0.2)

        # Clamp final commands to safe range
        adj_x     = max(-0.5, min(1.0, adj_x))
        adj_y     = max(-0.4, min(0.4, adj_y))
        adj_theta = max(-0.8, min(0.8, adj_theta))

        self._last_walk_cmd = (adj_x, adj_y, adj_theta)
        self.motion.moveToward(adj_x, adj_y, adj_theta)

    def _ensure_standing(self, max_attempts=3):
        """Bring the robot to StandInit from any starting posture.

        Strategy
        --------
        1. Enable full stiffness first (robot may be limp after a fall or
           a previous session's crouch-and-release).
        2. Check inertial sensors.  If already upright (|roll|<0.30 and
           |pitch|<0.30) just confirm with a gentle StandInit call.
        3. If tilted / on the ground: announce, then call
           goToPosture("StandInit", speed=0.3) – slow is safe.
           NAOqi's posture manager knows the correct get-up motion for each
           face-down / face-up starting configuration.
        4. Wait up to 5 s per attempt, re-check inertial sensors.
        5. Retry up to max_attempts times.

        Returns True when the robot is confirmed upright, False if all
        attempts fail (the FSM will stay in INIT and not drive the motors).
        """
        UPRIGHT_ROLL  = 0.30   # rad – threshold for "close enough to vertical"
        UPRIGHT_PITCH = 0.30
        GET_UP_SPEED  = 0.30   # slow: safer when coming from the floor
        SETTLE_TIME   = 5.0    # seconds to wait per attempt

        for attempt in range(1, max_attempts + 1):
            # Always ensure stiffness before trying to move
            try:
                self.motion.setStiffnesses("Body", 1.0)
            except Exception:
                pass

            roll, pitch = self._read_inertial()
            upright = abs(roll) < UPRIGHT_ROLL and abs(pitch) < UPRIGHT_PITCH

            if upright and attempt == 1:
                # Robot is already upright – just confirm with StandInit
                print("[BotFC] Posture: upright (roll={:.2f} pitch={:.2f}). "
                      "Calling StandInit.".format(roll, pitch))
                try:
                    self.posture.goToPosture("StandInit", 0.5)
                except Exception:
                    pass
                return True

            print("[BotFC] Posture attempt {}/{}: roll={:.2f} pitch={:.2f} – "
                  "robot not upright. Attempting get-up...".format(
                      attempt, max_attempts, roll, pitch))
            try:
                self.tts.post.say("Stand by, getting up.")
            except Exception:
                pass

            try:
                self.posture.goToPosture("StandInit", GET_UP_SPEED)
            except Exception as e:
                print("[BotFC] goToPosture error: {}".format(e))

            # Wait for the motion to complete and the robot to settle
            time.sleep(SETTLE_TIME)

            roll, pitch = self._read_inertial()
            if abs(roll) < UPRIGHT_ROLL and abs(pitch) < UPRIGHT_PITCH:
                print("[BotFC] Get-up succeeded on attempt {}.".format(attempt))
                return True

            print("[BotFC] Still not upright after attempt {}.".format(attempt))
            time.sleep(1.0)

        print("[BotFC] WARN: Could not confirm upright after {} attempts. "
              "Proceeding anyway.".format(max_attempts))
        return False

    # ─── Startup / shutdown ──────────────────
    def start(self):
        if self.running:
            return

        try:
            self.motion   = ALProxy("ALMotion",         self.robot_ip, self.robot_port)
            self.posture  = ALProxy("ALRobotPosture",   self.robot_ip, self.robot_port)
            self.memory   = ALProxy("ALMemory",         self.robot_ip, self.robot_port)
            self.tts      = ALProxy("ALTextToSpeech",   self.robot_ip, self.robot_port)
            self.leds     = ALProxy("ALLeds",           self.robot_ip, self.robot_port)
            self.ball_det = ALProxy("ALRedBallDetection", self.robot_ip, self.robot_port)
            self.sonar_p  = ALProxy("ALSonar",          self.robot_ip, self.robot_port)
        except Exception as e:
            print("[BotFC] FATAL: Failed to init proxies: {}".format(e))
            return

        try:
            self.battery = ALProxy("ALBattery", self.robot_ip, self.robot_port)
        except Exception as e:
            print("[BotFC] ALBattery unavailable: {}".format(e))

        # ── Step 1: get the robot upright BEFORE anything else ────────────
        # This must happen before subscribing cameras or starting the FSM so
        # that no motion command fires while the robot is still on the floor.
        self.motion.setStiffnesses("Body", 1.0)
        self._ensure_standing()

        # ── Step 2: now it's safe to subscribe sensors / cameras ──────────
        # Disable collision protection so the robot can close in on the ball
        # without auto-shuffling away from "obstacles" (its own arms/opponent).
        try:
            self.motion.setExternalCollisionProtectionEnabled("All", False)
        except Exception:
            pass

        try:
            self.ball_det.subscribe("BotFCBrain", 33, 0.0)
        except Exception:
            pass
        try:
            self.sonar_p.subscribe("BotFCBrain")
        except Exception:
            pass

        # Cameras – subscribe to both via ALVideoDevice for reliable OpenCV processing
        try:
            self._vid = ALProxy("ALVideoDevice", self.robot_ip, self.robot_port)
            self._bot_cam_client = self._vid.subscribeCamera(
                "BotFC_BottomDetect", BOT_CAM_ID, BOT_CAM_RES, BOT_CAM_FORMAT, BOT_CAM_FPS)
            self._top_cam_client = self._vid.subscribeCamera(
                "BotFC_TopDetect", TOP_CAM_ID, BOT_CAM_RES, BOT_CAM_FORMAT, BOT_CAM_FPS)
            print("[BotFC] Both cameras subscribed via ALVideoDevice.")
        except Exception as e:
            print("[BotFC] Camera unavailable: {}".format(e))

        self.tts.post.say("Brain online. Let's play football.")

        try:
            p = self.motion.getRobotPosition(True)
            self.origin_x, self.origin_y, self.origin_theta = p[0], p[1], p[2]
        except Exception:
            pass

        self.last_ball_time = time.time()  # start grace period from NOW, not -100s

        with self.lock:
            self.state = STATE_SEARCH

        self.running = True
        self.data_logger.start()
        self.telemetry_client.start(self.trait)
        self.camera_streamer.start()
        self.command_poller.start()

        self.fsm_thread = threading.Thread(target=self._run)
        self.fsm_thread.daemon = True
        self.fsm_thread.start()

        print("[BotFC] Brain started. Role={}, Trait={}".format(self.role, self.trait))

    def stop(self):
        if not self.running:
            return
        self.running = False

        self.data_logger.stop()
        self.telemetry_client.stop()
        self.camera_streamer.stop()
        self.command_poller.stop()

        if self.fsm_thread and self.fsm_thread.is_alive():
            self.fsm_thread.join(timeout=5)

        # Unsubscribe cameras
        if self._vid:
            if getattr(self, '_bot_cam_client', None):
                try: self._vid.unsubscribe(self._bot_cam_client)
                except Exception: pass
            if getattr(self, '_top_cam_client', None):
                try: self._vid.unsubscribe(self._top_cam_client)
                except Exception: pass

        try:
            self.motion.stopMove()
        except Exception:
            pass
        try:
            self.ball_det.unsubscribe("BotFCBrain")
        except Exception:
            pass
        try:
            self.sonar_p.unsubscribe("BotFCBrain")
        except Exception:
            pass
        try:
            self.posture.goToPosture("Crouch", 0.8)
            self.motion.setStiffnesses("Body", 0.0)
        except Exception:
            pass

        print("[BotFC] Brain stopped safely.")

    # ─── Kill switch ────────────────────────
    def _check_kill_switch(self):
        """Return True if any head touch sensor is pressed.

        Pressing the head is the standard RoboCup SPL 'penalise / stop' signal.
        We halt all motion and crouch the robot to a safe posture immediately.
        """
        keys = [
            "Device/SubDeviceList/Head/Touch/Front/Sensor/Value",
            "Device/SubDeviceList/Head/Touch/Middle/Sensor/Value",
            "Device/SubDeviceList/Head/Touch/Rear/Sensor/Value",
        ]
        try:
            for k in keys:
                if float(self.memory.getData(k)) > 0.5:
                    return True
        except Exception:
            pass
        return False

    # ─── FSM Main Loop ──────────────────────
    def _run(self):
        while self.running:
            # ── Server command override ────────────────────────────────────
            srv_cmd = self.command_poller.get_and_clear()
            if srv_cmd:
                if srv_cmd == "STOP":
                    self.motion.stopMove()
                    with self.lock:
                        self.state = STATE_SEARCH
                elif srv_cmd in (STATE_SEARCH, STATE_APPROACH, STATE_ORBIT, STATE_ALIGN, STATE_KICK):
                    with self.lock:
                        self.state = srv_cmd
                elif srv_cmd == "KICK_NOW":
                    with self.lock:
                        self.state = STATE_KICK

            # ── Kill switch (head touch) ───────────────────────────────────
            if self._check_kill_switch():
                self.motion.stopMove()
                try:
                    self.posture.goToPosture("Crouch", 0.8)
                    self.motion.setStiffnesses("Body", 0.0)
                    self.tts.post.say("Stopping.")
                except Exception:
                    pass
                while self.running and self._check_kill_switch():
                    time.sleep(0.2)
                if self.running:
                    self._ensure_standing()
                    with self.lock:
                        self.state = STATE_SEARCH
                continue

            # ══════════════════════════════════════════════════════════════
            # SENSOR SNAPSHOT — read all sensors ONCE per cycle for
            # stability, ML logging, and FSM decisions.
            # ══════════════════════════════════════════════════════════════
            roll, pitch = self._read_inertial()
            gx, gy      = self._read_gyro()
            ax, ay, az  = self._read_accel()
            foot_lf, foot_lb, foot_rf, foot_rb = self._read_foot_pressure()
            joint_angles = self._read_joint_angles()
            max_temp     = self._read_max_motor_temp()

            # ── Update stability controller ────────────────────────────────
            self.stability.update(roll, pitch, gx, gy)

            # ── Fall recovery (mid-match) ──────────────────────────────────
            if self._is_fallen():
                self.motion.stopMove()
                self.ball_model.valid = False
                self.stability.record_fall()
                try:
                    self.leds.fadeRGB("AllLeds", 0xFF6600, 0.1)
                except Exception:
                    pass
                print("[BotFC] Fall detected (#{}) – recovery.".format(
                    self.stability.fall_count))
                self._ensure_standing(max_attempts=3)
                try:
                    self.leds.fadeRGB("AllLeds", 0x00FF00, 0.2)
                except Exception:
                    pass
                with self.lock:
                    self.state = STATE_SEARCH
                    self._state_enter_time = time.time()
                continue

            self._safety_check()
            self.ball_model.tick()

            with self.lock:
                s   = self.state
                k   = self.kick_count
                gc  = self.goal_count
                br  = self.break_remaining
                lbt = self.last_ball_time
                s_enter = self._state_enter_time

            ball_age = self.ball_model.age()
            time_in_state = time.time() - s_enter if s_enter > 0 else 0.0

            # Battery
            bat = -1
            try:
                if self.battery:
                    bat = int(self.battery.getBatteryCharge())
            except Exception:
                pass

            # Sonar
            sonar_l = sonar_r = 9.0
            try:
                sonar_l = float(self.memory.getData(
                    "Device/SubDeviceList/US/Left/Sensor/Value"))
                sonar_r = float(self.memory.getData(
                    "Device/SubDeviceList/US/Right/Sensor/Value"))
            except Exception:
                pass

            # Head yaw
            h_yaw = 0.0
            try:
                h_yaw = self.motion.getAngles("HeadYaw", False)[0]
            except Exception:
                pass

            # ── Build comprehensive telemetry snapshot ─────────────────────
            bm = self.ball_model
            stab_ml = self.stability.get_ml_dict()
            wcx, wcy, wct = self._last_walk_cmd

            telem = {
                # Timestamp + FSM
                "timestamp":       round(time.time(), 3),
                "state":           s,
                "time_in_state":   round(time_in_state, 2),
                # Ball
                "ball_valid":      1 if bm.valid else 0,
                "ball_bx":         round(bm.bx,  3),
                "ball_by":         round(bm.by,  3),
                "ball_bsz":        round(bm.bsz, 4),
                "ball_dist":       round(bm.dist, 2),
                "ball_vx":         round(bm.vbx, 3),
                "ball_vy":         round(bm.vby, 3),
                "ball_pred_bx":    round(bm.pred_bx, 3),
                "ball_pred_by":    round(bm.pred_by, 3),
                "ball_confidence": round(bm.confidence, 2),
                # IMU
                "imu_roll":        round(roll,  4),
                "imu_pitch":       round(pitch, 4),
                "gyro_x":          round(gx, 4),
                "gyro_y":          round(gy, 4),
                "accel_x":         round(ax, 4),
                "accel_y":         round(ay, 4),
                "accel_z":         round(az, 4),
                # Walk command
                "walk_speed_x":    round(wcx, 3),
                "walk_speed_y":    round(wcy, 3),
                "walk_speed_theta":round(wct, 3),
                # Foot pressure
                "foot_lf":         round(foot_lf, 3),
                "foot_lb":         round(foot_lb, 3),
                "foot_rf":         round(foot_rf, 3),
                "foot_rb":         round(foot_rb, 3),
                # Sonar
                "sonar_left":      round(sonar_l, 3),
                "sonar_right":     round(sonar_r, 3),
                # System
                "battery_pct":     bat,
                "max_motor_temp":  round(max_temp, 1),
                "kicks":           k,
                "goals":           gc,
                "goal_bearing":    round(self.goal_bearing, 3),
                "goal_confidence": round(self.goal_confidence, 2),
                # Legacy keys (for telemetry WS)
                "ball_age":        round(ball_age, 2),
                "break_remaining": br,
                "head_yaw":        round(h_yaw, 3),
                "inertial_roll":   round(roll, 3),
                "inertial_pitch":  round(pitch, 3),
            }
            # Merge stability
            telem.update(stab_ml)
            # Merge joint angles
            telem.update(joint_angles)

            # Push to websocket telemetry + ML logger
            self.telemetry_client.update(**telem)
            self.data_logger.update_telemetry(telem)
            self.data_logger.log_game_state(telem)

            if s != STATE_HALFTIME:
                self._enforce_bounds()
                self._update_goal_confidence()

                if s in (STATE_SEARCH, STATE_APPROACH):
                    self._detect_goal_posts()

                prev_state = s
                if   s == STATE_SEARCH:    self._do_search()
                elif s == STATE_APPROACH:  self._do_approach()
                elif s == STATE_ORBIT:     self._do_orbit()
                elif s == STATE_ALIGN:     self._do_align()
                elif s == STATE_KICK:      self._do_kick()
                elif s == STATE_TACKLE:    self._do_tackle()
                elif s == STATE_CELEBRATE: self._do_celebrate()
                else:
                    with self.lock:
                        self.state = STATE_SEARCH

                # Track state transitions for time_in_state
                with self.lock:
                    if self.state != prev_state:
                        self._state_enter_time = time.time()

            time.sleep(0.05)

    # ─── Ball perception helpers ─────────────

    def _read_ball(self):
        """Top camera ball detection using robust OpenCV circularity."""
        return self._detect_cam_opencv(self._top_cam_client, TOP_CAM_HFOV, TOP_CAM_VFOV, min_px=RED_MIN_PX)

    def _read_ball_raw(self):
        """Same as above, without cache delay. Redundant, kept for API compat."""
        return self._detect_cam_opencv(self._top_cam_client, TOP_CAM_HFOV, TOP_CAM_VFOV, min_px=RED_MIN_PX)

    def _detect_bottom_cam(self):
        """Bottom camera ball detection."""
        return self._detect_cam_opencv(self._bot_cam_client, BOT_CAM_HFOV, BOT_CAM_VFOV, min_px=20)

    def _detect_cam_opencv(self, client_id, hfov_rad, vfov_rad, min_px=20):
        """Detect red ball in the given camera using OpenCV for robust circularity validation.

        This uses finding contours and calculating circularity (4*pi*Area/Perimeter^2)
        to prevent false positives from irregular shapes like shoes or wires.
        Converts the raw image fractions to angle radians for the agent math.
        """
        if not (self._vid and client_id):
            return None
        try:
            img = self._vid.getImageRemote(client_id)
            if not img or len(img) < 7:
                return None
            width  = int(img[0])
            height = int(img[1])
            pixels = bytearray(img[6])

            # Use OpenCV to process the image frame
            import numpy as np
            import cv2
            
            # Reconstruct image from byte array (NAOqi sends as BGR)
            frame = np.frombuffer(pixels, dtype=np.uint8).reshape((height, width, 3))
            
            # Convert to HSV to better isolate red, which wraps around the hue channel
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            
            # Red color range 1
            lower_red1 = np.array([0, 120, 70])
            upper_red1 = np.array([10, 255, 255])
            mask1 = cv2.inRange(hsv, lower_red1, upper_red1)

            # Red color range 2
            lower_red2 = np.array([170, 120, 70])
            upper_red2 = np.array([180, 255, 255])
            mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
            
            # Combine masks
            mask = mask1 + mask2
            
            # Find contours
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            best_blob = None
            max_area = 0
            
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < min_px:
                    continue
                    
                perimeter = cv2.arcLength(cnt, True)
                if perimeter == 0:
                    continue
                    
                # Circularity: 4 * pi * (Area / Perimeter^2)
                # Perfect circle = 1.0. Square ~ 0.78. 
                circularity = 4 * np.pi * (area / (perimeter * perimeter))
                
                # Check validation: Must be roughly a circle > 0.6
                if circularity > 0.6 and area > max_area:
                    max_area = area
                    best_blob = cnt
                    
            self._vid.releaseImage(client_id)

            if best_blob is None:
                return None

            # Get image moments for centroid
            M = cv2.moments(best_blob)
            if M["m00"] == 0:
                return None
                
            cx_px = int(M["m10"] / M["m00"])
            cy_px = int(M["m01"] / M["m00"])

            # Normalise: centre of frame = 0, positive = LEFT
            frac_x = -(float(cx_px) - width  * 0.5) / width
            frac_y = (float(cy_px) - height * 0.5) / height
            
            # **CRITICAL**: Convert fractions to angles in radians according to camera HFOV
            cx = frac_x * hfov_rad
            cy = frac_y * vfov_rad
            
            sz = float(max_area) / (width * height)
            
            return (cx, cy, sz)
        except Exception as e:
            return None

    def _detect_goal_posts(self):
        """Detect goal posts using multiple colour profiles for different
        lighting conditions (bright yellow, dim yellow, white posts).

        Scans through GOAL_POST_PROFILES and uses whichever profile finds
        the most matching pixels above the minimum threshold.  Also checks
        that the blob has a vertical shape (taller than it is wide) to
        distinguish actual posts from floor markings or other yellow objects.

        Returns a world-relative bearing to the goal, or None.
        """
        if not (self._vid and self._bot_cam_client):
            return None
        try:
            img = self._vid.getImageRemote(self._bot_cam_client)
            if not img or len(img) < 7:
                return None
            width  = int(img[0])
            height = int(img[1])
            pixels = bytearray(img[6])

            # Only scan top 65% of frame.
            scan_rows = int(height * 0.65)
            stride = BOT_CAM_STRIDE * 3
            max_offset = min(scan_rows * width * 3, len(pixels) - 2)

            best_count  = 0
            best_bx_sum = 0
            best_y_min  = height
            best_y_max  = 0
            best_label  = ""

            for prof in GOAL_POST_PROFILES:
                p_r_min, p_g_min, p_b_max, p_diff, p_label = prof
                bx_sum = count = 0
                y_min = height
                y_max = 0

                for off in range(0, max_offset, stride):
                    b = pixels[off]
                    g = pixels[off + 1]
                    r = pixels[off + 2]

                    match = False
                    if p_diff >= 0:
                        # Coloured post (yellow): R/G high, B low.
                        if (r > p_r_min and g > p_g_min
                                and b < p_b_max
                                and min(r, g) - b > p_diff):
                            match = True
                    else:
                        # White post: all channels high and close together.
                        if (r > p_r_min and g > p_g_min
                                and b > 170
                                and abs(r - g) < 35 and abs(r - b) < 35):
                            match = True

                    if match:
                        px = (off // 3) % width
                        py = (off // 3) // width
                        bx_sum += px
                        if py < y_min:
                            y_min = py
                        if py > y_max:
                            y_max = py
                        count += 1

                if count > best_count:
                    best_count  = count
                    best_bx_sum = bx_sum
                    best_y_min  = y_min
                    best_y_max  = y_max
                    best_label  = p_label

            self._vid.releaseImage(self._bot_cam_client)

            if best_count < GOAL_POST_MIN_PX:
                return None

            # Shape check: blob must be vertically tall (goal posts are vertical).
            blob_height = best_y_max - best_y_min
            if blob_height < height * GOAL_POST_MIN_HEIGHT_RATIO:
                return None  # too flat — probably floor marking or noise

            # Centroid x, normalised to [-0.5, 0.5]
            goal_cx = (float(best_bx_sum) / best_count - width * 0.5) / width

            # NAO bottom camera HFOV ≈ 47.64° = 0.831 rad
            cam_bearing = goal_cx * BOT_CAM_HFOV

            head_yaw = 0.0
            try:
                head_yaw = self.motion.getAngles("HeadYaw", False)[0]
            except Exception:
                pass

            world_bearing = head_yaw - cam_bearing

            # EMA smoothing.
            now = time.time()
            alpha = 0.4 if (now - self.goal_last_seen) < 2.0 else 0.8
            self.goal_bearing = alpha * world_bearing + (1.0 - alpha) * self.goal_bearing
            self.goal_last_seen = now
            self.goal_confidence = min(1.0, self.goal_confidence + 0.15)

            return world_bearing
        except Exception:
            return None

    def _update_goal_confidence(self):
        """Decay goal confidence when not recently seen."""
        age = time.time() - self.goal_last_seen
        if age > 3.0:
            self.goal_confidence = max(0.0, self.goal_confidence - 0.02)
        if age > 10.0:
            self.goal_bearing = self.goal_bearing * 0.95 + DEFAULT_GOAL_BEARING * 0.05

    # ─── Goal scored detection ──────────────────
    def _check_goal_scored(self):
        """Check if the ball has disappeared after a kick (implying it went
        into the goal).  Called right after the kick motion completes.

        Strategy:
        1. The ball was at the robot's feet and we kicked toward goal_bearing.
        2. Look ahead in the kick direction for ~1 second.
        3. If the ball is NOT detected in most samples → we scored!
        4. If we still see the ball → it didn't go in (or went wide).

        Returns True if a goal was likely scored.
        """
        # Wait for the ball to travel.
        time.sleep(GOAL_CHECK_DELAY)

        # Look in the kick direction.
        self.motion.setAngles("HeadYaw",   0.0,  0.3)
        self.motion.setAngles("HeadPitch", 0.20, 0.3)  # look slightly down/ahead
        time.sleep(0.3)

        ball_gone_count = 0
        for _ in range(GOAL_CHECK_SAMPLES):
            ball = self._read_ball()
            if ball is None:
                ball = self._detect_bottom_cam()
            if ball is None:
                ball_gone_count += 1
            time.sleep(GOAL_CHECK_INTERVAL)

        return ball_gone_count >= GOAL_BALL_GONE_THRESH

    # ─── Celebration ───────────────────────────
    def _do_celebrate(self):
        """Full celebration routine when the robot scores a goal.

        Sequence:
        1. LED light show (cycling colours)
        2. Victory speech
        3. Arm raise animation
        4. Optional spin/wiggle
        5. Return to search
        """
        try:
            self.motion.stopMove()

            # ── LED light show ──────────────────────────────────────────
            colours = [0x00FF00, 0xFFFF00, 0x00FFFF, 0xFF00FF, 0xFFFFFF]
            for c in colours:
                self.leds.fadeRGB("AllLeds", c, 0.15)
                time.sleep(0.2)

            # ── Victory speech ──────────────────────────────────────────
            phrases = [
                "Goal! What a strike!",
                "Get in! Absolute banger!",
                "Goal! I am the greatest footballer!",
                "Yes! Nothing but net!",
                "Golazo! Magnificent!",
            ]
            import random
            phrase = random.choice(phrases)
            self.tts.post.say(phrase)

            # ── Arms up celebration ─────────────────────────────────────
            self.posture.goToPosture("Stand", 0.8)
            time.sleep(0.3)

            # Raise both arms up (low ShoulderPitch = arms up)
            self.motion.setAngles(
                ["LShoulderPitch", "RShoulderPitch",
                 "LShoulderRoll",  "RShoulderRoll",
                 "LElbowYaw",      "RElbowYaw"],
                [-1.0, -1.0,    # arms up
                  0.3, -0.3,    # spread out
                 -1.5,  1.5],   # elbows out
                0.3
            )
            time.sleep(1.5)

            # ── Victory wiggle ──────────────────────────────────────────
            for _ in range(3):
                self.motion.setAngles("HeadYaw",  0.4, 0.5)
                time.sleep(0.2)
                self.motion.setAngles("HeadYaw", -0.4, 0.5)
                time.sleep(0.2)
            self.motion.setAngles("HeadYaw", 0.0, 0.3)

            # ── Second LED flash ────────────────────────────────────────
            for _ in range(4):
                self.leds.fadeRGB("AllLeds", 0x00FF00, 0.08)
                time.sleep(0.15)
                self.leds.fadeRGB("AllLeds", 0x000000, 0.08)
                time.sleep(0.15)

            # ── Spin move ───────────────────────────────────────────────
            self.motion.setAngles(
                ["LShoulderPitch", "RShoulderPitch"],
                [1.5, 1.5], 0.3)  # arms back down
            time.sleep(0.3)
            self.posture.goToPosture("Stand", 0.8)
            self.motion.moveTo(0.0, 0.0, 3.14)   # full spin!
            time.sleep(0.5)

            self.tts.post.say("Let's go again!")

        except Exception:
            pass

        self.posture.goToPosture("StandInit", 0.8)
        with self.lock:
            self.state = STATE_SEARCH

    def _get_ball_and_update_model(self):
        """Query both cameras, update the BallModel if anything is found.

        Top camera (ALRedBallDetection) is the primary source for far-ball.
        Bottom camera is used in addition when we're in ALIGN / KICK range.
        Returns the raw (bx, by, bsz) of the freshest reading, or None.
        """
        ball = self._read_ball()           # top cam (far range)

        if ball is None:
            ball = self._detect_bottom_cam()  # bottom cam (close range / blind spot)

        if ball is not None:
            bx, by, bsz = ball
            try:
                head_yaw = self.motion.getAngles("HeadYaw", False)[0]
            except Exception:
                head_yaw = 0.0
            self.ball_model.update(bx, by, bsz, head_yaw)

        return ball

    # ─── Sonar / field map ──────────────────
    def _update_local_map(self):
        try:
            sl = float(self.memory.getData("Device/SubDeviceList/US/Left/Sensor/Value"))
            sr = float(self.memory.getData("Device/SubDeviceList/US/Right/Sensor/Value"))
            p  = self.motion.getRobotPosition(True)
            la = (p[2] + 0.5) * (180.0 / math.pi)
            ra = (p[2] - 0.5) * (180.0 / math.pi)
            sl_sec = int(la / 30.0) * 30
            sr_sec = int(ra / 30.0) * 30

            with self.lock:
                if sl_sec not in self.field_map or sl < self.field_map[sl_sec]:
                    self.field_map[sl_sec] = sl
                if sr_sec not in self.field_map or sr < self.field_map[sr_sec]:
                    self.field_map[sr_sec] = sr

            snapshot = {
                "headYaw":   self.motion.getAngles("HeadYaw",   False)[0],
                "headPitch": self.motion.getAngles("HeadPitch", False)[0],
                "sonarLeft": sl, "sonarRight": sr,
                "ballFound": self.ball_model.valid,
                "ballBx":    self.ball_model.bx,
                "ballBy":    self.ball_model.by,
                "ballBsz":   self.ball_model.bsz,
            }
            self.data_logger.update_telemetry(snapshot)
        except Exception:
            pass

    def _is_in_bounds(self, x, y):
        with self.lock:
            dx = x - self.origin_x
            dy = y - self.origin_y
            fm = self.field_map.copy()
        dist = math.sqrt(dx*dx + dy*dy)
        if dist > MAX_FIELD_RADIUS:
            return False
        angle  = math.atan2(dy, dx) * (180.0 / math.pi)
        sector = int(angle / 30.0) * 30
        if sector in fm and dist > fm[sector] * 0.85:
            return False
        return True

    def _enforce_bounds(self):
        try:
            p = self.motion.getRobotPosition(True)
            if not self._is_in_bounds(p[0], p[1]):
                self.motion.stopMove()
                self.leds.fadeRGB("AllLeds", 0xFF00FF, 0.15)
                target = math.atan2(self.origin_y - p[1], self.origin_x - p[0])
                turn = target - p[2]
                while turn >  math.pi: turn -= 2.0 * math.pi
                while turn < -math.pi: turn += 2.0 * math.pi
                self.motion.moveTo(0.0, 0.0, turn)
                self.motion.moveTo(0.3, 0.0, 0.0)
        except Exception:
            pass

    # ─── Safety / overheat ──────────────────
    def _safety_check(self):
        try:
            temps = [float(self.memory.getData(k)) for k in self.temp_keys]
        except Exception:
            return

        max_t = max(temps) if temps else 0.0
        if max_t <= MOTOR_TEMP_LIMIT:
            return

        self.motion.stopMove()
        now = time.time()
        if now - self.last_overheat_time < 180.0:
            self.overheat_count += 1
        else:
            self.overheat_count = 0
        self.last_overheat_time = now

        cd   = 60 + self.overheat_count * 30
        mins = cd // 60
        secs = cd % 60
        if mins > 0:
            phrase = "Motors at {}. I need a {} minute break.".format(int(max_t), mins)
        else:
            phrase = "Motors at {}. I need a {} second break.".format(int(max_t), secs)

        self.tts.post.say(phrase)
        self.leds.fadeRGB("AllLeds", 0xFFA200, 0.15)
        self.posture.goToPosture("Crouch", 0.8)
        self.motion.setStiffnesses("Body", 0.0)

        with self.lock:
            self.state = STATE_HALFTIME

        for r in range(cd, 0, -1):
            if not self.running:
                break
            with self.lock:
                self.break_remaining = r
            if r % 30 == 0:
                self.tts.post.say("{} minutes remaining.".format(r // 60))
            time.sleep(1)

        with self.lock:
            self.break_remaining = 0

        if self.running:
            self.tts.post.say("Cooling complete.")
            self.motion.setStiffnesses("Body", 1.0)
            self.posture.goToPosture("StandInit", 1.0)
            with self.lock:
                self.state = STATE_SEARCH

    # ─── SEARCH ─────────────────────────────
    def _do_search(self):
        self._update_local_map()
        self.leds.fadeRGB("AllLeds", 0xFF3300, 0.15)

        # Try both cameras.  _read_ball() guards against stale NAOqi cache.
        ball = self._get_ball_and_update_model()

        if ball is not None:
            # First detection is real (passed the stale-timestamp guard).
            # Quick verify: check bottom cam OR raw re-read to confirm.
            # This is less strict than before — the stale guard already
            # filtered out ghost data, so a single confirm is enough.
            confirm = self._detect_bottom_cam()
            if confirm is None:
                confirm = self._read_ball_raw()  # re-read without stale guard

            if confirm is not None:
                self.motion.stopMove()
                with self.lock:
                    self.last_ball_time = time.time()
                    self.state = STATE_APPROACH
                self.motion.setAngles("HeadPitch", 0.15, 0.2)
                self.motion.setAngles("HeadYaw",   0.0,  0.3)
                return
            # Single camera only — still transition, but don't freeze head.
            # The ball is probably real since _read_ball() passed the stale check.
            self.motion.stopMove()
            with self.lock:
                self.last_ball_time = time.time()
                self.state = STATE_APPROACH
            return
            # Fall through removed: first fresh detection is trusted.

        # ── Dead Reckoning & Sweep with heading memory ──────────────────────
        with self.lock:
            ltime = self.last_ball_time
            
        search_duration = time.time() - ltime

        # If entering search freshly, seed the yaw with last heading
        if getattr(self, '_search_sweeps_reset_time', 0) < ltime:
            self._search_sweeps_reset_time = time.time()
            self.search_sweeps = 0
            if self.ball_model.last_heading != 0.0:
                self.search_yaw = max(-1.0, min(1.0, self.ball_model.last_heading))
                self.search_yaw_dir = -1.0 if self.search_yaw > 0 else 1.0

        # Phase 1: Dead Reckoning (Look Left/Right locally for 2s)
        if search_duration < 2.0:
            # Stand still, just pan the head
            self.motion.stopMove()
            
            # Oscillate head quickly around last known position
            center = max(-0.8, min(0.8, self.ball_model.last_heading))
            oscillation = math.sin((search_duration / 2.0) * math.pi * 4) * 0.5
            look_yaw = center + oscillation
            look_yaw = max(-1.0, min(1.0, look_yaw))
            
            self.motion.setAngles("HeadPitch", 0.4, 0.25)
            self.motion.setAngles("HeadYaw", look_yaw, 0.4)
            return

        # Phase 2: Start Body Searching & Memory Bounds
        # Narrow search window if we just lost the ball, expanding after 4 seconds
        if search_duration < 4.0 and self.ball_model.last_heading != 0.0:
            center = max(-0.6, min(0.6, self.ball_model.last_heading))
            sweep_limit_high = min(1.0, center + 0.5)
            sweep_limit_low  = max(-1.0, center - 0.5)
        else:
            sweep_limit_low = -1.5
            sweep_limit_high = 1.50

        self.search_yaw += self.search_yaw_dir * SEARCH_HEAD_SPEED
        if self.search_yaw >= sweep_limit_high:
            self.search_yaw     = sweep_limit_high
            self.search_yaw_dir = -1.0
            self.search_sweeps += 1
        elif self.search_yaw <= sweep_limit_low:
            self.search_yaw     = sweep_limit_low
            self.search_yaw_dir = 1.0
            self.search_sweeps += 1

        # Move head slightly faster during search to cover ground quicker
        self.motion.setAngles("HeadYaw", self.search_yaw, 0.25)

        # Alternate pitch: look down at ground nearby, then further out.
        sweeps = self.search_sweeps
        if sweeps % 2 == 0:
            self.motion.setAngles("HeadPitch", SEARCH_PITCH_LOW, 0.15)
        else:
            self.motion.setAngles("HeadPitch", SEARCH_PITCH_HIGH, 0.15)

        # ── Body movement while searching ──────────────────────────────────
        if search_duration > SEARCH_WALK_DELAY:
            # Energy efficiency: Do not walk forward blindly. 
            # First, simply turn in place towards the last known ball direction.
            # Rotating in place uses significantly less battery and doesn't get us out of position.
            if search_duration < SEARCH_WALK_DELAY + 10.0:
                rot_dir = 0.3 if self.ball_model.last_heading > 0 else -0.3
                self._stable_walk(0.0, 0.0, rot_dir)
            else:
                # Only creep forward slowly if a full spin yielded nothing
                self._stable_walk(0.15, 0.0, 0.2 if sweeps % 4 < 2 else -0.2)
        else:
            # First few seconds: stand still and just scan with head.
            self.motion.stopMove()

    # ─── APPROACH ───────────────────────────
    def _do_approach(self):
        self._update_local_map()
        self.leds.fadeRGB("AllLeds", 0x00FF00, 0.15)

        now = time.time()

        # Update model — try top camera first, then bottom for close range.
        ball = self._get_ball_and_update_model()

        # When ball is getting close (bsz indicates nearness), also check
        # bottom camera to prevent blind-spot loss.
        if ball is None and self.ball_model.bsz > 0.06:
            bot_ball = self._detect_bottom_cam()
            if bot_ball is not None:
                bx_b, by_b, bsz_b = bot_ball
                try:
                    head_yaw_b = self.motion.getAngles("HeadYaw", False)[0]
                except Exception:
                    head_yaw_b = 0.0
                self.ball_model.update(bx_b, by_b, bsz_b, head_yaw_b)
                ball = bot_ball

        # If ball model is about to expire, try looking toward last heading.
        if ball is None and self.ball_model.valid:
            age = now - self.ball_model._last_update_t if self.ball_model._last_update_t else 99.0
            if age > 0.8:
                # Ball almost lost — snap head toward last known heading
                # to try to recover it before the model expires.
                recover_yaw = max(-0.8, min(0.8, -self.ball_model.last_heading))
                self.motion.setAngles("HeadYaw", recover_yaw, 0.4)
                self.motion.setAngles("HeadPitch", 0.35, 0.3)  # look down

        # Only drop to SEARCH when the model itself expires (BALL_LOSS_TIME
        # seconds of no detections).
        if not self.ball_model.valid:
            self.motion.stopMove()
            with self.lock:
                self.state = STATE_SEARCH
            return

        with self.lock:
            self.last_ball_time = now

        bx   = self.ball_model.bx
        bsz  = self.ball_model.bsz
        dist = self.ball_model.dist

        # ── Head tracking: LOCK ON to the ball ─────────────────────────
        # NAO convention: bx positive = ball LEFT of camera center.
        # HeadYaw positive = head turned LEFT.
        # So: desired_head_yaw = current_yaw + track_bx * gain
        head_yaw = 0.0
        try:
            head_yaw = self.motion.getAngles("HeadYaw", False)[0]
        except Exception:
            pass

        # Use prediction for fast-moving ball, raw bx otherwise
        ball_moving = abs(self.ball_model.vbx) > BALL_VEL_THRESH
        use_pred    = ball_moving and self.ball_model.confidence > 0.5
        track_bx    = self.ball_model.pred_bx if use_pred else bx

        # Direct head servo: move head toward the ball.
        # Larger gain = more aggressive tracking = ball stays in view.
        yaw_error = track_bx * HEAD_TRACK_GAIN  # positive bx (left) -> positive yaw
        new_head_yaw = head_yaw + yaw_error
        new_head_yaw = max(-0.8, min(0.8, new_head_yaw))
        self.motion.setAngles("HeadYaw", new_head_yaw, 0.6)

        # Pitch: ALWAYS look down. Ball is on the ground.
        # Positive HeadPitch = looking DOWN on NAO.
        if bsz > 0.10:
            pitch = 0.40 + min(0.12, (bsz - 0.10) * 3.0)  # 0.40→0.52
        elif bsz > 0.04:
            pitch = 0.20 + (bsz - 0.04) * 3.3              # 0.20→0.40
        else:
            pitch = 0.15 + bsz * 1.5                        # 0.15→0.21
        pitch = max(0.10, min(0.52, pitch))  # NEVER go below 0.10 (no sky!)
        self.motion.setAngles("HeadPitch", pitch, 0.25)

        # ── Sonar obstacle check ───────────────────────────────────────
        sl = sr = 9.0
        try:
            sl = float(self.memory.getData("Device/SubDeviceList/US/Left/Sensor/Value"))
            sr = float(self.memory.getData("Device/SubDeviceList/US/Right/Sensor/Value"))
        except Exception:
            pass
        min_sonar = min(sl, sr)

        if min_sonar <= COMBAT_DISTANCE and min_sonar < bsz * 5.0 + 0.1:
            self.motion.stopMove()
            with self.lock:
                self.state = STATE_TACKLE
            return

        # ── State transitions ──────────────────────────────────────────
        # Switch to ORBIT instead of ALIGN when getting close (bsz ~0.5m away)
        if bsz >= KICK_BSZ_READY * 0.5:
            self.motion.stopMove()
            with self.lock:
                self.state = STATE_ORBIT
            return

        # ── Body movement: P-Controller & Look-Then-Walk ───
        # The ball's world-relative bearing is approximately:
        #   ball_bearing ≈ head_yaw + bx  (positive = ball to the left)
        ball_bearing = head_yaw + track_bx
        
        # P-Controller for Body Turn Velocity (Theta)
        # Apply strict proportional control relative to rotational error
        Kp_turn = 1.0
        body_turn = max(-0.6, min(0.6, ball_bearing * Kp_turn))
        
        # Look-Then-Walk paradigm:
        # Prevent straight forward walking if the head hasn't fully centered the ball.
        # This keeps the robot planted until the visual angle error reaches < 0.25 rad.
        # Widen the deadband from 0.1 to 0.25 so it doesn't stutter-step.
        if abs(head_yaw) > 0.25:
            speed = 0.0  # Stand still and continue rotating (P-Controller doing the work)
        else:
            # When aligned, limit turn adjustments so it actually walks in a straight line
            # instead of constantly curving and slowing down.
            body_turn = max(-0.2, min(0.2, ball_bearing * 0.5))
            
            # Advancing to ball: increase speed multiplier so it doesn't walk too slow
            speed = max(0.40,  # Minimum walk speed (was too slow before)
                        min(APPROACH_MAX_SPEED, (dist - KICK_APPROACH_DIST) * 1.5))

        self._stable_walk(speed, 0.0, body_turn)

        self._stable_walk(speed, 0.0, body_turn)

    # ─── ORBIT_TO_POSITION ───────────────────
    def _do_orbit(self):
        """Orbital Pathing / Get Behind Ball.
        
        Uses vector math to find an 'Approach Point' (P) exactly 0.3m behind the ball
        (opposite the goal). Strafe/arc to this point before transitioning to ALIGN.
        """
        self._update_local_map()
        self.leds.fadeRGB("AllLeds", 0x8A2BE2, 0.15)  # purple = orbit

        now = time.time()
        ball = self._detect_bottom_cam()
        if ball is None:
            ball = self._read_ball()
            
        if ball is not None:
            bx, by, bsz = ball
            try:
                head_yaw = self.motion.getAngles("HeadYaw", False)[0]
            except Exception:
                head_yaw = 0.0
            self.ball_model.update(bx, by, bsz, head_yaw)

        if not self.ball_model.valid:
            self.motion.stopMove()
            with self.lock:
                self.state = STATE_SEARCH
            return

        with self.lock:
            self.last_ball_time = now

        bx   = self.ball_model.bx
        bsz  = self.ball_model.bsz
        dist = self.ball_model.dist  # meters from robot to ball
        
        # Sonar check
        sl = sr = 9.0
        try:
            sl = float(self.memory.getData("Device/SubDeviceList/US/Left/Sensor/Value"))
            sr = float(self.memory.getData("Device/SubDeviceList/US/Right/Sensor/Value"))
        except Exception:
            pass
        if min(sl, sr) <= COMBAT_DISTANCE:
            self.motion.stopMove()
            with self.lock:
                self.state = STATE_TACKLE
            return

        # Keep head locked on ball
        head_yaw = 0.0
        try:
            head_yaw = self.motion.getAngles("HeadYaw", False)[0]
        except Exception:
            pass
            
        new_head_yaw = max(-0.8, min(0.8, head_yaw + bx * HEAD_TRACK_GAIN))
        self.motion.setAngles("HeadYaw", new_head_yaw, 0.6)
        
        # Pitch down
        if bsz > 0.10: pitch = 0.40 + min(0.12, (bsz - 0.10) * 3.0)
        else: pitch = 0.35
        self.motion.setAngles("HeadPitch", pitch, 0.25)

        # ── Vector Math for Approach Point (P) ──────────────────────
        goal_bearing = getattr(self, 'goal_bearing', DEFAULT_GOAL_BEARING)
        robot_heading = 0.0
        try:
            p = self.motion.getRobotPosition(True)
            robot_heading = p[2]
        except Exception:
            pass

        yaw_to_goal = goal_bearing - robot_heading
        while yaw_to_goal >  math.pi: yaw_to_goal -= 2.0 * math.pi
        while yaw_to_goal < -math.pi: yaw_to_goal += 2.0 * math.pi
        
        yaw_to_ball = head_yaw + bx
        
        # Target Offset
        OFFSET_DIST = 0.30  # meters behind ball
        
        # We need to reach a point P that is OFFSET_DIST behind the ball on the goal-ball line.
        # In robot Cartesian space (x=forward, y=left):
        # Ball pos:
        ball_x = dist * math.cos(yaw_to_ball)
        ball_y = dist * math.sin(yaw_to_ball)
        
        # Goal vector from ball (approximated conceptually by yaw_to_goal vs yaw_to_ball)
        # Vector from ball TO robot TO goal... simpler: 
        # The vector pointing from the ball to the goal is at angle `yaw_to_goal_from_ball`.
        # For simplicity, we assume goal is very far, so `yaw_to_goal` is roughly the same everywhere locally.
        # So we want to be at: Ball - (Goal Direction * OFFSET_DIST)
        
        target_x = ball_x - (OFFSET_DIST * math.cos(yaw_to_goal))
        target_y = ball_y - (OFFSET_DIST * math.sin(yaw_to_goal))
        
        err_dist = math.hypot(target_x, target_y)
        err_angle = math.atan2(target_y, target_x)
        
        # ── Move and Transition ──────────────────────
        # "Close enough" threshold
        if err_dist < 0.10: 
            # We are at the orbital point, stop and align
            self.motion.stopMove()
            with self.lock:
                self.state = STATE_ALIGN
            return

        # Holonomic (strafe/Arc) drive
        # We want to move towards target_x, target_y while keeping our head pointing at the ball
        vx = max(-0.5, min(0.5, target_x * 2.0))
        vy = max(-0.4, min(0.4, target_y * 1.5))
        
        # Add rotation to keep body facing ball slightly, or just rely on ALIGN for body
        # Let's keep body facing ball for now
        vtheta = max(-0.4, min(0.4, yaw_to_ball * 0.8))
        
        self._stable_walk(vx, vy, vtheta)

    # ─── ALIGN ──────────────────────────────
    def _do_align(self):
        """Align the robot so that the ball is centered AND the robot faces
        toward the target (opponent goal).  This ensures the kick sends the
        ball in the right direction.

        Strategy:
        1. Keep tracking the ball with the bottom camera.
        2. Compare robot's current heading to the goal bearing.
        3. Circle-strafe around the ball to approach from behind it
           (relative to the goal direction).
        4. Once ball is centered and body faces the goal → transition to KICK.
        """
        self._update_local_map()
        self.leds.fadeRGB("AllLeds", 0x4D9FFF, 0.15)   # blue = aligning

        # Head: look down at ball near feet
        self.motion.setAngles("HeadPitch", 0.45, 0.3)
        self.motion.setAngles("HeadYaw",   0.0,  0.3)

        now = time.time()

        # In ALIGN prioritise bottom camera but still accept top-cam data.
        ball = self._detect_bottom_cam()
        if ball is None:
            ball = self._read_ball()

        if ball is not None:
            bx, by, bsz = ball
            try:
                head_yaw = self.motion.getAngles("HeadYaw", False)[0]
            except Exception:
                head_yaw = 0.0
            self.ball_model.update(bx, by, bsz, head_yaw)

        if not self.ball_model.valid:
            self.motion.stopMove()
            with self.lock:
                self.state = STATE_SEARCH
            return

        with self.lock:
            self.last_ball_time = now

        bx  = self.ball_model.bx
        bsz = self.ball_model.bsz

        # ── Sonar check ───────────────────────────────────────────────────
        sl = sr = 9.0
        try:
            sl = float(self.memory.getData("Device/SubDeviceList/US/Left/Sensor/Value"))
            sr = float(self.memory.getData("Device/SubDeviceList/US/Right/Sensor/Value"))
        except Exception:
            pass
        if min(sl, sr) <= COMBAT_DISTANCE:
            self.motion.stopMove()
            with self.lock:
                self.state = STATE_TACKLE
            return

        # ── Goal alignment ─────────────────────────────────────────────────
        # Compare robot heading to the desired kick direction.
        goal_bearing = getattr(self, 'goal_bearing', DEFAULT_GOAL_BEARING)
        robot_heading = 0.0
        try:
            p = self.motion.getRobotPosition(True)
            robot_heading = p[2]
        except Exception:
            pass

        heading_error = goal_bearing - robot_heading
        # Normalise to [-pi, pi]
        while heading_error >  math.pi: heading_error -= 2.0 * math.pi
        while heading_error < -math.pi: heading_error += 2.0 * math.pi

        ball_centered = abs(bx) < KICK_BX_MAX
        facing_goal   = abs(heading_error) < 0.25   # ~14 degrees tolerance

        # ── Kick-ready transition (HACKATHON QUICK-TRIGGER) ───────────────────
        # Don't wait to be perfectly aligned. If ball is close to feet (bsz is huge)
        # and we are roughly facing the right way (goal error < 0.3 rad ~17 deg), kick!
        if ball_centered and bsz > KICK_BSZ_READY * 0.90 and abs(heading_error) < 0.35:
            self.motion.stopMove()
            with self.lock:
                self.state = STATE_KICK
            return

        # ── Alignment corrections ──────────────────────────────────────────
        # Priority 1: Center the ball horizontally (bx → 0).
        # Priority 2: Rotate body toward the goal heading.

        if abs(bx) > ALIGN_BODY_DEADBAND:
            # Ball off-center: shuffle laterally and rotate to center it.
            lateral = bx * 0.20    # aggressive lateral shuffle (positive is left)
            turn    = bx * 0.70    # body rotation to re-centre ball (positive is left)
            self._stable_walk(0.02, lateral, turn)
        elif not facing_goal and bsz >= KICK_BSZ_READY * 0.8:
            # Ball is centered but we're not facing the goal.
            # Circle-strafe: walk sideways around the ball to change our
            # heading without losing sight of it.
            strafe_dir = 1.0 if heading_error > 0 else -1.0
            self._stable_walk(0.0, strafe_dir * 0.08, heading_error * 0.4)
        elif bsz < KICK_BSZ_READY * 0.90:
            # Well-centred but not close enough – creep forward.
            self._stable_walk(0.15, 0.0, 0.0)
        else:
            # Very close and centered – hold still.
            self.motion.stopMove()

    # ─── TACKLE ─────────────────────────────
    def _do_tackle(self):
        self.leds.fadeRGB("AllLeds", 0xFF0000, 0.15)
        try:
            self.tts.post.say("Pushing!")
            self.motion.setStiffnesses("Body", 1.0)
            self.posture.goToPosture("StandInit", 0.8)
            self.motion.setAngles(["LShoulderPitch", "RShoulderPitch"], [0.0, 0.0], 0.3)
            self.motion.setAngles(["LKneePitch",     "RKneePitch"],     [0.4, 0.4], 0.3)
            time.sleep(0.5)
            self._stable_walk(1.0, 0.0, 0.0)
            time.sleep(2.0)
            self.motion.stopMove()
            self.motion.setAngles(["LShoulderPitch", "RShoulderPitch"], [1.5, 1.5], 0.4)
            self.posture.goToPosture("StandInit", 0.8)
        except Exception:
            pass
        with self.lock:
            self.state = STATE_SEARCH

    # ─── KICK ───────────────────────────────
    def _do_kick(self):
        self.leds.fadeRGB("AllLeds", 0xFFFFFF, 0.15)   # white = kicking
        self.motion.stopMove()

        # Head: look down at ball at feet, centered.
        self.motion.setAngles("HeadYaw",   0.0,  0.3)
        self.motion.setAngles("HeadPitch", 0.52, 0.5)
        time.sleep(0.25)   # let robot settle before sampling

        # ── Kick walk-up ──────────────────────────────────────────────────
        # Walk forward slowly until the ball fills the frame.
        for _ in range(8):
            ball = self._detect_bottom_cam()
            if ball is None:
                ball_r = self._read_ball()
                if ball_r:
                    ball = ball_r
            if ball is not None:
                bx_wu, _, bsz_wu = ball
                if bsz_wu >= KICK_BSZ_READY * 1.4:
                    # Also do a final lateral correction during walk-up.
                    if abs(bx_wu) > 0.03:
                        lat = -bx_wu * 0.03
                        self.motion.moveTo(0.02, lat, 0.0)
                        time.sleep(0.2)
                    break
                self.motion.moveTo(0.05, 0.0, 0.0)
                time.sleep(0.3)
            else:
                break

        self.motion.stopMove()
        time.sleep(0.1)

        # ── Multi-sample verification ─────────────────────────────────────
        bx_samples = []
        for _ in range(KICK_VERIFY_SAMPLES):
            ball = self._detect_bottom_cam()
            if ball is None:
                ball = self._read_ball()
            if ball is not None:
                bx_samples.append(ball[0])
            time.sleep(KICK_VERIFY_INTERVAL)

        if len(bx_samples) < 2:
            with self.lock:
                self.state = STATE_ALIGN
            return

        bx_samples.sort()
        bx = bx_samples[len(bx_samples) // 2]

        # ── Select kick foot ──────────────────────────────────────────────
        side_step_y = -0.04 if bx < -0.02 else 0.04
        kick_leg    = "L"   if bx < -0.02 else "R"

        try:
            self.posture.goToPosture("Stand", 0.8)
            time.sleep(0.2)
            self.motion.moveTo(0.0, side_step_y, 0.0)
            time.sleep(0.15)

            if kick_leg == "R":
                hip = "RHipPitch"; knee = "RKneePitch"; roll = "LHipRoll"
            else:
                hip = "LHipPitch"; knee = "LKneePitch"; roll = "RHipRoll"

            # Phase 1 (0.0→0.20 s): shift weight onto support leg.
            # Phase 2 (0.20→0.40 s): wind-up (pull leg back).
            # Phase 3 (0.40→0.65 s): strike (snap forward hard).
            self.motion.angleInterpolation(
                [roll,  hip,   hip,   knee ],
                [0.18, -0.50,  0.90, -0.80],
                [0.20,  0.40,  0.65,  0.65],
                True
            )

            self.posture.goToPosture("Stand", 0.8)
            with self.lock:
                self.kick_count += 1

            # ── Check if we scored ─────────────────────────────────────
            if self._check_goal_scored():
                with self.lock:
                    self.goal_count += 1
                    self.state = STATE_CELEBRATE
            else:
                with self.lock:
                    self.state = STATE_SEARCH
        except Exception:
            try:
                self.posture.goToPosture("Stand", 0.8)
            except Exception:
                pass
            with self.lock:
                self.state = STATE_SEARCH


# ─────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────
g_brain = None


def signal_handler(signum, frame):
    global g_brain
    if g_brain:
        print("\n[BotFC] Signal caught. Stopping brain...")
        g_brain.stop()
    sys.exit(0)


def main():
    global g_brain

    parser = argparse.ArgumentParser(description="BotFC Python Brain")
    parser.add_argument("--ip",          default="127.0.0.1", help="Robot IP")
    parser.add_argument("--pip",         default=None,        help="Robot IP (alias)")
    parser.add_argument("--pport",       type=int, default=9559, help="Robot port")
    parser.add_argument("--trait",       default="balanced",  help="offense / defense / balanced")
    parser.add_argument("--server-ip",   default="127.0.0.1", help="BotFC API server IP")
    parser.add_argument("--server-port", type=int, default=5050, help="BotFC API server port")
    args = parser.parse_args()

    robot_ip   = args.pip if args.pip else args.ip
    robot_port = args.pport

    signal.signal(signal.SIGINT,  signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("=" * 50)
    print("  Bot FC – Python Brain (Enhanced Edition)")
    print("  Robot:  {}:{}".format(robot_ip, robot_port))
    print("  Trait:  {}".format(args.trait))
    print("  Server: {}:{}".format(args.server_ip, args.server_port))
    print("=" * 50)

    brain = BotFCBrain(robot_ip, robot_port, args.trait,
                       args.server_ip, args.server_port)
    g_brain = brain
    brain.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        brain.stop()


if __name__ == "__main__":
    main()
