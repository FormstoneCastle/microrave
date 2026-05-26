"""
MicroRave network adapters
==========================
Browser-based simulator IO: aiohttp HTTP+WebSocket server replaces the
physical button panel, HDMI display, and TV speakers.

Activated by setting MICRORAVE_IO=net before launching microrave.py.

These classes mirror the adapters_hw.py public API one-for-one. microrave.py
itself never changes — its factories (make_input_adapter / make_display /
make_audio / make_relays) select between hardware and net implementations.

Architecture:
  - A single _Server runs aiohttp in a background thread with its own
    asyncio loop. All four adapter classes share that server.
  - Pi -> browser messages go out over WebSocket (display frames, audio,
    DJ-light state).
  - Browser -> Pi messages are button-press events, dispatched into the
    Net adapters' registered callbacks (which match the lgpio-on-press
    contract used by HardwareInput).

Step 3a (this file's current state): scaffold only.
  - Server boots, serves a placeholder page, accepts WS connections.
  - All adapter methods are no-ops that log what microrave.py asked for.
  - Goal: `MICRORAVE_IO=net python microrave.py` runs cleanly and the
    browser shows "MicroRave Sim — connected" with a live log of what
    the Pi is doing.

Real rendering / audio / input handling are deferred to 3b–3e.
"""

import asyncio
import json
import logging
import os
import threading
import time
from typing import Callable

from aiohttp import web, WSMsgType

# Share the playcounts file path with the hardware audio adapter. The file
# lives on the Pi and is written by AudioHW during real-hardware play; the
# sim doesn't track plays but still needs to be able to clear the persistent
# tally between events.
from adapters_hw import (
    backup_playcounts,
    load_playcounts,
    playcount_key,
    save_playcounts,
)

log = logging.getLogger("MicroRave.net")

# Bind on all interfaces so the Windows machine can reach the Pi.
HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8080

# microrave.py is run from its repo root, so cwd holds music/ and sounds/.
# Use cwd (not __file__) because the Pi launcher does `cd /home/pi/MicroRave`
# before exec — that's the canonical layout.
_MICRORAVE_ROOT = os.path.abspath(os.getcwd())
_MUSIC_DIR     = os.path.join(_MICRORAVE_ROOT, "music")
_SOUNDS_DIR    = os.path.join(_MICRORAVE_ROOT, "sounds")


def _path_to_url(path: str) -> str:
    """Convert a track path (absolute or relative to MicroRave root) to a
    browser-reachable URL like '/music/dj1/song.mp3'."""
    if not path:
        return ""
    p = os.path.abspath(path)
    try:
        rel = os.path.relpath(p, _MICRORAVE_ROOT)
    except ValueError:
        # Different drive on Windows — fall back to basename in /music/.
        rel = os.path.join("music", os.path.basename(p))
    return "/" + rel.replace(os.sep, "/")


# =============================================================================
# PLACEHOLDER WEB CLIENT (inline; 3b/3c/3d will replace with static/index.html)
# =============================================================================

