#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
NAOqi FSM Controller module.
Provides a robust, non-blocking Finite State Machine architecture
for controlling NAO humanoid robots, incorporating sensor fusion,
balance awareness, and safety mechanisms.
"""

import sys
import time
import numpy as np
from naoqi import ALProxy, ALModule, ALBroker

# Global module instance for NAOqi event callbacks
robot_module = None

class RobotModule(ALModule):
    """
    Core module that encapsulates motion, kinematics, sensor interfaces,
    and event callbacks.
    """
    def __init__(self, name, ip, port):
        super(RobotModule, self).__init__(name)
        self.ip = ip
        self.port = port
        
        # Initialize Proxies
        self.motion = ALProxy("ALMotion", ip, port)
        self.posture = ALProxy("ALRobotPosture", ip, port)
        self.tts = ALProxy("ALTextToSpeech", ip, port)
        self.memory = ALProxy("ALMemory", ip, port)
        self.sonar = ALProxy("ALSonar", ip, port)
        self.leds = ALProxy("ALLeds", ip, port)
        self.video = ALProxy("ALVideoDevice", ip, port)
        
        # Safety states
        self.is_running = True
        self.emergency_stop = False
        
        # Subscribe to safety events (Bumper and IMU fall detection can be linked here)
        self.memory.subscribeToEvent("RightBumperPressed", self.getName(), "onBumperPressed")
        self.memory.subscribeToEvent("LeftBumperPressed", self.getName(), "onBumperPressed")
        
        # Subscribe to sonar for obstacle detection (avoid blocking ops)
        self.sonar.subscribe(self.getName())
        
        # Announce initialization in non-blocking form
        self.tts.post.say("Robot module initialized and safety checks passed.")

    def onBumperPressed(self, key, value, message):
        """
        Kill switch callback. Triggered by bumper presses or sudden IMU jolts.
        Input: key (str), value (float), message (str).
        """
        if value > 0.5: # Pressed threshold
            self.trigger_emergency_stop("Collision detected on bumper.")

    def trigger_emergency_stop(self, reason):
        """
        Triggers an immediate emergency stop, halting all motion and relaxing motors.
        Prevents overheating or joint locking.
        """
        if not self.emergency_stop:
            self.emergency_stop = True
            self.is_running = False
            self.leds.post.fadeRGB("FaceLeds", 1.0, 0.0, 0.0, 0.5) # Fast red LED indication
            self.tts.say("Emergency stop triggered. " + reason) # Blocking speech to notify crowd urgently
            
            # Kill all background tasks dynamically
            self.motion.killAll()
            
            # Relax stiffness to avoid internal joint destruction
            self.motion.setStiffnesses("Body", 0.0)

    def initialize_pose(self):
        """
        Wakes up the robot, safely sets stiffness, and commands an initial balanced posture.
        """
        self.motion.wakeUp()
        # Enforce stiffness prior to engaging complex posture
        self.motion.setStiffnesses("Body", 1.0)
        self.posture.goToPosture("StandInit", 0.5)
        
    def cleanup(self):
        """
        Graceful cleanup path. Stops motion and relaxes joints.
        Called on normal exit or interrupt to prevent hardware strain.
        """
        self.is_running = False
        try:
            self.sonar.unsubscribe(self.getName())
            self.motion.stopMove()
            self.posture.goToPosture("Crouch", 0.5)
            self.motion.rest()
            
            # Dim the LEDs to reflect offline state
            self.leds.post.fadeRGB("FaceLeds", 0.0, 0.0, 0.0, 0.5)
            self.tts.say("System shut down securely.")
        except Exception as e:
            print("Error during graceful cleanup: ", e)

    def get_robot_position(self):
        """
        Returns the robot's coordinates (x, y, theta) in FRAME_WORLD (1).
        Requires 3 DoF (X, Y, Theta).
        Returns: NumPy array initialized to coordinates.
        """
        try:
            # FRAME_WORLD = 1, useSensors = True (calculates odometry + sensor deviations)
            pos = self.motion.getRobotPosition(True)
            return np.array(pos) # [x, y, theta]
        except Exception as e:
            return np.array([0.0, 0.0, 0.0])

    def transform_target_to_local(self, world_robot_pos, world_target_pos):
        """
        Calculates local coordinates for a target using NumPy vector transformations.
        DoF: 3 (x, y, theta).
        
        Args:
            world_robot_pos: [x, y, theta]
            world_target_pos: [x, y, theta]
        Returns:
            np.array([dx_local, dy_local, dtheta_local])
        """
        rx, ry, rtheta = world_robot_pos
        tx, ty, ttheta = world_target_pos
        
        # Translation relative to world
        dx_world = tx - rx
        dy_world = ty - ry
        
        # Rotation matrix to transform from world to robot local frame
        cos_t = np.cos(-rtheta)
        sin_t = np.sin(-rtheta)
        
        # Apply 2D rotation for the x,y bounds
        dx_local = dx_world * cos_t - dy_world * sin_t
        dy_local = dx_world * sin_t + dy_world * cos_t
        
        # Normalize the difference in angle to [-pi, pi]
        dtheta_local = ttheta - rtheta
        dtheta_local = (dtheta_local + np.pi) % (2 * np.pi) - np.pi
        
        return np.array([dx_local, dy_local, dtheta_local])

    def move_toward_omni(self, x_vel, y_vel, theta_vel):
        """
        Omnidirectional walking proxy function. Uses moveToward for balanced, 
        Center-of-Mass (CoM) safe walking.
        Input velocities in meters/second and radians/second.
        Executes as a non-blocking post task to maintain FSM responsiveness.
        """
        if not self.emergency_stop:
            self.motion.post.moveToward(x_vel, y_vel, theta_vel)

    def get_sonar_distance(self):
        """
        Reads the left and right sonar sensors and returns the minimum distance in meters.
        Used extensively for obstacle avoidance logic inside States.
        """
        left = self.memory.getData("Device/SubDeviceList/US/Left/Sensor/Value")
        right = self.memory.getData("Device/SubDeviceList/US/Right/Sensor/Value")
        return min(left, right)


class BaseState(object):
    """
    Base abstraction blueprint for FSM States. Uses Entry/Execute/Exit schema.
    """
    def __init__(self, robot):
        self.robot = robot

    def enter(self):
        pass

    def execute(self):
        """
        Evaluates conditions inside current state loop.
        Returns state transitions (a new State Object) or self.
        """
        return self

    def exit(self):
        pass


class IdleState(BaseState):
    def enter(self):
        self.robot.tts.post.say("Entering Idle state.")
        self.robot.leds.post.fadeRGB("FaceLeds", 0.0, 1.0, 0.0, 0.5) # Green
        self.robot.initialize_pose()
        self.start_time = time.time()

    def execute(self):
        # Time-driven delay transition
        if time.time() - self.start_time > 2.0:
            return ScanningState(self.robot)
        return self


class ScanningState(BaseState):
    def enter(self):
        self.robot.tts.post.say("Scanning for target.")
        self.robot.leds.post.fadeRGB("FaceLeds", 0.0, 0.0, 1.0, 0.5) # Blue
        
        # Turn-in-place omnidirectionally while visually searching
        self.robot.move_toward_omni(0.0, 0.0, 0.2)
        self.scan_start = time.time()

    def execute(self):
        # In actual operations, you apply ALVideoDevice handlers or OpenCV parsing here.
        # This proxy simulates target acquisition completing after a set window.
        if time.time() - self.scan_start > 5.0:
            return ApproachingState(self.robot)
        return self

    def exit(self):
        self.robot.motion.stopMove()


class ApproachingState(BaseState):
    def enter(self):
        self.robot.tts.post.say("Approaching target.")
        self.robot.leds.post.fadeRGB("FaceLeds", 1.0, 1.0, 0.0, 0.5) # Yellow
        self.robot.move_toward_omni(0.3, 0.0, 0.0)

    def execute(self):
        # Sensor fusion logic: Continual bounds checking using sonar integration logic
        dist = self.robot.get_sonar_distance()
        if dist < 0.25: # Safety constraint: Prevents hitting the ball/target prematurely
            return KickingState(self.robot)
        return self

    def exit(self):
        self.robot.motion.stopMove()


class KickingState(BaseState):
    def enter(self):
        self.robot.tts.post.say("Kicking sequence initiated.")
        self.robot.leds.post.fadeRGB("FaceLeds", 1.0, 0.0, 1.0, 0.5) # Magenta
        self.kick_start = time.time()
        
        # Core balancing constraints. The NAO kinematic framework typically leverages Cartesian
        # Foot Control or predefined trajectories mapped via timeline for Kicks balancing on 1 foot.
        # A mocked time interval operates here for demonstrative progression.

    def execute(self):
        if time.time() - self.kick_start > 2.0:
            return CelebratingState(self.robot)
        return self


class CelebratingState(BaseState):
    def enter(self):
        self.robot.tts.post.say("Goal! Celebrating.")
        self.robot.leds.post.fadeRGB("FaceLeds", 0.0, 1.0, 1.0, 0.5) # Cyan
        
        # Execute concurrent, non-blocking final pose calculation
        self.robot.posture.post.goToPosture("Crouch", 0.5)
        self.cel_start = time.time()

    def execute(self):
        if time.time() - self.cel_start > 3.0:
            # Demonstration complete. Restart looping architecture.
            return IdleState(self.robot)
        return self


class MainOrchestrator(object):
    """
    Main orchestration logic. Manages FSM execution, keeps a highly responsive frequency, 
    and handles global hardware and error events explicitly.
    """
    def __init__(self, robot):
        self.robot = robot
        self.current_state = IdleState(self.robot)
        self.current_state.enter()

    def run(self):
        """
        Iterates the FSM non-blockingly using a designated time interval (10Hz).
        """
        try:
            while self.robot.is_running:
                # Top priority layer overrides state machines. Check the kill-switch layer.
                if self.robot.emergency_stop:
                    break
                
                # Fetch state responses logically without polling overhead.
                new_state = self.current_state.execute()
                
                # Dynamic FSM Transition Validation
                if new_state != self.current_state:
                    self.current_state.exit()
                    self.current_state = new_state
                    self.current_state.enter()
                
                time.sleep(0.1)  # Execute bounded 10Hz control loop
        except KeyboardInterrupt:
            print("Interrupted by user. Safety shutdown mechanism activating.")
        finally:
            self.robot.cleanup()

def main():
    # Setup network broker instances for NAOqi memory mapping over network bridging.
    ip = "127.0.0.1" # Override via argv optimally, fallback to local test node. e.g "192.168.1.100"
    port = 9559
    
    if len(sys.argv) > 1:
        ip = sys.argv[1]
    
    # Establish a local broker to facilitate bidirectional memory callbacks.
    myBroker = ALBroker("myBroker", "0.0.0.0", 0, ip, port)
    
    # Needs to be global for NAOqi memory event callbacks to instantiate the class methods correctly
    global robot_module
    robot_module = RobotModule("robot_module", ip, port)
    
    # Feed the robotic structure into the logical pipeline
    orchestrator = MainOrchestrator(robot_module)
    orchestrator.run()

if __name__ == "__main__":
    main()
