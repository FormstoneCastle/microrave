#!/bin/bash
# Configure the Pi to use Mike's laptop (DRSTRANGE) as its NTP time source.
#
# Run on the Pi:    bash ~/setup-pi-ntp-client.sh
# (Or copy it anywhere and run.)
#
# What this does:
#   1. Tells systemd-timesyncd to use DRSTRANGE.local (and a fallback IP) as the NTP server
#   2. Adds public NTP fallbacks for when at home with internet
#   3. Restarts timesyncd and shows status
#
# Expects: laptop has already been set up as NTP server via setup-laptop-ntp-server.ps1.
#
# If DRSTRANGE.local doesn't resolve (Windows mDNS issue), pass the laptop's current
# IP as the first argument:    bash setup-pi-ntp-client.sh 192.168.1.100

set -e

LAPTOP="${1:-DRSTRANGE.local}"

echo "==> Will configure Pi to sync time from: $LAPTOP"
echo

# Quick reachability check (best-effort; don't abort on failure since laptop may be offline now)
if ping -c 1 -W 2 "$LAPTOP" >/dev/null 2>&1; then
    echo "==> $LAPTOP reachable"
else
    echo "==> $LAPTOP NOT reachable right now (that's OK — time will sync next time laptop is on the network)"
fi
echo

echo "==> Writing /etc/systemd/timesyncd.conf..."
sudo tee /etc/systemd/timesyncd.conf > /dev/null <<EOF
# Managed by setup-pi-ntp-client.sh
# Primary: Mike's laptop as a LAN NTP source (works offline at venues)
# Fallback: public NTP for when at home with internet
[Time]
NTP=$LAPTOP
FallbackNTP=time.cloudflare.com 0.pool.ntp.org 1.pool.ntp.org
PollIntervalMinSec=32
PollIntervalMaxSec=2048
EOF

echo "==> Verifying fake-hwclock (battery-less time persistence)..."
if dpkg -s fake-hwclock >/dev/null 2>&1; then
    echo "    fake-hwclock installed."
    # Force a save NOW so we know the saved timestamp is current
    sudo fake-hwclock save
    echo "    Saved current time to /etc/fake-hwclock.data:"
    sudo cat /etc/fake-hwclock.data | sed 's/^/      /'
    # Ensure it saves on shutdown — already default on Pi OS via systemd unit
    sudo systemctl enable fake-hwclock-save.timer >/dev/null 2>&1 || true
    sudo systemctl enable fake-hwclock.service >/dev/null 2>&1 || true
else
    echo "    fake-hwclock NOT installed — installing..."
    sudo apt-get install -y fake-hwclock
    sudo fake-hwclock save
fi
echo

echo "==> Enabling and restarting systemd-timesyncd..."
sudo systemctl enable systemd-timesyncd >/dev/null 2>&1 || true
sudo systemctl restart systemd-timesyncd

# Make sure timesyncd is allowed to set the clock
sudo timedatectl set-ntp true

echo
echo "==> Current status (wait a few seconds and re-check if 'Server: (null)'):"
sleep 2
timedatectl status
echo
echo "==> Sync detail:"
timedatectl timesync-status 2>&1 || true
echo
echo "Done. To re-check later:  timedatectl timesync-status"
echo "If 'Server' shows the laptop, you're synced. If it falls back to a pool.ntp.org address, the laptop wasn't reachable."