_PLACEHOLDER_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>MicroRave Sim</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Oswald:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --panel-bg: #f0ebe2;      /* off-white panel face */
      --panel-border: #2dd92d;   /* bright green outer frame */
      --btn-face: #fafafa;       /* white control buttons */
      --btn-outline: #b8b8b8;
      --btn-text: #1a1a1a;
      --dj-face: #1a1a1a;
      --dj-text: #ffffff;
      --dj-lit-glow: #ffffff;
      --open-red: #d62828;
      --display-bg: #0a1a14;
      --seg-on: #36ff5c;
      --seg-off: #0c2e16;
      --pwr-green: #2dd92d;
    }
    html, body { margin: 0; height: 100%; overflow: hidden;
                 background: #2a2a2a; color: #ddd;
                 font-family: 'Oswald', 'Segoe UI', system-ui, sans-serif; }
    /* Floating status pip — tiny corner indicator instead of a full header. */
    #status { position: fixed; top: 8px; right: 10px; z-index: 20;
              font-size: 11px; padding: 2px 8px; border-radius: 10px;
              background: rgba(0,0,0,0.55); color: #888;
              letter-spacing: 0.04em; pointer-events: none; }
    #status.connected    { color: #4ade80; }
    #status.disconnected { color: #f87171; }

    /* Centered panel that scales to the viewport while keeping portrait
       proportions like the real microwave face. */
    .panel {
      position: absolute; inset: 0;
      margin: auto;
      width: min(94vw, calc(100vh * 0.62));
      height: min(98vh, calc(94vw / 0.62));
      background: var(--panel-border);
      padding: clamp(8px, 1.6vmin, 18px);
      border-radius: 8px;
      box-shadow: 0 10px 40px rgba(0,0,0,0.6);
      box-sizing: border-box;
      display: flex;
    }
    .panel-inner {
      flex: 1;
      background: var(--panel-bg);
      border-radius: 4px;
      padding: clamp(10px, 2.2vmin, 26px);
      box-sizing: border-box;
      display: grid;
      /* display (small) | dj-grid (small) | num-grid (fills) | open-btn (narrow) */
      grid-template-rows: auto auto 1fr auto;
      gap: clamp(8px, 1.6vmin, 18px);
      min-height: 0;
    }

    /* HDMI display — matches photo proportions (squat, rounded, ~65% width).
       Symmetric vertical breathing room above and below — the grid gap
       handles the space below, this margin-top mirrors it on top. */
    .display {
      background: var(--display-bg);
      border-radius: clamp(10px, 2.4vmin, 28px);
      padding: clamp(6px, 1.4vmin, 14px) clamp(10px, 2vmin, 22px);
      box-shadow: inset 0 2px 8px rgba(0,0,0,0.7);
      aspect-ratio: 1.85 / 1;
      width: 65%;
      /* extra breathing room below the clock before the DJ row */
      margin: clamp(8px, 1.6vmin, 18px) auto clamp(8px, 2vmin, 24px);
      display: flex; align-items: center; justify-content: center;
      box-sizing: border-box;
    }
    svg#display-svg { width: 100%; height: 100%; display: block; }
    .seg { stroke: none; }
    .seg.on  { fill: var(--seg-on);
               filter: drop-shadow(0 0 3px var(--seg-on)); }
    .seg.off { fill: var(--seg-off); }

    /* DJ button row (3×2, smaller than digit buttons) ---------------- */
    .dj-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      grid-template-rows: repeat(2, minmax(0, 1fr));
      gap: clamp(5px, 1vmin, 10px);
      /* Photo: DJ block is about 18% of panel content height */
      aspect-ratio: 3.6 / 1;
      width: 100%;
      min-height: 0;
      /* extra breathing room below DJ row before START/+30/STOP */
      margin-bottom: clamp(8px, 2vmin, 24px);
    }
    /* Numeric grid (3×5, fills remaining space — dominant area) ----- */
    .num-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      grid-template-rows: repeat(5, minmax(0, 1fr));
      gap: clamp(6px, 1.2vmin, 14px);
      min-height: 0;
    }
    /* Shared sizing/shape for every cell in the grid */
    .btn {
      border-radius: clamp(8px, 2.2vmin, 22px);
      display: flex; align-items: center; justify-content: center;
      cursor: pointer; user-select: none;
      min-height: 0; min-width: 0;
      box-sizing: border-box;
      text-align: center;
    }
    .dj-btn {
      background: var(--dj-face); color: var(--dj-text);
      font-family: 'Oswald', sans-serif; font-weight: 500;
      /* Title Case as engraved/printed — no uppercase transform */
      font-size: clamp(11px, 1.9vmin, 22px);
      line-height: 1.1; letter-spacing: 0.5px;
      padding: 2px 6px;
      border: 1px solid #0a0a0a;
      box-shadow: 0 1px 0 rgba(255,255,255,0.10) inset,
                  0 2px 3px rgba(0,0,0,0.3);
      transition: filter 60ms;
    }
    .dj-btn.lit {
      /* Background stays black; text glows white — matches LED behavior. */
      color: #fff;
      text-shadow:
        0 0 4px  var(--dj-lit-glow),
        0 0 10px var(--dj-lit-glow),
        0 0 18px var(--dj-lit-glow);
    }
    .dj-btn.pressed { filter: brightness(1.4); }

    /* Numeric + control buttons -------------------------------------- */
    .num-btn {
      background: var(--btn-face); color: var(--btn-text);
      border: 1px solid var(--btn-outline);
      font-family: 'Oswald', sans-serif; font-weight: 700;
      font-size: clamp(22px, 4.6vmin, 52px);
      box-shadow: 0 1px 2px rgba(0,0,0,0.08);
      transition: background 60ms;
    }
    .num-btn.small  { font-size: clamp(14px, 2.6vmin, 28px);
                      letter-spacing: 1px; }
    .num-btn:active, .num-btn.pressed { background: #d8d8d8; }
    .num-btn.pwr {
      flex-direction: column; gap: 2px;
      font-size: clamp(13px, 2.2vmin, 22px); letter-spacing: 1px;
    }
    .num-btn.pwr svg { display: block;
                       width: clamp(18px, 3vmin, 32px); height: auto; }
    .pwr-arrow { fill: var(--pwr-green); }

    /* OPEN door toggle — narrow pill centered on white panel area --- */
    .open-btn {
      background: var(--btn-face); border: 1px solid var(--btn-outline);
      border-radius: clamp(8px, 2vmin, 20px); color: var(--open-red);
      font-family: 'Oswald', sans-serif; font-weight: 700;
      font-size: clamp(36px, 7vmin, 78px);
      letter-spacing: clamp(4px, 1.2vmin, 10px);
      text-align: center;
      padding: clamp(6px, 1.4vmin, 14px) clamp(20px, 5vmin, 50px);
      width: 70%;
      margin: 0 auto;
      cursor: pointer; user-select: none;
      box-shadow: 0 2px 3px rgba(0,0,0,0.1);
      box-sizing: border-box;
    }
    .open-btn.closed { background: #e0e0e0; color: #777; }
  </style>
</head>
<body>
  <span id="status" class="disconnected">disconnected</span>
  <div class="panel">
    <div class="panel-inner">

      <div class="display">
        <svg id="display-svg" preserveAspectRatio="xMidYMid meet"></svg>
      </div>

      <!-- DJ button order matches the built panel (photo, not the SVG file):
           Row 0: LoveGrove   | Who      | Adam T. Rush
           Row 1: ChillieWillie | HISTORY | DJ Impulse -->
      <div class="dj-grid">
        <div class="btn dj-btn" data-pin="4">DJ LoveGrove</div>
        <div class="btn dj-btn" data-pin="5">DJ Who</div>
        <div class="btn dj-btn" data-pin="6">Adam T. Rush</div>
        <div class="btn dj-btn" data-pin="10">ChillieWillie</div>
        <div class="btn dj-btn" data-pin="27">HISTORY</div>
        <div class="btn dj-btn" data-pin="9">DJ Impulse</div>
      </div>

      <div class="num-grid">
        <div class="btn num-btn small" data-pin="2">START</div>
        <div class="btn num-btn small" data-pin="12">+30</div>
        <div class="btn num-btn small" data-pin="11">STOP</div>

        <div class="btn num-btn" data-pin="13">1</div>
        <div class="btn num-btn" data-pin="16">2</div>
        <div class="btn num-btn" data-pin="17">3</div>

        <div class="btn num-btn" data-pin="18">4</div>
        <div class="btn num-btn" data-pin="19">5</div>
        <div class="btn num-btn" data-pin="20">6</div>

        <div class="btn num-btn" data-pin="21">7</div>
        <div class="btn num-btn" data-pin="22">8</div>
        <div class="btn num-btn" data-pin="23">9</div>

        <!-- Photo: left PWR has up-arrow (volume up), right PWR has down-arrow -->
        <div class="btn num-btn pwr" data-pin="3">
          <svg viewBox="0 0 22 14"><polygon class="pwr-arrow" points="11,1 21,13 1,13"/></svg>
          <span>PWR</span>
        </div>
        <div class="btn num-btn" data-pin="24">0</div>
        <div class="btn num-btn pwr" data-pin="8">
          <svg viewBox="0 0 22 14"><polygon class="pwr-arrow" points="1,1 21,1 11,13"/></svg>
          <span>PWR</span>
        </div>
      </div>

      <div class="open-btn closed" id="openBtn">CLOSED</div>

    </div>
  </div>

  <script>
  // ============================================================
  // 7-segment renderer — mirrors adapters_hw.Display visuals.
  // ============================================================
  const CHAR_SEGS = {
    '0': 'abcdef',
    '1': 'bc',
    '2': 'abdeg',
    '3': 'abcdg',
    '4': 'bcfg',
    '5': 'acdfg',
    '6': 'acdefg',
    '7': 'abc',
    '8': 'abcdefg',
    '9': 'abcdfg',
    'd': 'bcdeg',
    'J': 'bcd',
    '-': 'g',
    ' ': ''
  };

  // Digit geometry — mirrors adapters_hw.Display._seg_rects exactly.
  // Hardware: H=180-ish, W=H*0.55, T=H*0.10, G=max(2, T*0.15) ≈ 3.
  // Drawn as plain rectangles with a small border-radius (rx=2 in SVG ≙
  // pygame's border_radius=2). Segments DO NOT touch — there are T+G gaps
  // at the corners between horizontal and vertical bars.
  const H = 180;
  const W = 99;              // H * 0.55
  const T = 18;              // H * 0.10
  const G = 3;               // T * 0.15, min 2
  const H2 = H / 2;
  const BAR_W = W - 2*T - 2*G;
  const SIDE_H = H2 - T - 2*G;
  // [x, y, w, h] per segment, origin at top-left of the digit box.
  const SEG_RECTS = {
    a: [T + G,   0,                BAR_W, T    ],
    b: [W - T,   T + G,            T,     SIDE_H],
    c: [W - T,   H2 + G,           T,     SIDE_H],
    d: [T + G,   H - T,            BAR_W, T    ],
    e: [0,       H2 + G,           T,     SIDE_H],
    f: [0,       T + G,            T,     SIDE_H],
    g: [T + G,   H2 - T/2,         BAR_W, T    ],
  };

  // Build SVG: 4 digits + colon. Total viewBox width matches layout.
  const svg = document.getElementById('display-svg');
  const NS = 'http://www.w3.org/2000/svg';
  const SP = 16;             // spacing between adjacent digits
  const COLON_W = 28;        // width allotted to the colon block (hw uses dw/3)
  const DIGIT_PITCH = W + SP;
  const TOTAL_W = 4 * W + 4 * SP + COLON_W;
  svg.setAttribute('viewBox', `0 0 ${TOTAL_W} ${H}`);

  const digitRefs = [];      // [{a:<rect>, b:..., ...}, x4]
  const xPositions = [
    0,
    DIGIT_PITCH,
    2 * DIGIT_PITCH + COLON_W,
    3 * DIGIT_PITCH + COLON_W
  ];
  for (let d = 0; d < 4; d++) {
    const g = document.createElementNS(NS, 'g');
    g.setAttribute('transform', `translate(${xPositions[d]},0)`);
    const refs = {};
    for (const seg of 'abcdefg') {
      const [rx, ry, rw, rh] = SEG_RECTS[seg];
      const r = document.createElementNS(NS, 'rect');
      r.setAttribute('x', rx);
      r.setAttribute('y', ry);
      r.setAttribute('width', rw);
      r.setAttribute('height', rh);
      r.setAttribute('rx', 2);
      r.setAttribute('ry', 2);
      r.setAttribute('class', 'seg off');
      g.appendChild(r);
      refs[seg] = r;
    }
    svg.appendChild(g);
    digitRefs.push(refs);
  }
  // Colon: 2 circles between digits 1 and 2. Hardware uses radius T/2,
  // positioned at oy + dh/3 and oy + 2*dh/3.
  const colonX = 2 * DIGIT_PITCH - SP + COLON_W/2;
  const colonTop = document.createElementNS(NS, 'circle');
  const colonBot = document.createElementNS(NS, 'circle');
  const colonR = Math.max(4, T / 2);
  for (const [c, cy] of [[colonTop, H / 3], [colonBot, 2 * H / 3]]) {
    c.setAttribute('cx', colonX);
    c.setAttribute('cy', cy);
    c.setAttribute('r', colonR);
    c.setAttribute('class', 'seg off');
    svg.appendChild(c);
  }

  // ============================================================
  // Render functions
  // ============================================================
  function setDigit(d, lit) {
    // lit is a Set of segment letters that should be 'on'.
    for (const seg of 'abcdefg') {
      digitRefs[d][seg].setAttribute(
        'class', 'seg ' + (lit.has(seg) ? 'on' : 'off')
      );
    }
  }
  function setColon(on) {
    const cls = 'seg ' + (on ? 'on' : 'off');
    colonTop.setAttribute('class', cls);
    colonBot.setAttribute('class', cls);
  }
  function renderText(text, colon) {
    // text is exactly 4 chars (Pi pads/truncates).
    const padded = (text + '    ').slice(0, 4);
    for (let d = 0; d < 4; d++) {
      const ch = padded[d];
      const segStr = CHAR_SEGS[ch] !== undefined ? CHAR_SEGS[ch] : '';
      setDigit(d, new Set(segStr));
    }
    setColon(!!colon);
  }
  function renderSegs(segsList, colon) {
    // segsList is an array of 4 arrays of letters.
    for (let d = 0; d < 4; d++) {
      const segs = segsList[d] || [];
      setDigit(d, new Set(segs));
    }
    setColon(!!colon);
  }

  // ============================================================
  // Button panel — wire up the static markup. data-pin attrs already
  // mirror the microrave.py PIN_* constants.
  // ============================================================
  function firePress(btn) {
    const pin = parseInt(btn.dataset.pin, 10);
    btn.classList.add('pressed');
    setTimeout(() => btn.classList.remove('pressed'), 120);
    send({ type: 'press', pin });
  }
  for (const btn of document.querySelectorAll('[data-pin]')) {
    btn.addEventListener('mousedown', () => firePress(btn));
    btn.addEventListener('touchstart', (e) => { e.preventDefault(); firePress(btn); },
                         { passive: false });
  }

  // Door toggle — OPEN button. Pi assumes door starts closed.
  let doorClosed = true;
  const doorBtn = document.getElementById('openBtn');
  function paintDoor() {
    doorBtn.classList.toggle('closed', doorClosed);
    doorBtn.textContent = doorClosed ? 'CLOSED' : 'OPEN';
  }
  doorBtn.addEventListener('click', () => {
    doorClosed = !doorClosed;
    paintDoor();
    send({ type: 'door', closed: doorClosed });
  });

  // Keyboard shortcuts: 0-9 → digit pins, q/w/e/r/t/y → DJ1..DJ6,
  // Enter → Start, Esc/Backspace → Stop, +/= → +30s, Space → door toggle.
  const KEY_TO_PIN = {
    '0': 24, '1': 13, '2': 16, '3': 17, '4': 18,
    '5': 19, '6': 20, '7': 21, '8': 22, '9': 23,
    'q': 4, 'w': 5, 'e': 6, 'r': 10, 't': 27, 'y': 9,
    'Enter': 2, 'Escape': 11, 'Backspace': 11,
    '+': 12, '=': 12,
  };
  document.addEventListener('keydown', (ev) => {
    if (ev.repeat) return;
    if (ev.key === ' ') { ev.preventDefault(); doorBtn.click(); return; }
    const pin = KEY_TO_PIN[ev.key];
    if (pin !== undefined) {
      const btn = document.querySelector('[data-pin="' + pin + '"]');
      if (btn) firePress(btn);
      else send({ type: 'press', pin });
    }
  });

  // ============================================================
  // WebSocket
  // ============================================================
  const status = document.getElementById('status');
  // Minimal log: console + optional callout to user when audio is blocked.
  // The on-screen event drawer was dropped in the panel redesign.
  function append(typ, raw) {
    if (window.console) console.log('[ws]', typ, raw);
  }
  // ============================================================
  // Audio — HTMLAudioElement playback. Music + easter share one
  // element (only one of them is ever active). Beep/ding are
  // separate so they can layer over music like the hardware
  // pygame channels do.
  // ============================================================
  const musicEl  = new Audio();
  const easterEl = new Audio();
  const beepEl   = new Audio('/sounds/beep.mp3');
  const dingEl   = new Audio('/sounds/ding.mp3');
  beepEl.preload = 'auto';
  dingEl.preload = 'auto';

  // Browsers block <audio>.play() until the user has interacted with
  // the page. Track that so we can warn instead of throwing.
  let userGestured = false;
  document.addEventListener('pointerdown', () => { userGestured = true; },
                            { once: false, capture: true });
  document.addEventListener('keydown',     () => { userGestured = true; },
                            { once: false, capture: true });

  function safePlay(el) {
    const p = el.play();
    if (p && p.catch) p.catch((err) => {
      if (!userGestured) {
        append('audio.gesture-needed', 'click anywhere to enable audio');
      } else {
        append('audio.err', err.message || String(err));
      }
    });
  }

  musicEl.addEventListener('ended', () => {
    send({ type: 'music_ended' });
  });
  easterEl.addEventListener('ended', () => {
    send({ type: 'easter_ended' });
  });

  function playMusic(url, volume) {
    easterEl.pause();
    musicEl.src = url;
    if (typeof volume === 'number') musicEl.volume = volume;
    safePlay(musicEl);
  }
  function playEaster(url, volume) {
    musicEl.pause();
    easterEl.src = url;
    if (typeof volume === 'number') easterEl.volume = volume;
    safePlay(easterEl);
  }
  function playSfx(el, url) {
    // Use a clone so rapid re-triggers don't cut off the previous one.
    const c = el.cloneNode();
    if (url) c.src = url;
    safePlay(c);
  }

  function handle(msg) {
    switch (msg.type) {
      case 'display.show':
        renderText(msg.text, msg.colon);
        break;
      case 'display.show_segs':
        renderSegs(msg.segs, msg.colon);
        break;
      case 'audio.start':
        playMusic(msg.url, msg.volume);
        break;
      case 'audio.start_easter':
        playEaster(msg.url, msg.volume);
        break;
      case 'audio.pause':
        musicEl.pause();
        easterEl.pause();
        break;
      case 'audio.resume':
        // Resume whichever was playing — easter wins if it has a src.
        if (easterEl.src && easterEl.currentTime > 0) safePlay(easterEl);
        else if (musicEl.src) safePlay(musicEl);
        break;
      case 'audio.stop':
        musicEl.pause();
        musicEl.currentTime = 0;
        musicEl.removeAttribute('src');
        easterEl.pause();
        easterEl.currentTime = 0;
        easterEl.removeAttribute('src');
        break;
      case 'audio.beep':
        playSfx(beepEl, msg.url);
        break;
      case 'audio.ding':
        playSfx(dingEl, msg.url);
        break;
      case 'audio.volume':
        if (typeof msg.volume === 'number') {
          musicEl.volume  = msg.volume;
          easterEl.volume = msg.volume;
        }
        break;
      case 'audio.shutdown':
        musicEl.pause(); easterEl.pause();
        break;
      case 'lights.dj':
        setActiveDj(msg.dj);
        break;
    }
  }

  // DJ light indicator — pin map mirrors microrave.PIN_DJ.
  // Text glows white; background stays black (matches the LED behavior
  // of the physical lit/unlit DJ buttons).
  const DJ_PIN_BY_NUM = { 1: 4, 2: 5, 3: 6, 4: 10, 5: 27, 6: 9 };
  function setActiveDj(dj) {
    for (const b of document.querySelectorAll('.dj-btn.lit')) {
      b.classList.remove('lit');
    }
    const pin = DJ_PIN_BY_NUM[dj];
    if (pin === undefined) return;
    const btn = document.querySelector('.dj-btn[data-pin="' + pin + '"]');
    if (btn) btn.classList.add('lit');
  }
  let activeWs = null;
  const sendQueue = [];
  function send(msg) {
    if (activeWs && activeWs.readyState === WebSocket.OPEN) {
      activeWs.send(JSON.stringify(msg));
    } else {
      sendQueue.push(msg);
    }
  }
  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(proto + '://' + location.host + '/ws');
    ws.onopen = () => {
      activeWs = ws;
      status.textContent = 'connected';
      status.className = 'connected';
      while (sendQueue.length) ws.send(JSON.stringify(sendQueue.shift()));
    };
    ws.onclose = () => {
      activeWs = null;
      status.textContent = 'disconnected — retrying';
      status.className = 'disconnected';
      setTimeout(connect, 1000);
    };
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        handle(msg);
      } catch (err) {
        append('?', e.data);
      }
    };
  }
  connect();
  paintDoor();
  </script>
