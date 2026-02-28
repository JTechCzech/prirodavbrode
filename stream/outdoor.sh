#!/bin/bash

while true; do
  echo "$(date) - Spouštím FFmpeg..."

  ffmpeg \
    -rtsp_transport tcp \
    -timeout 5000000 \
    -i "rtsp://admin:pass@192.168.0.41/11" \
    -c:v copy \
    -c:a aac \
    -b:a 128k \
    -ar 44100 \
    -fflags +nobuffer \
    -max_delay 500000 \
    -f flv \
    "rtmps://a.rtmps.youtube.com/live2/key"

  echo "$(date) - FFmpeg spadl, čekám 5s a restartuji..."
  sleep 5
done
