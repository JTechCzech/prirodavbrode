#!/bin/bash

LOCAL_DIR="/home/jtech/Documents/prulety-nvr/nvr"
FTP_HOST="host"
FTP_USER="user"
FTP_PASS="pass"
REMOTE_DIR="/www/nvr"

inotifywait -m -r -e create -e moved_to "$LOCAL_DIR" | while read path action file
do
    echo "Nový soubor: $file – synchronizuji..."

    lftp -u "$FTP_USER","$FTP_PASS" ftp://"$FTP_HOST" <<EOF
    mirror -R --only-newer --parallel=4 "$LOCAL_DIR" "$REMOTE_DIR"
    quit
EOF

done
