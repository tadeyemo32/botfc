#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
BotFC Brain v2 — Smart Football AI with HEAVY LOGGING
Runs on the NAO robot (Python 2.7 + NAOqi SDK).

Every sensor read, state transition, and decision is logged
to both stdout and /home/nao/botfc_brain.log.
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
import socket
import logging
import traceback

# ═══════════════════════════════════════════════
# LOGGING SETUP — writes to file AND stdout
# ═══════════════════════════════════════════════
LOG_FILE = "/home/nao/botfc_brain.log"
STATS_FILE = "/home/nao/botfc_stats.json"
logger = logging.getLogger("BotFC")
logger.setLevel(logging.DEBUG)
_fmt = logging.Formatter(
    "[%(asctime)s] %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S")
_fh = logging.FileHandler(LOG_FILE, mode="w")
_fh.setFormatter(_fmt)
_fh.setLevel(logging.DEBUG)
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
_sh.setLevel(logging.INFO)
logger.addHandler(_fh)
logger.addHandler(_sh)

from naoqi import ALProxy

# ═══════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════
MAX_FIELD_RADIUS = 3.0
MOTOR_TEMP_LIMIT = 60.0
PI = math.pi
TWO_PI = 2.0 * PI

# Ball detection memory key format:
# [timestamp, [centerX_rad, centerY_rad, sizeX_rad, sizeY_rad],
#  cam_pose_torso, cam_pose_robot, camera_id]
BALL_MEM_KEY = "redBallDetected"

# Sonar memory keys
SONAR_L_KEY = "Device/SubDeviceList/US/Left/Sensor/Value"
SONAR_R_KEY = "Device/SubDeviceList/US/Right/Sensor/Value"

# Head limits (radians)
HEAD_YAW_MIN = -1.3
HEAD_YAW_MAX = 1.3
HEAD_PITCH_SEARCH = 0.35   # look at ground when searching
HEAD_PITCH_APPROACH = 0.30  # look slightly down when chasing ball
HEAD_PITCH_CLOSE = 0.50    # look steeply down when ball is close

# States
S_INIT = "INIT"
S_SEARCH = "SEARCH"
S_PURSUE = "PURSUE"
S_DRIBBLE = "DRIBBLE"
S_ALIGN = "ALIGN"
S_SHOOT = "SHOOT"
S_DEFEND = "DEFEND"
S_INTERCEPT = "INTERCEPT"
S_TACKLE = "TACKLE"
S_RECOVER = "RECOVER"
S_HALFTIME = "HALFTIME"

TRAIT_TO_ROLE = {"offense": "STRIKER", "defense": "DEFENDER",
                 "balanced": "BALANCED"}


# ═══════════════════════════════════════════════
# STATS TRACKER — running counters
# ═══════════════════════════════════════════════
class Stats(object):
    def __init__(self):
        self.frames_captured = 0
        self.frames_sent_to_host = 0
        self.ball_detections = 0
        self.ball_losses = 0
        self.state_transitions = 0
        self.kicks_attempted = 0
        self.kicks_completed = 0
        self.dribble_touches = 0
        self.tackles = 0
        self.overheat_breaks = 0
        self.host_vision_hits = 0
        self.host_vision_misses = 0
        self.sonar_obstacles = 0
        self.out_of_bounds = 0
        self.start_time = time.time()

    def to_dict(self):
        uptime = time.time() - self.start_time
        return {
            "uptime_sec": int(uptime),
            "frames_captured": self.frames_captured,
            "frames_to_host": self.frames_sent_to_host,
            "ball_detections": self.ball_detections,
            "ball_losses": self.ball_losses,
            "detection_rate": round(
                self.ball_detections /
                max(1, self.ball_detections + self.ball_losses), 3),
            "state_transitions": self.state_transitions,
            "kicks_attempted": self.kicks_attempted,
            "kicks_completed": self.kicks_completed,
            "dribble_touches": self.dribble_touches,
            "tackles": self.tackles,
            "overheat_breaks": self.overheat_breaks,
            "host_vision_hits": self.host_vision_hits,
            "host_vision_misses": self.host_vision_misses,
            "sonar_obstacles": self.sonar_obstacles,
            "out_of_bounds": self.out_of_bounds,
        }

    def save(self):
        try:
            with open(STATS_FILE, "w") as f:
                json.dump(self.to_dict(), f, indent=2)
        except Exception:
            pass


