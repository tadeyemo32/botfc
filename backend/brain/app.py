"""""
from naoqi import ALProxy
import sys

# Replace with the actual IP address of the robot if not running locally/simulated
ROBOT_IP = "127.0.0.1" 
ROBOT_PORT = 9559

def main():
    try:
        # Initialize the proxy to the TextToSpeech module
        tts = ALProxy("ALTextToSpeech", ROBOT_IP, ROBOT_PORT)
        
        # Make the robot say hello world
        text = "Hello world!"
        print("Sending to robot: " + text)
        tts.say(text)
        
    except Exception as e:
        print("Could not create proxy to ALTextToSpeech")
        print("Error was: ", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
"""""


"""
Bot FC - Flask API Bridge
Connects the React frontend to NAOqi robots via HTTP.
Python 2.7 compatible.
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import json
import os
import sys

# -- NAOqi import (safe fallback if SDK not available) --------
# Safe - won't crash if naoqi isn't installed
try:
    from naoqi import ALProxy
    NAOQI_AVAILABLE = True
except ImportError:
    NAOQI_AVAILABLE = False
    print("[WARN] naoqi not found - running in SIMULATION mode")

app = Flask(__name__)
CORS(app)

# -- Robot config ---------------------------------------------
ROBOTS = {
    "robot1": {"name": "ATLAS", "ip": "169.254.249.203", "port": 9559},
    "robot2": {"name": "ARES",  "ip": None,              "port": 9559},
}

# -- Match state (in-memory) ----------------------------------
match_state = {
    "running": False,
    "robot1":  {"trait": "balanced", "difficulty": "medium"},
    "robot2":  {"trait": "balanced", "difficulty": "medium"},
}

# -- Load traits ----------------------------------------------
TRAITS_PATH = os.path.join(os.path.dirname(__file__), "traits.json")
try:
    with open(TRAITS_PATH, "r") as f:
        TRAITS = json.load(f)
except IOError:
    TRAITS = {}
    print("[WARN] traits.json not found")

# -- NAOqi helper ---------------------------------------------
def get_proxy(robot_id, module):
    """Get a NAOqi proxy for a robot module, or None if unavailable."""
    robot = ROBOTS.get(robot_id)
    if not robot or not robot["ip"] or not NAOQI_AVAILABLE:
        return None
    try:
        return ALProxy(module, robot["ip"], robot["port"])
    except Exception as e:
        print("[ERROR] proxy %s on %s: %s" % (module, robot_id, str(e)))
        return None

def get_battery(robot_id):
    proxy = get_proxy(robot_id, "ALBattery")
    if proxy:
        try:
            return proxy.getBatteryCharge()
        except:
            pass
    return None

def get_robot_name(robot_id):
    proxy = get_proxy(robot_id, "ALSystem")
    if proxy:
        try:
            return proxy.robotName()
        except:
            pass
    return ROBOTS[robot_id]["name"]

# -- Routes ---------------------------------------------------

@app.route("/api/ping")
def ping():
    return jsonify({"ok": True, "naoqi": NAOQI_AVAILABLE})


@app.route("/api/telemetry")
def get_telemetry():
    """Returns battery and connection status for all robots."""
    result = []
    for robot_id, robot in ROBOTS.items():
        online = False
        battery = None
        if robot["ip"] and NAOQI_AVAILABLE:
            battery = get_battery(robot_id)
            online = battery is not None
        result.append({
            "id":      robot_id,
            "name":    robot["name"],
            "ip":      robot["ip"],
            "online":  online,
            "battery": battery,
            "trait":   match_state[robot_id]["trait"],
            "status":  "running" if match_state["running"] else "idle",
        })
    return jsonify(result)


@app.route("/api/set_trait", methods=["POST"])
def set_trait():
    """
    Set the personality trait for a robot.
    Body: { "robot_id": "robot1", "trait": "aggressive", "difficulty": "hard" }
    """
    data = request.get_json()
    robot_id  = data.get("robot_id")
    trait     = data.get("trait", "balanced")
    difficulty = data.get("difficulty", "medium")

    if robot_id not in ROBOTS:
        return jsonify({"error": "Unknown robot_id"}), 400

    match_state[robot_id]["trait"]      = trait
    match_state[robot_id]["difficulty"] = difficulty

    # Apply trait params to robot if connected
    trait_params = TRAITS.get(trait, {})
    proxy = get_proxy(robot_id, "ALMotion")
    if proxy and trait_params:
        try:
            speed = trait_params.get("WalkVelocity", 0.5)
            proxy.setMoveArmsEnabled(True, True)
            # Set max walk speed
            proxy.setMotionConfig([["MAX_STEP_X", speed]])
        except Exception as e:
            print("[WARN] Could not apply trait motion: %s" % str(e))

    return jsonify({
        "ok":       True,
        "robot_id": robot_id,
        "trait":    trait,
        "params":   trait_params,
    })


@app.route("/api/start_match", methods=["POST"])
def start_match():
    """
    Start the match with given player configs.
    Body: {
      "player1": { "name": "ATLAS", "mode": "offense", "difficulty": "medium" },
      "player2": { "name": "ARES",  "mode": "defense", "difficulty": "easy"   }
    }
    """
    data = request.get_json()
    p1 = data.get("player1", {})
    p2 = data.get("player2", {})

    # Map frontend mode -> trait
    mode_to_trait = {
        "offense":  "aggressive",
        "defense":  "defensive",
        "balanced": "balanced",
    }

    match_state["running"]           = True
    match_state["robot1"]["trait"]   = mode_to_trait.get(p1.get("mode", "balanced"), "balanced")
    match_state["robot1"]["difficulty"] = p1.get("difficulty", "medium")
    match_state["robot2"]["trait"]   = mode_to_trait.get(p2.get("mode", "balanced"), "balanced")
    match_state["robot2"]["difficulty"] = p2.get("difficulty", "medium")

    # Wake up robots and announce match start
    for robot_id in ["robot1", "robot2"]:
        tts = get_proxy(robot_id, "ALTextToSpeech")
        motion = get_proxy(robot_id, "ALMotion")
        posture = get_proxy(robot_id, "ALRobotPosture")
        if posture:
            try:
                posture.goToPosture("StandInit", 0.5)
            except Exception as e:
                print("[WARN] posture error: %s" % str(e))
        if tts:
            try:
                tts.say("Match starting. I am ready.")
            except Exception as e:
                print("[WARN] tts error: %s" % str(e))

    return jsonify({
        "ok":      True,
        "running": True,
        "robot1":  match_state["robot1"],
        "robot2":  match_state["robot2"],
    })


@app.route("/api/stop_match", methods=["POST"])
def stop_match():
    """Stop the match and return robots to rest posture."""
    match_state["running"] = False
    for robot_id in ["robot1", "robot2"]:
        posture = get_proxy(robot_id, "ALRobotPosture")
        tts     = get_proxy(robot_id, "ALTextToSpeech")
        if tts:
            try:
                tts.say("Match over.")
            except:
                pass
        if posture:
            try:
                posture.goToPosture("Crouch", 0.5)
            except:
                pass
    return jsonify({"ok": True, "running": False})


if __name__ == "__main__":
    print("Bot FC Brain starting...")
    print("NAOqi available: %s" % NAOQI_AVAILABLE)
    for rid, r in ROBOTS.items():
        print("  %s: %s" % (rid, r["ip"] or "NOT CONFIGURED"))
    app.run(host="0.0.0.0", port=5000, debug=True)