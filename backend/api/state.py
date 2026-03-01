"""Thread-safe async state store – replaces HttpServer member variables."""

import asyncio
from typing import Optional

from fastapi import WebSocket


class State:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.telemetry: dict = {
            "state": "IDLE",
            "trait": "none",
            "running": False,
            "kicks": 0,
            "last_ball_seen": -1,
            "break_remaining": 0,
            "robot_connected": False,
            "battery_pct": -1,
        }
        self.latest_frame: str = ""
        self.robot_ip: str = ""
        self.pending_command: Optional[dict] = None

        self.frontend_sessions: set[WebSocket] = set()
        self.camera_sessions: set[WebSocket] = set()

    # ── Telemetry ─────────────────────────────────────────────

    async def update_telemetry(self, patch: dict):
        async with self._lock:
            self.telemetry.update(patch)

    async def get_telemetry(self) -> dict:
        async with self._lock:
            return dict(self.telemetry)

    # ── Camera frame ──────────────────────────────────────────

    async def set_frame(self, frame_json: str):
        async with self._lock:
            self.latest_frame = frame_json

    async def get_frame(self) -> str:
        async with self._lock:
            return self.latest_frame

    # ── Robot IP ──────────────────────────────────────────────

    async def set_robot_ip(self, ip: str):
        async with self._lock:
            self.robot_ip = ip

    async def get_robot_ip(self) -> str:
        async with self._lock:
            return self.robot_ip

    # ── Bot command channel ───────────────────────────────────

    async def queue_command(self, cmd: dict):
        async with self._lock:
            self.pending_command = cmd

    async def pop_command(self) -> dict:
        async with self._lock:
            if self.pending_command is None:
                return {"action": None}
            cmd = self.pending_command
            self.pending_command = None
            return cmd

    # ── Session management ────────────────────────────────────

    async def add_frontend(self, ws: WebSocket):
        async with self._lock:
            self.frontend_sessions.add(ws)

    async def remove_frontend(self, ws: WebSocket):
        async with self._lock:
            self.frontend_sessions.discard(ws)

    async def add_camera(self, ws: WebSocket):
        async with self._lock:
            self.camera_sessions.add(ws)

    async def remove_camera(self, ws: WebSocket):
        async with self._lock:
            self.camera_sessions.discard(ws)

    # ── Broadcast helpers ─────────────────────────────────────
    # Copy the session set under the lock, then release it before doing
    # async network IO.  Holding asyncio.Lock across awaited sends blocks
    # all other coroutines that need the lock (session add/remove).

    async def broadcast_telemetry(self, msg: str):
        async with self._lock:
            sessions = frozenset(self.frontend_sessions)
        dead = []
        for ws in sessions:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self.frontend_sessions.discard(ws)

    async def broadcast_frame(self, frame_json: str):
        async with self._lock:
            sessions = frozenset(self.camera_sessions)
        dead = []
        for ws in sessions:
            try:
                await ws.send_text(frame_json)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self.camera_sessions.discard(ws)


store = State()
