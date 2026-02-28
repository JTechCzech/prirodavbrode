#!/bin/bash

while true; do
    echo "$(date) - Spouštím FFmpeg..."

    ffmpeg \
        -rtsp_transport tcp \
        -use_wallclock_as_timestamps 1 \
        -fflags +genpts+discardcorrupt \
        -avoid_negative_ts make_zero \
        -timeout 5000000 \
        -i "rtsp://admin:pass@192.168.0.214" \
        -c:v copy \
        -c:a aac \
        -b:a 128k \
        -ar 44100 \
        -reset_timestamps 1 \
        -muxdelay 0 \
        -muxpreload 0 \
        -f flv \
        "rtmps://a.rtmps.youtube.com/live2/key"

    echo "$(date) - FFmpeg spadl, čekám 5s a restartuji..."
    sleep 5
done
