#!/usr/bin/env python3
"""
Generate a MicroRave-style 7-segment wallpaper showing ' E:rr'.
Matches the exact geometry and colors of the live display.

Run once on the Pi:
    python3 make_wallpaper.py

Then set the wallpaper:
    pcmanfm --set-wallpaper /home/pi/MicroRave/wallpaper.png --wallpaper-mode=stretched
"""

import os
import sys

# Headless — no display needed
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import pygame

# ── Match microrave.py colors ─────────────────────────────────────────────────
COLOR_BG  = (  0,   0,   0)
COLOR_ON  = (  0, 255,   0)
COLOR_DIM = (  0,  13,   0)
SHOW_DIM  = True

W, H = 1920, 1080
OUT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wallpaper.png")

# 7-segment map — same subset as microrave.py, plus E and r
#   _a_
#  f   b
#   _g_
#  e   c
#   _d_
CHAR_SEGS = {
    ' ': set(),
    'E': set('adefg'),   # top, middle, bottom, upper-left, lower-left
    'r': set('eg'),      # middle stub + lower-left  (standard 7-seg lowercase r)
}


def seg_rects(x, y, dw, dh, T, G) -> dict:
    h2 = dh // 2
    return {
        'a': (x + T + G,  y,             dw - 2*T - 2*G, T       ),
        'b': (x + dw - T, y + T + G,     T,              h2-T-2*G),
        'c': (x + dw - T, y + h2 + G,    T,              h2-T-2*G),
        'd': (x + T + G,  y + dh - T,    dw - 2*T - 2*G, T       ),
        'e': (x,          y + h2 + G,     T,              h2-T-2*G),
        'f': (x,          y + T + G,      T,              h2-T-2*G),
        'g': (x + T + G,  y + h2 - T//2, dw - 2*T - 2*G, T       ),
    }


def draw_char(surf, x, y, ch, dw, dh, T, G):
    lit = CHAR_SEGS.get(ch, set())
    for seg, rect in seg_rects(x, y, dw, dh, T, G).items():
        if seg in lit:
            color = COLOR_ON
        elif SHOW_DIM:
            color = COLOR_DIM
        else:
            continue
        pygame.draw.rect(surf, color, rect, border_radius=2)


pygame.init()
surf = pygame.Surface((W, H))
surf.fill(COLOR_BG)

# ── Geometry — mirrors Display.__init__ in microrave.py ───────────────────────
MARGIN  = 20
avail_w = W - 2 * MARGIN
avail_h = H - 2 * MARGIN
ASPECT  = 0.55

dh_from_w = avail_w / (ASPECT * (4 + 1/3 + 0.4))
dh = int(min(dh_from_w, avail_h))
dw = int(dh * ASPECT)
T  = max(8,  int(dh * 0.10))
G  = max(2,  int(T  * 0.15))

col_w = dw // 3
sp    = max(4, int(dw * 0.08))

total_w = 4 * dw + col_w + 5 * sp
ox = (W - total_w) // 2
oy = (H - dh) // 2

dx = [
    ox,
    ox +     dw + sp,
    ox + 2 * dw + 2 * sp + col_w + sp,
    ox + 3 * dw + 3 * sp + col_w + sp,
]
colon_x = ox + 2 * dw + 2 * sp

# ── Render " E:rr" ────────────────────────────────────────────────────────────
for i, ch in enumerate([' ', 'E', 'r', 'r']):
    draw_char(surf, dx[i], oy, ch, dw, dh, T, G)

# Colon dots
cx = colon_x + col_w // 2
dot_r = max(4, T // 2)
pygame.draw.circle(surf, COLOR_ON, (cx, oy + dh // 3),     dot_r)
pygame.draw.circle(surf, COLOR_ON, (cx, oy + 2 * dh // 3), dot_r)

pygame.image.save(surf, OUT)
pygame.quit()
print(f"Saved: {OUT}")
print(f"Set wallpaper with:")
print(f"  pcmanfm --set-wallpaper {OUT} --wallpaper-mode=stretched")