# ═══════════════════════════════════════════════
# KALMAN FILTER — Ball Tracker
# ═══════════════════════════════════════════════
class BallKalman(object):
    """Tracks ball in angular (radian) camera space."""

    def __init__(self):
        self.x = 0.0  # horizontal angle (rad)
        self.y = 0.0  # vertical angle (rad)
        self.vx = 0.0
        self.vy = 0.0
        self.last_seen = 0.0
        self.seen_count = 0
        self.lost_count = 0
        self.last_size = 0.0  # angular size

    def predict(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vx *= 0.92   # friction decay
        self.vy *= 0.92

    def update(self, mx, my, size):
        now = time.time()
        dt = now - self.last_seen if self.last_seen > 0 else 0.1
        if 0 < dt < 3.0:
            new_vx = (mx - self.x) / dt
            new_vy = (my - self.y) / dt
            alpha = 0.3  # blend factor
            self.vx = self.vx * (1 - alpha) + new_vx * alpha
            self.vy = self.vy * (1 - alpha) + new_vy * alpha
        # Lerp position toward measurement
        gain = 0.7
        self.x = self.x * (1 - gain) + mx * gain
        self.y = self.y * (1 - gain) + my * gain
        self.last_size = size
        self.last_seen = now
        self.seen_count += 1
        self.lost_count = 0

    def mark_lost(self):
        self.lost_count += 1

    @property
    def age(self):
        if self.last_seen <= 0:
            return 999.0
        return time.time() - self.last_seen

    @property
    def is_fresh(self):
        return self.age < 1.5

    @property
    def is_recent(self):
        return self.age < 5.0

    def debug_str(self):
        return ("x={:.3f} y={:.3f} vx={:.3f} vy={:.3f} "
                "sz={:.4f} age={:.1f}s seen={}").format(
            self.x, self.y, self.vx, self.vy,
            self.last_size, self.age, self.seen_count)


# ═══════════════════════════════════════════════
# FIELD MODEL — knows where the goal is
# ═══════════════════════════════════════════════
class FieldModel(object):
    def __init__(self, motion):
        self.motion = motion
        try:
            p = motion.getRobotPosition(True)
            self.origin = (p[0], p[1], p[2])
            self.goal_theta = p[2]  # initial heading = toward opp goal
            logger.info("FIELD: origin=(%.2f, %.2f) goal_dir=%.2f rad",
                        p[0], p[1], p[2])
        except Exception as e:
            logger.warning("FIELD: Could not read initial pose: %s", e)
            self.origin = (0.0, 0.0, 0.0)
            self.goal_theta = 0.0

    def get_position(self):
        try:
            p = self.motion.getRobotPosition(True)
            return (p[0] - self.origin[0],
                    p[1] - self.origin[1], p[2])
        except Exception:
            return (0.0, 0.0, 0.0)

    def get_angle_to_goal(self):
        _, _, theta = self.get_position()
        diff = self.goal_theta - theta
        while diff > PI:
            diff -= TWO_PI
        while diff < -PI:
            diff += TWO_PI
        return diff

    def is_facing_goal(self, tol=0.35):
        return abs(self.get_angle_to_goal()) < tol

    def distance_from_start(self):
        x, y, _ = self.get_position()
        return math.sqrt(x * x + y * y)

    def is_out_of_bounds(self):
        return self.distance_from_start() > MAX_FIELD_RADIUS


# ═══════════════════════════════════════════════
# HOST VISION CLIENT (TCP to MacBook)
# ═══════════════════════════════════════════════
class HostVisionClient(object):
    def __init__(self, host_ip, port=5060):
        self.host_ip = host_ip
        self.port = port
        self.sock = None
        self.connected = False
        self.lock = threading.Lock()
        self.busy = False
        self.latest = {}
        self.frame_id = 0

    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(3)
            self.sock.connect((self.host_ip, self.port))
            self.connected = True
            logger.info("HOST_VISION: Connected to %s:%d", self.host_ip, self.port)
            return True
        except Exception as e:
            logger.warning("HOST_VISION: Connect failed: %s", e)
            self.connected = False
            return False

    def send_frame(self, raw, w, h, fmt="yuv422"):
        if not self.connected:
            return None
        try:
            self.frame_id += 1
            meta = json.dumps({"width": w, "height": h,
                                "format": fmt, "frame_id": self.frame_id})
            payload = meta.encode("utf-8") + b"\n" + bytes(raw)
            msg = struct.pack("!I", len(payload)) + payload
            self.sock.sendall(msg)
            hdr = self._recv(4)
            if not hdr:
                raise Exception("no response header")
            rlen = struct.unpack("!I", hdr)[0]
            body = self._recv(rlen)
            if not body:
                raise Exception("incomplete body")
            result = json.loads(body)
            with self.lock:
                self.latest = result
            return result
        except Exception as e:
            logger.debug("HOST_VISION: frame send error: %s", e)
            self.connected = False
            try:
                self.sock.close()
            except Exception:
                pass
            return None

    def _recv(self, n):
        d = b""
        while len(d) < n:
            c = self.sock.recv(n - len(d))
            if not c:
                return None
            d += c
        return d

    def get_latest(self):
        with self.lock:
            return self.latest.copy() if self.latest else {}

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════
# TELEMETRY — sends state to C++ server via WS
# ═══════════════════════════════════════════════
class TelemetryClient(object):
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        self.data = {}

    def start(self, trait):
        self.trait = trait
        self.running = True
        self.thread = threading.Thread(target=self._loop)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        self.running = False

    def update(self, d):
        with self.lock:
            self.data = d.copy()

    def _loop(self):
        while self.running:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect((self.host, self.port))
                hs = ("GET /api/ws/bot HTTP/1.1\r\n"
                      "Host: {h}:{p}\r\n"
                      "Upgrade: websocket\r\n"
                      "Connection: Upgrade\r\n"
                      "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n"
                      ).format(h=self.host, p=self.port)
                sock.sendall(hs.encode("utf-8"))
                resp = b""
                while b"\r\n\r\n" not in resp:
                    resp += sock.recv(4096)
                if b"101" not in resp.split(b"\r\n")[0]:
                    raise Exception("WS upgrade rejected")
                logger.info("TELEMETRY: WS connected to %s:%d",
                            self.host, self.port)
                while self.running:
                    with self.lock:
                        d = self.data.copy()
                    d["trait"] = self.trait
                    self._ws_send(sock, json.dumps(d))
                    time.sleep(0.15)
            except Exception as e:
                logger.debug("TELEMETRY: error: %s", e)
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
            if self.running:
                time.sleep(3)

    @staticmethod
    def _ws_send(sock, msg):
        data = msg.encode("utf-8")
        frame = bytearray([0x81])
        ln = len(data)
        if ln <= 125:
            frame.append(0x80 | ln)
        elif ln <= 65535:
            frame.append(0x80 | 126)
            frame.extend(struct.pack("!H", ln))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack("!Q", ln))
        mask = bytearray(os.urandom(4))
        data_bytes = bytearray(data)
        masked = bytearray(b ^ mask[i % 4] for i, b in enumerate(data_bytes))
        frame.extend(masked)
        sock.sendall(bytes(frame))


