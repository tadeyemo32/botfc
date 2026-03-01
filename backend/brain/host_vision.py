#!/usr/bin/env python3
"""
host_vision.py – BotFC Host-Side Vision Processor

Runs on the development laptop (not the robot).

  1. Polls GET /api/camera/frame  →  latest JPEG from the robot's CameraStreamer
  2. Decodes the base64 JPEG and runs an OpenCV HSV red-ball detector
  3. POSTs detected ball coordinates to POST /api/vision/ball so the C++ server
     can merge them into the telemetry stream and frontend HUD.

Usage:
  pip install opencv-python requests
  python3 host_vision.py --server-ip 192.168.1.100 --server-port 5050

The host-side detector acts as a backup / cross-check for the on-robot detector.
Because the laptop has more CPU headroom it can use full HSV morphology rather
than the stride-sampled BGR loop the robot uses.
"""

import argparse
import base64
import sys
import time

try:
    import cv2
    import numpy as np
    import requests
except ImportError as e:
    print("[host_vision] Missing dependency: {}".format(e))
    print("  pip install opencv-python requests")
    sys.exit(1)

# ──────────────────────────────────────────────────────────────────────────────
# HSV thresholds for a standard red RoboCup ball under typical lighting.
# Tune these if your field lighting is unusually warm / cool.
# Red wraps around the hue axis, so we need two ranges:
#   lower red  : H 0-10
#   upper red  : H 160-180
# ──────────────────────────────────────────────────────────────────────────────
HSV_LOWER1 = np.array([0,   120,  80], dtype=np.uint8)
HSV_UPPER1 = np.array([10,  255, 255], dtype=np.uint8)
HSV_LOWER2 = np.array([160, 120,  80], dtype=np.uint8)
HSV_UPPER2 = np.array([180, 255, 255], dtype=np.uint8)

# Minimum contour area (pixels²) to be counted as a ball blob
MIN_BLOB_AREA = 120


def detect_ball(frame_bgr):
    """Run HSV red-ball detection on a BGR frame.

    Returns (bx, by, bsz) normalised the same way as the robot's _read_ball():
      bx  – horizontal offset from centre  [-0.5, 0.5], positive = right
      by  – vertical   offset from centre  [-0.5, 0.5], positive = down
      bsz – blob area as fraction of total frame area [0, 1]
    Returns None if no ball detected.
    """
    h, w = frame_bgr.shape[:2]
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

    mask1 = cv2.inRange(hsv, HSV_LOWER1, HSV_UPPER1)
    mask2 = cv2.inRange(hsv, HSV_LOWER2, HSV_UPPER2)
    mask  = cv2.bitwise_or(mask1, mask2)

    # Morphological cleanup: close small holes, remove isolated noise
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask   = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask   = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    area    = cv2.contourArea(largest)
    if area < MIN_BLOB_AREA:
        return None

    M = cv2.moments(largest)
    if M["m00"] == 0:
        return None

    cx = M["m10"] / M["m00"]
    cy = M["m01"] / M["m00"]

    bx  = (cx - w * 0.5) / w
    by  = (cy - h * 0.5) / h
    bsz = area / (w * h)
    return (bx, by, bsz)


def main():
    parser = argparse.ArgumentParser(description="BotFC Host Vision Processor")
    parser.add_argument("--server-ip",   default="127.0.0.1",
                        help="IP address of the BotFC C++ server")
    parser.add_argument("--server-port", type=int, default=5050,
                        help="Port of the BotFC C++ server")
    parser.add_argument("--fps",         type=float, default=5.0,
                        help="How many times per second to poll the camera frame")
    parser.add_argument("--show",        action="store_true",
                        help="Display the annotated frame in a window (requires display)")
    args = parser.parse_args()

    base_url   = "http://{}:{}".format(args.server_ip, args.server_port)
    frame_url  = base_url + "/api/camera/frame"
    vision_url = base_url + "/api/vision/ball"
    interval   = 1.0 / args.fps

    print("[host_vision] Polling {} at {:.0f} fps".format(frame_url, args.fps))
    if args.show:
        print("[host_vision] Press 'q' in the window to quit.")

    session = requests.Session()

    while True:
        t0 = time.time()
        try:
            resp = session.get(frame_url, timeout=2)
            if resp.status_code != 200:
                time.sleep(interval)
                continue

            data = resp.json()
            b64  = data.get("jpg") or data.get("frame")
            if not b64:
                # No frame yet from robot
                time.sleep(interval)
                continue

            jpg_bytes = base64.b64decode(b64)
            arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
            bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if bgr is None:
                time.sleep(interval)
                continue

            result = detect_ball(bgr)

            if result is not None:
                bx, by, bsz = result
                payload = {"bx": round(bx, 4), "by": round(by, 4), "bsz": round(bsz, 6)}
                try:
                    session.post(vision_url, json=payload, timeout=1)
                    print("[host_vision] Ball at bx={:.3f} by={:.3f} bsz={:.5f}".format(
                        bx, by, bsz))
                except Exception as e:
                    print("[host_vision] POST failed: {}".format(e))

            if args.show:
                display = bgr.copy()
                if result is not None:
                    h, w = display.shape[:2]
                    cx = int((result[0] + 0.5) * w)
                    cy = int((result[1] + 0.5) * h)
                    cv2.circle(display, (cx, cy), 12, (0, 255, 0), 2)
                    cv2.putText(display, "BALL", (cx + 14, cy),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                cv2.imshow("BotFC Host Vision", display)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

        except Exception as e:
            print("[host_vision] Error: {}".format(e))

        elapsed = time.time() - t0
        sleep_t = max(0.0, interval - elapsed)
        time.sleep(sleep_t)

    if args.show:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