</body>
</html>
"""


# =============================================================================
# SERVER  (singleton, started lazily by whichever Net* adapter loads first)
# =============================================================================

class _Server:
    """aiohttp app running in a background thread with its own asyncio loop."""

    _instance: "_Server | None" = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> "_Server":
        with cls._lock:
            if cls._instance is None:
                cls._instance = _Server()
                cls._instance.start()
            return cls._instance

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._clients: set[web.WebSocketResponse] = set()
        self._clients_lock = threading.Lock()
        # Handlers keyed by inbound message "type". Multiple adapters can
        # register without stepping on each other (NetInput owns press/door,
        # NetAudio owns music_ended/easter_ended).
        self._handlers: dict[str, Callable[[dict], None]] = {}
        self._runner: web.AppRunner | None = None
        # Replay cache: keyed by message type, value is the most recent payload.
        # On new WS connect we resend each so the client paints current state
        # instead of waiting for the next event (e.g. clock ticks once a minute).
        self._replay: dict[str, dict] = {}
        self._replay_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._thread_main, name="NetServer", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("NetServer failed to start within 5 s")
        log.info("NetServer listening on http://%s:%d/", HTTP_HOST, HTTP_PORT)

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_setup())
            self._ready.set()
            self._loop.run_forever()
        except Exception:
            log.exception("NetServer crashed")
        finally:
            self._loop.close()

    async def _async_setup(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/ws", self._handle_ws)
        # Serve audio assets directly from the Pi's MicroRave directory so
        # the browser can use plain <audio> elements (no PCM streaming).
        for url_prefix, fs_path in (("/music", _MUSIC_DIR), ("/sounds", _SOUNDS_DIR)):
            if os.path.isdir(fs_path):
                app.router.add_static(url_prefix, fs_path, show_index=False)
            else:
                log.warning("Static dir missing, skipping route: %s -> %s",
                            url_prefix, fs_path)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, HTTP_HOST, HTTP_PORT)
        await site.start()

    # ------------------------------------------------------------------
    # HTTP / WS handlers
    # ------------------------------------------------------------------

    async def _handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=_PLACEHOLDER_HTML, content_type="text/html")

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20.0)
        await ws.prepare(request)
        peer = request.remote
        with self._clients_lock:
            self._clients.add(ws)
        log.info("WS client connected (%s, total=%d)", peer, len(self._clients))
        try:
            await ws.send_json({"type": "hello", "msg": "MicroRave sim connected"})
            # Replay cached state so this client sees the current display/lights
            # without waiting for the next tick.
            with self._replay_lock:
                replay = list(self._replay.values())
            for msg in replay:
                await ws.send_json(msg)
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    self._handle_client_msg(msg.data)
                elif msg.type == WSMsgType.ERROR:
                    log.warning("WS error from %s: %s", peer, ws.exception())
        finally:
            with self._clients_lock:
                self._clients.discard(ws)
            log.info("WS client disconnected (%s, remaining=%d)",
                     peer, len(self._clients))
        return ws

    def _handle_client_msg(self, raw: str) -> None:
        """Parse a browser message and dispatch to the handler registered
        for its "type". Message shapes from browser:
          {"type":"press","pin":N}            — momentary button press
          {"type":"door","closed":true|false} — door toggle
          {"type":"music_ended"}              — main music track finished
          {"type":"easter_ended"}             — easter-egg track finished
        """
        try:
            import json
            msg = json.loads(raw)
        except Exception:
            log.warning("WS bad JSON: %s", raw[:200])
            return
        msg_type = msg.get("type")
        handler = self._handlers.get(msg_type)
        if handler is None:
            log.debug("WS recv (no handler for %r): %s", msg_type, msg)
            return
        try:
            handler(msg)
        except Exception:
            log.exception("Handler for %r raised", msg_type)

    # ------------------------------------------------------------------
    # Outbound broadcast (thread-safe, callable from any thread)
    # ------------------------------------------------------------------

    # Message types whose latest value should be replayed to new clients.
    _REPLAY_TYPES = frozenset({
        "display.show", "display.show_segs", "lights.dj",
    })

    def broadcast(self, msg: dict) -> None:
        if self._loop is None:
            return
        msg_type = msg.get("type")
        if msg_type in self._REPLAY_TYPES:
            with self._replay_lock:
                # display.show and display.show_segs are mutually exclusive
                # display states — keep only one in the cache.
                if msg_type == "display.show":
                    self._replay.pop("display.show_segs", None)
                elif msg_type == "display.show_segs":
                    self._replay.pop("display.show", None)
                self._replay[msg_type] = msg
        self._loop.call_soon_threadsafe(self._broadcast_on_loop, msg)

    def _broadcast_on_loop(self, msg: dict) -> None:
        with self._clients_lock:
            clients = list(self._clients)
        for ws in clients:
            if not ws.closed:
                asyncio.create_task(self._send_safe(ws, msg))

    async def _send_safe(self, ws: web.WebSocketResponse, msg: dict) -> None:
        try:
            await ws.send_json(msg)
        except (ConnectionResetError, RuntimeError):
            pass

    # ------------------------------------------------------------------
    # Inbound message routing — one handler per message "type".
    # ------------------------------------------------------------------

    def register_handler(self, msg_type: str,
                         fn: Callable[[dict], None] | None) -> None:
        if fn is None:
            self._handlers.pop(msg_type, None)
        else:
            self._handlers[msg_type] = fn


# =============================================================================
# ADAPTER STUBS  (Step 3a: no-ops that log + broadcast event names)
# =============================================================================

class NetDisplay:
    """Stub for adapters_hw.Display. 3b will stream pygame surfaces."""

    def __init__(self):
        self._server = _Server.get()
        log.info("NetDisplay ready (stub — 3b will add real rendering)")

    def show(self, text: str, colon: bool = True) -> None:
        self._server.broadcast({"type": "display.show", "text": text, "colon": colon})

    def show_segs(self, segs, colon: bool = False) -> None:
        # segs is a list of 4 sets of segment letters (e.g. {'a','b'}).
        # JSON can't carry sets, so serialize each as a sorted list.
        payload = [sorted(list(s)) for s in segs]
        self._server.broadcast({"type": "display.show_segs", "segs": payload, "colon": colon})

    def render(self) -> None:
        # Hardware Display.render() is called every frame from the main loop.
        # In net mode there's nothing to flip — broadcasts already went out in show().
        pass


class NetAudio:
    """Browser-side audio. The Pi tells the browser what to play via
    audio.* WS messages; the browser plays files served from /music/ and
    /sounds/ using HTMLAudioElement. Track-end is reported back over WS
    so the playlist advances exactly as it does on hardware."""

    # Per-channel sim volume, mirrored to the browser. Hardware does this
    # via pygame.mixer.Channel.set_volume() but the values aren't important
    # to microrave.py (it never reads them) — just keeps Vol+/Vol- working.
    _VOL_STEP = 0.1

    def __init__(self):
        self._server = _Server.get()
        self._track_provider: Callable[[], str] | None = None
        self._easter_on_complete: Callable[[], None] | None = None
        self._music_volume: float = 0.8
        # Sim contributes to the same unified playcounts.json as hardware.
        # Save cadence matches AudioHW: every 5 plays, to limit SD writes.
        self._counts = load_playcounts()
        self._save_counter = 0
        # Register inbound handlers — browser reports playback completion.
        self._server.register_handler("music_ended", self._on_music_ended)
        self._server.register_handler("easter_ended", self._on_easter_ended)
        log.info("NetAudio ready (browser playback via /music + /sounds)")

    def _record_play(self, path: str) -> None:
        """Tally a play of `path` and flush to disk every 5 plays. Mirrors
        AudioHW._play's counting logic so hardware and sim agree."""
        key = playcount_key(path)
        self._counts[key] = self._counts.get(key, 0) + 1
        self._save_counter += 1
        if self._save_counter >= 5:
            save_playcounts(self._counts)
            self._save_counter = 0
        log.info("Playing: %s (play #%d)", key, self._counts[key])

    # ------------------------------------------------------------------
    # Outbound: tell the browser what to play.
    # ------------------------------------------------------------------

    def start(self, track_provider) -> None:
        """Begin playlist. track_provider() returns the next track path
        each time it's called — we cache it for music_ended advancement."""
        self._track_provider = track_provider
        try:
            track = track_provider()
        except Exception:
            log.exception("track_provider raised on start")
            return
        if not track:
            log.warning("track_provider returned empty path")
            return
        self._record_play(track)
        self._server.broadcast({
            "type": "audio.start",
            "url": _path_to_url(track),
            "volume": self._music_volume,
        })

    def start_easter(self, track: str, on_complete=None) -> None:
        self._easter_on_complete = on_complete
        self._record_play(track)
        self._server.broadcast({
            "type": "audio.start_easter",
            "url": _path_to_url(track),
            "volume": self._music_volume,
        })

    def pause(self) -> None:
        self._server.broadcast({"type": "audio.pause"})

    def resume(self) -> None:
        self._server.broadcast({"type": "audio.resume"})

    def stop(self) -> None:
        self._track_provider = None
        self._easter_on_complete = None
        self._server.broadcast({"type": "audio.stop"})

    def beep(self) -> None:
        self._server.broadcast({"type": "audio.beep", "url": "/sounds/beep.mp3"})

    def ding(self) -> None:
        # Match hardware behavior: ding stops the music first, then plays the
        # bell. adapters_hw.AudioHW.ding() does self.stop() before triggering
        # the ding channel, so net mode must too — otherwise the song keeps
        # playing past 0:00.
        self.stop()
        self._server.broadcast({"type": "audio.ding", "url": "/sounds/ding.mp3"})

    def volume_up(self) -> None:
        self._music_volume = min(1.0, self._music_volume + self._VOL_STEP)
        self._server.broadcast({"type": "audio.volume", "volume": self._music_volume})
        log.info("Volume → %d%%", round(self._music_volume * 100))

    def volume_down(self) -> None:
        # 10% floor matches adapters_hw.AudioHW.volume_down (max(10, ...) on
        # the 0–100 scale). Never let users mute completely — at a public
        # event, silent volume means an unresponsive-looking microRave.
        self._music_volume = max(0.1, self._music_volume - self._VOL_STEP)
        self._server.broadcast({"type": "audio.volume", "volume": self._music_volume})
        log.info("Volume → %d%%", round(self._music_volume * 100))

    def clear_playcounts(self) -> None:
        # Sim now tallies into the same playcounts.json as hardware, so reset
        # mirrors AudioHW.clear_playcounts: archive first, then wipe both the
        # in-memory dict and the on-disk file.
        backup_playcounts()
        self._counts = {}
        self._save_counter = 0
        save_playcounts(self._counts)
        log.info("Playcounts cleared")

    def notify_music_end(self) -> None:
        # Hardware-only path (pygame USEREVENT). In net mode the browser
        # reports end-of-track via _on_music_ended below, so this no-ops.
        pass

    def shutdown(self) -> None:
        self._server.register_handler("music_ended", None)
        self._server.register_handler("easter_ended", None)
        self._server.broadcast({"type": "audio.shutdown"})

    # ------------------------------------------------------------------
    # Inbound: browser reports a track finished naturally.
    # ------------------------------------------------------------------

    def _on_music_ended(self, msg: dict) -> None:
        if self._track_provider is None:
            return
        try:
            track = self._track_provider()
        except Exception:
            log.exception("track_provider raised on advance")
            return
        if not track:
            return
        self._record_play(track)
        self._server.broadcast({
            "type": "audio.start",
            "url": _path_to_url(track),
            "volume": self._music_volume,
        })

    def _on_easter_ended(self, msg: dict) -> None:
        cb = self._easter_on_complete
        self._easter_on_complete = None
        if cb is not None:
            try:
                cb()
            except Exception:
                log.exception("easter on_complete raised")


