# MicroRave — Quick Reference

## What Is This?
MicroRave is a microwave-shell music player running on a Raspberry Pi 5.
It behaves like a real microwave: enter a time, close the door, music plays.

---

## Running MicroRave

| What | Command |
|------|---------|
| Run manually (with terminal output) | `rave` |
| View play stats | `rave --stats` |
| View live log | `sudo journalctl -u microrave -f` |
| View detailed log file | `tail -f /home/pi/MicroRave/microrave.log` |

MicroRave **starts automatically on boot** via systemd. The `rave` command
stops the background service, runs interactively, then restarts the service on exit.

---

## Service Control

| What | Command |
|------|---------|
| Check if running | `sudo systemctl status microrave` |
| Stop service | `sudo systemctl stop microrave` |
| Start service | `sudo systemctl start microrave` |
| Disable autostart | `sudo systemctl disable microrave` |
| Re-enable autostart | `sudo systemctl enable microrave` |

---

## Button Layout

```
Row 1:  DJ1    DJ2    DJ3
Row 2:  DJ4    DJ5    DJ6
Row 3:  Start  Stop   +30s
Row 4:  1      2      3
Row 5:  4      5      6
Row 6:  7      8      9
Row 7:  VolUp  0      VolDn
Row 8:  Door Switch
```

---

## How to Use

1. **Select a DJ** — press DJ1–DJ6 to choose a playlist
2. **Enter time** — digits shift in from the right (like a real microwave)
3. **Start** — press Start (door must be closed), or just close the door
4. **Pause** — open the door
5. **Resume** — close the door or press Start
6. **Add time** — press +30s at any time during countdown
7. **Cancel** — press Stop/Clear

---

## Easter Eggs

Type these digit sequences and press Start:

| Type | Plays for |
|------|-----------|
| 007  | 1m 42s    |
| 42   | 3m 33s    |
| 069  | 0m 69s    |
| 420  | 4m 20s    |
| 666  | 6m 66s    |
| 67   | 0m 67s    |
| 6767 | 0m 67s    |
| 8008 | 80m 08s   |

Easter eggs play a special clip, show the number flashing 3 times,
then a race-around animation before the countdown starts.
If no audio file is found for an egg, it plays normally.

---

## Music & Audio Files

```
/home/pi/MicroRave/
  music/
    dj1/   dj2/   dj3/
    dj4/   dj5/   dj6/
  sounds/
    beep.mp3
    ding.mp3
    easter/
      007/   42/   069/   420/
      666/   067/  6767/  8008/
  playcounts.json   ← play history (auto-updated)
```

Supported formats: `.mp3  .wav  .ogg  .flac  .m4a`

---

## Hardware

- **Pi 5** — GPIO via `lgpio` (NOT RPi.GPIO — doesn't work on Pi 5)
- **Display** — HDMI fullscreen 7-segment clock, green on black
- **Audio** — HDMI → TV speakers
- **22 switches** wired (SPDT, NO→GPIO, internal pull-up) — all on labeled Adafruit Perma-Proto Pi HAT pads
- **SPI disabled** in `raspi-config` (frees CE0/MOSI for switch inputs)
- **Arduino UNO** — USB to Pi, controls DJ indicator lights via relay board

---

## Arduino → Relay Board Wiring

**Connection:** Arduino USB-B → Pi USB-A (`/dev/ttyACM0`, 9600 baud)

### Control Side (low voltage, safe)

| Arduino Pin | Relay Board Pin | Purpose |
|-------------|-----------------|---------|
| 5V          | VCC             | Power   |
| GND         | GND             | Ground  |
| D2          | IN1             | DJ1 light |
| D3          | IN2             | DJ2 light |
| D4          | IN3             | DJ3 light |
| D5          | IN4             | DJ4 light |
| D6          | IN5             | DJ5 light |
| D7          | IN6             | DJ6 light |
| D8          | IN7             | spare   |
| D9          | IN8             | spare   |

### Load Side (120V AC — use caution)

Each relay channel switches the **hot wire only** to a lamp socket:

```
AC plug hot (black)  → Relay IN terminal → Relay OUT terminal → Socket hot
AC plug neutral (white) ──────────────────────────────────────→ Socket neutral
AC plug ground (green)  ──────────────────────────────────────→ Socket ground
```

- Relay board: SainSmart 8-channel SSR (active HIGH, 2.5–20V trigger)
- Load rating: 0.1–2A per channel at 75–264V AC
- All AC connections must be inside an enclosure

### Serial Commands (for manual testing)

| Command | Effect |
|---------|--------|
| `DJ:1` – `DJ:6` | Activate that DJ's relay, deactivate all others |
| `OFF`   | Deactivate all relays |

---

## Boot Display (Black Screen)

The Pi is configured for a clean black boot with no splash screens.
Backups of the original boot files are stored on the Pi.

**To restore the original boot splash/text:**
```bash
sudo cp /boot/firmware/config.txt.backup /boot/firmware/config.txt
sudo cp /boot/firmware/cmdline.txt.backup /boot/firmware/cmdline.txt
sudo reboot
```

**To re-apply the black boot (after restoring):**
```bash
echo "disable_splash=1" | sudo tee -a /boot/firmware/config.txt
sudo sed -i 's/$/ quiet splash/' /boot/firmware/cmdline.txt
sudo reboot
```

Note: the black screen only affects the HDMI display — SSH always works
regardless of what the screen shows.

---

## Files

| File | Purpose |
|------|---------|
| `microrave.py` | Main application |
| `switch_test.py` | GPIO switch diagnostic tool |
| `playcounts.json` | Persistent play count history |
| `microrave.log` | Detailed application log |
| `microrave.service` | systemd service definition |
| `start_microrave.sh` | Boot launcher script |
| `rave.sh` | Source for the `rave` command |
