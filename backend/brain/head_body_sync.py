#!/usr/bin/env python2
"""
head_body_sync.py

A standalone Python script for a NAO V6 robot that demonstrates Synchronized Head-Body Tracking.
This script ensures the robot walks in a "straight line" (on a string path) toward a tracked ball 
by proportionally linking its rotational body velocity (v_theta) directly to its HeadYaw sensor,
while dynamically scaling its forward velocity (v_x) to slow down during hard turns.

Usage: 
  python head_body_sync.py --ip <ROBOT_IP> --port 9559
"""

import sys
import time
import math
import argparse

try:
    from naoqi import ALProxy
except ImportError:
    print("Error: Could not import naoqi. Please run this script in an environment with the Python 2.7 NAOqi SDK.")
    sys.exit(1)


# -----------------------------------------------------------------------------
# TUNING GUIDE FOR ROBOCUP SPL CARPET (AstroTurf)
# -----------------------------------------------------------------------------
# You must tune Kp (Proportional Gain) and EMA_ALPHA (Low-Pass Filter) precisely
# for the physical friction of your hackathon carpet.
#
# 1. Kp (Proportional Gain):
#    - Too Low (e.g., 0.1): The robot's motors will stall. The friction of the carpet
#      prevents the robot from turning against its ankle joints. It will walk straight 
#      and ignore the head angle completely.
#    - Too High (e.g., 1.5): The robot will violently over-correct back and forth, 
#      "jittering" sideways and losing sight of the ball entirely.
#    - Ideal for Medium Carpet: ~0.6 to 0.8
# 
# 2. EMA_ALPHA (Exponential Moving Average Filter Weight):
#    - This script uses an EMA low-pass filter: `current_v_theta = (alpha * new_raw) + ((1 - alpha) * old)`
#    - We need this because ALMemory sensors have micro-stutters. If you feed raw sensor data
#      directly to ALMotion, the robot stutters and shakes.
#    - Too Low (e.g., 0.05): The robot turns very sluggishly, acting like it's in molasses.
#    - Too High (e.g., 1.0): No filtering at all. Expect violent jitter on every minor head adjustment.
#    - Ideal for Medium Carpet: ~0.4 to 0.6
# -----------------------------------------------------------------------------

def synchronized_approach(robot_ip, port=9559):
    # 1. Initialize Proxies
    try:
        memory = ALProxy("ALMemory", robot_ip, port)
        motion = ALProxy("ALMotion", robot_ip, port)
        tts    = ALProxy("ALTextToSpeech", robot_ip, port)
    except Exception as e:
        print("Could not create proxy to ALProxy. Error: {}".format(e))
        sys.exit(1)

    print("Connected to NAO. Starting Synchronized Head-Body Tracking...")
    tts.say("Synchronizing Head and Body")

    # Wake up and stand
    motion.wakeUp()
    
    # Tuning Constants
    Kp_turn         = 0.70    # Proportional gain for turning
    EMA_ALPHA       = 0.50    # Low-pass filter weight (1.0 = raw, 0.0 = completely frozen)
    MAX_SPEED_X     = 0.60    # Maximum forward speed (ALMotion normalized 0.0-1.0)
    MAX_TURN_THETA  = 0.50    # Maximum turn speed (ALMotion normalized 0.0-1.0)
    
    # State tracking
    last_ball_seen_time = time.time()
    BALL_LOST_TIMEOUT = 3.0   # seconds
    
    # EMA memory state
    filtered_v_theta = 0.0
    
    try:
        # ── MAIN TRACKING LOOP ───────────────────────────────────────────
        while True:
            # Simulated check for whether the tracking vision actually sees the ball
            # In your real codebase, this would be `if best_blob is not None: last_ball_seen_time = time.time()`
            # For this standalone script, we assume the ball is ALWAYS in view unless a timeout hits.
            # Example: 
            # if your_vision_algorithm_sees_ball():
            #     last_ball_seen_time = time.time()
            last_ball_seen_time = time.time() # Hack: Keep loop alive for demonstration
            
            # --- 1. Timeout Check ---
            if (time.time() - last_ball_seen_time) > BALL_LOST_TIMEOUT:
                print("Ball lost for {} seconds! Stopping approach.".format(BALL_LOST_TIMEOUT))
                motion.stopMove()
                tts.say("Ball lost")
                break

            # --- 2. Read Physical HeadYaw Sensor from ALMemory ---
            try:
                # ALMemory gives us the exact physical radian angle of the neck joint
                # Positive is LEFT, Negative is RIGHT
                head_yaw_rad = float(memory.getData("Device/SubDeviceList/HeadYaw/Position/Sensor/Value"))
            except Exception as e:
                print("Sensor read error: {}".format(e))
                head_yaw_rad = 0.0
                
            # --- 3. Forward Velocity Scaler ---
            # MATHEMATICAL CONSTRAINT: 
            # cos(0 rad) = 1.0 (Looking straight ahead -> Full speed)
            # cos(+/- 1.57 rad) = 0.0 (Looking 90 degrees left/right -> Forward speed becomes 0)
            # This mathematically forces the robot to slow down forward progress the more it has to turn,
            # keeping it on a "string" arc path perfectly.
            raw_v_x = MAX_SPEED_X * math.cos(head_yaw_rad)
            v_x = max(0.0, raw_v_x) # Prevent walking backwards if head turns > 90 deg

            # --- 4. Proportional Controller for Turning ---
            # The body needs to rotate to bring the head_yaw back to 0.0 (facing forward)
            raw_v_theta = Kp_turn * head_yaw_rad
            
            # Clamp to max safety speeds
            raw_v_theta = max(-MAX_TURN_THETA, min(MAX_TURN_THETA, raw_v_theta))

            # --- 5. Exponential Moving Average (EMA) Low-Pass Filter ---
            # Applies the filter to prevent shaking/jittering when adapting to sensor noise
            filtered_v_theta = (EMA_ALPHA * raw_v_theta) + ((1.0 - EMA_ALPHA) * filtered_v_theta)

            # --- 6. Command the Movement ---
            # ALMotion.move(X, Y, Theta) takes normalized fractions of max velocity [-1.0 to 1.0]
            motion.move(v_x, 0.0, filtered_v_theta)

            # Let the FSM breathe (roughly 20Hz loop rate)
            time.sleep(0.05)
            
    except KeyboardInterrupt:
        print("\nInterrupt received. Stopping.")
    
    # Safe shutdown
    motion.stopMove()
    motion.rest()
    print("Tracking ended.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NAO Synchronized Head-Body Tracking")
    parser.add_argument("--ip", type=str, default="127.0.0.1", help="Robot IP address")
    parser.add_argument("--port", type=int, default=9559, help="Robot port number")
    
    args = parser.parse_args()
    
    synchronized_approach(args.ip, args.port)
