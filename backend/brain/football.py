"""
Football Agent v3 -- On-Robot Striker.

Deploys a complete Python 2.7 agent to the robot that runs natively
with direct NAOqi access at ~20 Hz. The Mac side only handles
deploy, monitor, and stop.

On-robot phases:
  Phase 1: Environment Mapping  -- 360-degree sonar scan, polar vectors
  Phase 2: Boundary Checking    -- world-frame geofence via getRobotPosition
  Phase 3: Ball Seeking         -- ALRedBallDetection, camera->world, 20cm approach
  Phase 4: The Strike           -- weight-shift side-step + kick
  Phase 5: The Tackle           -- Physical push to clear opponents holding ball
  Safety:  fall recovery, dynamic motor temp "half time", stiffness off on exit
"""
import json
import logging
import time
import textwrap

logger = logging.getLogger("brain")

# ==================================================================
# ON-ROBOT AGENT SCRIPT (Python 2.7, pure ASCII)
# ==================================================================
# Uploaded to /tmp/_botfc_agent.py and executed via SSH.
# Uses placeholders for PORT, TRAIT, BACKEND_IP, BACKEND_PORT.

ON_ROBOT_AGENT = textwrap.dedent('''\
#!/usr/bin/env python2.7
# -*- coding: utf-8 -*-
"""Bot FC -- On-Robot Football Striker Agent."""
import sys
sys.path.insert(0, '/opt/aldebaran/lib/python2.7/site-packages')

import time
import math
import json
import signal
import socket
import struct
import base64
import os
from naoqi import ALProxy

# -- Config (injected at deploy) -------------------------------------------
ROBOT_IP = "127.0.0.1"
PORT     = __PORT__
TRAIT    = "__TRAIT__"
BACKEND_IP = "__BACKEND_IP__"
BACKEND_PORT = __BACKEND_PORT__

# -- Proxies ---------------------------------------------------------------
motion   = ALProxy("ALMotion",           ROBOT_IP, PORT)
posture  = ALProxy("ALRobotPosture",     ROBOT_IP, PORT)
mem      = ALProxy("ALMemory",           ROBOT_IP, PORT)
tts      = ALProxy("ALTextToSpeech",     ROBOT_IP, PORT)
leds     = ALProxy("ALLeds",             ROBOT_IP, PORT)
sonar_p  = ALProxy("ALSonar",            ROBOT_IP, PORT)
ball_det = ALProxy("ALRedBallDetection", ROBOT_IP, PORT)
aware    = ALProxy("ALBasicAwareness",   ROBOT_IP, PORT)
life     = ALProxy("ALAutonomousLife",   ROBOT_IP, PORT)

# -- Constants -------------------------------------------------------------
SCAN_STEP_DEG           = 20        # degrees per scan increment
MAX_FIELD_RADIUS        = 2.5       # metres -- geofence radius
BALL_APPROACH_DIST      = 0.20      # metres -- stop 20cm behind ball
KICK_RANGE              = 0.25      # metres -- close enough to kick
MOTOR_TEMP_LIMIT        = 60        # degrees C -- trigger half-time
SONAR_DANGER            = 0.35      # metres -- hard stop
SONAR_CAUTION           = 0.80      # metres -- slow down
COMBAT_DISTANCE         = 0.40      # metres -- Distance to trigger opponent push logic if guarding ball

# -- State -----------------------------------------------------------------
STATE                   = "INIT"
running                 = True
kick_count              = 0
field_map               = []              # list of (radius, theta_rad) polar vectors
origin                  = (0.0, 0.0, 0.0) # starting world position
last_ball_time          = 0.0
ball_history            = []              # [(t, cam_x, cam_y, size), ...]

# Combat & Safety System
overheat_count          = 0
last_overheat_time      = 0.0
break_remaining         = 0             # Sent in telemetry during HALFTIME

# -- Minimal WS Client -----------------------------------------------------
class MiniWSClient:
    def __init__(self, host, port, path):
        self.host = host
        self.port = port
        self.path = path
        self.sock = None
        self.connect()
        
    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(1.0)
            self.sock.connect((self.host, self.port))
            
            # Handshake
            key = base64.b64encode(os.urandom(16))
            req = (
                "GET %s HTTP/1.1\\r\\n"
                "Host: %s:%d\\r\\n"
                "Upgrade: websocket\\r\\n"
                "Connection: Upgrade\\r\\n"
                "Sec-WebSocket-Key: %s\\r\\n"
                "Sec-WebSocket-Version: 13\\r\\n"
                "\\r\\n"
            ) % (self.path, self.host, self.port, key)
            self.sock.sendall(req)
            self.sock.recv(4096)
        except Exception as e:
            self.sock = None
            
    def send(self, text):
        if not self.sock:
            self.connect()
        if not self.sock:
            return
            
        try:
            msg = text.encode('utf-8')
            length = len(msg)
            
            header = bytearray()
            header.append(0x81) # FIN + text frame
            
            if length <= 125:
                header.append(0x80 | length)
            elif length >= 126 and length <= 65535:
                header.append(0x80 | 126)
                header.extend(struct.pack("!H", length))
            else:
                header.append(0x80 | 127)
                header.extend(struct.pack("!Q", length))
                
            mask = bytearray(os.urandom(4))
            header.extend(mask)
            
            masked_msg = bytearray(len(msg))
            for i in range(len(msg)):
                masked_msg[i] = ord(msg[i]) ^ mask[i % 4]
                
            self.sock.sendall(header + masked_msg)
        except Exception:
            self.sock = None

ws_client = MiniWSClient(BACKEND_IP, BACKEND_PORT, "/api/ws/bot")

# -- Helpers ---------------------------------------------------------------

def log(msg):
    ts = time.time() % 10000
    sys.stdout.write("[%.1f] %s\\n" % (ts, msg))
    sys.stdout.flush()

def say(text):
    try:
        tts.post.say(str(text))
    except:
        pass

def set_leds(r, g, b):
    try:
        leds.fadeRGB("AllLeds", r, g, b, 0.15)
    except:
        pass

def get_sonar():
    try:
        l = mem.getData("Device/SubDeviceList/US/Left/Sensor/Value")
        r = mem.getData("Device/SubDeviceList/US/Right/Sensor/Value")
        return l, r
    except:
        return 9.0, 9.0

def any_bumper():
    try:
        keys = [
            "Device/SubDeviceList/LFoot/Bumper/Front/Sensor/Value",
            "Device/SubDeviceList/LFoot/Bumper/Rear/Sensor/Value",
            "Device/SubDeviceList/RFoot/Bumper/Front/Sensor/Value",
            "Device/SubDeviceList/RFoot/Bumper/Rear/Sensor/Value",
        ]
        return any(mem.getData(k) > 0.5 for k in keys)
    except:
        return False

def is_upright():
    try:
        ax = abs(mem.getData("Device/SubDeviceList/InertialSensor/AngleX/Sensor/Value"))
        ay = abs(mem.getData("Device/SubDeviceList/InertialSensor/AngleY/Sensor/Value"))
        return ax < 0.6 and ay < 0.6
    except:
        return True

def get_motor_temp():
    joints = ["LHipPitch", "RHipPitch", "LKneePitch", "RKneePitch"]
    max_t = 0.0
    for j in joints:
        try:
            t = mem.getData("Device/SubDeviceList/%s/Temperature/Sensor/Value" % j)
            if t > max_t:
                max_t = t
        except:
            pass
    return max_t

def get_robot_pos():
    try:
        p = motion.getRobotPosition(True)
        return p[0], p[1], p[2]
    except:
        return 0.0, 0.0, 0.0

def detect_ball():
    try:
        data = mem.getData("redBallDetected")
        if data and len(data) >= 2:
            info = data[1]
            return True, info[0], info[1], info[2]
    except:
        pass
    return False, 0.0, 0.0, 0.0

def predict_ball_pos():
    if len(ball_history) < 3:
        return None
    recent = ball_history[-5:]
    t0, x0, y0, s0 = recent[0]
    t1, x1, y1, s1 = recent[-1]
    dt = t1 - t0
    if dt < 0.05:
        return None
    vx = (x1 - x0) / dt
    vy = (y1 - y0) / dt
    px = x1 + vx * 0.3
    py = y1 + vy * 0.3
    return px, py

# ======================================================================
# PHASE 1: ENVIRONMENT MAPPING (The Scan)
# ======================================================================

def phase1_scan():
    global field_map, origin, STATE
    log("PHASE 1: Environment mapping -- 360-degree scan")
    say("Scanning the pitch.")
    set_leds(255, 255, 0)
    posture.goToPosture("StandInit", 1.0)
    time.sleep(0.5)
    origin = get_robot_pos()
    
    field_map = []
    steps = int(360 / SCAN_STEP_DEG)
    step_rad = math.radians(SCAN_STEP_DEG)

    for i in range(steps):
        theta = i * step_rad
        sl, sr = get_sonar()
        avg_dist = min(sl, sr)
        field_map.append((avg_dist, theta))

        if i < steps - 1:
            motion.moveTo(0.0, 0.0, step_rad)
            time.sleep(0.3)

    rx, ry, rt = get_robot_pos()
    correction = origin[2] - rt
    if abs(correction) > 0.1:
        motion.moveTo(0.0, 0.0, correction)

    STATE = "SEARCH"

# ======================================================================
# PHASE 2: BOUNDARY CHECKING (The Geofence)
# ======================================================================

def is_in_bounds(x, y):
    dx = x - origin[0]
    dy = y - origin[1]
    dist = math.sqrt(dx * dx + dy * dy)

    if dist > MAX_FIELD_RADIUS:
        return False

    if len(field_map) > 0:
        angle = math.atan2(dy, dx)
        if angle < 0:
            angle += 2 * math.pi

        best_idx = 0
        best_diff = 999.0
        for i, (r, t) in enumerate(field_map):
            diff = abs(angle - t)
            if diff > math.pi:
                diff = 2 * math.pi - diff
            if diff < best_diff:
                best_diff = diff
                best_idx = i

        wall_dist = field_map[best_idx][0]
        if dist > wall_dist * 0.85:
            return False

    return True

def enforce_bounds():
    rx, ry, rt = get_robot_pos()
    if not is_in_bounds(rx, ry):
        motion.stopMove()
        set_leds(255, 0, 255)
        
        dx = origin[0] - rx
        dy = origin[1] - ry
        target_angle = math.atan2(dy, dx)
        turn = target_angle - rt
        while turn > math.pi: turn -= 2 * math.pi
        while turn < -math.pi: turn += 2 * math.pi

        motion.moveTo(0.0, 0.0, turn)
        motion.moveTo(0.3, 0.0, 0.0)
        return True
    return False

# ======================================================================
# PHASE 3: COMBAT & BALL SEEKING
# ======================================================================

def do_search():
    global STATE, last_ball_time
    set_leds(255, 50, 0)

    scan_positions = [-1.0, -0.5, 0.0, 0.5, 1.0, 0.5, 0.0, -0.5]
    for yaw in scan_positions:
        if not running: return
        motion.setAngles("HeadYaw", yaw, 0.4)
        motion.setAngles("HeadPitch", 0.15, 0.3)
        time.sleep(0.25)

        found, bx, by, bsz = detect_ball()
        if found:
            last_ball_time = time.time()
            ball_history.append((time.time(), bx, by, bsz))
            motion.setAngles("HeadYaw", 0.0, 0.5)
            say("I see the ball!")
            STATE = "APPROACH"
            return

    motion.setAngles("HeadYaw", 0.0, 0.3)
    motion.moveTo(0.0, 0.0, math.radians(60))

    if time.time() - last_ball_time > 30.0:
        rx, ry, rt = get_robot_pos()
        dx = origin[0] - rx
        dy = origin[1] - ry
        target_angle = math.atan2(dy, dx)
        turn = target_angle - rt
        while turn > math.pi: turn -= 2 * math.pi
        while turn < -math.pi: turn += 2 * math.pi
        motion.moveTo(0.0, 0.0, turn)
        motion.moveTo(0.3, 0.0, 0.0)
        last_ball_time = time.time()

def do_approach():
    global STATE, last_ball_time
    set_leds(0, 255, 0)
    motion.setAngles("HeadPitch", 0.25, 0.3)

    found, bx, by, bsz = detect_ball()
    if not found:
        pred = predict_ball_pos()
        if pred:
            px, py = pred
            motion.moveToward(0.3, 0.0, -px * 2.0)
            time.sleep(0.3)
            motion.stopMove()
        STATE = "SEARCH"
        return

    last_ball_time = time.time()
    ball_history.append((time.time(), bx, by, bsz))
    while len(ball_history) > 20:
        ball_history.pop(0)

    sl, sr = get_sonar()
    min_sonar = min(sl, sr)
    
    # Check if ball is far away but sonar detects something very close 
    # OR sonar hits an object holding the ball
    if min_sonar <= COMBAT_DISTANCE and min_sonar < bsz * 5.0 + 0.1:
        motion.stopMove()
        log("APPROACH: Opponent blocking ball at %.2fm! Engaging combat..." % min_sonar)
        STATE = "TACKLE"
        return

    if min_sonar < SONAR_DANGER:
        motion.stopMove()
        say("Obstacle!")
        STATE = "SEARCH"
        return

    if bsz > 0.08:
        motion.stopMove()
        STATE = "ALIGN"
        return

    turn = max(-0.6, min(0.6, -bx * 3.0))
    speed = max(0.2, min(0.7, 0.6 * (1.0 - min(bsz / 0.08, 0.8))))

    if min_sonar < SONAR_CAUTION:
        factor = (min_sonar - SONAR_DANGER) / (SONAR_CAUTION - SONAR_DANGER)
        speed *= max(0.1, factor)

    motion.moveToward(speed, 0.0, turn)

def do_align():
    global STATE, last_ball_time
    set_leds(0, 255, 100)
    motion.setAngles("HeadPitch", 0.3, 0.3)

    found, bx, by, bsz = detect_ball()
    if not found:
        motion.stopMove()
        STATE = "SEARCH"
        return

    last_ball_time = time.time()
    ball_history.append((time.time(), bx, by, bsz))

    sl, sr = get_sonar()
    min_sonar = min(sl, sr)
    if min_sonar <= COMBAT_DISTANCE:
        motion.stopMove()
        log("ALIGN: Opponent contested ball! Tackling.")
        STATE = "TACKLE"
        return

    if abs(bx) < 0.05 and bsz > 0.10:
        motion.stopMove()
        say("I see the goal, preparing for the strike!")
        STATE = "KICK"
        return

    if abs(bx) > 0.03:
        lateral = -bx * 0.15
        turn = -bx * 0.8
        motion.moveToward(0.1, lateral, turn)
    else:
        motion.moveToward(0.15, 0.0, 0.0)


# ======================================================================
# PHASE 4/5: COMBAT AND STRIKING
# ======================================================================

def do_tackle():
    """Aggressive maneuver to physically push opponent off the ball."""
    global STATE, overheat_count
    
    log("TACKLE: Initiating physical push maneuver!")
    set_leds(255, 0, 0)
    
    try:
        if TRAIT == "offense": say("Pushing off!")
        elif TRAIT == "defense": say("Get back!")
        else: say("My ball!")
        
        # 1. Brace impact - Stiffen up and establish center of gravity
        motion.setStiffnesses("Body", 1.0)
        posture.goToPosture("StandInit", 0.8)
        
        # 2. Lower CoM and actuate Arms horizontally for physical shielding
        motion.setAngles(["LShoulderPitch", "RShoulderPitch"], [0.0, 0.0], 0.3)
        motion.setAngles(["LKneePitch", "RKneePitch"], [0.4, 0.4], 0.3) # Bend knees slightly
        time.sleep(0.5)
        
        # 3. Aggressively push forward in spite of obstacle boundaries
        log("TACKLE: Driving forward.")
        motion.moveToward(1.0, 0.0, 0.0) # Full speed forward
        time.sleep(2.0)
        motion.stopMove()
        
        # 4. Recover standard locomotion pose
        motion.setAngles(["LShoulderPitch", "RShoulderPitch"], [1.5, 1.5], 0.4)
        posture.goToPosture("StandInit", 0.8)
        
    except Exception as e:
        log("TACKLE ERROR: %s" % str(e))
        posture.goToPosture("StandInit", 0.8)

    # After clearing bounds, scan to see if we regained ball
    STATE = "SEARCH"

def do_kick():
    global STATE, kick_count
    set_leds(0, 0, 255)
    motion.stopMove()

    motion.setAngles("HeadPitch", 0.35, 0.5)
    time.sleep(0.2)
    found, bx, by, bsz = detect_ball()
    if not found:
        STATE = "SEARCH"
        return

    if bx < -0.02:
        kick_leg = "L"
        side_step_y = -0.04
    else:
        kick_leg = "R"
        side_step_y = 0.04

    if TRAIT == "offense": say("Goooal!")
    else: say("Kick!")

    try:
        posture.goToPosture("Stand", 0.8)
        time.sleep(0.2)
        motion.moveTo(0.0, side_step_y, 0.0)
        time.sleep(0.2)

        if kick_leg == "R":
            hip, knee = "RHipPitch", "RKneePitch"
            support_roll = "LHipRoll"
        else:
            hip, knee = "LHipPitch", "LKneePitch"
            support_roll = "RHipRoll"

        motion.setAngles(support_roll, 0.15, 0.4)
        time.sleep(0.3)
        motion.setAngles(hip, -0.4, 0.5)
        time.sleep(0.2)
        motion.setAngles(hip, 0.8, 1.0)
        motion.setAngles(knee, -0.7, 1.0)
        time.sleep(0.3)
        posture.goToPosture("Stand", 0.8)
        kick_count += 1
    except Exception as e:
        posture.goToPosture("Stand", 0.8)

    STATE = "RECOVER"

def do_recover():
    global STATE
    set_leds(255, 255, 0)
    posture.goToPosture("Stand", 0.7)
    motion.setAngles("HeadPitch", 0.15, 0.3)
    time.sleep(0.3)

    found, bx, by, bsz = detect_ball()
    if found and bsz > 0.08:
        say("Again!")
        STATE = "APPROACH"
    elif found:
        say("Chasing!")
        STATE = "APPROACH"
    else:
        if TRAIT == "offense": say("What a shot!")
        else: say("Cleared!")
        STATE = "SEARCH"

# ======================================================================
# SAFETY & ROBUSTNESS
# ======================================================================

def safety_check():
    global STATE, running, overheat_count, last_overheat_time, break_remaining

    if any_bumper():
        motion.stopMove()
        say("Contact!")
        time.sleep(0.3)

    if not is_upright():
        set_leds(255, 0, 0)
        motion.stopMove()
        try:
            motion.setStiffnesses("Body", 1.0)
            posture.goToPosture("Stand", 1.0)
        except: pass
        set_leds(0, 255, 0)
        say("I am back up!")
        STATE = "SEARCH"

    # DYNAMIC HALF-TIME OVERHEAT CHECK
    temp = get_motor_temp()
    if temp > MOTOR_TEMP_LIMIT:
        motion.stopMove()
        
        # Calculate penalty multiplier
        time_since_last = time.time() - last_overheat_time
        if time_since_last < 180.0:  # If overheated again within 3 minutes of last break
            overheat_count += 1
        else:
            overheat_count = 0  # Reset if it's been a long time
            
        last_overheat_time = time.time()
        
        # Base 60s + 30s for every sequential overheat
        cooldown_duration = 60 + (overheat_count * 30)
        mins = int(cooldown_duration / 60)
        secs = cooldown_duration % 60
        
        log("SAFETY: Motor temp %.1f C. Overheats: %d. Calling Half-time for %ds." % (temp, overheat_count, cooldown_duration))
        
        if mins > 0:
            say("Motors at %d degrees. I need a %d minute and %d second break to cool down." % (int(temp), mins, secs))
        else:
            say("Motors at %d degrees. I need a %d second break." % (int(temp), secs))
            
        set_leds(255, 165, 0)
        posture.goToPosture("Crouch", 0.8)
        motion.setStiffnesses("Body", 0.0)
        
        # Dynamic Cooldown loop - Push Telemetry while we wait
        STATE = "HALFTIME"
        for remaining in range(cooldown_duration, 0, -1):
            if not running: break
            
            break_remaining = remaining
            ws_client.send(json.dumps({
                "state": STATE,
                "kicks": kick_count,
                "ball_age": 0.0,
                "trait": TRAIT,
                "break_remaining": break_remaining
            }))
            
            if remaining % 30 == 0:
                m_r = int(remaining / 60)
                s_r = remaining % 60
                say("%d minutes, %d seconds remaining" % (m_r, s_r))
                
            time.sleep(1)
            
        break_remaining = 0
            
        if running:
            say("Cooling sequence complete. Back in the game!")
            motion.setStiffnesses("Body", 1.0)
            posture.goToPosture("StandInit", 1.0)
            set_leds(0, 255, 0)
            STATE = "SEARCH"

    enforce_bounds()

def cleanup():
    try: motion.stopMove()
    except: pass
    try: ball_det.unsubscribe("BotFC")
    except: pass
    try: sonar_p.unsubscribe("BotFC")
    except: pass
    try:
        set_leds(0, 0, 0)
        say("Game over. Good match!")
        posture.goToPosture("Crouch", 0.8)
        time.sleep(1)
        motion.setStiffnesses("Body", 0.0)
    except: pass

# -- Signal handler --------------------------------------------------------
def handle_signal(sig, frame):
    global running
    running = False

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT,  handle_signal)

# ======================================================================
# MAIN LOOP
# ======================================================================
log("=== Bot FC Striker Agent Combat Edition (trait=%s) ===" % TRAIT)

try:
    life.setState("disabled")
    aware.pauseAwareness()
    motion.setStiffnesses("Body", 1.0)
    time.sleep(1.5)
    try: motion.setMotionConfig([["ENABLE_FOOT_CONTACT_PROTECTION", True]])
    except: pass
    try: ball_det.subscribe("BotFC", 33, 0.0)
    except: pass
    try: sonar_p.subscribe("BotFC")
    except: pass

    posture.goToPosture("StandInit", 1.0)
    time.sleep(0.5)

    if TRAIT == "offense": say("Let us go! Time to score!")
    elif TRAIT == "defense": say("Holding the line!")
    else: say("Ready to play football!")
    set_leds(0, 255, 0)

    phase1_scan()
    last_ball_time = time.time()

    FSM = {
        "SEARCH":   do_search,
        "APPROACH": do_approach,
        "ALIGN":    do_align,
        "KICK":     do_kick,
        "RECOVER":  do_recover,
        "TACKLE":   do_tackle,
    }

    while running:
        safety_check()

        if STATE != "HALFTIME": # Handled asynchronously in safety check loops
            handler = FSM.get(STATE)
            if handler: handler()
            else: STATE = "SEARCH"

            # WebSockets Telemetry
            payload = json.dumps({
                "state": STATE,
                "kicks": kick_count,
                "ball_age": round(time.time() - last_ball_time, 1) if last_ball_time else -1,
                "trait": TRAIT,
                "break_remaining": 0
            })
            ws_client.send(payload)

            time.sleep(0.05)  # ~20 Hz

except KeyboardInterrupt:
    log("Interrupted by user")
except Exception as e:
    log("FATAL: %s" % str(e))
finally:
    try: ws_client.sock.close()
    except: pass
    cleanup()
    log("=== Agent exited ===")
''')


