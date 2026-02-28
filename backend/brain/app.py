"""
Brain – entry point.

Modes:
  python app.py              → Test "Hello world!"
  python app.py --serve      → Start Flask API on :5050
  python app.py --football   → Deploy football agent directly
  python app.py --football --trait=offense
"""
import logging
import os
import sys
import time

# ── Paths ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR  = os.path.join(BASE_DIR, "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ── Logger setup ──────────────────────────────────────────────────────
LOG_FILE = os.path.join(LOG_DIR, "brain.log")

logger = logging.getLogger("brain")
logger.setLevel(logging.DEBUG)

fh = logging.FileHandler(LOG_FILE)
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

# ── Imports (after logger is set up) ──────────────────────────────────
from robot import PepperRobot
from football import FootballAgent

# ── Main ──────────────────────────────────────────────────────────────

def test_hello():
    """Quick connectivity test."""
    logger.info("=== Brain test: Hello World ===")
    with PepperRobot() as robot:
        robot.say("Hello world!")
    logger.info("Test complete")


def run_football(trait="balanced"):
    """Deploy the football agent to the robot and monitor output."""
    logger.info("=== Football mode (trait=%s) ===", trait)
    with PepperRobot() as robot:
        agent = FootballAgent(robot, trait=trait)
        agent.deploy()
        try:
            while agent.running:
                status = agent.poll()
                time.sleep(0.5)
        except KeyboardInterrupt:
            logger.info("Ctrl+C – stopping agent")
        finally:
            agent.stop()


if __name__ == "__main__":
    if "--serve" in sys.argv:
        sys.path.insert(0, os.path.join(BASE_DIR, "..", "api"))
        from server import app
        logger.info("=== Bot FC API server starting ===")
        app.run(host="0.0.0.0", port=5050, debug=False)

    elif "--football" in sys.argv:
        trait = "balanced"
        for arg in sys.argv:
            if arg.startswith("--trait="):
                trait = arg.split("=")[1]
        run_football(trait)

    else:
        test_hello()
