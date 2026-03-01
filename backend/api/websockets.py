"""WebSocket endpoints – replaces WsSession.cpp role-based handlers."""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .decision import compute_decision
from .state import store

router = APIRouter()
log = logging.getLogger("botfc.ws")


@router.websocket("/ws/bot")
async def ws_bot(ws: WebSocket):
    """Robot sends telemetry JSON. Server updates store and broadcasts to frontends."""
    await ws.accept()
    log.info("Bot telemetry connected: %s", ws.client)
    try:
        while True:
            msg = await ws.receive_text()
            try:
                j = json.loads(msg)
                await store.update_telemetry(j)
                telem = await store.get_telemetry()
                telem["cpp_action"] = compute_decision(telem)
                await store.broadcast_telemetry(json.dumps(telem))
            except json.JSONDecodeError:
                pass
    except Exception:
        pass
    finally:
        log.info("Bot telemetry disconnected: %s", ws.client)


@router.websocket("/ws/bot_camera")
async def ws_bot_camera(ws: WebSocket):
    """Robot sends camera frame JSON. Server stores + relays to camera viewers."""
    await ws.accept()
    log.info("Bot camera connected: %s", ws.client)
    try:
        while True:
            msg = await ws.receive_text()
            await store.set_frame(msg)
            await store.broadcast_frame(msg)
    except Exception:
        pass
    finally:
        log.info("Bot camera disconnected: %s", ws.client)


@router.websocket("/ws/frontend")
async def ws_frontend(ws: WebSocket):
    """Browser connects to receive telemetry updates."""
    await ws.accept()
    await store.add_frontend(ws)
    telem = await store.get_telemetry()
    try:
        await ws.send_text(json.dumps(telem))
        while True:
            # Frontend is receive-only; drain any messages to keep alive
            await ws.receive_text()
    except Exception:
        pass
    finally:
        await store.remove_frontend(ws)


@router.websocket("/ws/camera_feed")
async def ws_camera_feed(ws: WebSocket):
    """Browser connects to receive camera frames."""
    await ws.accept()
    await store.add_camera(ws)
    # Push latest frame immediately if available
    frame = await store.get_frame()
    if frame:
        try:
            await ws.send_text(frame)
        except Exception:
            pass
    try:
        while True:
            await ws.receive_text()
    except Exception:
        pass
    finally:
        await store.remove_camera(ws)