# ==================================================================
# MAC-SIDE ORCHESTRATOR
# ==================================================================

class FootballAgent:
    """Deploy, monitor, and stop the on-robot football agent.

    Parameters
    ----------
    robot : PepperRobot
        Connected robot instance.
    trait : str
        'offense', 'defense', or 'balanced'.
    backend_ip : str
        Backend WebSockets Server IP.
    backend_port : int
        Backend WebSockets Server Port.
    """

    REMOTE_SCRIPT = "/tmp/_botfc_agent.py"

    def __init__(self, robot, trait="balanced", backend_ip="127.0.0.1", backend_port=5050):
        self.robot = robot
        self.trait = trait
        self.backend_ip = backend_ip
        self.backend_port = backend_port
        self.running = False
        self._channel = None

    def deploy(self):
        """Upload the agent script and start it on the robot."""
        self.robot._ensure_connected()

        script = ON_ROBOT_AGENT.replace(
            "__PORT__", str(self.robot.port)
        ).replace(
            "__TRAIT__", self.trait
        ).replace(
            "__BACKEND_IP__", self.backend_ip
        ).replace(
            "__BACKEND_PORT__", str(self.backend_port)
        )

        logger.info("Deploying striker agent (trait=%s, backend=%s:%d)...", 
                    self.trait, self.backend_ip, self.backend_port)
        
        sftp = self.robot._ssh.open_sftp()
        with sftp.file(self.REMOTE_SCRIPT, "w") as f:
            f.write(script)
        sftp.close()

        cmd = "python2.7 -u %s" % self.REMOTE_SCRIPT
        transport = self.robot._ssh.get_transport()
        self._channel = transport.open_session()
        self._channel.exec_command(cmd)
        self.running = True
        logger.info("Striker agent deployed and running")

    def poll(self):
        """Read stdout/stderr logs from the running agent. Non-blocking."""
        if not self._channel:
            return

        try:
            if self._channel.recv_stderr_ready():
                data = self._channel.recv_stderr(4096).decode()
                for line in data.strip().split("\\n"):
                    if line: logger.debug("[ROBOT STDERR] %s", line)
        except Exception:
            pass

        try:
            if self._channel.recv_ready():
                data = self._channel.recv(4096).decode()
                for line in data.strip().split("\\n"):
                    if line: logger.info("[ROBOT] %s", line)
        except Exception:
            pass

        if self._channel.exit_status_ready():
            self.running = False
            logger.info("Agent exited with code %d",
                        self._channel.recv_exit_status())

    def stop(self):
        """Gracefully stop the on-robot agent."""
        if self._channel and self.running:
            logger.info("Stopping striker agent...")
            try:
                self._channel.send("\\x03")  # SIGINT
            except Exception:
                pass
            try:
                self.robot._ssh.exec_command("pkill -f _botfc_agent.py")
            except Exception:
                pass
            time.sleep(1)
            self.poll()
            self.running = False
            logger.info("Agent stopped")

