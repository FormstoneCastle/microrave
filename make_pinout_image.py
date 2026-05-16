"""Render the MicroRave switch pinout as a printable PNG."""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

ROWS = [
    ("SW",  "Button",     "GPIO", "HAT Pad", "Phys"),
    ("1",   "DJ1",        "4",    "#4",      "7"),
    ("2",   "DJ2",        "5",    "#5",      "29"),
    ("3",   "DJ3",        "6",    "#6",      "31"),
    ("4",   "DJ4",        "10",   "MOSI",    "19"),
    ("5",   "DJ5",        "27",   "#27",     "13"),
    ("6",   "DJ6",        "9",    "MISO",    "21"),
    ("7",   "Start",      "2",    "SDA",     "3"),
    ("8",   "Stop/Clear", "11",   "CLK",     "23"),
    ("9",   "+30s",       "12",   "#12",     "32"),
    ("10",  "Digit 1",    "13",   "#13",     "33"),
    ("11",  "Digit 2",    "16",   "#16",     "36"),
    ("12",  "Digit 3",    "17",   "#17",     "11"),
    ("13",  "Digit 4",    "18",   "#18",     "12"),
    ("14",  "Digit 5",    "19",   "#19",     "35"),
    ("15",  "Digit 6",    "20",   "#20",     "38"),
    ("16",  "Digit 7",    "21",   "#21",     "40"),
    ("17",  "Digit 8",    "22",   "#22",     "15"),
    ("18",  "Digit 9",    "23",   "#23",     "16"),
    ("19",  "Digit 0",    "24",   "#24",     "18"),
    ("20",  "Door",       "25",   "#25",     "22"),
    ("21",  "Vol Up",     "3",    "SCL",     "5"),
    ("22",  "Vol Down",   "8",    "CE0",     "24"),
    ("—",   "Unused",     "14",   "TXD",     "8"),
    ("—",   "Unused",     "15",   "RXD",     "10"),
    ("—",   "Unused",     "7",    "CE1",     "26"),
]

TITLE = "MicroRave — Switch Pinout"
SUBTITLE = "Adafruit Perma-Proto Pi HAT  |  Pi 5  |  SPI must be disabled"

# Letter @ 200 DPI = 1700 x 2200; we'll target 1700 wide and let height adapt.
WIDTH = 1700
MARGIN = 80
ROW_H = 64
HEADER_PAD = 200
FOOTER_PAD = 220

# Layout: 5 columns
COLS = [
    ("SW",      120, "center"),
    ("Button",  380, "left"),
    ("GPIO",    180, "center"),
    ("HAT Pad", 220, "center"),
    ("Phys",    140, "center"),
]
TABLE_W = sum(c[1] for c in COLS)
TABLE_X = (WIDTH - TABLE_W) // 2

height = HEADER_PAD + ROW_H * len(ROWS) + FOOTER_PAD
img = Image.new("RGB", (WIDTH, height), "white")
draw = ImageDraw.Draw(img)

def font(size, bold=False):
    name = "arialbd.ttf" if bold else "arial.ttf"
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()

F_TITLE   = font(54, bold=True)
F_SUB     = font(28)
F_HEADER  = font(32, bold=True)
F_CELL    = font(30)
F_CELL_B  = font(30, bold=True)
F_FOOT    = font(22)

# Title
draw.text((WIDTH // 2, 50), TITLE, fill="black", font=F_TITLE, anchor="mt")
draw.text((WIDTH // 2, 120), SUBTITLE, fill="#444444", font=F_SUB, anchor="mt")

# Header row
y = HEADER_PAD
draw.rectangle([(TABLE_X, y), (TABLE_X + TABLE_W, y + ROW_H)], fill="#1a1a1a")
x = TABLE_X
for label, w, align in COLS:
    if align == "center":
        draw.text((x + w // 2, y + ROW_H // 2), label, fill="white",
                  font=F_HEADER, anchor="mm")
    else:
        draw.text((x + 20, y + ROW_H // 2), label, fill="white",
                  font=F_HEADER, anchor="lm")
    x += w
y += ROW_H

# Data rows — group-coloured bands for readability
GROUP_COLORS = {
    "DJ":     "#fff4e0",
    "CTRL":   "#e8f1ff",
    "DIGIT":  "#f3f3f3",
    "DOOR":   "#ffe0e0",
    "VOL":    "#e6ffe6",
    "UNUSED": "#dddddd",
}
def group(button):
    b = button.lower()
    if b == "unused": return "UNUSED"
    if b.startswith("dj"): return "DJ"
    if b.startswith("digit"): return "DIGIT"
    if b == "door": return "DOOR"
    if b.startswith("vol"): return "VOL"
    return "CTRL"

for row in ROWS[1:]:
    sw_id, button, gpio, pad, phys = row
    bg = GROUP_COLORS[group(button)]
    draw.rectangle([(TABLE_X, y), (TABLE_X + TABLE_W, y + ROW_H)], fill=bg)
    # vertical separators
    x = TABLE_X
    for _, w, _ in COLS:
        draw.line([(x, y), (x, y + ROW_H)], fill="#cccccc", width=1)
        x += w
    draw.line([(x, y), (x, y + ROW_H)], fill="#cccccc", width=1)
    # cell content
    cells = [sw_id, button, gpio, pad, phys]
    x = TABLE_X
    for cell, (_, w, align) in zip(cells, COLS):
        f = F_CELL_B if align == "center" else F_CELL
        if align == "center":
            draw.text((x + w // 2, y + ROW_H // 2), cell, fill="black",
                      font=f, anchor="mm")
        else:
            draw.text((x + 20, y + ROW_H // 2), cell, fill="black",
                      font=f, anchor="lm")
        x += w
    y += ROW_H

# Bottom border
draw.line([(TABLE_X, y), (TABLE_X + TABLE_W, y)], fill="#1a1a1a", width=2)

# Footer notes
y += 40
notes = [
    "Wiring per switch:  Common → GND rail   |   NO → HAT pad   |   NC → unconnected",
    "Switch OPEN = pin HIGH (1)   |   Switch CLOSED = pin LOW (0)   |   Internal pull-ups in software",
    "Required:  sudo raspi-config → Interface Options → SPI → No → reboot",
    "Avoid: GPIO 0/1 (HAT EEPROM, ID_SD/ID_SC)  |  GPIO 26 (not broken out on this HAT)",
]
for line in notes:
    draw.text((WIDTH // 2, y), line, fill="#222222", font=F_FOOT, anchor="mt")
    y += 36

out = Path(__file__).parent / "switch_pinout.png"
img.save(out, dpi=(200, 200))
print(f"Saved: {out}  ({img.size[0]}x{img.size[1]})")
