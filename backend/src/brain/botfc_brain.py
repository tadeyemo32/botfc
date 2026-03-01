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
HEAD_TRACK_GAIN       = 0.50   # head servo gain (rad / bx unit)
BODY_FOLLOW_THRESHOLD = 0.18   # |head_yaw| above which we rotate before walking
ALIGN_BODY_DEADBAND   = 0.07   # bx dead-zone in ALIGN to kill oscillation

# ─────────────────────────────────────────────
# Kick constants
# ─────────────────────────────────────────────
KICK_VERIFY_SAMPLES  = 4
KICK_VERIFY_INTERVAL = 0.05   # s between samples
KICK_BSZ_READY       = 0.10   # ball must appear at least this large to kick
KICK_BX_MAX          = 0.05   # horizontal tolerance before kicking
KICK_APPROACH_DIST   = 0.22   # target distance (m) for kick walk-up

# ─────────────────────────────────────────────
# Bottom camera (BGR, QVGA=320×240, 10 fps)
# Subscribe as a separate NAOqi client so we can run it alongside the
# top-camera ALRedBallDetection subscription.
# ─────────────────────────────────────────────
BOT_CAM_ID      = 1    # bottom camera
BOT_CAM_RES     = 1    # kQVGA  (320 × 240)
BOT_CAM_FORMAT  = 13   # kBGR
BOT_CAM_FPS     = 10
BOT_CAM_STRIDE  = 4    # check every Nth pixel (speed vs accuracy)

# Red-ball thresholds in BGR colour space.
# Tune RED_R_MIN downward if the robot misses the ball under warm/dim lighting.
RED_R_MIN      = 135
RED_B_MAX      = 90
RED_G_MAX      = 110
RED_DIFF_MIN   = 55    # red must exceed max(B,G) by this margin
RED_MIN_PX     = 40    # minimum blob pixel count

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

