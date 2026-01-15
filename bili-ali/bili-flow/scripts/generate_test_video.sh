#!/bin/bash
# Generates a 5-second test video with a counter
ffmpeg -y -f lavfi -i testsrc=duration=5:size=1280x720:rate=30 -f lavfi -i sine=frequency=1000:duration=5 -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest test_video.mp4
