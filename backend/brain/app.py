from naoqi import ALProxy
import sys

# Replace with the actual IP address of the robot if not running locally/simulated
ROBOT_IP = "127.0.0.1" 
ROBOT_PORT = 9559

def main():
    try:
        # Initialize the proxy to the TextToSpeech module
        tts = ALProxy("ALTextToSpeech", ROBOT_IP, ROBOT_PORT)
        
        # Make the robot say hello world
        text = "Hello world!"
        print("Sending to robot: " + text)
        tts.say(text)
        
    except Exception as e:
        print("Could not create proxy to ALTextToSpeech")
        print("Error was: ", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
