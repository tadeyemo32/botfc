"""
PepperRobot – robust SSH-based interface to control Pepper the robot.

Every method generates a small NAOqi Python 2.7 script, uploads it to
the robot via SFTP, and executes it over SSH.  This avoids the Linux-only
pynaoqi .so limitation on macOS ARM.

Usage:
    from brain.robot import PepperRobot
    robot = PepperRobot()
    robot.connect()
    robot.say("Hello world!")
    robot.move_forward(0.3)
    robot.disconnect()
"""
import logging
import os
import socket
import textwrap

import paramiko
import yaml

logger = logging.getLogger("brain")

# ── Load config from YAML ─────────────────────────────────────────────
_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "robot.yaml"
)

def _load_config():
    """Read config/robot.yaml; return dict with defaults as fallback."""
    defaults = {
        "robot": {"ip": "10.85.8.57", "port": 9559,
                  "username": "nao", "password": "Na0Na0"},
        "naoqi_path": "/opt/aldebaran/lib/python2.7/site-packages",
    }
    try:
        with open(_CONFIG_PATH, "r") as f:
            cfg = yaml.safe_load(f) or {}
        # Merge
        for k, v in defaults.items():
            cfg.setdefault(k, v)
        if isinstance(cfg.get("robot"), dict):
            for k, v in defaults["robot"].items():
                cfg["robot"].setdefault(k, v)
        logger.debug("Config loaded from %s", _CONFIG_PATH)
        return cfg
    except Exception as e:
        logger.warning("Could not load %s (%s), using defaults", _CONFIG_PATH, e)
        return defaults

_CFG = _load_config()

DEFAULT_IP   = _CFG["robot"]["ip"]
DEFAULT_PORT = _CFG["robot"]["port"]
DEFAULT_USER = _CFG["robot"]["username"]
DEFAULT_PASS = _CFG["robot"]["password"]

NAOQI_PATH    = _CFG.get("naoqi_path", "/opt/aldebaran/lib/python2.7/site-packages")
REMOTE_SCRIPT = "/tmp/_brain_cmd.py"


# ── Exceptions ────────────────────────────────────────────────────────

class RobotConnectionError(Exception):
    """Raised when the SSH connection to the robot fails."""

class RobotCommandError(Exception):
    """Raised when a command executed on the robot fails."""


# ── Main class ────────────────────────────────────────────────────────

