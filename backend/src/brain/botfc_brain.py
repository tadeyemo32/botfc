#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
BotFC Brain – Python Port (1:1 from C++)
Runs directly on the NAO/Pepper robot using the NAOqi Python SDK.

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

# NAOqi Python SDK (pre-installed on the robot)
from naoqi import ALBroker, ALProxy

# ─────────────────────────────────────────────
# Constants (matching C++ originals)
# ─────────────────────────────────────────────
MAX_FIELD_RADIUS = 2.5
COMBAT_DISTANCE = 0.40
MOTOR_TEMP_LIMIT = 60.0

# ─────────────────────────────────────────────
# Roles & States (from Roles.h)
# ─────────────────────────────────────────────
ROLE_STRIKER = "STRIKER"
ROLE_DEFENDER = "DEFENDER"
ROLE_BALANCED = "BALANCED"

STATE_INIT = "INIT"
STATE_SEARCH = "SEARCH"
STATE_APPROACH = "APPROACH"
STATE_ALIGN = "ALIGN"
STATE_TACKLE = "TACKLE"
STATE_KICK = "KICK"
STATE_RECOVER = "RECOVER"
STATE_HALFTIME = "HALFTIME"


# ─────────────────────────────────────────────
# Telemetry Client (from TelemetryClient.cpp)
# WebSocket client that sends state to the
# macOS Boost server via /api/ws/bot
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
            "state": STATE_INIT,
            "kicks": 0,
            "ball_age": -1.0,
            "break_remaining": 0,
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

    def update(self, state, kicks, ball_age, break_remaining):
        with self.lock:
            self.current_data = {
                "state": state,
                "kicks": kicks,
                "ball_age": ball_age,
                "break_remaining": break_remaining,
            }

    def _build_payload(self):
        with self.lock:
            d = self.current_data.copy()
        d["trait"] = self.trait
        return json.dumps(d)

    def _loop(self):
        """WebSocket send loop with reconnection, using raw sockets
        since the robot's Python 2.7 env may not have the websocket lib."""
        import socket
        import hashlib
        import base64

        while self.running:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect((self.host, self.port))

                # WebSocket handshake (RFC 6455)
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

                # Read HTTP response
                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise Exception("Handshake failed: connection closed")
                    resp += chunk

                if b"101" not in resp.split(b"\r\n")[0]:
                    raise Exception("WebSocket upgrade rejected")

                print("[Telemetry] Connected to ws://{}:{}/api/ws/bot".format(
                    self.host, self.port))

                # Send loop
                while self.running:
                    payload = self._build_payload()
                    self._ws_send(sock, payload)
                    time.sleep(0.1)

                # Close frame
                self._ws_close(sock)
            except Exception as e:
                print("[Telemetry] Error: {}. Retrying in 2s...".format(e))
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
        """Send a WebSocket text frame (masked, per RFC 6455 client)."""
        data = message.encode("utf-8")
        length = len(data)
        frame = bytearray()
        frame.append(0x81)  # FIN + text opcode
        if length <= 125:
            frame.append(0x80 | length)  # MASK bit set
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
        """Send WebSocket close frame."""
        frame = bytearray([0x88, 0x80])  # FIN + close, masked, 0 len
        frame.extend(os.urandom(4))      # mask key
        try:
            sock.sendall(bytes(frame))
        except Exception:
            pass


# ─────────────────────────────────────────────
# ML Data Logger (from MLDataLogger.cpp)
# Captures camera frames + FSM state for
# training data collection
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
            # kTopCamera=0, kQVGA=1, kYUV422=9, 5fps
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

    def _save_json(self, path, t):
        try:
            with open(path, "w") as f:
                json.dump(t, f, indent=2)
        except Exception:
            pass

    def _loop(self):
        while self.running:
            try:
                img_data = self.video_device.getImageRemote(
                    self.video_client_name)
                if img_data and len(img_data) > 6:
                    raw_bytes = img_data[6]
                    base_name = "{}frame_{}".format(
                        self.out_dir, self.frame_index)
                    img_path = base_name + ".raw"
                    json_path = base_name + ".json"

                    with open(img_path, "wb") as f:
                        f.write(raw_bytes)

                    with self.lock:
                        t = self.current_telemetry.copy()
                    self._save_json(json_path, t)

                    self.frame_index += 1
                    self.video_device.releaseImage(self.video_client_name)
            except Exception:
                pass
            time.sleep(0.2)  # 5Hz