STATE_INIT     = "INIT"
STATE_STANDBY  = "STANDBY"
STATE_SEARCH   = "SEARCH"
STATE_APPROACH = "APPROACH"
STATE_ALIGN    = "ALIGN"
STATE_TACKLE   = "TACKLE"
STATE_KICK     = "KICK"
STATE_RECOVER  = "RECOVER"
STATE_HALFTIME = "HALFTIME"


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
        mask = os.urandom(4)
        frame.extend(mask)
        masked = bytearray(b ^ mask[i % 4] for i, b in enumerate(data))
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
class MLDataLogger(object):
    def __init__(self, robot_ip, robot_port):
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        self.frame_index = 0
        self.out_dir = "/home/nao/ml_data/"
        self.video_client_name = ""
        self.video_device = None
        self.current_telemetry = {}

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

    # CSV columns for ML training
    CSV_HEADER = (
        "timestamp,state,ball_valid,ball_bx,ball_by,ball_bsz,ball_dist,"
        "ball_vx,ball_vy,ball_pred_bx,ball_pred_by,ball_confidence,"
        "head_yaw,inertial_roll,inertial_pitch,kicks,battery_pct\n"
    )

    def update_telemetry(self, snapshot):
        with self.lock:
            self.current_telemetry = snapshot.copy()

    def _ensure_csv_header(self):
        csv_path = self.out_dir + "game_log.csv"
        try:
            import os as _os
            if not _os.path.exists(csv_path):
                with open(csv_path, "w") as f:
                    f.write(MLDataLogger.CSV_HEADER)
        except Exception:
            pass
        return csv_path

    def log_game_state(self, t):
        """Append one structured row to the game_log CSV for ML training.

        t must be a dict from TelemetryClient.current_data (already includes
        ball_bx, ball_vx, etc.).  Called from BotFCBrain._run() each cycle.
        """
        try:
            csv_path = self.out_dir + "game_log.csv"
            row = "{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{}\n".format(
                time.time(),
                t.get("state",            "UNKNOWN"),
                1 if t.get("ball_valid")  else 0,
                t.get("ball_bx",          0.0),
                t.get("ball_by",          0.0),
                t.get("ball_bsz",         0.0),
                t.get("ball_dist",        9.9),
                t.get("ball_vx",          0.0),
                t.get("ball_vy",          0.0),
                t.get("ball_pred_bx",     0.0),
                t.get("ball_pred_by",     0.0),
                t.get("ball_confidence",  0.0),
                t.get("head_yaw",         0.0),
                t.get("inertial_roll",    0.0),
                t.get("inertial_pitch",   0.0),
                t.get("kicks",            0),
                t.get("battery_pct",      -1),
            )
            with open(csv_path, "a") as f:
                f.write(row)
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
    """Grabs JPEG frames from NAOqi and streams them to the C++ server via
    WebSocket /api/ws/bot_camera.  The server stores the latest frame and
    relays it to browser clients on /api/ws/camera_feed.

    Uses kJpegColorSpace=21 so no re-encoding is needed on the robot.
    """

    def __init__(self, robot_ip, robot_port, server_host, server_port):
        self.robot_ip    = robot_ip
        self.robot_port  = robot_port
        self.server_host = server_host
        self.server_port = server_port
        self.running     = False
        self.thread      = None
        self._vid        = None
        self._cam_client = ""

    def start(self):
        if self.running:
            return
        try:
            self._vid = ALProxy("ALVideoDevice", self.robot_ip, self.robot_port)
            self._cam_client = self._vid.subscribeCamera(
                "BotFC_Stream", STREAM_CAM_ID, STREAM_CAM_RES,
                STREAM_CAM_FORMAT, STREAM_CAM_FPS)
        except Exception as e:
            print("[CamStream] Camera init failed: {}".format(e))
            return
        self.running = True
        self.thread  = threading.Thread(target=self._loop)
        self.thread.daemon = True
        self.thread.start()
        print("[CamStream] Started JPEG stream to {}:{}.".format(
            self.server_host, self.server_port))

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        if self._vid and self._cam_client:
            try:
                self._vid.unsubscribe(self._cam_client)
            except Exception:
                pass

    def _loop(self):
        import socket as _socket
        import base64 as _b64

        while self.running:
            sock = None
            try:
                sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect((self.server_host, self.server_port))

                ws_key = _b64.b64encode(os.urandom(16))
                handshake = (
                    "GET /api/ws/bot_camera HTTP/1.1\r\n"
                    "Host: {host}:{port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Key: {key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    "User-Agent: BotFC-CamStream-v1\r\n"
                    "\r\n"
                ).format(host=self.server_host, port=self.server_port, key=ws_key)
                sock.sendall(handshake.encode("utf-8"))

                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise Exception("Handshake failed")
                    resp += chunk

                if b"101" not in resp.split(b"\r\n")[0]:
                    raise Exception("WS upgrade rejected")

                print("[CamStream] Connected.")

                interval = 1.0 / STREAM_CAM_FPS
                while self.running:
                    img = self._vid.getImageRemote(self._cam_client)
                    if img and len(img) > 6:
                        w   = int(img[0])
                        h   = int(img[1])
                        jpg = bytes(bytearray(img[6]))
                        b64 = _b64.b64encode(jpg).decode("ascii")
                        payload = json.dumps({"type": "frame", "w": w, "h": h, "jpg": b64})
                        TelemetryClient._ws_send(sock, payload)
                        self._vid.releaseImage(self._cam_client)
                    time.sleep(interval)

                TelemetryClient._ws_close(sock)
            except Exception as e:
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
        self.field_map         = {}
        self.fsm_thread        = None

        # Ball perception
        self._last_ball_ts     = None     # NAOqi timestamp guard (stale detection)
        self.ball_model        = BallModel()

        # Bottom camera
        self._vid              = None     # ALVideoDevice proxy
        self._bot_cam_client   = ""       # subscription name

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

    # ─── Posture / fall helpers ──────────────
    def _read_inertial(self):
        """Return (roll_rad, pitch_rad) from the inertial sensor unit.

        AngleX ≈ roll (lean left/right), AngleY ≈ pitch (lean forward/back).
        Returns (0, 0) if the sensor is unavailable.
        """
        try:
            roll  = float(self.memory.getData(
                "Device/SubDeviceList/InertialSensor/AngleX/Sensor/Value"))
            pitch = float(self.memory.getData(
                "Device/SubDeviceList/InertialSensor/AngleY/Sensor/Value"))
            return (roll, pitch)
        except Exception:
            return (0.0, 0.0)

    def _is_fallen(self):
        """Return True if the inertial sensor shows the robot is no longer upright.

        Thresholds (radians):
          |roll|  > 0.55 rad (~31°) → sideways fall
          |pitch| > 0.80 rad (~46°) → forward/backward fall
        These are conservative: even a strong lean will trigger recovery before
        the robot has fully toppled.
        """
        roll, pitch = self._read_inertial()
        return abs(roll) > 0.55 or abs(pitch) > 0.80

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
            self.leds     = ALProxy("ALLeds",           self.robot_ip, self.robot_port)
            self.ball_det = ALProxy("ALRedBallDetection", self.robot_ip, self.robot_port)
            self.sonar_p  = ALProxy("ALSonar",          self.robot_ip, self.robot_port)
        except Exception as e:
            print("[BotFC] FATAL: Failed to init proxies: {}".format(e))
            return

        try:
            self.tts = ALProxy("ALTextToSpeech", self.robot_ip, self.robot_port)
        except Exception as e:
            print("[BotFC] ALTextToSpeech unavailable (muted mode): {}".format(e))

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

        # Bottom camera – separate subscription so we can query it in align/kick
        # even when ALRedBallDetection is using the top camera.
        try:
            self._vid = ALProxy("ALVideoDevice", self.robot_ip, self.robot_port)
            self._bot_cam_client = self._vid.subscribeCamera(
                "BotFC_BottomDetect", BOT_CAM_ID, BOT_CAM_RES, BOT_CAM_FORMAT, BOT_CAM_FPS)
            print("[BotFC] Bottom camera subscribed.")
        except Exception as e:
            print("[BotFC] Bottom camera unavailable: {}".format(e))

        if self.tts:
            self.tts.post.say("Brain online. Let's play football.")

        try:
            p = self.motion.getRobotPosition(True)
            self.origin_x, self.origin_y, self.origin_theta = p[0], p[1], p[2]
        except Exception:
            pass

        self.last_ball_time = time.time()  # start grace period from NOW, not -100s

        with self.lock:
            self.state = STATE_STANDBY

        self.running = True
        # data_logger subscribes camera 0 with kRGB (format 9) which conflicts
        # with CameraStreamer (kJpeg, format 21) on the same physical camera,
        # causing empty frames.  Keep data_logger stopped; telemetry is still
        # updated in-memory and logged via update_telemetry / log_game_state.
        # self.data_logger.start()
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

        # Unsubscribe bottom camera
        if self._vid and self._bot_cam_client:
            try:
                self._vid.unsubscribe(self._bot_cam_client)
            except Exception:
                pass

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
            # The C++ server (or frontend operator) can push commands via
            # POST /api/command.  We honour them here, before the kill switch,
            # so the operator always has full authority.
            srv_cmd = self.command_poller.get_and_clear()
            if srv_cmd:
                if srv_cmd == "STOP":
                    self.motion.stopMove()
                    with self.lock:
                        self.state = STATE_STANDBY
                elif srv_cmd in (STATE_SEARCH, STATE_APPROACH, STATE_ALIGN, STATE_KICK):
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
                # Wait until touch is released before resuming
                while self.running and self._check_kill_switch():
                    time.sleep(0.2)
                if self.running:
                    self._ensure_standing()
                    with self.lock:
                        self.state = STATE_STANDBY
                continue

            # ── Fall recovery (mid-match) ──────────────────────────────────
            if self._is_fallen():
                self.motion.stopMove()
                self.ball_model.valid = False   # invalidate stale ball data
                try:
                    self.leds.fadeRGB("AllLeds", 0xFF6600, 0.1)
                except Exception:
                    pass
                print("[BotFC] Fall detected – attempting recovery.")
                self._ensure_standing(max_attempts=3)
                try:
                    self.leds.fadeRGB("AllLeds", 0x00FF00, 0.2)
                except Exception:
                    pass
                with self.lock:
                    self.state = STATE_STANDBY
                continue

            self._safety_check()

            # Tick the ball model: expire data older than BALL_LOSS_TIME
            self.ball_model.tick()

            with self.lock:
                s  = self.state
                k  = self.kick_count
                br = self.break_remaining
                lbt= self.last_ball_time

            ball_age = self.ball_model.age()

            # Battery
            bat = -1
            try:
                if self.battery:
                    bat = int(self.battery.getBatteryCharge())
            except Exception:
                pass

            # Head yaw & inertial sensors for telemetry
            h_yaw = 0.0
            try:
                h_yaw = self.motion.getAngles("HeadYaw", False)[0]
            except Exception:
                pass
            roll, pitch = self._read_inertial()

            bm = self.ball_model
            self.telemetry_client.update(
                state=s, kicks=k,
                ball_age=round(ball_age, 2),
                break_remaining=br,
                battery_pct=bat,
                # Ball state
                ball_valid=bm.valid,
                ball_bx=round(bm.bx,  3), ball_by=round(bm.by,  3),
                ball_bsz=round(bm.bsz, 4),
                ball_dist=round(bm.dist, 2),
                ball_vx=round(bm.vbx, 3),  ball_vy=round(bm.vby, 3),
                ball_pred_bx=round(bm.pred_bx, 3),
                ball_pred_by=round(bm.pred_by, 3),
                ball_confidence=round(bm.confidence, 2),
                # Robot pose
                head_yaw=round(h_yaw, 3),
                inertial_roll=round(roll,  3),
                inertial_pitch=round(pitch, 3),
            )

            # Log game state for ML training (every cycle = ~20 Hz)
            with self.telemetry_client.lock:
                telem_snap = self.telemetry_client.current_data.copy()
            self.data_logger.update_telemetry(telem_snap)
            self.data_logger.log_game_state(telem_snap)

            if s != STATE_HALFTIME:
                self._enforce_bounds()

                if   s == STATE_STANDBY:  pass   # wait for SEARCH command from operator
                elif s == STATE_SEARCH:   self._do_search()
                elif s == STATE_APPROACH: self._do_approach()
                elif s == STATE_ALIGN:    self._do_align()
                elif s == STATE_KICK:     self._do_kick()
                elif s == STATE_TACKLE:   self._do_tackle()
                else:
                    with self.lock:
                        self.state = STATE_STANDBY

            time.sleep(0.05)

    # ─── Ball perception helpers ─────────────

    def _read_ball(self):
        """Return (bx, by, bsz) from ALRedBallDetection if the timestamp is NEW.

        NAOqi never clears redBallDetected when the ball leaves view – it just
        stops advancing the timestamp.  Comparing consecutive timestamps is the
        only reliable way to distinguish a live detection from stale cache.
        """
        try:
            data = self.memory.getData("redBallDetected")
            if not (data and len(data) >= 2):
                return None
            ts = (int(data[0][0]), int(data[0][1]))
            if ts == self._last_ball_ts:
                return None             # unchanged timestamp → stale
            self._last_ball_ts = ts     # new timestamp → live detection
            info = data[1]
            return (float(info[0]), float(info[1]), float(info[2]))
        except Exception:
            return None

    def _detect_bottom_cam(self):
        """Detect red ball in the bottom camera using BGR thresholding.

        Sampling every BOT_CAM_STRIDE pixels keeps CPU usage low on the NAO's
        ARM core while still giving a reliable centroid for close-range use.
        Returns (bx, by, bsz) normalised to the same convention as _read_ball(),
        or None if no blob found.
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

            bx_sum = by_sum = count = 0
            stride = BOT_CAM_STRIDE * 3   # bytes to skip per sampled pixel

            for off in range(0, len(pixels) - 2, stride):
                b = pixels[off]
                g = pixels[off + 1]
                r = pixels[off + 2]
                if (r > RED_R_MIN and b < RED_B_MAX and g < RED_G_MAX
                        and r - max(b, g) > RED_DIFF_MIN):
                    px = (off // 3) % width
                    py = (off // 3) // width
                    bx_sum += px
                    by_sum += py
                    count  += 1

            self._vid.releaseImage(self._bot_cam_client)

            if count < RED_MIN_PX:
                return None

            # Normalise: centre of frame = 0, half-width = ±0.5
            cx = (float(bx_sum) / count - width  * 0.5) / width
            cy = (float(by_sum) / count - height * 0.5) / height
            sz = float(count) / (width * height)
            return (cx, cy, sz)
        except Exception:
            return None

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

        if self.tts:
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
            if r % 30 == 0 and self.tts:
                self.tts.post.say("{} minutes remaining.".format(r // 60))
            time.sleep(1)

        with self.lock:
            self.break_remaining = 0

        if self.running:
            if self.tts:
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
            # Freeze head at its current yaw so the next frame is taken with
            # a stationary camera (eliminates motion-blur ghost detections).
            try:
                cy = self.motion.getAngles("HeadYaw", False)[0]
                self.motion.setAngles("HeadYaw", cy, 0.3)
            except Exception:
                pass
            time.sleep(0.08)

            # Re-verify: a second fresh detection within 80 ms confirms the
            # ball is truly visible (not a single-frame noise spike).
            ball2 = self._get_ball_and_update_model()
            if ball2 is not None:
                self.motion.stopMove()
                with self.lock:
                    self.last_ball_time = time.time()
                    self.state = STATE_APPROACH
                self.motion.setAngles("HeadPitch", 0.15, 0.2)
                if self.tts:
                    self.tts.post.say("Ball found!")
                return
            # Single-frame ghost: fall through and keep sweeping.

        # ── Sweep with heading memory ──────────────────────────────────────
        # On the first sweep cycle after losing the ball, seed the head yaw
        # to the last known ball heading so we search there first.
        if (self.search_yaw == 0.0 and self.search_yaw_dir == 1.0
                and self.ball_model.last_heading != 0.0):
            seeded = max(-1.0, min(1.0, -self.ball_model.last_heading))
            self.search_yaw = seeded

        # 0.06 rad/step at 50 ms/cycle ≈ 1.2 rad/s – slow enough for the
        # camera to fire a detection event as the ball passes through the FOV.
        self.search_yaw += self.search_yaw_dir * 0.06
        if self.search_yaw >= 1.0:
            self.search_yaw    = 1.0
            self.search_yaw_dir = -1.0
        elif self.search_yaw <= -1.0:
            self.search_yaw    = -1.0
            self.search_yaw_dir = 1.0

        self.motion.setAngles("HeadYaw",   self.search_yaw, 0.15)
        # Pitch down enough to see the ball on the floor (~14° = 0.25 rad).
        self.motion.setAngles("HeadPitch", 0.25, 0.15)

        with self.lock:
            ltime = self.last_ball_time
        if time.time() - ltime > 15.0:
            # Been searching a long time – rotate body slowly to cover ground.
            self.motion.moveToward(0.0, 0.0, 0.2)
        else:
            self.motion.stopMove()

    # ─── APPROACH ───────────────────────────
    def _do_approach(self):
        self._update_local_map()
        self.leds.fadeRGB("AllLeds", 0x00FF00, 0.15)
        self.motion.setAngles("HeadPitch", 0.25, 0.3)

        now = time.time()
        if now - self.last_man_on_time > 4.0:
            if self.tts:
                self.tts.post.say("Man on, man on")
            self.last_man_on_time = now

        # Update model if a fresh reading exists.
        self._get_ball_and_update_model()

        # KEY: only drop to SEARCH when the model itself expires (BALL_LOSS_TIME
        # seconds of no detections).  A single missed camera frame is NOT enough
        # to abort the approach – that's what caused the jittery search loops.
        if not self.ball_model.valid:
            with self.lock:
                self.state = STATE_SEARCH
            return

        with self.lock:
            self.last_ball_time = now

        bx  = self.ball_model.bx
        bsz = self.ball_model.bsz
        dist = self.ball_model.dist

        # ── Head tracking with ball-movement prediction ─────────────────────
        # When confidence is high and the ball is moving fast, servo the head
        # toward the PREDICTED position (BALL_PRED_HORIZON s ahead) instead of
        # the current position.  This keeps the ball in frame even when it is
        # rolling across the camera FOV.  At low confidence (just found ball)
        # track the measured position to avoid overshooting.
        head_yaw = 0.0
        try:
            head_yaw = self.motion.getAngles("HeadYaw", False)[0]
            ball_moving = abs(self.ball_model.vbx) > BALL_VEL_THRESH
            use_pred    = ball_moving and self.ball_model.confidence > 0.5
            track_bx    = self.ball_model.pred_bx if use_pred else bx
            # NAO: positive HeadYaw = head left. bx > 0 = ball right of frame.
            target_yaw = max(-1.0, min(1.0, head_yaw - track_bx * HEAD_TRACK_GAIN))
            self.motion.setAngles("HeadYaw", target_yaw, 0.4)
            head_yaw = target_yaw
        except Exception:
            pass

        # ── Sonar obstacle check ───────────────────────────────────────────
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

        # ── State transitions ──────────────────────────────────────────────
        if bsz >= KICK_BSZ_READY:
            # Ball is large and (approximately) centred – go straight to ALIGN.
            self.motion.stopMove()
            self.motion.setAngles("HeadYaw", 0.0, 0.2)
            with self.lock:
                self.state = STATE_ALIGN
            return

        # ── Body motion ────────────────────────────────────────────────────
        # If the head had to turn significantly to find the ball, rotate the
        # body first so the head re-centres.  This eliminates the odometry
        # drift that caused the robot to walk past the ball.
        if abs(head_yaw) > BODY_FOLLOW_THRESHOLD:
            body_turn = max(-0.5, min(0.5, head_yaw * 1.5))
            self.motion.moveToward(0.1, 0.0, body_turn)
        else:
            # Walking straight – nudge head back to centre so head and body
            # remain aligned during forward motion.  Low speed (0.10) avoids
            # jerky correction; the ball tracker will re-acquire the offset.
            self.motion.setAngles("HeadYaw", 0.0, 0.10)
            # Scale walk speed by estimated distance: fast when far, slow when close.
            speed = max(0.2, min(0.7, (dist - KICK_APPROACH_DIST) * 0.6))
            self.motion.moveToward(speed, 0.0, 0.0)

    # ─── ALIGN ──────────────────────────────
    def _do_align(self):
        self._update_local_map()
        self.leds.fadeRGB("AllLeds", 0x00FF00, 0.15)

        # Raise head pitch so ball falls inside the bottom camera FOV –
        # this eliminates the blind spot between the two cameras.
        self.motion.setAngles("HeadPitch", 0.45, 0.3)

        now = time.time()
        if now - self.last_man_on_time > 4.0:
            if self.tts:
                self.tts.post.say("Man on, man on")
            self.last_man_on_time = now

        # In ALIGN we prioritise the bottom camera but still accept top-cam data.
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

        # ── Kick-ready transition ─────────────────────────────────────────
        if abs(bx) < KICK_BX_MAX and bsz > KICK_BSZ_READY:
            self.motion.stopMove()
            if self.tts:
                self.tts.post.say("I see the goal")
            with self.lock:
                self.state = STATE_KICK
            return

        # ── Alignment corrections (with dead-band to kill oscillation) ────
        if abs(bx) > ALIGN_BODY_DEADBAND:
            lateral = -bx * 0.10    # gentle lateral shuffle
            turn    = -bx * 0.65    # body rotation to re-centre
            self.motion.moveToward(0.08, lateral, turn)
        elif bsz < 0.15:
            # Well-centred but not yet close enough – creep forward.
            self.motion.moveToward(0.10, 0.0, 0.0)
        else:
            # Very close and centred – hold still, let kick transition fire.
            self.motion.stopMove()

    # ─── TACKLE ─────────────────────────────
    def _do_tackle(self):
        self.leds.fadeRGB("AllLeds", 0xFF0000, 0.15)
        try:
            if self.tts:
                self.tts.post.say("Pushing!")
            self.motion.setStiffnesses("Body", 1.0)
            self.posture.goToPosture("StandInit", 0.8)
            self.motion.setAngles(["LShoulderPitch", "RShoulderPitch"], [0.0, 0.0], 0.3)
            self.motion.setAngles(["LKneePitch",     "RKneePitch"],     [0.4, 0.4], 0.3)
            time.sleep(0.5)
            self.motion.moveToward(1.0, 0.0, 0.0)
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
        self.leds.fadeRGB("AllLeds", 0x0000FF, 0.15)
        self.motion.stopMove()

        # Raise head pitch to use the bottom camera's FOV, eliminating the
        # blind spot between the two cameras when the ball is at the feet.
        self.motion.setAngles("HeadYaw",   0.0,  0.3)
        self.motion.setAngles("HeadPitch", 0.52, 0.5)
        time.sleep(0.25)   # let robot settle before sampling

        # ── Kick walk-up ──────────────────────────────────────────────────
        # If the ball appears smaller than expected (robot stopped a bit far),
        # walk forward slowly until the ball fills the frame.  This mimics
        # the B-Human "walk-up to ball" before an in-walk kick.
        for _ in range(8):
            ball = self._detect_bottom_cam()
            if ball is None:
                ball_r = self._read_ball()
                if ball_r:
                    ball = ball_r
            if ball is not None:
                _, _, bsz = ball
                if bsz >= KICK_BSZ_READY * 1.4:
                    break          # close enough to kick
                self.motion.moveTo(0.05, 0.0, 0.0)   # 5 cm step forward
                time.sleep(0.3)
            else:
                break

        self.motion.stopMove()
        time.sleep(0.1)

        # ── Multi-sample verification ─────────────────────────────────────
        # Collect KICK_VERIFY_SAMPLES fresh readings.  Because _read_ball()
        # advances _last_ball_ts, each sample must have a new NAOqi timestamp –
        # stale cache is never counted.  Bottom-cam samples are also accepted.
        bx_samples = []
        for _ in range(KICK_VERIFY_SAMPLES):
            ball = self._detect_bottom_cam()
            if ball is None:
                ball = self._read_ball()
            if ball is not None:
                bx_samples.append(ball[0])
            time.sleep(KICK_VERIFY_INTERVAL)

        if len(bx_samples) < 2:
            # Ball not reliably in view – go back to ALIGN for another attempt.
            with self.lock:
                self.state = STATE_ALIGN
            return

        # Median of samples to reject single-frame noise spikes.
        bx_samples.sort()
        bx = bx_samples[len(bx_samples) // 2]

        # ── Select kick foot ──────────────────────────────────────────────
        # bx < 0: ball is left of centre → kick with left foot (L).
        # bx > 0: ball is right → kick with right foot (R).
        side_step_y = -0.04 if bx < -0.02 else 0.04
        kick_leg    = "L"   if bx < -0.02 else "R"

        if self.tts:
            self.tts.post.say("Kick!")

        try:
            self.posture.goToPosture("Stand", 0.8)
            time.sleep(0.2)
            # Step laterally to plant the support foot cleanly.
            self.motion.moveTo(0.0, side_step_y, 0.0)
            time.sleep(0.15)

            if kick_leg == "R":
                hip = "RHipPitch"; knee = "RKneePitch"; roll = "LHipRoll"
            else:
                hip = "LHipPitch"; knee = "LKneePitch"; roll = "RHipRoll"

            # angleInterpolation gives precise absolute timing (seconds) rather
            # than a speed fraction – the same technique B-Human uses for kicks.
            # Times are cumulative seconds from "now".
            # Phase 1 (0.0→0.25 s): shift weight onto support leg.
            # Phase 2 (0.25→0.45 s): wind-up (pull knee back).
            # Phase 3 (0.45→0.75 s): strike (snap forward + extend knee).
            self.motion.angleInterpolation(
                [roll,  hip,   hip,   knee ],
                [0.15, -0.45,  0.85, -0.75],
                [0.25,  0.45,  0.75,  0.75],
                True   # isAbsolute
            )

            self.posture.goToPosture("Stand", 0.8)
            with self.lock:
                self.kick_count += 1
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