class PepperRobot:
    """High-level, SSH-based interface to SoftBank Pepper.

    Parameters
    ----------
    ip : str
        Robot IP address.
    port : int
        NAOqi port on the robot.
    username / password : str
        SSH credentials.
    """

    def __init__(self, ip=DEFAULT_IP, port=DEFAULT_PORT,
                 username=DEFAULT_USER, password=DEFAULT_PASS):
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password
        self._ssh = None

    # ── connection management ─────────────────────────────────────────

    def connect(self):
        """Open (or re-open) the SSH session to the robot."""
        if self._ssh is not None:
            self.disconnect()

        logger.info("Connecting to robot at %s …", self.ip)
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=self.ip,
                username=self.username,
                password=self.password,
                timeout=10,
            )
            self._ssh = ssh
            logger.info("Connected to robot at %s", self.ip)
        except paramiko.AuthenticationException as e:
            logger.error("SSH auth failed for %s@%s: %s", self.username, self.ip, e)
            raise RobotConnectionError("Authentication failed: {}".format(e))
        except (socket.timeout, socket.error) as e:
            logger.error("Network error connecting to %s: %s", self.ip, e)
            raise RobotConnectionError("Network error: {}".format(e))
        except Exception as e:
            logger.exception("Unexpected connection error: %s", e)
            raise RobotConnectionError(str(e))

    def disconnect(self):
        """Close the SSH session."""
        if self._ssh:
            self._ssh.close()
            self._ssh = None
            logger.debug("SSH connection closed")

    @property
    def connected(self):
        return self._ssh is not None and self._ssh.get_transport() is not None

    def _ensure_connected(self):
        if not self.connected:
            self.connect()

    # ── low-level execution ───────────────────────────────────────────

    def _run_on_robot(self, py_code, timeout=30):
        """Upload *py_code* to the robot and execute with Python 2.7.

        Returns (stdout, stderr, exit_status).
        Raises RobotCommandError on non-zero exit.
        """
        self._ensure_connected()
        full_code = textwrap.dedent("""\
            import sys
            sys.path.insert(0, '{naoqi}')
            {code}
        """).format(naoqi=NAOQI_PATH, code=py_code)

        # Upload via SFTP
        try:
            sftp = self._ssh.open_sftp()
            with sftp.file(REMOTE_SCRIPT, "w") as f:
                f.write(full_code)
            sftp.close()
        except IOError as e:
            logger.error("SFTP upload failed: %s", e)
            raise RobotCommandError("SFTP upload failed: {}".format(e))

        # Execute
        logger.debug("Executing remote script …")
        stdin, stdout, stderr = self._ssh.exec_command(
            "python2.7 {}".format(REMOTE_SCRIPT), timeout=timeout
        )
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
        exit_status = stdout.channel.recv_exit_status()

        if out:
            logger.debug("Robot stdout: %s", out)
        if err and exit_status != 0:
            logger.error("Robot stderr (exit %d): %s", exit_status, err)
        elif err:
            logger.debug("Robot stderr (info): %s", err)

        if exit_status != 0:
            raise RobotCommandError(
                "Exit {}: {}".format(exit_status, err or out)
            )
        return out, err, exit_status

    def _naoqi(self, service, method, *args):
        """Call *service*.*method*(*args) on the robot via NAOqi proxy."""
        arg_str = ", ".join(repr(a) for a in args)
        code = (
            "from naoqi import ALProxy\n"
            "proxy = ALProxy('{svc}', '127.0.0.1', {port})\n"
            "result = proxy.{method}({args})\n"
            "if result is not None:\n"
            "    print(result)\n"
        ).format(svc=service, port=self.port, method=method, args=arg_str)
        return self._run_on_robot(code)

    # ══════════════════════════════════════════════════════════════════
    #  HIGH-LEVEL ACTIONS
    # ══════════════════════════════════════════════════════════════════

    # ── Speech ────────────────────────────────────────────────────────

    def say(self, text, speed=100, shape=100):
        """Make the robot say *text* with animated body language."""
        logger.info("say: %s", text)
        escaped = text.replace("\\", "\\\\").replace("'", "\\'")
        code = (
            "from naoqi import ALProxy\n"
            "tts = ALProxy('ALAnimatedSpeech', '127.0.0.1', {port})\n"
            "tts.say('\\\\RSPD={speed}\\\\ \\\\VCT={shape} \\\\{text}')\n"
        ).format(port=self.port, speed=speed, shape=shape, text=escaped)
        self._run_on_robot(code)

    def set_volume(self, volume):
        """Set speaker volume (0-100)."""
        logger.info("set_volume: %d", volume)
        self._naoqi("ALAudioDevice", "setOutputVolume", int(volume))

    # ── Posture ───────────────────────────────────────────────────────

    def stand(self):
        """Stand up (StandInit posture)."""
        logger.info("stand")
        self._naoqi("ALRobotPosture", "goToPosture", "Stand", 1.0)

    def rest(self):
        """Crouch / rest position."""
        logger.info("rest")
        self._naoqi("ALRobotPosture", "goToPosture", "Crouch", 1.0)

    # ── Movement ──────────────────────────────────────────────────────

    def move_forward(self, speed):
        """Walk forward (positive) or backward (negative)."""
        logger.info("move_forward: %s", speed)
        self._naoqi("ALMotion", "move", float(speed), 0.0, 0.0)

    def turn(self, speed):
        """Turn in place. Positive = right, negative = left."""
        logger.info("turn: %s", speed)
        self._naoqi("ALMotion", "move", 0.0, 0.0, float(speed))

    def move_to(self, x, y, theta):
        """Walk to a relative position (x forward, y left, theta rotation)."""
        logger.info("move_to: x=%.2f y=%.2f θ=%.2f", x, y, theta)
        self._naoqi("ALMotion", "moveTo", float(x), float(y), float(theta))

    def stop(self):
        """Stop all movement immediately."""
        logger.info("stop")
        self._naoqi("ALMotion", "stopMove")

    # ── Head ──────────────────────────────────────────────────────────

    def head_up(self):
        """Tilt head up."""
        logger.info("head_up")
        self._naoqi("ALMotion", "setAngles", "HeadPitch", -0.4, 0.2)

    def head_down(self):
        """Tilt head down (look at the ball)."""
        logger.info("head_down")
        self._naoqi("ALMotion", "setAngles", "HeadPitch", 0.46, 0.2)

    def head_default(self):
        """Head to neutral position."""
        logger.info("head_default")
        self._naoqi("ALMotion", "setAngles", "HeadPitch", 0.0, 0.2)

    def head_turn(self, yaw):
        """Turn head left (positive) / right (negative) in radians."""
        logger.info("head_turn: %.2f", yaw)
        self._naoqi("ALMotion", "setAngles", "HeadYaw", float(yaw), 0.2)

    # ── Hands ─────────────────────────────────────────────────────────

    def open_hand(self, side="right"):
        """Open left or right hand."""
        logger.info("open_hand: %s", side)
        joint = "RHand" if side == "right" else "LHand"
        self._naoqi("ALMotion", "setAngles", joint, 1.0, 0.2)

    def close_hand(self, side="right"):
        """Close left or right hand."""
        logger.info("close_hand: %s", side)
        joint = "RHand" if side == "right" else "LHand"
        self._naoqi("ALMotion", "setAngles", joint, 0.0, 0.2)

    # ── Joints (low-level) ────────────────────────────────────────────

    def set_joints(self, names, angles, speed=0.2):
        """Set joint angles. *names* and *angles* are parallel lists."""
        logger.info("set_joints: %s → %s (speed %.2f)", names, angles, speed)
        self._naoqi("ALMotion", "setAngles", list(names), list(angles), float(speed))

    # ── LEDs ──────────────────────────────────────────────────────────

    def set_leds(self, r, g, b):
        """Set all eye LEDs to an RGB colour (0-255 each)."""
        logger.info("set_leds: (%d, %d, %d)", r, g, b)
        self._naoqi("ALLeds", "fadeRGB", "AllLeds", int(r), int(g), int(b), 1.0)

    def leds_off(self):
        """Turn off all LEDs."""
        self.set_leds(0, 0, 0)

    # ── Animations ────────────────────────────────────────────────────

    def play_animation(self, name):
        """Run a built-in gesture animation by name (e.g. 'Hey_1')."""
        logger.info("play_animation: %s", name)
        code = (
            "from naoqi import ALProxy\n"
            "ap = ALProxy('ALAnimationPlayer', '127.0.0.1', {port})\n"
            "ap.run('animations/[posture]/Gestures/{anim}', _async=False)\n"
        ).format(port=self.port, anim=name)
        self._run_on_robot(code)

    def mood_happy(self):
        """Play a happy animation."""
        logger.info("mood_happy")
        code = (
            "from naoqi import ALProxy\n"
            "ap = ALProxy('ALAnimationPlayer', '127.0.0.1', {port})\n"
            "ap.run('animations/Stand/Emotions/Positive/Happy_4', _async=False)\n"
        ).format(port=self.port)
        self._run_on_robot(code)

    def greet(self):
        """Wave hello with a random greeting animation."""
        logger.info("greet")
        code = (
            "import random\n"
            "from naoqi import ALProxy\n"
            "ap = ALProxy('ALAnimationPlayer', '127.0.0.1', {port})\n"
            "anim = random.choice(['Hey_1','Hey_3','Hey_4','Hey_6'])\n"
            "ap.run('animations/[posture]/Gestures/' + anim, _async=False)\n"
        ).format(port=self.port)
        self._run_on_robot(code)

    # ── Awareness ─────────────────────────────────────────────────────

    def awareness_on(self):
        """Resume basic awareness (look for people, etc.)."""
        logger.info("awareness_on")
        self._naoqi("ALBasicAwareness", "resumeAwareness")

    def awareness_off(self):
        """Pause basic awareness — useful during football play."""
        logger.info("awareness_off")
        self._naoqi("ALBasicAwareness", "pauseAwareness")

    # ── Autonomous life ───────────────────────────────────────────────

    def autonomous_life_on(self):
        """Enable autonomous life (interactive mode)."""
        logger.info("autonomous_life_on")
        self._naoqi("ALAutonomousLife", "setState", "interactive")

    def autonomous_life_off(self):
        """Disable autonomous life — full manual control."""
        logger.info("autonomous_life_off")
        self._naoqi("ALAutonomousLife", "setState", "disabled")
        self.stand()

    # ── Safety ────────────────────────────────────────────────────────

    def set_security_distance(self, distance=0.05):
        """Set obstacle avoidance distance in metres."""
        logger.info("set_security_distance: %.2f m", distance)
        self._naoqi("ALMotion", "setOrthogonalSecurityDistance", float(distance))

    # ── Tracking ──────────────────────────────────────────────────────

    def track_ball(self):
        """Start tracking a red ball with both arms."""
        logger.info("track_ball")
        code = (
            "from naoqi import ALProxy\n"
            "tr = ALProxy('ALTracker', '127.0.0.1', {port})\n"
            "tr.registerTarget('RedBall', 0.06)\n"
            "tr.setMode('Move')\n"
            "tr.track('RedBall')\n"
        ).format(port=self.port)
        self._run_on_robot(code)

    def stop_tracking(self):
        """Stop all tracking."""
        logger.info("stop_tracking")
        code = (
            "from naoqi import ALProxy\n"
            "tr = ALProxy('ALTracker', '127.0.0.1', {port})\n"
            "tr.stopTracker()\n"
            "tr.unregisterAllTargets()\n"
            "tr.setEffector('None')\n"
        ).format(port=self.port)
        self._run_on_robot(code)

    # ── System ────────────────────────────────────────────────────────

    def battery_status(self):
        """Return battery charge percentage (int)."""
        out, _, _ = self._naoqi("ALBattery", "getBatteryCharge")
        try:
            level = int(out)
        except ValueError:
            level = -1
        logger.info("battery: %d%%", level)
        return level

    def get_name(self):
        """Return the robot's name."""
        out, _, _ = self._naoqi("ALSystem", "robotName")
        logger.info("Robot name: %s", out)
        return out.strip()

    # ── Kick (football!) ──────────────────────────────────────────────

    def kick(self, side="right"):
        """Perform a balanced kick: shift weight to support leg, then swing.

        This is the Phase-3 "safe kick" with weight transfer.
        """
        logger.info("kick: %s", side)
        if side == "right":
            hip, knee = "RHipPitch", "RKneePitch"
            support_hip, support_roll = "LHipPitch", "LHipRoll"
        else:
            hip, knee = "LHipPitch", "LKneePitch"
            support_hip, support_roll = "RHipPitch", "RHipRoll"

        code = (
            "import time\n"
            "from naoqi import ALProxy\n"
            "motion = ALProxy('ALMotion', '127.0.0.1', {port})\n"
            "posture = ALProxy('ALRobotPosture', '127.0.0.1', {port})\n"
            "# 1. Stand stable\n"
            "posture.goToPosture('Stand', 0.8)\n"
            "time.sleep(0.3)\n"
            "# 2. Shift weight to support leg\n"
            "motion.setAngles('{support_roll}', 0.15, 0.3)\n"
            "time.sleep(0.4)\n"
            "# 3. Wind up kicking leg\n"
            "motion.setAngles('{hip}', -0.3, 0.4)\n"
            "time.sleep(0.3)\n"
            "# 4. Kick forward fast\n"
            "motion.setAngles('{hip}', 0.7, 1.0)\n"
            "motion.setAngles('{knee}', -0.6, 1.0)\n"
            "time.sleep(0.5)\n"
            "# 5. Recover\n"
            "posture.goToPosture('Stand', 0.8)\n"
        ).format(port=self.port, hip=hip, knee=knee,
                 support_hip=support_hip, support_roll=support_roll)
        self._run_on_robot(code, timeout=20)

    # ── Safety (Phase 1) ──────────────────────────────────────────────

    def set_stiffness(self, body_part="Body", value=1.0):
        """Set motor stiffness. 1.0 = full, 0.0 = limp."""
        logger.info("set_stiffness: %s → %.1f", body_part, value)
        self._naoqi("ALMotion", "setStiffnesses", body_part, float(value))

    def is_upright(self):
        """Check if robot is approximately upright via IMU.

        Returns True if upright, False if fallen.
        """
        code = (
            "import json\n"
            "from naoqi import ALProxy\n"
            "mem = ALProxy('ALMemory', '127.0.0.1', {port})\n"
            "ax = mem.getData('Device/SubDeviceList/InertialSensor/AngleX/Sensor/Value')\n"
            "ay = mem.getData('Device/SubDeviceList/InertialSensor/AngleY/Sensor/Value')\n"
            "print(json.dumps({{'ax': ax, 'ay': ay}}))\n"
        ).format(port=self.port)
        import json as _json
        out, _, _ = self._run_on_robot(code)
        try:
            data = _json.loads(out)
            ax, ay = abs(data["ax"]), abs(data["ay"])
            upright = ax < 0.6 and ay < 0.6  # ~35 degrees tolerance
            logger.debug("IMU ax=%.2f ay=%.2f upright=%s", ax, ay, upright)
            return upright
        except Exception:
            logger.warning("Could not read IMU, assuming upright")
            return True

    def get_posture(self):
        """Return current posture name (e.g. 'Standing', 'Sitting')."""
        out, _, _ = self._naoqi("ALRobotPosture", "getPostureFamily")
        logger.debug("Posture: %s", out)
        return out.strip()

    def recover_from_fall(self):
        """Detect fall direction and get back up."""
        logger.info("recover_from_fall")
        code = (
            "from naoqi import ALProxy\n"
            "motion = ALProxy('ALMotion', '127.0.0.1', {port})\n"
            "posture = ALProxy('ALRobotPosture', '127.0.0.1', {port})\n"
            "motion.setStiffnesses('Body', 1.0)\n"
            "posture.goToPosture('Stand', 0.8)\n"
        ).format(port=self.port)
        self._run_on_robot(code, timeout=20)

    # ── Sensors (Phase 2) ─────────────────────────────────────────────

    def get_sonar(self):
        """Read front sonar distances. Returns (left_m, right_m)."""
        code = (
            "import json\n"
            "from naoqi import ALProxy\n"
            "mem = ALProxy('ALMemory', '127.0.0.1', {port})\n"
            "left = mem.getData('Device/SubDeviceList/US/Left/Sensor/Value')\n"
            "right = mem.getData('Device/SubDeviceList/US/Right/Sensor/Value')\n"
            "print(json.dumps({{'left': left, 'right': right}}))\n"
        ).format(port=self.port)
        import json as _json
        out, _, _ = self._run_on_robot(code)
        try:
            data = _json.loads(out)
            logger.debug("Sonar L=%.2fm R=%.2fm", data["left"], data["right"])
            return data["left"], data["right"]
        except Exception:
            logger.warning("Could not read sonar")
            return 999.0, 999.0

    def detect_red_ball(self):
        """Check if red ball is visible. Returns (found, x, y, size) or None.

        Uses ALRedBallDetection via ALMemory.
        """
        code = (
            "import json, time\n"
            "from naoqi import ALProxy\n"
            "tracker = ALProxy('ALRedBallDetection', '127.0.0.1', {port})\n"
            "mem = ALProxy('ALMemory', '127.0.0.1', {port})\n"
            "tracker.subscribe('BrainBallDetect', 500, 0.0)\n"
            "time.sleep(0.6)\n"
            "data = mem.getData('redBallDetected')\n"
            "tracker.unsubscribe('BrainBallDetect')\n"
            "if data and len(data) > 0:\n"
            "    info = data[1]\n"
            "    print(json.dumps({{'found': True, 'x': info[0], 'y': info[1], 'size': info[2]}}))\n"
            "else:\n"
            "    print(json.dumps({{'found': False}}))\n"
        ).format(port=self.port)
        import json as _json
        out, _, _ = self._run_on_robot(code)
        try:
            data = _json.loads(out)
            if data.get("found"):
                logger.info("Ball detected at x=%.3f y=%.3f size=%.3f",
                            data["x"], data["y"], data["size"])
            else:
                logger.debug("No ball detected")
            return data
        except Exception:
            logger.warning("Ball detection parse error")
            return {"found": False}

    def get_bumpers(self):
        """Read foot bumper states. Returns dict with left/right pressed bools."""
        code = (
            "import json\n"
            "from naoqi import ALProxy\n"
            "mem = ALProxy('ALMemory', '127.0.0.1', {port})\n"
            "lf = mem.getData('Device/SubDeviceList/LFoot/Bumper/Front/Sensor/Value')\n"
            "lb = mem.getData('Device/SubDeviceList/LFoot/Bumper/Rear/Sensor/Value')\n"
            "rf = mem.getData('Device/SubDeviceList/RFoot/Bumper/Front/Sensor/Value')\n"
            "rb = mem.getData('Device/SubDeviceList/RFoot/Bumper/Rear/Sensor/Value')\n"
            "print(json.dumps({{'lf': lf, 'lb': lb, 'rf': rf, 'rb': rb}}))\n"
        ).format(port=self.port)
        import json as _json
        out, _, _ = self._run_on_robot(code)
        try:
            data = _json.loads(out)
            any_pressed = any(v > 0.5 for v in data.values())
            if any_pressed:
                logger.info("Bumper pressed: %s", data)
            return data
        except Exception:
            return {"lf": 0, "lb": 0, "rf": 0, "rb": 0}

    # ── Context manager support ───────────────────────────────────────

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()
        return False