# ─────────────────────────────────────────────
# BotFCBrain (from BotFCBrain.cpp)
# The main Finite State Machine controlling
# the robot's soccer behavior
# ─────────────────────────────────────────────
class BotFCBrain(object):
    def __init__(self, robot_ip, robot_port, trait, server_ip, server_port):
        self.robot_ip = robot_ip
        self.robot_port = robot_port

        # Role
        if trait == "offense":
            self.role = ROLE_STRIKER
        elif trait == "defense":
            self.role = ROLE_DEFENDER
        else:
            self.role = ROLE_BALANCED
            trait = "balanced"
        self.trait = trait

        # State
        self.state = STATE_INIT
        self.lock = threading.Lock()
        self.running = False
        self.kick_count = 0
        self.overheat_count = 0
        self.break_remaining = 0
        self.origin_x = 0.0
        self.origin_y = 0.0
        self.origin_theta = 0.0
        self.last_ball_time = 0.0
        self.last_man_on_time = 0.0
        self.last_overheat_time = 0.0
        self.search_yaw = 0.0
        self.search_yaw_dir = 1.0
        self.field_map = {}
        self.fsm_thread = None

        # Telemetry
        self.telemetry_client = TelemetryClient(server_ip, server_port)

        # ML Data Logger
        self.data_logger = MLDataLogger(robot_ip, robot_port)

        # Proxies (initialized on start())
        self.motion = None
        self.posture = None
        self.memory = None
        self.tts = None
        self.leds = None
        self.ball_det = None
        self.sonar_p = None

        # Temperature sensor keys
        self.temp_keys = [
            "Device/SubDeviceList/LHipPitch/Temperature/Sensor/Value",
            "Device/SubDeviceList/RHipPitch/Temperature/Sensor/Value",
            "Device/SubDeviceList/LKneePitch/Temperature/Sensor/Value",
            "Device/SubDeviceList/RKneePitch/Temperature/Sensor/Value",
        ]

    def start(self):
        if self.running:
            return

        try:
            self.motion = ALProxy("ALMotion", self.robot_ip, self.robot_port)
            self.posture = ALProxy("ALRobotPosture", self.robot_ip, self.robot_port)
            self.memory = ALProxy("ALMemory", self.robot_ip, self.robot_port)
            self.tts = ALProxy("ALTextToSpeech", self.robot_ip, self.robot_port)
            self.leds = ALProxy("ALLeds", self.robot_ip, self.robot_port)
            self.ball_det = ALProxy("ALRedBallDetection", self.robot_ip, self.robot_port)
            self.sonar_p = ALProxy("ALSonar", self.robot_ip, self.robot_port)
        except Exception as e:
            print("[BotFC] FATAL: Failed to init proxies: {}".format(e))
            return

        self.motion.setStiffnesses("Body", 1.0)

        try:
            self.ball_det.subscribe("BotFCBrain", 33, 0.0)
        except Exception:
            pass
        try:
            self.sonar_p.subscribe("BotFCBrain")
        except Exception:
            pass

        self.posture.goToPosture("StandInit", 1.0)
        self.tts.post.say("Python Brain online.")

        try:
            p = self.motion.getRobotPosition(True)
            self.origin_x = p[0]
            self.origin_y = p[1]
            self.origin_theta = p[2]
        except Exception:
            pass

        self.last_ball_time = time.time() - 100.0

        with self.lock:
            self.state = STATE_SEARCH

        self.running = True

        # Start subsystems
        self.data_logger.start()
        self.telemetry_client.start(self.trait)

        # Spawn FSM thread
        self.fsm_thread = threading.Thread(target=self._run)
        self.fsm_thread.daemon = True
        self.fsm_thread.start()

        print("[BotFC] Brain started. Role={}, Trait={}".format(
            self.role, self.trait))

    def stop(self):
        if not self.running:
            return
        self.running = False

        self.data_logger.stop()
        self.telemetry_client.stop()

        if self.fsm_thread and self.fsm_thread.is_alive():
            self.fsm_thread.join(timeout=5)

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

    # ─── FSM Main Loop ─────────────────────
    def _run(self):
        while self.running:
            self._safety_check()

            with self.lock:
                s = self.state
                k = self.kick_count
                br = self.break_remaining
                lbt = self.last_ball_time

            # Update telemetry
            ball_age = (time.time() - lbt) if lbt > 0 else -1.0
            self.telemetry_client.update(s, k, ball_age, br)

            if s != STATE_HALFTIME:
                self._enforce_bounds()

                if s == STATE_SEARCH:
                    self._do_search()
                elif s == STATE_APPROACH:
                    self._do_approach()
                elif s == STATE_ALIGN:
                    self._do_align()
                elif s == STATE_KICK:
                    self._do_kick()
                elif s == STATE_TACKLE:
                    self._do_tackle()
                else:
                    with self.lock:
                        self.state = STATE_SEARCH

            time.sleep(0.05)

    # ─── Sonar Local Map ────────────────────
    def _update_local_map(self):
        try:
            sl = float(self.memory.getData(
                "Device/SubDeviceList/US/Left/Sensor/Value"))
            sr = float(self.memory.getData(
                "Device/SubDeviceList/US/Right/Sensor/Value"))
            p = self.motion.getRobotPosition(True)
            left_angle = (p[2] + 0.5) * (180.0 / math.pi)
            right_angle = (p[2] - 0.5) * (180.0 / math.pi)
            sec_l = int(left_angle / 30.0) * 30
            sec_r = int(right_angle / 30.0) * 30

            with self.lock:
                if sec_l not in self.field_map or sl < self.field_map[sec_l]:
                    self.field_map[sec_l] = sl
                if sec_r not in self.field_map or sr < self.field_map[sec_r]:
                    self.field_map[sec_r] = sr

            # Build ML telemetry snapshot
            snapshot = {
                "headYaw": self.motion.getAngles("HeadYaw", False)[0],
                "headPitch": self.motion.getAngles("HeadPitch", False)[0],
                "sonarLeft": sl,
                "sonarRight": sr,
                "ballFound": False,
                "ballBx": 0.0,
                "ballBy": 0.0,
                "ballBsz": 0.0,
            }

            data = self.memory.getData("redBallDetected")
            if data and len(data) >= 2:
                info = data[1]
                snapshot["ballFound"] = True
                snapshot["ballBx"] = float(info[0])
                snapshot["ballBy"] = float(info[1])
                snapshot["ballBsz"] = float(info[2])

            self.data_logger.update_telemetry(snapshot)
        except Exception:
            pass

    # ─── Bounds Check ───────────────────────
    def _is_in_bounds(self, x, y):
        with self.lock:
            dx = x - self.origin_x
            dy = y - self.origin_y
            fm = self.field_map.copy()
        dist = math.sqrt(dx * dx + dy * dy)
        if dist > MAX_FIELD_RADIUS:
            return False
        angle = math.atan2(dy, dx) * (180.0 / math.pi)
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
                target_angle = math.atan2(
                    self.origin_y - p[1], self.origin_x - p[0])
                turn = target_angle - p[2]
                while turn > math.pi:
                    turn -= 2.0 * math.pi
                while turn < -math.pi:
                    turn += 2.0 * math.pi
                self.motion.moveTo(0.0, 0.0, turn)
                self.motion.moveTo(0.3, 0.0, 0.0)
        except Exception:
            pass

    # ─── Safety / Overheat ──────────────────
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

        cd = 60 + (self.overheat_count * 30)
        mins = cd // 60
        secs = cd % 60

        if mins > 0:
            phrase = "Motors at {}. I need a {} minute break.".format(
                int(max_t), mins)
        else:
            phrase = "Motors at {}. I need a {} second break.".format(
                int(max_t), secs)

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

    # ─── doSearch ───────────────────────────
    def _do_search(self):
        self._update_local_map()
        self.leds.fadeRGB("AllLeds", 0xFF3300, 0.15)

        try:
            data = self.memory.getData("redBallDetected")
            if data and len(data) >= 2:
                self.motion.stopMove()
                with self.lock:
                    self.last_ball_time = time.time()
                    self.state = STATE_APPROACH
                self.motion.setAngles("HeadPitch", 0.0, 0.2)
                self.motion.setAngles("HeadYaw", 0.0, 0.2)
                self.tts.post.say("Ball found!")
                return
        except Exception:
            pass

        self.search_yaw += self.search_yaw_dir * 0.15
        if self.search_yaw > 1.0:
            self.search_yaw = 1.0
            self.search_yaw_dir = -1.0
        elif self.search_yaw < -1.0:
            self.search_yaw = -1.0
            self.search_yaw_dir = 1.0

        self.motion.setAngles("HeadYaw", self.search_yaw, 0.2)
        self.motion.setAngles("HeadPitch", 0.15, 0.2)

        with self.lock:
            ltime = self.last_ball_time
        if time.time() - ltime > 15.0:
            self.motion.moveToward(0.0, 0.0, 0.2)
        else:
            self.motion.stopMove()

    # ─── doApproach ─────────────────────────
    def _do_approach(self):
        self._update_local_map()
        self.leds.fadeRGB("AllLeds", 0x00FF00, 0.15)
        self.motion.setAngles("HeadPitch", 0.25, 0.3)

        now = time.time()
        if now - self.last_man_on_time > 4.0:
            self.tts.post.say("Man on, man on")
            self.last_man_on_time = now

        found = False
        bx = by = bsz = 0.0
        try:
            data = self.memory.getData("redBallDetected")
            if data and len(data) >= 2:
                found = True
                info = data[1]
                bx = float(info[0])
                by = float(info[1])
                bsz = float(info[2])
        except Exception:
            pass

        if not found:
            with self.lock:
                self.state = STATE_SEARCH
            return

        with self.lock:
            self.last_ball_time = now

        sl = sr = 9.0
        try:
            sl = float(self.memory.getData(
                "Device/SubDeviceList/US/Left/Sensor/Value"))
            sr = float(self.memory.getData(
                "Device/SubDeviceList/US/Right/Sensor/Value"))
        except Exception:
            pass
        min_sonar = min(sl, sr)

        if min_sonar <= COMBAT_DISTANCE and min_sonar < bsz * 5.0 + 0.1:
            self.motion.stopMove()
            with self.lock:
                self.state = STATE_TACKLE
            return

        if bsz > 0.08:
            self.motion.stopMove()
            with self.lock:
                self.state = STATE_ALIGN
            return

        turn = max(-0.6, min(0.6, -bx * 3.0))
        speed = max(0.2, min(0.7, 0.6 * (1.0 - min(bsz / 0.08, 0.8))))
        self.motion.moveToward(speed, 0.0, turn)

    # ─── doAlign ────────────────────────────
    def _do_align(self):
        self._update_local_map()
        self.leds.fadeRGB("AllLeds", 0x00FF00, 0.15)
        self.motion.setAngles("HeadPitch", 0.3, 0.3)

        now = time.time()
        if now - self.last_man_on_time > 4.0:
            self.tts.post.say("Man on, man on")
            self.last_man_on_time = now

        found = False
        bx = bsz = 0.0
        try:
            data = self.memory.getData("redBallDetected")
            if data and len(data) >= 2:
                found = True
                info = data[1]
                bx = float(info[0])
                bsz = float(info[2])
        except Exception:
            pass

        if not found:
            self.motion.stopMove()
            with self.lock:
                self.state = STATE_SEARCH
            return

        with self.lock:
            self.last_ball_time = now

        sl = sr = 9.0
        try:
            sl = float(self.memory.getData(
                "Device/SubDeviceList/US/Left/Sensor/Value"))
            sr = float(self.memory.getData(
                "Device/SubDeviceList/US/Right/Sensor/Value"))
        except Exception:
            pass
        min_sonar = min(sl, sr)

        if min_sonar <= COMBAT_DISTANCE:
            self.motion.stopMove()
            with self.lock:
                self.state = STATE_TACKLE
            return

        if abs(bx) < 0.05 and bsz > 0.10:
            self.motion.stopMove()
            self.tts.post.say("I see the goal")
            with self.lock:
                self.state = STATE_KICK
            return

        if abs(bx) > 0.03:
            lateral = -bx * 0.15
            turn = -bx * 0.8
            self.motion.moveToward(0.1, lateral, turn)
        else:
            self.motion.moveToward(0.15, 0.0, 0.0)

    # ─── doTackle ───────────────────────────
    def _do_tackle(self):
        self.leds.fadeRGB("AllLeds", 0xFF0000, 0.15)
        try:
            self.tts.post.say("Pushing!")
            self.motion.setStiffnesses("Body", 1.0)
            self.posture.goToPosture("StandInit", 0.8)

            self.motion.setAngles(
                ["LShoulderPitch", "RShoulderPitch"], [0.0, 0.0], 0.3)
            self.motion.setAngles(
                ["LKneePitch", "RKneePitch"], [0.4, 0.4], 0.3)

            time.sleep(0.5)

            self.motion.moveToward(1.0, 0.0, 0.0)
            time.sleep(2.0)
            self.motion.stopMove()

            self.motion.setAngles(
                ["LShoulderPitch", "RShoulderPitch"], [1.5, 1.5], 0.4)
            self.posture.goToPosture("StandInit", 0.8)
        except Exception:
            pass

        with self.lock:
            self.state = STATE_SEARCH

    # ─── doKick ─────────────────────────────
    def _do_kick(self):
        self.leds.fadeRGB("AllLeds", 0x0000FF, 0.15)
        self.motion.stopMove()
        self.motion.setAngles("HeadPitch", 0.35, 0.5)
        time.sleep(0.2)

        found = False
        bx = 0.0
        try:
            data = self.memory.getData("redBallDetected")
            if data and len(data) >= 2:
                info = data[1]
                found = True
                bx = float(info[0])
        except Exception:
            pass

        if not found:
            with self.lock:
                self.state = STATE_SEARCH
            return

        side_step_y = -0.04 if bx < -0.02 else 0.04
        kick_leg = "L" if bx < -0.02 else "R"

        self.tts.post.say("Kick!")

        try:
            self.posture.goToPosture("Stand", 0.8)
            time.sleep(0.2)
            self.motion.moveTo(0.0, side_step_y, 0.0)
            time.sleep(0.2)

            if kick_leg == "R":
                hip = "RHipPitch"
                knee = "RKneePitch"
                support_roll = "LHipRoll"
            else:
                hip = "LHipPitch"
                knee = "LKneePitch"
                support_roll = "RHipRoll"

            self.motion.setAngles(support_roll, 0.15, 0.4)
            time.sleep(0.3)
            self.motion.setAngles(hip, -0.4, 0.5)
            time.sleep(0.2)
            self.motion.setAngles(hip, 0.8, 1.0)
            self.motion.setAngles(knee, -0.7, 1.0)
            time.sleep(0.3)

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
# Main Entry Point (from brain_main.cpp)
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
    parser.add_argument("--ip", default="127.0.0.1",
                        help="Robot IP (default: 127.0.0.1)")
    parser.add_argument("--pip", default=None,
                        help="Robot IP (alias for --ip)")
    parser.add_argument("--pport", type=int, default=9559,
                        help="Robot port (default: 9559)")
    parser.add_argument("--trait", default="balanced",
                        help="Player trait: offense, defense, balanced")
    parser.add_argument("--server-ip", default="127.0.0.1",
                        help="BotFC API server IP")
    parser.add_argument("--server-port", type=int, default=5050,
                        help="BotFC API server port")
    args = parser.parse_args()

    robot_ip = args.pip if args.pip else args.ip
    robot_port = args.pport

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("=" * 50)
    print("  Bot FC – Python Brain")
    print("  Robot: {}:{}".format(robot_ip, robot_port))
    print("  Trait: {}".format(args.trait))
    print("  Server: {}:{}".format(args.server_ip, args.server_port))
    print("=" * 50)

    brain = BotFCBrain(robot_ip, robot_port, args.trait,
                       args.server_ip, args.server_port)
    g_brain = brain
    brain.start()

    # Block forever (like the C++ while(true) sleep loop)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        brain.stop()


if __name__ == "__main__":
    main()
