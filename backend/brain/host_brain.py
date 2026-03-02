#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
host_brain.py – BotFC Host-PC Brain
====================================
Runs on the HOST PC (not on the robot).

Architecture:
  Host PC ──(pynaoqi/TCP)──> NAO ALVideoDevice  (raw frames)
  Host PC ──(pynaoqi/TCP)──> NAO ALMotion        (movement commands)

Threads:
  VisionThread  – pulls QQVGA BGR frames at ~25 fps, runs HSV masking
  FSMThread     – 20 Hz decision loop, sends motion commands
  Main thread   – optional OpenCV debug window (press 'q' to quit)

Requirements (on host PC):
  pip install numpy opencv-python
  pynaoqi SDK in PYTHONPATH (same Python 2.7 used by your robot tools)

Usage:
  python host_brain.py --ip 192.168.x.x
  python host_brain.py --ip 192.168.x.x --port 9559 --nodisplay
"""
from __future__ import print_function

import sys
import time
import threading
import argparse

import numpy as np
import cv2

from naoqi import ALProxy

# ── Camera constants ─────────────────────────────────────────────────────────
#   QQVGA (160×120) keeps network load minimal and latency near-instant.
#   Switch to RES_ID=1 (QVGA 320×240) if detection accuracy is insufficient.
RES_ID     = 0    # 0=QQVGA 160×120, 1=QVGA 320×240, 2=VGA 640×480
COLOR_SPC  = 13   # kBGRColorSpace – directly usable by OpenCV (no conversion)
CAMERA_ID  = 0    # 0 = top camera
CAP_FPS    = 25   # frames-per-second requested from NAOqi
IMG_W      = 160  # must match RES_ID
IMG_H      = 120
NAO_PORT   = 9559 # default NAOqi broker port
SUB_NAME   = "HostBrainVision"

# ── Ball detection constants ─────────────────────────────────────────────────
#   Red soccer ball in HSV.  Hue wraps around 0°, so we use two ranges.
HSV_LO1    = np.array([  0, 100,  80], dtype=np.uint8)
HSV_HI1    = np.array([ 12, 255, 255], dtype=np.uint8)
HSV_LO2    = np.array([165, 100,  80], dtype=np.uint8)
HSV_HI2    = np.array([180, 255, 255], dtype=np.uint8)
MIN_RADIUS = 5    # px – blobs smaller than this are ignored

# ── Motion constants ─────────────────────────────────────────────────────────
DEAD_ZONE      = 18    # px from horizontal centre – counts as "centred"
ROTATE_SPD     = 0.25  # rad/s  (positive = turn left / CCW in NAOqi)
WALK_SPD       = 0.15  # m/s forward
BALL_LOST_SECS = 2.0   # watchdog: stopMove() if no ball seen for this long
FSM_HZ         = 20    # decision loop rate


# ── Shared state between threads ─────────────────────────────────────────────
class SharedState(object):
    """Thread-safe container for the latest ball detection result."""

    def __init__(self):
        self.lock      = threading.Lock()
        self.ball_cx   = None    # float px, or None when not visible
        self.ball_cy   = None
        self.ball_r    = None    # float px radius
        self.last_seen = 0.0     # time.time() of last valid detection
        self.frame     = None    # latest BGR ndarray for debug display
        self.running   = True


# ── Vision thread ─────────────────────────────────────────────────────────────
def vision_thread(nao_ip, nao_port, state):
    """
    Subscribes to NAO's top camera, pulls raw BGR frames as fast as possible,
    runs HSV red-ball masking + contour analysis to estimate ball centre.
    Writes results into SharedState under the lock.
    """
    print("[Vision] Connecting to ALVideoDevice at {}:{}".format(nao_ip, nao_port))
    try:
        cam = ALProxy("ALVideoDevice", nao_ip, nao_port)
    except Exception as e:
        print("[Vision] FATAL – ALVideoDevice unavailable:", e)
        state.running = False
        return

    # Clean up any stale subscription from a previous crashed run
    try:
        cam.unsubscribe(SUB_NAME)
    except Exception:
        pass

    handle = cam.subscribe(SUB_NAME, RES_ID, COLOR_SPC, CAP_FPS)
    if not handle:
        print("[Vision] subscribe() returned empty handle – aborting.")
        state.running = False
        return

    print("[Vision] Subscribed as '{}'. {}×{}  BGR  {} fps".format(
        handle, IMG_W, IMG_H, CAP_FPS))

    # Build morphology kernel once
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    try:
        while state.running:
            img = cam.getImageRemote(handle)
            if img is None:
                time.sleep(0.01)
                continue

            # NAOqi image tuple layout:
            #   [0]=width [1]=height [2]=nChannels [3]=colorSpace
            #   [4]=ts_sec [5]=ts_usec [6]=raw_bytes [7]=cameraID ...
            raw   = img[6]
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(IMG_H, IMG_W, 3)

            # ── HSV red-ball mask ─────────────────────────────────────────
            hsv  = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            mask = cv2.bitwise_or(
                cv2.inRange(hsv, HSV_LO1, HSV_HI1),
                cv2.inRange(hsv, HSV_LO2, HSV_HI2),
            )
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

            # ── Largest contour → ball centre ─────────────────────────────
            # findContours returns (image, contours, hier) in cv2 2.x and
            # (contours, hier) in cv2 3.x / 4.x.
            raw_cnts = cv2.findContours(mask.copy(), cv2.RETR_EXTERNAL,
                                        cv2.CHAIN_APPROX_SIMPLE)
            cnts = raw_cnts[1] if len(raw_cnts) == 3 else raw_cnts[0]

            found = False
            if cnts:
                biggest = max(cnts, key=cv2.contourArea)
                (cx, cy), r = cv2.minEnclosingCircle(biggest)
                if r >= MIN_RADIUS:
                    with state.lock:
                        state.ball_cx   = float(cx)
                        state.ball_cy   = float(cy)
                        state.ball_r    = float(r)
                        state.last_seen = time.time()
                    found = True

            with state.lock:
                if not found:
                    state.ball_cx = None
                    state.ball_cy = None
                    state.ball_r  = None
                state.frame = frame.copy()

            # Release the image buffer back to NAOqi (mandatory)
            cam.releaseImage(handle)

    finally:
        try:
            cam.unsubscribe(handle)
        except Exception:
            pass
        print("[Vision] Unsubscribed and exiting.")


# ── FSM / decision thread ─────────────────────────────────────────────────────
def fsm_thread(nao_ip, nao_port, state):
    """
    20 Hz decision loop.  Reads ball position from SharedState and sends
    motion commands to ALMotion.

    States (implicit):
      STANDBY    – robot has not yet seen the ball (or watchdog fired)
      ROTATE_L   – ball is left of centre, turn to face it
      ROTATE_R   – ball is right of centre, turn to face it
      WALK_FWD   – ball is centred, advance toward it
    """
    print("[FSM] Connecting to ALMotion at {}:{}".format(nao_ip, nao_port))
    try:
        motion = ALProxy("ALMotion", nao_ip, nao_port)
    except Exception as e:
        print("[FSM] FATAL – ALMotion unavailable:", e)
        state.running = False
        return

    # Wake up motors
    try:
        posture = ALProxy("ALRobotPosture", nao_ip, nao_port)
        posture.goToPosture("StandInit", 0.8)
    except Exception:
        pass
    try:
        motion.setStiffnesses("Body", 1.0)
    except Exception:
        pass

    IMG_CX   = IMG_W / 2.0    # horizontal image centre (80 px for QQVGA)
    INTERVAL = 1.0 / FSM_HZ
    last_cmd = None            # track last issued command to skip duplicates

    print("[FSM] Running at {} Hz.  Image centre = {:.0f} px".format(FSM_HZ, IMG_CX))

    while state.running:
        t_start = time.time()

        with state.lock:
            cx        = state.ball_cx
            last_seen = state.last_seen

        ball_age = time.time() - last_seen if last_seen > 0 else 9999.0

        # ── Watchdog – ball not seen for too long ─────────────────────────
        if ball_age > BALL_LOST_SECS:
            if last_cmd != "STOP":
                motion.stopMove()
                last_cmd = "STOP"
                print("[FSM] Watchdog – ball lost ({:.1f}s). Stopping.".format(ball_age))
            elapsed = time.time() - t_start
            time.sleep(max(0.0, INTERVAL - elapsed))
            continue

        # Ball seen recently but not in the current frame
        if cx is None:
            elapsed = time.time() - t_start
            time.sleep(max(0.0, INTERVAL - elapsed))
            continue

        # ── Compute horizontal error ──────────────────────────────────────
        error = cx - IMG_CX    # positive = ball is RIGHT of centre

        if error < -DEAD_ZONE:
            new_cmd = "ROTATE_L"
            vx, vy, vt = 0.0, 0.0, ROTATE_SPD       # turn left (CCW)
        elif error > DEAD_ZONE:
            new_cmd = "ROTATE_R"
            vx, vy, vt = 0.0, 0.0, -ROTATE_SPD      # turn right (CW)
        else:
            new_cmd = "WALK_FWD"
            vx, vy, vt = WALK_SPD, 0.0, 0.0          # walk forward

        # ── Issue command only when direction changes ──────────────────────
        #   Calling stopMove() before each new direction prevents the robot
        #   from accumulating a queue of stale rotation commands ("stuck spin")
        if new_cmd != last_cmd:
            motion.stopMove()                  # flush command queue
            motion.post.move(vx, vy, vt)      # non-blocking walk/rotate
            last_cmd = new_cmd
            print("[FSM] {:10s}  err={:+5.1f}px  (cx={:.0f})".format(
                new_cmd, error, cx))

        elapsed = time.time() - t_start
        time.sleep(max(0.0, INTERVAL - elapsed))

    # Safe shutdown
    try:
        motion.stopMove()
        motion.setStiffnesses("Body", 0.0)
    except Exception:
        pass
    print("[FSM] Stopped and stiffness released.")


# ── Optional debug display (main thread) ──────────────────────────────────────
def display_loop(state):
    """
    Shows the live QQVGA camera view upscaled 2× with a ball overlay.
    Press 'q' in the window to quit.
    """
    SCALE = 2
    DW, DH = IMG_W * SCALE, IMG_H * SCALE

    while state.running:
        with state.lock:
            frame = state.frame
            cx    = state.ball_cx
            cy    = state.ball_cy
            r     = state.ball_r

        if frame is None:
            time.sleep(0.03)
            continue

        vis = cv2.resize(frame, (DW, DH), interpolation=cv2.INTER_NEAREST)

        # Centre crosshair
        cx_mid = DW // 2
        cv2.line(vis, (cx_mid, 0), (cx_mid, DH), (60, 60, 60), 1)
        cv2.line(vis, (0, DH // 2), (DW, DH // 2), (60, 60, 60), 1)

        # Dead-zone lines
        lx = cx_mid - int(DEAD_ZONE * SCALE)
        rx = cx_mid + int(DEAD_ZONE * SCALE)
        cv2.line(vis, (lx, 0), (lx, DH), (0, 80, 80), 1)
        cv2.line(vis, (rx, 0), (rx, DH), (0, 80, 80), 1)

        # Ball circle + centre dot
        if cx is not None:
            sc_cx, sc_cy = int(cx * SCALE), int(cy * SCALE)
            sc_r         = max(1, int(r * SCALE))
            cv2.circle(vis, (sc_cx, sc_cy), sc_r, (0, 255,  80), 2)
            cv2.circle(vis, (sc_cx, sc_cy), 3,    (0,   0, 255), -1)
            cv2.putText(vis, "BALL", (sc_cx + sc_r + 4, sc_cy),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 80), 1)

        cv2.imshow("HostBrain – NAO Camera (q=quit)", vis)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            state.running = False
            break

    cv2.destroyAllWindows()


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="BotFC Host-PC Brain – OpenCV ball tracking on the host "
                    "with direct ALProxy motion commands to the NAO.")
    parser.add_argument("--ip",        required=True,
                        help="NAO robot IP address (e.g. 192.168.1.100)")
    parser.add_argument("--port",      type=int, default=NAO_PORT,
                        help="NAOqi port (default {})".format(NAO_PORT))
    parser.add_argument("--nodisplay", action="store_true",
                        help="Disable the OpenCV debug window (headless mode)")
    args = parser.parse_args()

    print("=" * 58)
    print("  BotFC Host-PC Brain")
    print("  Robot    : {}:{}".format(args.ip, args.port))
    print("  Capture  : QQVGA {}x{}  BGR  {} fps".format(IMG_W, IMG_H, CAP_FPS))
    print("  Detection: HSV red-mask + largest-contour")
    print("  Dead zone: ±{} px  |  Walk: {:.2f} m/s  |  Rotate: {:.2f} rad/s".format(
        DEAD_ZONE, WALK_SPD, ROTATE_SPD))
    print("  Watchdog : {:.1f} s".format(BALL_LOST_SECS))
    print("=" * 58)

    state = SharedState()

    t_vision = threading.Thread(target=vision_thread,
                                args=(args.ip, args.port, state))
    t_fsm    = threading.Thread(target=fsm_thread,
                                args=(args.ip, args.port, state))
    t_vision.daemon = True
    t_fsm.daemon    = True

    t_vision.start()
    t_fsm.start()

    if args.nodisplay:
        print("[Main] Running headless. Press Ctrl+C to stop.")
        try:
            while state.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    else:
        try:
            display_loop(state)
        except KeyboardInterrupt:
            pass

    print("\n[Main] Shutting down...")
    state.running = False
    t_vision.join(timeout=4.0)
    t_fsm.join(timeout=4.0)
    print("[Main] Done.")


if __name__ == "__main__":
    main()
