#!/bin/bash

# Ensure we're using python 2.7
export PYTHONPATH=$PYTHONPATH:/Users/tadeyemo32/dev/botfc/deps/pepper/Pepper-Controller/pynaoqi/lib/python2.7/site-packages
export DYLD_LIBRARY_PATH=$DYLD_LIBRARY_PATH:/Users/tadeyemo32/dev/botfc/deps/pepper/Pepper-Controller/pynaoqi/lib

echo "Starting the Pepper brain..."
python2 /Users/tadeyemo32/dev/botfc/backend/brain/app.py
