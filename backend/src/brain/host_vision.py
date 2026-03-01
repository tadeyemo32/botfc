#!/usr/bin/env python3
"""
BotFC Host Vision Server
Runs on the MacBook alongside the C++ server.
Receives camera frames from the robot via WebSocket,
processes them with OpenCV, and sends back detections.

Usage:
  python3 host_vision.py --port 5060
"""

import asyncio
import json
import struct
import time
import numpy as np

try:
    import cv2
except ImportError:
    print("[Vision] ERROR: opencv-python-headless not installed.")
    print("  pip3 install opencv-python-headless numpy")
    exit(1)

class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, np.bool_):
            return bool(obj)
        else:
            return super(NpEncoder, self).default(obj)


# ─────────────────────────────────────────────
# Ball Detector (HSV + Hough Circles)
# ─────────────────────────────────────────────
class BallDetector:
    """Detects a red/orange ball using HSV color filtering + circle detection."""

    def __init__(self):
        # Red ball HSV ranges (two ranges because red wraps around 0/180)
        self.lower_red1 = np.array([0, 100, 80])
        self.upper_red1 = np.array([10, 255, 255])
        self.lower_red2 = np.array([160, 100, 80])
        self.upper_red2 = np.array([180, 255, 255])
        # Orange range (many "red" balls are actually orange)
        self.lower_orange = np.array([5, 120, 100])
        self.upper_orange = np.array([20, 255, 255])

    def detect(self, frame):
        """Returns (found, cx, cy, radius) in normalized coords [-0.5, 0.5]."""
        h, w = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # Combined red + orange mask
        mask1 = cv2.inRange(hsv, self.lower_red1, self.upper_red1)
        mask2 = cv2.inRange(hsv, self.lower_red2, self.upper_red2)
        mask3 = cv2.inRange(hsv, self.lower_orange, self.upper_orange)
        mask = mask1 | mask2 | mask3

        # Morphological cleanup
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        # Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)

        if not contours:
            return {"found": False}

        # Find the most circular, largest contour
        best = None
        best_score = 0
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < 50:
                continue
            perimeter = cv2.arcLength(cnt, True)
            if perimeter == 0:
                continue
            circularity = 4 * np.pi * area / (perimeter * perimeter)
            score = area * circularity
            if score > best_score:
                best_score = score
                best = cnt

        if best is None:
            return {"found": False}

        # Minimum enclosing circle
        (cx, cy), radius = cv2.minEnclosingCircle(best)

        # Normalize to [-0.5, 0.5] range (center of image = 0,0)
        nx = (cx / w) - 0.5
        ny = (cy / h) - 0.5
        nr = radius / max(w, h)

        return {
            "found": True,
            "x": round(nx, 4),
            "y": round(ny, 4),
            "radius": round(nr, 4),
            "confidence": round(min(best_score / 5000.0, 1.0), 3),
            "pixel_x": int(cx),
            "pixel_y": int(cy),
            "pixel_r": int(radius),
        }


# ─────────────────────────────────────────────
# Goal Detector (colored posts)
# ─────────────────────────────────────────────
class GoalDetector:
    """Detects yellow or blue goal posts via color and vertical line detection."""

    def __init__(self):
        # Yellow goal
        self.lower_yellow = np.array([18, 80, 100])
        self.upper_yellow = np.array([35, 255, 255])
        # Blue goal
        self.lower_blue = np.array([95, 80, 60])
        self.upper_blue = np.array([130, 255, 255])

    def detect(self, frame):
        h, w = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        results = {}
        for color, lower, upper in [
            ("yellow", self.lower_yellow, self.upper_yellow),
            ("blue", self.lower_blue, self.upper_blue),
        ]:
            mask = cv2.inRange(hsv, lower, upper)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 8))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
            # Look for tall vertical shapes (goal posts)
            posts = []
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < 200:
                    continue
                x, y, bw, bh = cv2.boundingRect(cnt)
                aspect = bh / max(bw, 1)
                if aspect > 1.5:  # Tall and thin = goal post
                    cx = (x + bw / 2.0) / w - 0.5
                    posts.append(round(cx, 4))

            if posts:
                # Goal center is average of post positions
                center = round(sum(posts) / len(posts), 4)
                results[color] = {
                    "found": True,
                    "center_x": center,
                    "posts": len(posts),
                    "width": round(max(posts) - min(posts), 4) if len(posts) > 1 else 0,
                }
            else:
                results[color] = {"found": False}

        return results


# ─────────────────────────────────────────────
# Opponent Detector
# ─────────────────────────────────────────────
class OpponentDetector:
    """Detects other NAO robots by their white body + colored jersey."""

    def detect(self, frame):
        h, w = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # NAO robots are mostly white with some colored parts
        # Detect large white blobs that aren't the field
        lower_white = np.array([0, 0, 180])
        upper_white = np.array([180, 40, 255])
        mask = cv2.inRange(hsv, lower_white, upper_white)

        # Remove small noise
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)

        opponents = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < 800:  # Too small to be a robot
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            aspect = bh / max(bw, 1)
            if 0.8 < aspect < 3.0:  # roughly human-shaped proportions
                cx = (x + bw / 2.0) / w - 0.5
                cy = (y + bh / 2.0) / h - 0.5
                size = (bw * bh) / (w * h)
                opponents.append({
                    "x": round(cx, 4),
                    "y": round(cy, 4),
                    "size": round(size, 4),
                })

        return {"count": len(opponents), "positions": opponents[:3]}