# ═══════════════════════════════════════════════
# MAIN BRAIN
# ═══════════════════════════════════════════════
class BotFCBrainV2(object):
    def __init__(self, robot_ip, robot_port, trait, server_ip, server_port):
        self.robot_ip = robot_ip
        self.robot_port = robot_port
        self.server_ip = server_ip
        self.role = TRAIT_TO_ROLE.get(trait, "BALANCED")
        self.trait = trait

        self.state = S_INIT
        self.prev_state = S_INIT
        self.running = False
        self.kick_count = 0
        self.break_remaining = 0
        self.stats = Stats()

        # Ball tracking
        self.ball = BallKalman()

        # Search state
        self.search_yaw = 0.0
        self.search_dir = 1.0
        self.search_sweep_count = 0

        # Dribble
        self.last_dribble_time = 0.0

        # Subsystems
        self.telemetry = TelemetryClient(server_ip, server_port)
        self.host_vision = HostVisionClient(server_ip, 5060)

        # Proxies
        self.motion = None
        self.posture = None
        self.memory = None
        self.tts = None
        self.leds = None
        self.ball_det = None
        self.sonar_p = None
        self.field = None
        self.video = None
        self.video_client = ""
        self.video_subs_id = None
        self.current_camera = 0 # 0=Top, 1=Bottom

    # ─── START ──────────────────────────────
    def start(self):
        logger.info("=" * 55)
        logger.info("  BOTFC BRAIN V2 STARTING")
        logger.info("  Robot: %s:%d", self.robot_ip, self.robot_port)
        logger.info("  Role: %s  Trait: %s", self.role, self.trait)
        logger.info("  Server: %s", self.server_ip)
        logger.info("=" * 55)

        # Connect proxies
        proxies = {
            "ALMotion": "motion", "ALRobotPosture": "posture",
            "ALMemory": "memory", "ALTextToSpeech": "tts",
            "ALLeds": "leds", "ALRedBallDetection": "ball_det",
            "ALSonar": "sonar_p", "ALVideoDevice": "video",
        }
        for svc, attr in proxies.items():
            try:
                p = ALProxy(svc, self.robot_ip, self.robot_port)
                setattr(self, attr, p)
                logger.info("PROXY: %s  ✓ connected", svc)
            except Exception as e:
                logger.error("PROXY: %s  ✗ FAILED: %s", svc, e)
                setattr(self, attr, None)

        if not self.motion or not self.memory:
            logger.critical("Cannot run without ALMotion+ALMemory. Aborting.")
            return

        # Field model (knows goal direction)
        self.field = FieldModel(self.motion)

        # Activate motors and fall manager
        try:
            if hasattr(self.motion, 'setFallManagerEnabled'):
                self.motion.setFallManagerEnabled(True)
                logger.info("MOTORS: Fall Manager ENABLED")
            self.motion.setStiffnesses("Body", 1.0)
            logger.info("MOTORS: Stiffness ON")
        except Exception as e:
            logger.error("MOTORS: %s", e)

        # Subscribe to ball detector
        if self.ball_det:
            try:
                self.ball_det.subscribe("BotFCv2", 33, 0.0)
                logger.info("BALL_DET: Subscribed (period=33ms)")
            except Exception as e:
                logger.error("BALL_DET: Subscribe failed: %s", e)

        # Subscribe to sonar
        if self.sonar_p:
            try:
                self.sonar_p.subscribe("BotFCv2")
                logger.info("SONAR: Subscribed")
            except Exception as e:
                logger.error("SONAR: Subscribe failed: %s", e)

        # Subscribe to camera for host vision
        if self.video:
            try:
                # 0=top camera, 1=320x240, 13=BGR, 5=5fps
                self.current_camera = 0
                self.video_client = self.video.subscribeCamera(
                    "BotFC_Eye", self.current_camera, 1, 13, 5)
                logger.info("CAMERA: Subscribed (%s) 320x240 BGR 5fps",
                            self.video_client)
            except Exception as e:
                logger.error("CAMERA: Subscribe failed: %s", e)

        # Stand up
        if self.posture:
            try:
                self.posture.goToPosture("StandInit", 0.8)
                logger.info("POSTURE: StandInit")
            except Exception as e:
                logger.error("POSTURE: %s", e)

        # Look down at ground
        try:
            self.motion.setAngles("HeadPitch", HEAD_PITCH_SEARCH, 0.3)
            self.motion.setAngles("HeadYaw", 0.0, 0.3)
        except Exception:
            pass

        # Say hello
        if self.tts:
            self.tts.post.say("Brain version 2. Role {}. Searching.".format(
                self.role))

        # Host vision (non-blocking connect)
        threading.Thread(target=self.host_vision.connect).start()

        # Start telemetry
        self.telemetry.start(self.trait)

        # GO
        self._set_state(S_SEARCH)
        self.running = True
        self.fsm_thread = threading.Thread(target=self._run)
        self.fsm_thread.daemon = True
        self.fsm_thread.start()
        logger.info("FSM: Running. Ctrl+C to stop.")

    # ─── STOP ───────────────────────────────
    def stop(self):
        logger.info("BRAIN: Shutting down...")
        self.running = False
        self.telemetry.stop()
        self.host_vision.close()
        try:
            self.motion.stopMove()
        except Exception:
            pass
        if self.ball_det:
            try:
                self.ball_det.unsubscribe("BotFCv2")
            except Exception:
                pass
        if self.sonar_p:
            try:
                self.sonar_p.unsubscribe("BotFCv2")
            except Exception:
                pass
        if self.video and self.video_client:
            try:
                self.video.unsubscribe(self.video_client)
            except Exception:
                pass
        if self.posture:
            try:
                self.posture.goToPosture("Crouch", 0.8)
                self.motion.setStiffnesses("Body", 0.0)
            except Exception:
                pass
        self.stats.save()
        logger.info("STATS: %s", json.dumps(self.stats.to_dict(), indent=2))
        logger.info("BRAIN: Stopped.")

    def switch_camera(self, cam_id):
        """Switches the active camera between Top (0) and Bottom (1)"""
        if self.current_camera == cam_id:
            return
        try:
            if hasattr(self.video, 'setActiveCamera'):
                self.video.setActiveCamera(self.video_client, cam_id)
                self.current_camera = cam_id
                cam_name = "TOP" if cam_id == 0 else "BOTTOM"
                self._log("VISION", "Switched active camera to: " + cam_name)
        except Exception as e:
            self._log("ERROR", "Failed to switch camera: " + str(e))

    # ─── STATE MACHINE ──────────────────────
    def _set_state(self, new_state):
        old = self.state
        if old != new_state:
            self.state = new_state
            self.prev_state = old
            self.stats.state_transitions += 1
            logger.info("STATE: %s → %s", old, new_state)

    # ─── SENSOR READS ───────────────────────
    def _read_ball(self):
        """Read from ALRedBallDetection.
        Returns (found, center_x_rad, center_y_rad, size_x_rad).
        center_x is negative=left, positive=right.
        """
        try:
            data = self.memory.getData(BALL_MEM_KEY)
            if data and isinstance(data, list) and len(data) >= 2:
                ts = data[0]  # [seconds, microseconds]
                info = data[1]  # [centerX, centerY, sizeX, sizeY]
                if isinstance(info, list) and len(info) >= 4:
                    cx = float(info[0])   # angle in radians, horiz
                    cy = float(info[1])   # angle in radians, vert
                    sx = float(info[2])   # angular width
                    sy = float(info[3])   # angular height
                    size = max(sx, sy)

                    # Sanity check: size should be positive, angles reasonable
                    if size > 0.001 and abs(cx) < 1.5 and abs(cy) < 1.5:
                        logger.debug("BALL: FOUND cx=%.3f cy=%.3f "
                                     "sx=%.4f sy=%.4f", cx, cy, sx, sy)
                        return True, cx, cy, size
            logger.debug("BALL: not detected (data=%s)",
                         type(data).__name__)
        except Exception as e:
            logger.debug("BALL: read error: %s", e)
        return False, 0.0, 0.0, 0.0

    def _read_sonar(self):
        sl = sr = 9.0
        try:
            sl = float(self.memory.getData(SONAR_L_KEY))
            sr = float(self.memory.getData(SONAR_R_KEY))
            if sl < 0.4 or sr < 0.4:
                self.stats.sonar_obstacles += 1
                logger.debug("SONAR: L=%.2fm R=%.2fm ⚠ CLOSE", sl, sr)
            else:
                logger.debug("SONAR: L=%.2fm R=%.2fm", sl, sr)
        except Exception as e:
            logger.debug("SONAR: read error: %s", e)
        return sl, sr

    def _read_temps(self):
        keys = [
            "Device/SubDeviceList/LHipPitch/Temperature/Sensor/Value",
            "Device/SubDeviceList/RHipPitch/Temperature/Sensor/Value",
            "Device/SubDeviceList/LKneePitch/Temperature/Sensor/Value",
            "Device/SubDeviceList/RKneePitch/Temperature/Sensor/Value",
        ]
        temps = []
        try:
            for k in keys:
                temps.append(float(self.memory.getData(k)))
        except Exception:
            pass
        return temps

    def _grab_camera_frame(self):
        """Grab a frame from the camera for host vision."""
        if not self.video or not self.video_client:
            return None
        try:
            img = self.video.getImageRemote(self.video_client)
            if img and len(img) >= 7:
                w = img[0]
                h = img[1]
                raw = img[6]
                self.stats.frames_captured += 1
                logger.debug("CAMERA: Grabbed frame %dx%d (%d bytes)",
                             w, h, len(raw) if raw else 0)
                self.video.releaseImage(self.video_client)
                return raw, w, h
        except Exception as e:
            logger.debug("CAMERA: grab error: %s", e)
        return None

    def _send_frame_to_host(self):
        """Send camera frame to host OpenCV pipeline."""
        with self.host_vision.lock:
            if self.host_vision.busy:
                return None
            self.host_vision.busy = True

        try:
            result = self._grab_camera_frame()
            if not result:
                return None
            raw, w, h = result
            
            resp = self.host_vision.send_frame(raw, w, h, "bgr")
            if resp:
                self.stats.frames_sent_to_host += 1
                ball_info = resp.get("ball", {})
                if ball_info.get("found"):
                    self.stats.host_vision_hits += 1
                    logger.debug("HOST_VISION: Ball @ (%.3f, %.3f) "
                                 "conf=%.2f r=%.4f",
                                 ball_info["x"], ball_info["y"],
                                 ball_info.get("confidence", 0),
                                 ball_info.get("radius", 0))
                else:
                    self.stats.host_vision_misses += 1
                goals = resp.get("goals", {})
                for color in ("yellow", "blue"):
                    g = goals.get(color, {})
                    if g.get("found"):
                        logger.debug("HOST_VISION: %s goal center=%.3f "
                                     "posts=%d", color, g["center_x"],
                                     g["posts"])
                return resp
        except Exception as e:
            logger.debug("HOST_VISION: error: %s", e)
        finally:
            with self.host_vision.lock:
                self.host_vision.busy = False
        return None

    def _log(self, tag, msg):
        logger.info("%s: %s", tag, msg)

    # ─── MAIN FSM LOOP ──────────────────────
    def _run(self):
        tick = 0
        host_vision_result = None
        last_log_time = time.time()

        while self.running:
            loop_start = time.time()
            try:
                # ── Predict ball ──
                self.ball.predict(0.06)

                # ── Read ball from NAOqi ──
                found, bx, by, bsz = self._read_ball()
                if found:
                    self.ball.update(bx, by, bsz)
                    self.stats.ball_detections += 1
                else:
                    self.ball.mark_lost()
                    self.stats.ball_losses += 1

                # ── Read sonar ──
                sl, sr = self._read_sonar()

                # ── Host vision every ~0.5s ──
                if tick % 8 == 0:
                    t = threading.Thread(target=self._send_frame_to_host)
                    t.daemon = True
                    t.start()

                # ── Try host vision ball if NAOqi missed it ──
                if not found:
                    hv = self.host_vision.get_latest()
                    if hv and "ball" in hv:
                        hvb = hv["ball"]
                        if hvb.get("found") and hvb.get("confidence", 0) > 0.3:
                            # Map host [-0.5, 0.5] to radians (~1.0 rad FOV)
                            host_bx = hvb["x"] * 1.0
                            host_by = hvb["y"] * 0.8
                            host_bsz = hvb.get("radius", 0.03) * 2.0
                            self.ball.update(host_bx, host_by, host_bsz)
                            found = True
                            bx, by, bsz = host_bx, host_by, host_bsz
                            logger.info("BALL: Rescued by host vision "
                                        "cx=%.3f cy=%.3f", host_bx, host_by)

                # ── Temperature check ──
                if tick % 100 == 0:
                    temps = self._read_temps()
                    if temps:
                        max_t = max(temps)
                        logger.info("TEMP: max=%.1f°C %s",
                                    max_t,
                                    "⚠HOT" if max_t > 50 else "OK")
                        if max_t > MOTOR_TEMP_LIMIT:
                            self._trigger_cooldown(max_t)
                            continue

                # ── Periodic summary log (every 5s) ──
                if time.time() - last_log_time > 5.0:
                    last_log_time = time.time()
                    pos = self.field.get_position() if self.field else (0, 0, 0)
                    logger.info(
                        "TICK=%d STATE=%s BALL=[%s] POS=(%.2f,%.2f,%.1f°) "
                        "SONAR=L%.2f/R%.2f KICKS=%d HOST=%s",
                        tick, self.state, self.ball.debug_str(),
                        pos[0], pos[1], math.degrees(pos[2]),
                        sl, sr, self.kick_count,
                        "ON" if self.host_vision.connected else "OFF")
                    self.stats.save()

                # ── Update telemetry for frontend ──
                facing = self.field.is_facing_goal() if self.field else False
                self.telemetry.update({
                    "state": self.state,
                    "kicks": self.kick_count,
                    "ball_age": round(self.ball.age, 1),
                    "break_remaining": self.break_remaining,
                    "robot_connected": True,
                    "ball_x": round(self.ball.x, 3),
                    "ball_y": round(self.ball.y, 3),
                    "facing_goal": facing,
                    "ball_found": found,
                    "host_vision": self.host_vision.connected,
                })

                # ── Out of bounds? ──
                if self.field and self.field.is_out_of_bounds():
                    self.stats.out_of_bounds += 1
                    logger.warning("NAV: Out of bounds! Returning to field.")
                    self._return_to_field()
                else:
                    # ── Fall Check ──
                    if tick % 8 == 0 and self.posture and self.state not in (S_RECOVER, S_INIT, S_HALFTIME):
                        try:
                            posture_fam = self.posture.getPostureFamily()
                            if posture_fam in ("Belly", "Back", "Left", "Right"):
                                logger.critical("FALL DETECTED: Posture=%s! Assuming crash position.", posture_fam)
                                self._set_state(S_RECOVER)
                        except Exception as e:
                            logger.error("POSTURE CHECK: %s", e)
                            
                    # ── Dispatch FSM state ──
                    self._dispatch(found, bx, by, bsz, sl, sr)

            except Exception:
                logger.error("FSM: Exception:\n%s", traceback.format_exc())

            # Rate limit to ~16Hz
            elapsed = time.time() - loop_start
            sleep_time = max(0.01, 0.06 - elapsed)
            time.sleep(sleep_time)
            tick += 1

    def _dispatch(self, found, bx, by, bsz, sl, sr):
        s = self.state
        if s == S_SEARCH:
            self._do_search(found, bx, by, bsz)
        elif s == S_PURSUE:
            self.switch_camera(0)
            self._do_pursue(found, bx, by, bsz, sl, sr)
        elif s == S_DRIBBLE:
            self.switch_camera(1)
            self._do_dribble(found, bx, by, bsz, sl, sr)
        elif s == S_ALIGN:
            self.switch_camera(1)
            self._do_align(found, bx, by, bsz, sl, sr)
        elif s == S_SHOOT:
            self.switch_camera(1)
            self._do_shoot()
        elif s == S_DEFEND:
            self._do_defend(found, bx, by, bsz, sl, sr)
        elif s == S_TACKLE:
            self._do_tackle()
        elif s == S_RECOVER:
            self._do_recover()

    # ─── RETURN TO FIELD ────────────────────
    def _return_to_field(self):
        logger.info("NAV: Returning to field center")
        self.motion.stopMove()
        heading = self.field.get_return_heading() if self.field else 0.0
        try:
            self.motion.moveTo(0.0, 0.0, heading)
            self.motion.moveTo(0.4, 0.0, 0.0)
        except Exception as e:
            logger.error("NAV: return error: %s", e)

    # ─── SEARCH ─────────────────────────────
    def _do_search(self, found, bx, by, bsz):
        # LED yellow
        if self.leds:
            try:
                self.leds.fadeRGB("AllLeds", 0xFFCC00, 0.2)
            except Exception:
                pass

        if found:
            logger.info("SEARCH: Ball found at cx=%.3f cy=%.3f sz=%.4f",
                        bx, by, bsz)
            self.motion.stopMove()
            self._set_state(S_PURSUE)
            return

        # Sweep head left-right while looking alternating Near/Far
        self.search_yaw += self.search_dir * 0.08
        if self.search_yaw > HEAD_YAW_MAX:
            self.search_yaw = HEAD_YAW_MAX
            self.search_dir = -1.0
            self.search_sweep_count += 1
        elif self.search_yaw < HEAD_YAW_MIN:
            self.search_yaw = HEAD_YAW_MIN
            self.search_dir = 1.0
            self.search_sweep_count += 1

        try:
            # Even sweeps: Top Cam, Far Horizon. Odd sweeps: Bottom Cam, Near Feet.
            is_bottom_cam = (self.search_sweep_count % 2 != 0)
            target_pitch = HEAD_PITCH_CLOSE if is_bottom_cam else HEAD_PITCH_SEARCH
            self.switch_camera(1 if is_bottom_cam else 0)

            self.motion.setAngles("HeadYaw", self.search_yaw, 0.12)
            self.motion.setAngles("HeadPitch", target_pitch, 0.12)
        except Exception:
            pass

        # After 2 full sweeps without finding ball, walk and search
        if self.search_sweep_count >= 4:
            logger.info("SEARCH: %d sweeps, no ball. Walking+searching.",
                        self.search_sweep_count)
            # If we had a recent sighting, walk toward prediction
            if self.ball.is_recent:
                turn = max(-0.3, min(0.3, -self.ball.x * 1.5))
                logger.debug("SEARCH: Walking toward predicted ball "
                             "(turn=%.2f)", turn)
                self.motion.moveToward(0.3, 0.0, turn)
            else:
                # Slow rotation to scan 360 degrees
                logger.debug("SEARCH: Slow rotate to scan")
                self.motion.moveToward(0.1, 0.0, 0.3)

            # Reset sweep count after some walking
            if self.search_sweep_count >= 8:
                self.search_sweep_count = 0

    # ─── PURSUE ─────────────────────────────
    def _do_pursue(self, found, bx, by, bsz, sl, sr):
        if self.leds:
            try:
                self.leds.fadeRGB("AllLeds", 0x00FF00, 0.2)
            except Exception:
                pass

        # Lost ball?
        if not found and not self.ball.is_fresh:
            logger.info("PURSUE: Lost ball (age=%.1fs). Back to SEARCH.",
                        self.ball.age)
            self.motion.stopMove()
            self._set_state(S_SEARCH)
            return

        # Head tracking — keep head pointed at ball
        cx = self.ball.x
        cy = self.ball.y
        try:
            head_yaw = max(-1.0, min(1.0, -cx * 0.8))
            head_pitch = max(0.0, min(0.5, 0.25 + cy * 0.5))
            self.motion.setAngles("HeadYaw", head_yaw, 0.15)
            self.motion.setAngles("HeadPitch", head_pitch, 0.15)
        except Exception:
            pass

        # Obstacle nearby?
        min_sonar = min(sl, sr)
        if min_sonar < 0.3:
            logger.info("PURSUE: Obstacle at %.2fm! Dodge.", min_sonar)
            # Dodge sideways
            dodge_lat = 0.15 if sl < sr else -0.15
            self.motion.moveToward(0.1, dodge_lat, 0.0)
            return

        # Ball very close? → dribble
        if found and bsz > 0.06:
            logger.info("PURSUE: Ball close (sz=%.4f). → DRIBBLE", bsz)
            self.motion.stopMove()
            self._set_state(S_DRIBBLE)
            return

        # Walk toward ball
        # Turn to center ball in view
        turn_rate = max(-0.5, min(0.5, -cx * 2.5))
        # Walk faster when ball is far, slower when close
        forward = max(0.2, min(0.6, 0.6 - bsz * 5.0))
        logger.debug("PURSUE: cx=%.3f sz=%.4f → fwd=%.2f turn=%.2f",
                      cx, bsz, forward, turn_rate)
        self.motion.moveToward(forward, 0.0, turn_rate)

    # ─── DRIBBLE ────────────────────────────
    def _do_dribble(self, found, bx, by, bsz, sl, sr):
        if self.leds:
            try:
                self.leds.fadeRGB("AllLeds", 0x00FFAA, 0.2)
            except Exception:
                pass

        # Look steeply down at ball
        try:
            self.motion.setAngles("HeadPitch", HEAD_PITCH_CLOSE, 0.2)
            head_yaw = max(-0.8, min(0.8, -bx * 0.5))
            self.motion.setAngles("HeadYaw", head_yaw, 0.15)
        except Exception:
            pass

        if not found:
            logger.info("DRIBBLE: Lost ball. → SEARCH")
            self.motion.stopMove()
            self._set_state(S_SEARCH)
            return

        # Facing goal? → SHOOT
        facing_goal = self.field.is_facing_goal(0.2) if self.field else False
        if bsz > 0.08 and facing_goal:
            logger.info("DRIBBLE: Facing goal + ball close. → SHOOT")
            self.motion.stopMove()
            self._set_state(S_SHOOT)
            return

        if bsz > 0.08 and not facing_goal:
            logger.info("DRIBBLE: Not facing goal. → ALIGN")
            self.motion.stopMove()
            self._set_state(S_ALIGN)
            return

        # Opponent blocking?
        if min(sl, sr) < 0.35:
            logger.info("DRIBBLE: Opponent at %.2fm! → TACKLE",
                        min(sl, sr))
            self.motion.stopMove()
            self._set_state(S_TACKLE)
            return

        # Dribble: nudge ball toward goal with small walks
        goal_angle = self.field.get_angle_to_goal() if self.field else 0.0
        now = time.time()

        if abs(goal_angle) > 0.5 and self.field:
            # Need to turn toward goal first
            logger.debug("DRIBBLE: Turning toward goal (angle=%.2f)",
                         goal_angle)
            turn = max(-0.3, min(0.3, goal_angle * 0.4))
            self.motion.moveToward(0.05, 0.0, turn)
        elif now - self.last_dribble_time > 2.0:
            # Walk into ball to push it
            logger.info("DRIBBLE: Nudging ball forward (touch #%d)",
                        self.stats.dribble_touches + 1)
            self.motion.moveToward(0.5, 0.0, -bx * 1.5)
            time.sleep(0.8)
            self.motion.stopMove()
            self.stats.dribble_touches += 1
            self.last_dribble_time = now
        else:
            # Walk slowly behind ball
            turn = max(-0.3, min(0.3, -bx * 1.5))
            self.motion.moveToward(0.15, 0.0, turn)

    # ─── ALIGN (Orbit around ball) ──────────
    def _do_align(self, found, bx, by, bsz, sl, sr):
        if self.leds:
            try:
                self.leds.fadeRGB("AllLeds", 0x00FFFF, 0.1) # Cyan
            except Exception:
                pass

        if not found:
            logger.info("ALIGN: Lost ball. → SEARCH")
            self.motion.stopMove()
            self._set_state(S_SEARCH)
            return

        goal_angle = self.field.get_angle_to_goal() if self.field else 0.0
        
        if self.field and self.field.is_facing_goal(0.2):
            logger.info("ALIGN: Facing Goal! → SHOOT")
            self.motion.stopMove()
            self._set_state(S_SHOOT)
            return
            
        # Orbit logic based on TDDD63 arching
        orbit_speed = 0.4
        turn_speed = 0.3
        
        # Pull back slightly if too close
        x_speed = 0.0
        if bsz > 0.15:
            x_speed = -0.15
            
        if goal_angle > 0: # Goal is Left, orbit right, turn left
            self.motion.moveToward(x_speed, -orbit_speed, turn_speed)
        else:
            self.motion.moveToward(x_speed, orbit_speed, -turn_speed)

    # ─── SHOOT ──────────────────────────────
    def _do_shoot(self):
        if self.leds:
            try:
                self.leds.fadeRGB("AllLeds", 0xFFFFFF, 0.1)
            except Exception:
                pass

        logger.info("SHOOT: === SHOOTING ===")
        self.stats.kicks_attempted += 1
        self.motion.stopMove()
        time.sleep(0.3)

        # Re-check ball
        found, bx, by, bsz = self._read_ball()
        if not found:
            logger.info("SHOOT: Ball lost before kick. → SEARCH")
            self._set_state(S_SEARCH)
            return

        logger.info("SHOOT: Ball at cx=%.3f cy=%.3f sz=%.4f. Kicking!",
                     bx, by, bsz)

        try:
            # Choose kick leg based on ball position
            if bx < -0.02:
                kick_leg = "L"
                side_step = 0.04
            else:
                kick_leg = "R"
                side_step = -0.04

            logger.info("SHOOT: Kicking with %s leg", kick_leg)

            # Setup
            self.posture.goToPosture("Stand", 0.8)
            time.sleep(0.2)
            self.motion.moveTo(0.0, side_step, 0.0)
            time.sleep(0.2)

            if kick_leg == "R":
                hip, knee = "RHipPitch", "RKneePitch"
                support = "LHipRoll"
            else:
                hip, knee = "LHipPitch", "LKneePitch"
                support = "RHipRoll"

            # Shift weight to support leg
            self.motion.setAngles(support, 0.12, 0.3)
            time.sleep(0.3)
            # Wind up
            self.motion.setAngles(hip, -0.5, 0.5)
            time.sleep(0.2)
            # KICK!
            self.motion.setAngles(hip, 0.9, 1.0)
            self.motion.setAngles(knee, -0.8, 1.0)
            time.sleep(0.3)
            # Recover
            self.posture.goToPosture("Stand", 0.8)

            self.kick_count += 1
            self.stats.kicks_completed += 1
            logger.info("SHOOT: Kick #%d COMPLETE ✓", self.kick_count)

            if self.tts:
                self.tts.post.say("Goal!")

        except Exception as e:
            logger.error("SHOOT: Kick failed: %s", e)
            try:
                self.posture.goToPosture("Stand", 0.8)
            except Exception:
                pass

        self._set_state(S_SEARCH)

    # ─── DEFEND ─────────────────────────────
    def _do_defend(self, found, bx, by, bsz, sl, sr):
        if self.leds:
            try:
                self.leds.fadeRGB("AllLeds", 0x4D9FFF, 0.2)
            except Exception:
                pass

        if found:
            # Ball close? → intercept
            if bsz > 0.05:
                logger.info("DEFEND: Ball close (sz=%.4f). → PURSUE", bsz)
                self._set_state(S_PURSUE)
                return
            # Track ball with head
            try:
                self.motion.setAngles("HeadYaw",
                                      max(-1.0, min(1.0, -bx * 0.8)), 0.15)
            except Exception:
                pass
            # Move laterally to stay between ball and goal
            self.motion.moveToward(0.0, -bx * 0.1, -bx * 0.3)
        else:
            # Scan for ball
            self.search_yaw += self.search_dir * 0.1
            if abs(self.search_yaw) > 1.2:
                self.search_dir *= -1.0
            try:
                self.motion.setAngles("HeadYaw", self.search_yaw, 0.12)
                self.motion.setAngles("HeadPitch", HEAD_PITCH_SEARCH, 0.12)
            except Exception:
                pass
            self.motion.moveToward(0.0, 0.0, 0.15)

    # ─── TACKLE ─────────────────────────────
    def _do_tackle(self):
        logger.info("TACKLE: Charging!")
        self.stats.tackles += 1
        if self.leds:
            try:
                self.leds.fadeRGB("AllLeds", 0xFF0000, 0.1)
            except Exception:
                pass
        if self.tts:
            self.tts.post.say("Mine!")
        try:
            self.posture.goToPosture("StandInit", 0.8)
            self.motion.moveToward(0.8, 0.0, 0.0)
            time.sleep(1.5)
            self.motion.stopMove()
            self.posture.goToPosture("StandInit", 0.8)
        except Exception as e:
            logger.error("TACKLE: %s", e)
        self._set_state(S_SEARCH)

    # ─── COOLDOWN ───────────────────────────
    def _trigger_cooldown(self, max_t):
        logger.warning("OVERHEAT: %.1f°C  Taking a break.", max_t)
        self.stats.overheat_breaks += 1
        self.motion.stopMove()
        if self.tts:
            self.tts.post.say("Motors too hot. Resting.")
        if self.posture:
            self.posture.goToPosture("Crouch", 0.8)
        self.motion.setStiffnesses("Body", 0.0)
        self._set_state(S_HALFTIME)

        cd = 90
        for r in range(cd, 0, -1):
            if not self.running:
                break
            self.break_remaining = r
            if r % 30 == 0:
                logger.info("COOLDOWN: %ds remaining", r)
            time.sleep(1)
        self.break_remaining = 0

        if self.running:
            logger.info("COOLDOWN: Done. Resuming.")
            self.motion.setStiffnesses("Body", 1.0)
            if self.posture:
                self.posture.goToPosture("StandInit", 0.8)
            self._set_state(S_SEARCH)

    # ─── RECOVER (Get Up After Fall) ────────
    def _do_recover(self):
        logger.info("RECOVER: Recovering from fall...")
        try:
            self.motion.stopMove()
            # Give the robot a moment to settle on the ground
            time.sleep(1.5)
            
            # Re-engage stiffness just in case Fall Manager killed it
            self.motion.setStiffnesses("Body", 1.0)
            
            # Stand back up
            if self.posture:
                logger.info("RECOVER: Executing StandInit")
                self.posture.goToPosture("StandInit", 0.8)
            
            logger.info("RECOVER: Successfully standing. Returning to SEARCH.")
        except Exception as e:
            logger.error("RECOVER: Failed to get up: %s", e)
            
        self._set_state(S_SEARCH)


