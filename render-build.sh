#!/usr/bin/env bash
# exit on error
set -o errexit

# Install python dependencies
pip install -r requirements.txt

# OpenCV dependency for Render Linux environment
# Note: Render doesn't allow 'apt-get', so we use opencv-python-headless
# if you find errors with libGL. I have already updated requirements.txt 
# to use opencv-contrib-python which usually works.
