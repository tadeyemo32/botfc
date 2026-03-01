"""YAML config loader – replaces C++ Config singleton."""

import os
import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG_PATH = os.path.join(_ROOT, "config", "robot.yaml")


def _load() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


_cfg = _load()

ROBOT_IP: str = _cfg.get("robot", {}).get("ip", "10.85.8.57")
ROBOT_PORT: int = _cfg.get("robot", {}).get("port", 9559)
ROBOT_USER: str = _cfg.get("robot", {}).get("username", "nao")
ROBOT_PASS: str = _cfg.get("robot", {}).get("password", "nao")
NAOQI_PATH: str = _cfg.get("naoqi_path", "/opt/aldebaran/lib/python2.7/site-packages")
SERVER_PORT: int = _cfg.get("server", {}).get("port", 5050)
