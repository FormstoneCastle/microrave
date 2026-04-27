#!/bin/bash
# start_microrave.sh
# Waits for the XWayland display socket to appear, then launches MicroRave.
# Called by systemd — do not run this directly.

export DISPLAY=:0
export XAUTHORITY=/home/pi/.Xauthority

cd /home/pi/MicroRave

# Block until XWayland's Unix socket exists (desktop is up and accepting connections)
echo "MicroRave launcher: waiting for display..."
until [ -S /tmp/.X11-unix/X0 ]; do
    sleep 1
done

# Paint the screen black immediately — before compositor finishes settling
# This hides the desktop wallpaper/taskbar during MicroRave startup
xsetroot -solid black

# Small extra pause to let the compositor fully settle
sleep 2

echo "MicroRave launcher: display ready. Starting MicroRave..."
exec venv/bin/python microrave.py
