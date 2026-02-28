"""
Bot FC – Flask API server.

Endpoints:
    POST /api/start_match   – Deploy the football agent to the robot
    POST /api/stop_match    – Stop the football agent
    POST /api/set_trait     – Update player personality mid-match
    GET  /api/status        – Get real-time telemetry (state, kicks, ball age)
    GET  /api/health        – Health check
    WS   /api/ws/bot        - WebSocket for the robot agent pushing telemetry
    WS   /api/ws/frontend   - WebSocket for the React frontend receiving live telemetry
"""
import logging
import os
import sys
import threading
import json

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sock import Sock

# Ensure brain modules are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))

from robot import PepperRobot
from football import FootballAgent

# ── Logger ────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR  = os.path.join(BASE_DIR, "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("brain")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(os.path.join(LOG_DIR, "brain.log"))
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(fh)
    logger.addHandler(ch)

# ── Flask app ─────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app)
sock = Sock(app)

_robot = None
_agent = None

# Multithreaded telemetry state
_telemetry_lock = threading.Lock()
_last_telemetry = {
    "state": "IDLE", "trait": "none",
    "running": False, "kicks": 0, "last_ball_seen": -1,
}

_frontend_clients = set()
_clients_lock = threading.Lock()

def _get_robot():
    global _robot
    if _robot is None or not _robot.connected:
        _robot = PepperRobot()
        _robot.connect()
    return _robot

def _broadcast_telemetry():
    """Push telemetry to all connected frontend WebSockets."""
    with _telemetry_lock:
        data_str = json.dumps(_last_telemetry)
    
    with _clients_lock:
        dead_clients = set()
        for client in _frontend_clients:
            try:
                client.send(data_str)
            except Exception:
                dead_clients.add(client)
        
        for dead in dead_clients:
            _frontend_clients.discard(dead)

def _agent_monitor():
    """Background thread that waits for the agent SSH process to end."""
    if _agent:
        while _agent.running:
            # We don't need to poll stdout/stderr for telemetry anymore, 
            # because the bot will push it via WebSockets.
            # But we still run this just to reap the SSH process or log stdout.
            _agent.poll()
            import time; time.sleep(0.5)
        
        # When agent stops, update state
        with _telemetry_lock:
            global _last_telemetry
            _last_telemetry["running"] = False
            _last_telemetry["state"] = "IDLE"
        _broadcast_telemetry()


# ── WebSockets ────────────────────────────────────────────────────────

@sock.route("/api/ws/bot")
def ws_bot(ws):
    """The robot connects here and streams telemetry as JSON strings."""
    logger.info("Bot connected to WebSocket")
    while True:
        try:
            data = ws.receive()
            if data:
                try:
                    telemetry = json.loads(data)
                    with _telemetry_lock:
                        global _last_telemetry
                        _last_telemetry.update(telemetry)
                        _last_telemetry["running"] = True
                    
                    _broadcast_telemetry()
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            logger.warning("Bot WebSocket disconnected: %s", e)
            with _telemetry_lock:
                 _last_telemetry["running"] = False
            _broadcast_telemetry()
            break

@sock.route("/api/ws/frontend")
def ws_frontend(ws):
    """The frontend connects here to receive low-latency telemetry."""
    logger.info("Frontend connected to WebSocket")
    with _clients_lock:
        _frontend_clients.add(ws)
    try:
        # Send initial state immediately
        with _telemetry_lock:
            ws.send(json.dumps(_last_telemetry))
        
        while True:
            # Keep connection alive, wait for client disconnect
            data = ws.receive()
    except Exception:
        pass
    finally:
        with _clients_lock:
            _frontend_clients.discard(ws)
            logger.info("Frontend WebSocket disconnected")


# ── Routes ────────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "connected": _robot is not None and _robot.connected,
    })


@app.route("/api/start_match", methods=["POST"])
def start_match():
    global _agent

    if _agent and _agent.running:
        return jsonify({"error": "Match already running"}), 400

    data = request.get_json(silent=True) or {}
    trait = data.get("trait", "balanced")

    try:
        robot = _get_robot()
        
        # Pass backend IP implicitly or fetch from config
        backend_ip = "127.0.0.1"
        try:
            # Just grab the active LAN IP for the backend to pass to the bot.
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            backend_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

        _agent = FootballAgent(robot, trait=trait, backend_ip=backend_ip, backend_port=5050)
        _agent.deploy()

        # Update local state
        with _telemetry_lock:
            _last_telemetry["running"] = True
            _last_telemetry["trait"] = trait
            _last_telemetry["state"] = "INIT"
        _broadcast_telemetry()

        # Start process monitor thread
        threading.Thread(target=_agent_monitor, daemon=True).start()

        logger.info("Match started with trait=%s, given backend IP=%s", trait, backend_ip)
        return jsonify({"status": "started", "trait": trait})

    except Exception as e:
        logger.error("Failed to start match: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/stop_match", methods=["POST"])
def stop_match():
    if _agent and _agent.running:
        _agent.stop()
        with _telemetry_lock:
            _last_telemetry["running"] = False
            _last_telemetry["state"] = "IDLE"
        _broadcast_telemetry()
        return jsonify({"status": "stopped"})
    return jsonify({"status": "not_running"})


@app.route("/api/set_trait", methods=["POST"])
def set_trait():
    """To change trait mid-match, we restart with new config."""
    global _agent

    data = request.get_json(silent=True) or {}
    trait = data.get("trait", "balanced")

    if _agent and _agent.running:
        _agent.stop()
        import time; time.sleep(1)
        robot = _get_robot()
        
        backend_ip = "127.0.0.1"
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            backend_ip = s.getsockname()[0]
            s.close()
        except:
            pass

        _agent = FootballAgent(robot, trait=trait, backend_ip=backend_ip, backend_port=5050)
        _agent.deploy()
        
        with _telemetry_lock:
            _last_telemetry["running"] = True
            _last_telemetry["trait"] = trait
            _last_telemetry["state"] = "INIT"
        _broadcast_telemetry()

        threading.Thread(target=_agent_monitor, daemon=True).start()
        return jsonify({"status": "restarted", "trait": trait})

    return jsonify({"status": "stored", "trait": trait,
                    "note": "Will apply when match starts"})


@app.route("/api/status", methods=["GET"])
def status():
    """Fallback standard HTTP GET for status."""
    with _telemetry_lock:
        return jsonify(_last_telemetry)

# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("=== Bot FC API server starting ===")
    app.run(host="0.0.0.0", port=5050, debug=False)
