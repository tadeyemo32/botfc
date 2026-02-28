# ⚽ PDR: Bot FC – Autonomous Humanoid Sportsmen

**Status:** Frontend Complete | **Goal:** Backend & AI Implementation

## 1. Project Overview

**Bot FC** is a multi-agent football ecosystem for two NAO robots. The system features a "Manager’s Dugout" (the React/Vite Web App) that allows real-time personality injection and an "Evolutionary Engine" that uses Reinforcement Learning (RL) to improve player performance (kicking/walking) over the course of a match.

## 2. Technical Stack Integration

* **Frontend:** React + Vite 
* **Middleware:** Flask/FastAPI (Python 2.7 compatible via NAOqi bridge or running on a separate 3.x server interacting with a 2.7 client)
* **Robotics:** NAOqi SDK (Version 2.1/2.8)
* **Vision:** OpenCV (Color-space segmentation & blob detection)
* **Communication:** UDP Broadcast (Port 9559) for low-latency peer-to-peer sync.

---

## 3. Core AI Modules

### A. The Personality Engine (`brain/traits.json`)

The AI must map UI "Traits" to physical robot parameters.

* **Aggressive (The Striker):** High `WalkVelocity`, low `ObstacleAvoidance`, prioritized `Kick` state.
* **Tactical (The Playmaker):** Frequent `HeadYaw` scanning, maintains 0.5m distance from teammate, passes ball.
* **Defensive (The Wall):** Uses ultrasonic sensors to track the opponent and stays in the "Goal-to-Ball" vector.

### B. The ML Improvement Loop (`brain/optimizer.py`)

To fulfill the "getting better" requirement, implement a **Stochastic Gradient Descent (SGD)** optimizer for the kick:

* **Input:** `InertialSensor` (Gyro/Accel) data from the last kick.
* **Optimization:** Adjust `AnklePitch` and `ComHeight` (Center of Mass) to minimize "Fall Probability."
* **Persistence:** Save updated weights to `/models/` so the robot "remembers" how to balance better in the next round.

### C. Multi-Agent Coordination (`robot_engine/communication.py`)

A "Decentralized World Model":

* **Packet Sharing:** Robots broadcast `(x, y)` coordinates and `BallDistance`.
* **Arbitration:** If `Robot_A.BallDist < Robot_B.BallDist`, Robot A claims the `STRIKER` role; Robot B switches to `SUPPORT`.

---

## 4. Operational State Machine (FSM)

The `main_agent.py` will cycle through these states:

1. **IDLE:** Waiting for "Start" signal from the Web App.
2. **SEARCH:** 360-degree head scan for the red ball.
3. **APPROACH:** PID-controlled walk toward the ball.
4. **ALIGN:** Side-stepping to position the ball at the "Kick Foot."
5. **EXECUTE:** Triggering the ML-optimized kick motion.
6. **RECOVER:** Returning to a stable posture and checking goal success.

---

## 5. Backend Implementation Plan

1. **Flask (app.py):** Create API endpoints for `/start_match`, `/set_trait`, and `/get_telemetry`.
2. **Traits (traits.json):** Define variables for 'Speed', 'KickPower', and 'ScanFrequency' for three personas: Berserker, Tactician, and Wall.
3. **Communication (communication.py):** Use Python `socket` to create a UDP broadcast so two NAOs can share their distance to the ball.
4. **ML (optimizer.py):** Write a script that takes a 'Stability Score' (0-1) and adjusts the NAO's `MaxStepX` walk parameter to prevent falling.
5. **Agent (main_agent.py):** Create a Finite State Machine that integrates Vision, Motion, and Communication to play a 2v0 or 1v1 football game.

*Implementation Note: Use Python 2.7 syntax where `naoqi` is required, and ensure the code is modular so it can run on two different Robot IPs.*