# ═══════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════
g_brain = None

def signal_handler(signum, frame):
    global g_brain
    logger.info("SIGNAL: Received %d. Stopping.", signum)
    if g_brain:
        g_brain.stop()
    sys.exit(0)

def main():
    global g_brain
    parser = argparse.ArgumentParser(description="BotFC Brain v2")
    parser.add_argument("--ip", default="127.0.0.1")
    parser.add_argument("--pip", default=None)
    parser.add_argument("--pport", type=int, default=9559)
    parser.add_argument("--trait", default="balanced")
    parser.add_argument("--server-ip", default="127.0.0.1")
    parser.add_argument("--server-port", type=int, default=5050)
    args = parser.parse_args()

    robot_ip = args.pip if args.pip else args.ip

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("=" * 55)
    logger.info("  BotFC Brain v2 — Smart Football AI")
    logger.info("  Robot: %s:%d", robot_ip, args.pport)
    logger.info("  Trait: %s  Role: %s",
                args.trait, TRAIT_TO_ROLE.get(args.trait, "BALANCED"))
    logger.info("  Server: %s:%d", args.server_ip, args.server_port)
    logger.info("  Vision: %s:5060", args.server_ip)
    logger.info("  Log: %s", LOG_FILE)
    logger.info("=" * 55)

    brain = BotFCBrainV2(robot_ip, args.pport, args.trait,
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
