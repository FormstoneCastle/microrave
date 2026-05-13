"""
Detect correct rotation for each photo in data.json using MediaPipe face detection.

For each unchecked photo, downloads a resized version through wsrv.nl (which caches,
sparing the source server), runs face detection in all 4 rotations, and records
the rotation with the highest combined face-confidence as the correct one.

Run nightly via run_nightly.bat. Resume-safe: re-running picks up where the previous
run left off, because each processed photo gets a `rotation_checked_at` field.

CLI:
    python detect_rotations.py                          # default: 500 photos, 4s delay
    python detect_rotations.py --limit 100              # smaller batch
    python detect_rotations.py --event sonar0110        # one event only (good for testing)
    python detect_rotations.py --delay 2                # faster (use sparingly)
    python detect_rotations.py --recheck                # re-process even already-checked photos
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from io import BytesIO

# Force line-buffered stdout so progress prints reach the log file immediately
# even when stdout is redirected (otherwise Python uses 8KB block buffering).
sys.stdout.reconfigure(line_buffering=True)

import cv2
import numpy as np
import requests
import mediapipe as mp
from mediapipe.tasks import python as mp_py
from mediapipe.tasks.python import vision as mp_vision

# ── Paths ────────────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "data.json")
MODEL_PATH = os.path.join(HERE, "models", "blaze_face_short_range.tflite")

# ── Config ───────────────────────────────────────────────────────────────────
WSRV_BASE = "https://wsrv.nl/?url="
# Request a modest-sized image: small enough to be fast, large enough that
# faces are still resolvable. wsrv.nl serves these cached so repeated runs
# are cheap and the source server is not hit.
WSRV_WIDTH = 640
HTTP_TIMEOUT = 20
MIN_CONF = 0.3      # MediaPipe detection threshold — low so we catch faint faces
MARGIN = 0.15       # winner must beat runner-up by this margin or we mark "ambiguous"
SCORE_FLOOR = 0.5   # if best score is below this, treat as "no_face" (rejects false-positive
                    # detections on people-less photos like flyers, landscapes, calendars)

UA = ("MikeB-ResearchScraper/1.0 - Orientation detection for personal archive. "
      "Thank you SideShow Bob! - Mike B, mike@formstonecastleart.com")

# rotation code → wsrv.nl `ro` value (both are clockwise degrees)
ROTATIONS = [
    (0,   None),
    (90,  cv2.ROTATE_90_CLOCKWISE),
    (180, cv2.ROTATE_180),
    (270, cv2.ROTATE_90_COUNTERCLOCKWISE),
]


# ── Detection ────────────────────────────────────────────────────────────────
def make_detector():
    base_opts = mp_py.BaseOptions(model_asset_path=MODEL_PATH)
    opts = mp_vision.FaceDetectorOptions(
        base_options=base_opts,
        min_detection_confidence=MIN_CONF,
    )
    return mp_vision.FaceDetector.create_from_options(opts)


# MediaPipe FaceDetector instances are not thread-safe — each worker thread
# gets its own via threading.local. Created lazily on first use.
_thread_local = threading.local()

def get_thread_detector():
    if not hasattr(_thread_local, "detector"):
        _thread_local.detector = make_detector()
    return _thread_local.detector


def score_rotation(detector, bgr_img):
    """Return (n_faces, total_confidence) for a single oriented image."""
    rgb = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2RGB)
    mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = detector.detect(mp_img)
    dets = result.detections or []
    total = sum(d.categories[0].score for d in dets)
    return len(dets), total


def detect_best_rotation(detector, img):
    """Return dict with rotation, score, status, and per-rotation scores."""
    scores = {}
    for deg, code in ROTATIONS:
        oriented = img if code is None else cv2.rotate(img, code)
        n, total = score_rotation(detector, oriented)
        scores[deg] = {"n_faces": n, "score": round(total, 3)}

    ranked = sorted(scores.items(), key=lambda kv: kv[1]["score"], reverse=True)
    best_deg, best = ranked[0]
    runner = ranked[1][1]["score"]

    # No faces detected anywhere, or only weak false positives → don't rotate
    if best["score"] < SCORE_FLOOR:
        status = "no_face"
    elif best["score"] - runner < MARGIN:
        status = "ambiguous"
    else:
        status = "ok"

    return {
        "rotation": best_deg if status != "no_face" else 0,
        "rotation_score": best["score"],
        "rotation_status": status,
        "rotation_scores": scores,   # full breakdown for debugging
    }


# ── I/O ──────────────────────────────────────────────────────────────────────
def _swap_ext_case(url):
    """Toggle final extension case (.JPG <-> .jpg). Mirrors gallery.html fallback."""
    if "." not in url.rsplit("/", 1)[-1]:
        return url
    head, ext = url.rsplit(".", 1)
    swapped = ext.upper() if ext == ext.lower() else ext.lower()
    return f"{head}.{swapped}"


def _try_fetch(url, session):
    stripped = url.replace("http://", "").replace("https://", "")
    proxy = f"{WSRV_BASE}{stripped}&w={WSRV_WIDTH}"
    try:
        r = session.get(proxy, timeout=HTTP_TIMEOUT)
    except Exception as e:
        return None, f"network: {e}"
    if r.status_code != 200 or not r.content:
        return None, f"http {r.status_code}"
    return r.content, None


def download_image(full_url, session):
    """Fetch image via wsrv.nl. Retries with swapped extension case, and once
    again after a backoff if the 404 looked transient (wsrv.nl sometimes
    returns 404s under heavy concurrent load for images that do exist)."""
    content, err = _try_fetch(full_url, session)
    if content is None and err and err.startswith("http 404"):
        swapped = _swap_ext_case(full_url)
        if swapped != full_url:
            c2, _ = _try_fetch(swapped, session)
            if c2 is not None:
                content = c2
                err = None
    # Both cases 404 → could be transient wsrv overload. Back off and try once more.
    if content is None and err and err.startswith("http 404"):
        time.sleep(3)
        content, err = _try_fetch(full_url, session)
    if content is None:
        return None, err
    arr = np.frombuffer(content, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None, "decode-failed"
    return img, None


def iter_pending_photos(data, recheck=False, retry_errors=False, event_slug=None):
    """Yield (event_idx, photo_idx, event, photo) for photos needing detection.

    Order: newest events first (data.json events are in archive-page order, which is
    newest-first on ssbproductions.com).
    """
    for ei, ev in enumerate(data.get("events", [])):
        if event_slug and event_slug not in ev["url"]:
            continue
        if ev.get("status") != "alive":
            continue
        for pi, p in enumerate(ev.get("photos", [])):
            already_checked = bool(p.get("rotation_checked_at"))
            had_error = str(p.get("rotation_status", "")).startswith("download_error")
            if recheck or (retry_errors and had_error) or not already_checked:
                yield ei, pi, ev, p


def save_data(data):
    """Atomic write — temp file then rename."""
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DATA_FILE)


def process_one(p, session):
    """Worker fn: download + detect for a single photo. Mutates p in place.
    Returns (status_label, info_str) for logging."""
    img, err = download_image(p["full"], session)
    p["rotation_checked_at"] = datetime.now(timezone.utc).isoformat()
    if img is None:
        p["rotation_status"] = f"download_error:{err}"
        return "ERR", err
    result = detect_best_rotation(get_thread_detector(), img)
    p.update(result)
    p["rotation_method"] = "mediapipe_blazeface_short_v1"
    n_top = result["rotation_scores"][result["rotation"]]["n_faces"]
    return "OK", (f"rot={result['rotation']:>3}  score={result['rotation_score']:.2f}  "
                  f"faces={n_top}  status={result['rotation_status']}")


# ── Main loop ────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500,
                    help="max photos to process this run (default 500). Use 0 for no limit.")
    ap.add_argument("--delay", type=float, default=4.0,
                    help="seconds between photo submissions, per worker (default 4)")
    ap.add_argument("--workers", type=int, default=1,
                    help="concurrent worker threads (default 1). 10 is a good bulk-run value.")
    ap.add_argument("--event", type=str, default=None,
                    help="only process photos whose event URL contains this string")
    ap.add_argument("--recheck", action="store_true",
                    help="re-process photos even if already checked")
    ap.add_argument("--retry-errors", action="store_true",
                    help="re-process photos whose previous run hit a download error")
    ap.add_argument("--save-every", type=int, default=50,
                    help="flush data.json to disk after every N completed photos (default 50)")
    args = ap.parse_args()

    if not os.path.exists(MODEL_PATH):
        print(f"ERROR: model not found at {MODEL_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    # Allow many parallel keep-alive connections to wsrv
    adapter = requests.adapters.HTTPAdapter(pool_connections=args.workers,
                                            pool_maxsize=args.workers * 2)
    session.mount("https://", adapter)

    pending = list(iter_pending_photos(
        data, recheck=args.recheck, retry_errors=args.retry_errors, event_slug=args.event))
    total_pending = len(pending)
    if total_pending == 0:
        print("Nothing to do — all photos already have rotation_checked_at.")
        return

    todo = pending if args.limit == 0 else pending[:args.limit]
    print(f"Pending photos: {total_pending}.  Processing {len(todo)} this run.")
    print(f"Workers: {args.workers}.  Delay per worker: {args.delay}s.\n")

    start = time.time()
    save_lock = threading.Lock()
    completed = 0
    errors = 0

    # Serial fast-path: keeps existing single-thread behavior unchanged for small runs.
    if args.workers <= 1:
        for ei, pi, ev, p in todo:
            label, info = process_one(p, session)
            completed += 1
            if label == "ERR":
                errors += 1
            print(f"  [{completed}/{len(todo)}] {ev['name'][:40]:40} {label} {info}")
            if completed % args.save_every == 0:
                save_data(data)
            if completed < len(todo) and args.delay > 0:
                time.sleep(args.delay)
        save_data(data)
    else:
        # Parallel mode: keep (workers * 2) futures in flight at any time, so
        # submission and result-collection are interleaved. (The previous version
        # submitted all 41k futures first — workers ran but nothing was logged or
        # saved until submission finished, which never happened in practice.)
        meta = {}
        in_flight = set()
        photo_iter = iter(todo)

        def submit_next(executor):
            try:
                _, _, ev, p = next(photo_iter)
            except StopIteration:
                return False
            fut = executor.submit(process_one, p, session)
            meta[fut] = (ev, p)
            in_flight.add(fut)
            if args.delay > 0:
                time.sleep(args.delay)
            return True

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for _ in range(args.workers * 2):
                if not submit_next(ex):
                    break

            while in_flight:
                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for fut in done:
                    in_flight.discard(fut)
                    ev, p = meta.pop(fut)
                    try:
                        label, info = fut.result()
                    except Exception as e:
                        label, info = "EXC", str(e)
                    completed += 1
                    if label != "OK":
                        errors += 1
                    print(f"  [{completed}/{len(todo)}] {ev['name'][:40]:40} {label} {info}")
                    if completed % args.save_every == 0:
                        with save_lock:
                            save_data(data)
                    submit_next(ex)
        save_data(data)

    elapsed = time.time() - start
    rate = completed / elapsed if elapsed > 0 else 0
    print(f"\nDone. Processed {completed} photos ({errors} errors) in {elapsed/60:.1f} min "
          f"({rate:.2f} photos/sec).")
    print(f"Remaining pending: {total_pending - completed}")


if __name__ == "__main__":
    main()
