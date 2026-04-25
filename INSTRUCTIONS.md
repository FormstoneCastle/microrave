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
Row 7:  VolUp  0      VolDn   (Vol not yet wired)
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
- **20 switches** wired (SPDT, NO→GPIO, internal pull-up)

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