# ─────────────────────────────────────────────
# Field Detector (green field boundaries)
# ─────────────────────────────────────────────
class FieldDetector:
    """Detects green field boundaries to know where the playing area is."""

    def detect(self, frame):
        h, w = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        lower_green = np.array([35, 40, 40])
        upper_green = np.array([85, 255, 255])
        mask = cv2.inRange(hsv, lower_green, upper_green)

        # What fraction of the frame is green field?
        green_ratio = np.count_nonzero(mask) / (h * w)

        # Find field boundary (top edge of green area)
        col_sums = np.sum(mask > 0, axis=0)
        # Left and right extent of field
        field_cols = np.where(col_sums > h * 0.2)[0]
        if len(field_cols) > 0:
            left_x = round(field_cols[0] / w - 0.5, 4)
            right_x = round(field_cols[-1] / w - 0.5, 4)
        else:
            left_x = -0.5
            right_x = 0.5

        return {
            "green_ratio": round(green_ratio, 3),
            "field_left": left_x,
            "field_right": right_x,
            "on_field": green_ratio > 0.15,
        }


# ─────────────────────────────────────────────
# YUV422 to BGR converter (NAOqi camera format)
# ─────────────────────────────────────────────
def yuv422_to_bgr(data, width, height):
    """Convert NAOqi YUYV (YUV422) raw bytes to BGR numpy array."""
    expected = width * height * 2
    if len(data) < expected:
        return None
    yuv = np.frombuffer(data[:expected], dtype=np.uint8).reshape((height, width, 2))
    bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_YUYV)
    return bgr


# ─────────────────────────────────────────────
# WebSocket Vision Server
# ─────────────────────────────────────────────
class VisionServer:
    def __init__(self, host="0.0.0.0", port=5060):
        self.host = host
        self.port = port
        self.ball_det = BallDetector()
        self.goal_det = GoalDetector()
        self.opp_det = OpponentDetector()
        self.field_det = FieldDetector()
        self.frame_count = 0
        self.fps = 0
        self.last_fps_time = time.time()

    async def handle_client(self, reader, writer):
        addr = writer.get_extra_info("peername")
        print(f"[Vision] Client connected: {addr}")

        buf = b""
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                buf += chunk

                # Protocol: 4-byte length prefix + JSON metadata + raw frame
                while len(buf) >= 4:
                    msg_len = struct.unpack("!I", buf[:4])[0]
                    if len(buf) < 4 + msg_len:
                        break

                    payload = buf[4:4 + msg_len]
                    buf = buf[4 + msg_len:]

                    # Parse: first line is JSON metadata, rest is raw image
                    try:
                        nl_idx = payload.index(b"\n")
                        meta = json.loads(payload[:nl_idx])
                        raw_frame = payload[nl_idx + 1:]
                    except (ValueError, json.JSONDecodeError):
                        continue

                    width = meta.get("width", 320)
                    height = meta.get("height", 240)
                    fmt = meta.get("format", "yuv422")

                    # Convert to BGR
                    if fmt == "yuv422":
                        frame = yuv422_to_bgr(raw_frame, width, height)
                    elif fmt == "bgr":
                        frame = np.frombuffer(raw_frame, dtype=np.uint8)
                        frame = frame.reshape((height, width, 3))
                    else:
                        continue

                    if frame is None:
                        continue

                    # Run all detectors
                    result = {
                        "ball": self.ball_det.detect(frame),
                        "goals": self.goal_det.detect(frame),
                        "opponents": self.opp_det.detect(frame),
                        "field": self.field_det.detect(frame),
                        "frame_id": meta.get("frame_id", 0),
                        "timestamp": time.time(),
                    }

                    # FPS tracking
                    self.frame_count += 1
                    now = time.time()
                    if now - self.last_fps_time >= 5.0:
                        self.fps = self.frame_count / (now - self.last_fps_time)
                        self.frame_count = 0
                        self.last_fps_time = now
                        print(f"[Vision] Processing at {self.fps:.1f} fps")

                    # Send back result
                    resp = json.dumps(result, cls=NpEncoder).encode("utf-8")
                    resp_msg = struct.pack("!I", len(resp)) + resp
                    writer.write(resp_msg)
                    await writer.drain()

        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            print(f"[Vision] Client disconnected: {addr}")
            writer.close()

    async def run(self):
        server = await asyncio.start_server(
            self.handle_client, self.host, self.port)
        print(f"[Vision] Host Vision Server listening on {self.host}:{self.port}")
        print(f"[Vision] Detectors: Ball(HSV+Hough), Goal(Yellow/Blue), "
              f"Opponent(White blob), Field(Green)")
        async with server:
            await server.serve_forever()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BotFC Host Vision Server")
    parser.add_argument("--port", type=int, default=5060)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    server = VisionServer(args.host, args.port)
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\n[Vision] Shutting down.")