class NetLights:
    """Stub for adapters_hw.RelayController. Just announces DJ selection."""

    def __init__(self):
        self._server = _Server.get()
        log.info("NetLights ready (stub — DJ light state broadcast only)")

    def set_dj(self, dj: int) -> None:
        self._server.broadcast({"type": "lights.dj", "dj": int(dj)})

    def close(self) -> None:
        log.info("NetLights closed")


class NetInput:
    """Browser-driven input adapter. The web client sends presses over WS;
    NetInput dispatches them to the callbacks microrave.py registered."""

    def __init__(self):
        self._server = _Server.get()
        self._buttons: dict[int, Callable[[], None]] = {}
        self._door_pin: int | None = None
        self._door_close: Callable[[], None] | None = None
        self._door_open: Callable[[], None] | None = None
        log.info("NetInput ready (browser-driven input)")

    def register_button(self, pin: int, on_press) -> None:
        self._buttons[int(pin)] = on_press
        log.debug("NetInput: registered button on pin %d", pin)

    def register_door(self, pin: int, on_close, on_open) -> None:
        self._door_pin = int(pin)
        self._door_close = on_close
        self._door_open = on_open
        log.debug("NetInput: registered door on pin %d", pin)

    def door_closed_initial(self) -> bool:
        # Sim assumes the door starts closed (normal microwave state).
        return True

    def start(self) -> None:
        self._server.register_handler("press", self._on_press)
        self._server.register_handler("door", self._on_door)
        log.info("NetInput started (buttons=%d, door=%s)",
                 len(self._buttons), self._door_close is not None)

    def stop(self) -> None:
        self._server.register_handler("press", None)
        self._server.register_handler("door", None)
        log.info("NetInput stopped")

    # ------------------------------------------------------------------
    # Message dispatch — invoked by _Server on the asyncio thread.
    # Callbacks were given to us by microrave.py and post to its dispatch
    # queue, so it's safe to invoke them from any thread.
    # ------------------------------------------------------------------

    def _on_press(self, msg: dict) -> None:
        pin = msg.get("pin")
        cb = self._buttons.get(int(pin)) if pin is not None else None
        if cb is None:
            log.warning("Press on unregistered pin: %r", pin)
            return
        cb()

    def _on_door(self, msg: dict) -> None:
        closed = bool(msg.get("closed"))
        cb = self._door_close if closed else self._door_open
        if cb is not None:
            cb()
