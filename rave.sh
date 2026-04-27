#!/bin/bash

if [ "$1" = "--stats" ]; then
    python3 - <<'EOF'
import json, os

f = '/home/pi/MicroRave/playcounts.json'
if not os.path.exists(f):
    print("No play counts recorded yet.")
else:
    counts = json.load(open(f))
    if not counts:
        print("No play counts recorded yet.")
    else:
        ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        print(f"\n{'PLAYS':>5}  TRACK")
        print("─" * 40)
        for track, count in ranked:
            print(f"{count:>5}  {track}")
        print()
EOF
    exit 0
fi

# Stop the auto-started service so GPIO and audio are free
sudo systemctl stop microrave
sleep 1   # give the kernel a moment to release GPIO and audio devices

# Run MicroRave directly (output visible in this terminal)
cd /home/pi/MicroRave && sudo DISPLAY=:0 XAUTHORITY=/home/pi/.Xauthority venv/bin/python microrave.py

# When you quit (Ctrl+C or Esc), restart the service
sudo systemctl start microrave
