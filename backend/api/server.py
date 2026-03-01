"""FastAPI application – replaces HttpSession.cpp route handlers + main.cpp."""

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config as cfg
from .decision import compute_decision
from .deployer import deploy_and_run, stop_match, test_connection
from .state import store
from .websockets import router as ws_router

app = FastAPI(title="botfc-server", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount WebSocket routes under /api
app.include_router(ws_router, prefix="/api")


@app.on_event("startup")
async def startup():
    await store.set_robot_ip(cfg.ROBOT_IP)


# ── HTTP routes (all under /api) ──────────────────────────────────────────


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/status")
async def status():
    return await store.get_telemetry()


@app.get("/api/robot/config")
async def get_robot_config():
    ip = await store.get_robot_ip()
    return {"ip": ip}


@app.post("/api/robot/config")
async def set_robot_config(request: Request):
    body = await request.json()
    if "ip" in body:
        await store.set_robot_ip(body["ip"])
    return {"status": "ok"}


@app.post("/api/robot/test")
async def robot_test():
    ip = await store.get_robot_ip()

    async def _test():
        ok = await asyncio.to_thread(test_connection, ip)
        await store.update_telemetry({"robot_connected": ok})
        telem = await store.get_telemetry()
        await store.broadcast_telemetry(json.dumps(telem))

    asyncio.create_task(_test())
    return {"status": "testing"}


@app.post("/api/start_match")
async def start_match(request: Request):
    body = await request.json()
    trait = "balanced"
    if "player1" in body and "mode" in body.get("player1", {}):
        trait = body["player1"]["mode"]
    elif "player2" in body and "mode" in body.get("player2", {}):
        trait = body["player2"]["mode"]

    ip = await store.get_robot_ip()

    async def _deploy():
        ok = await asyncio.to_thread(deploy_and_run, ip, trait)
        if ok:
            await store.update_telemetry({"running": True, "state": "INIT"})
            telem = await store.get_telemetry()
            await store.broadcast_telemetry(json.dumps(telem))

    asyncio.create_task(_deploy())
    return {"status": "started"}


@app.post("/api/stop_match")
async def stop_match_endpoint(request: Request):
    ip = await store.get_robot_ip()

    async def _stop():
        ok = await asyncio.to_thread(stop_match, ip)
        if ok:
            await store.update_telemetry({"running": False, "state": "IDLE"})
            telem = await store.get_telemetry()
            await store.broadcast_telemetry(json.dumps(telem))

    asyncio.create_task(_stop())
    return {"status": "stopped"}


@app.get("/api/camera/frame")
async def camera_frame():
    f = await store.get_frame()
    if not f:
        return {"frame": None}
    return JSONResponse(content=json.loads(f))


@app.post("/api/vision/ball")
async def vision_ball(request: Request):
    body = await request.json()
    patch: dict = {"host_ball_valid": True}
    for key in ("bx", "by", "bsz"):
        if key in body:
            patch[f"host_ball_{key}"] = body[key]
    await store.update_telemetry(patch)
    telem = await store.get_telemetry()
    telem["cpp_action"] = compute_decision(telem)
    await store.broadcast_telemetry(json.dumps(telem))
    return {"status": "ok"}


@app.post("/api/command")
async def post_command(request: Request):
    body = await request.json()
    if "action" in body:
        await store.queue_command(body)
    return {"status": "ok"}


@app.get("/api/bot/command")
async def get_bot_command():
    return await store.pop_command()


# ── OPTIONS catch-all for CORS preflight ──────────────────────────────────


@app.options("/{path:path}")
async def options_handler(path: str):
    return JSONResponse(content={}, status_code=200)
