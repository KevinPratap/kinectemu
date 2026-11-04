"""Hands-free regression test of xenia's skeleton injection in Kinect Adventures.

Runs the title in the fork's xenia with --nui=pipe and
--nui_skeleton_inject=pipe:<name>, feeds it from a source that needs nobody in
front of a camera, screenshots the game window at fixed times, then grades
what the title saw (--nui_log_play_frames) and prints PASS/FAIL per check:

  --case synthetic         scripted_skeleton.py: reaches forward to select,
                           then steps of known size and side and jumps at
                           known times (exact ground truth)
  --case cursor            scripted_skeleton.py --mode cursor: stands still
                           and sweeps the raised right hand from side to side
  --case frames:<folder>   synth_depth.py --live replaying recorded webcam
                           frames (fixtures/..., or --record-frames output)

Checks
  bound       the hook found the title's skeleton frame site and bound it
  fresh       injection never fell back to the title's own tracker after it
              started taking poses
  tracking    one tracking id, and an injected body in >= 95% of logged frames
              after the first one
  direction   the injected hip agrees with the player mask the title's own
              tracker cut from our depth: column = 160 + 290 x / z, as the
              title's own skeleton does (correlation > 0.7, error < 40 px)
  lateral     synthetic: steps reach the scripted x on the scripted side;
              frames: the hip's p5..p95 sideways range (reported)
  jumps       synthetic: every scripted jump seen as a hip rise > 0.15 m;
              frames: rises counted (reported)
  paused      no "GAME PAUSED" screen in any screenshot
  raft        River Rush only: the raft's screen x follows the player's x
              (reported unless clear; the camera follows the raft, so it is
              a weak signal)
  cursor      cursor case: the title's hand cursor moves across the screen
              the way the player's right hand moves (the mirror check a
              player sees: hand to the player's right, cursor to the right)

  python skeleton_harness.py --case synthetic --seconds 300 --out ../../runs/syn01
  python skeleton_harness.py --case frames:../../fixtures/fullbody_02 --out ../../runs/fb02
"""
import argparse
import csv
import ctypes
import ctypes.wintypes as wt
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
XENIA = ROOT / "external/ryantimjohn-xenia/build/bin/Windows/Release/xenia_canary.exe"
ISO = Path(r"E:\Kinect Adventures!\Kinect Adventures!.iso")
PYTHON = ROOT / "tools/.venv/Scripts/python.exe"
PAUSE_TEMPLATE = HERE / "templates/game_paused.png"
CURSOR_TEMPLATE = HERE / "templates/hand_cursor_start.png"

PLAY = re.compile(r"play frame (\d+): id (\d+) position \(([-\d.]+), ([-\d.]+), ([-\d.]+)\)")
MASK = re.compile(r"play frame (\d+): mask 320x240 .*?(?:centroid \(([-\d.]+), ([-\d.]+)\)|no centroid)")
FLOOR = re.compile(r"play frame (\d+): floor")


# --- screenshots -----------------------------------------------------------

user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32


def window_of(pid):
    """The largest visible top-level window of the process, or None."""
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            found.append(((r.right - r.left) * (r.bottom - r.top), hwnd))
        return True

    user32.EnumWindows(visit, 0)
    return max(found)[1] if found else None


def screenshot(hwnd):
    """The window's pixels as a BGR array (PrintWindow, so it may be covered)."""
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)  # PW_RENDERFULLCONTENT

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return np.frombuffer(buf, np.uint8).reshape(h, w, 4)[:, :, :3].copy()


# --- running ---------------------------------------------------------------

def run(args, out):
    log = out / "xenia.log"
    xenia = subprocess.Popen([
        str(args.xenia), str(args.iso), "--nui=pipe", f"--nui_pipe={args.pipe}",
        f"--nui_skeleton_inject=pipe:{args.pipe}-skel", "--nui_tilt_override=0",
        f"--nui_log_play_frames={args.log_every}", f"--log_file={log}", "--flush_log=true",
        "--mute=true"])
    if args.case in ("synthetic", "cursor"):
        source = [str(PYTHON), "scripted_skeleton.py", "--pipe", args.pipe,
                  "--skeleton-pipe", f"{args.pipe}-skel", "--seconds", str(args.seconds + 60),
                  "--settle", str(args.settle), "--truth", str(out / "truth.csv"),
                  "--mode", "play" if args.case == "synthetic" else "cursor"]
    else:
        frames = args.case.split(":", 1)[1]
        source = [str(PYTHON), "synth_depth.py", "--models", str(ROOT / "models"), "--live",
                  "--live-frames", frames, "--intrinsics", str(ROOT / "docs/data/intrinsics.json"),
                  "--push-mm", "700", "--no-window", "--pipe", args.pipe,
                  "--skeleton-pipe", f"{args.pipe}-skel", "--gain-x", str(args.gain_x),
                  "--trace", str(out / "trace.csv")]
    with open(out / "source.out", "w") as so:
        src = subprocess.Popen(source, cwd=HERE, stdout=so, stderr=subprocess.STDOUT)
    shots, t0 = [], time.monotonic()
    try:
        for at in range(args.shot_every, args.seconds + 1, args.shot_every):
            while time.monotonic() - t0 < at:
                if xenia.poll() is not None:
                    break
                time.sleep(0.2)
            if xenia.poll() is not None:
                print(f"xenia exited after {time.monotonic() - t0:.0f} s", flush=True)
                break
            hwnd = window_of(xenia.pid)
            img = screenshot(hwnd) if hwnd else None
            if img is not None:
                # 960 px wide JPEGs: one a second for ten minutes is ~60 MB.
                img = cv2.resize(img, (960, round(img.shape[0] * 960 / img.shape[1])),
                                 interpolation=cv2.INTER_AREA)
                path = out / f"shot_{at:04d}.jpg"
                cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
                shots.append((at, path))
                with open(out / "shots.csv", "a") as f:
                    f.write(f"{at},{time.time():.3f}\n")
    finally:
        for p in (src, xenia):
            if p.poll() is None:
                p.kill()
        for p in (src, xenia):
            p.wait(timeout=10)
    return shots


# --- grading ---------------------------------------------------------------

def parse_log(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    play, mask, frames = {}, {}, set()
    for m in PLAY.finditer(text):
        play.setdefault(int(m.group(1)), []).append(
            (int(m.group(2)), float(m.group(3)), float(m.group(4)), float(m.group(5))))
    for m in MASK.finditer(text):
        if m.group(2):
            mask[int(m.group(1))] = (float(m.group(2)), float(m.group(3)))
    for m in FLOOR.finditer(text):
        frames.add(int(m.group(1)))
    return text, play, mask, sorted(frames)


def rises(series, fps, threshold=0.15):
    """Hip rises above the rolling 2 s median of more than threshold metres."""
    count, inside, window = 0, False, max(3, int(2 * fps))
    for i, y in enumerate(series):
        base = statistics.median(series[max(0, i - window):i + 1])
        if y - base > threshold and not inside:
            count, inside = count + 1, True
        elif y - base < threshold / 2:
            inside = False
    return count


def pause_score(img, template):
    """Best normalized match of the GAME PAUSED badge over a range of scales."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    tpl = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    best = 0.0
    # The template was cut from a game area 960 px wide; screenshots are kept
    # 960 px wide, menus and borders included, and older ones were wider.
    for s in np.linspace(0.6, 2.2, 17):
        t = cv2.resize(tpl, None, fx=s, fy=s)
        if t.shape[0] >= gray.shape[0] or t.shape[1] >= gray.shape[1]:
            continue
        best = max(best, float(cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED).max()))
    return best


def cursor_x(img, template):
    """Screen x (0..1) of the title's hand cursor on the START screen, or None
    when not seen. The cursor looks different over a button, where it fills
    with blue; the cursor case keeps it off the button."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    tpl = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    best, where = 0.0, None
    # Cut from a 960 px wide screenshot.
    for s in np.linspace(0.85, 1.15, 4) * img.shape[1] / 960:
        t = cv2.resize(tpl, None, fx=s, fy=s)
        res = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score > best:
            best, where = score, (loc[0] + t.shape[1] / 2) / img.shape[1]
    # The cursor on green scores ~0.95; other screens reached 0.73.
    return where if best > 0.85 else None


def raft_x(img):
    """Screen x (0..1) of the red raft in the lower middle, or None."""
    h, w = img.shape[:2]
    roi = img[int(h * 0.55):int(h * 0.98), int(w * 0.15):int(w * 0.85)].astype(np.int16)
    b, g, r = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
    red = (r > 140) & (r - g > 70) & (r - b > 70)
    if red.sum() < 0.01 * red.size:
        return None
    xs = np.nonzero(red)[1]
    return (xs.mean() + w * 0.15) / w


def grade(args, out, shots):
    text, play, mask, frames = parse_log(out / "xenia.log")
    results = []

    def check(name, ok, detail):
        results.append({"check": name, "result": ok, "detail": detail})

    check("bound", "now bound to the host" in text,
          "frame callback thunk bound" if "now bound to the host" in text else "no bind line in the log")
    starts = text.find("takes poses from the pipe")
    dropped = text.count("has no fresh pose", starts) if starts >= 0 else -1
    check("fresh", starts >= 0 and dropped == 0,
          "never took a pose" if starts < 0 else f"{dropped} fall-backs to the title's tracker")

    bodied = [f for f in frames if f in play]
    first = bodied[0] if bodied else None
    after = [f for f in frames if first is not None and f >= first]
    ids = sorted({p[0] for f in bodied for p in play[f]})
    share = len([f for f in after if f in play]) / max(len(after), 1)
    check("tracking", len(ids) == 1 and share >= 0.95,
          f"ids {ids}, body in {share:.0%} of {len(after)} logged frames")

    pairs = [(160 + 290 * play[f][0][1] / play[f][0][3], mask[f][0])
             for f in bodied if f in mask and len(play[f]) == 1]
    if len(pairs) >= 10:
        pred, seen = zip(*pairs)
        corr = float(np.corrcoef(pred, seen)[0, 1]) if np.std(pred) > 1 and np.std(seen) > 1 else float("nan")
        err = statistics.mean(abs(a - b) for a, b in pairs)
        flipped = statistics.mean(abs((320 - a) - b) for a, b in pairs)
        check("direction", (corr > 0.7 or np.isnan(corr)) and err < 40,
              f"corr {corr:+.2f}, mean |error| {err:.0f} px (mirrored would be {flipped:.0f} px), {len(pairs)} frames")
    else:
        check("direction", False, f"only {len(pairs)} frames with both a body and a mask")

    fps = 30.0 / args.log_every
    hips = [(f, play[f][0][1], play[f][0][2]) for f in bodied]
    xs = [h[1] for h in hips]
    p = lambda v, q: sorted(v)[int(q * (len(v) - 1))] if v else float("nan")
    rise_count = rises([h[2] for h in hips], fps)
    if args.case == "synthetic" and (out / "truth.csv").exists():
        truth = list(csv.DictReader(open(out / "truth.csv")))
        span = float(truth[-1]["t_s"]) if truth else 0
        # Steps: logged hips beyond +-0.25 m on each side, against the script.
        left, right = sum(x < -0.25 for x in xs), sum(x > 0.25 for x in xs)
        check("lateral", left > 0 and right > 0 and p(xs, 0.02) < -0.3 and p(xs, 0.98) > 0.3,
              f"hip x p2..p98 {p(xs, .02):+.2f}..{p(xs, .98):+.2f} m (script -0.35..+0.35), "
              f"{left} logged frames left, {right} right")
        jumps_scripted = sum(1 for i in range(1, len(truth))
                             if float(truth[i]["lift"]) > 0 and float(truth[i - 1]["lift"]) == 0)
        # The last jump may fall after the log ends.
        check("jumps", jumps_scripted > 0 and rise_count >= jumps_scripted - 1,
              f"{rise_count} rises seen, {jumps_scripted} scripted in {span:.0f} s")
    else:
        check("lateral", None, f"hip x p5..p95 {p(xs, .05):+.2f}..{p(xs, .95):+.2f} m (reported)")
        check("jumps", None, f"{rise_count} hip rises > 0.15 m (reported)")

    template = cv2.imread(str(PAUSE_TEMPLATE))
    paused = [(at, round(pause_score(cv2.imread(str(path)), template), 2)) for at, path in shots]
    hits = [at for at, s in paused if s > 0.6]
    check("paused", not hits, f"GAME PAUSED in screenshots at {hits} s" if hits else
          f"none in {len(shots)} screenshots")

    rafts = [(at, raft_x(cv2.imread(str(path)))) for at, path in shots]
    rafts = [(at, x) for at, x in rafts if x is not None]
    detail = f"raft on screen in {len(rafts)} of {len(shots)} screenshots"
    epochs = {}
    if (out / "shots.csv").exists():
        epochs = {int(a): float(e) for a, e in csv.reader(open(out / "shots.csv"))}
    verdict = None
    if args.case == "synthetic" and len(rafts) >= 20 and epochs and (out / "truth.csv").exists():
        # The raft answers a step after the game's smoothing and its own
        # inertia, so the scripted x is taken 0..4 s before each screenshot.
        truth = list(csv.DictReader(open(out / "truth.csv")))
        times = np.array([float(r["epoch_s"]) for r in truth])
        seen = [x for _, x in rafts]
        best = (0.0, 0.0)
        for lag in np.arange(0.0, 4.01, 0.5):
            script_x = [float(truth[int(np.argmin(abs(times - (epochs[at] - lag))))]["hip_x"])
                        for at, _ in rafts]
            if np.std(script_x) > 0.05:
                corr = float(np.corrcoef(script_x, seen)[0, 1])
                if abs(corr) > abs(best[0]):
                    best = (corr, lag)
        detail += (f"; raft x vs scripted hip x {best[1]:.1f} s earlier: corr {best[0]:+.2f} "
                   "(+ = the raft goes the player's way, - = mirrored)")
        verdict = True if best[0] > 0.5 else (False if best[0] < -0.5 else None)
    check("raft", verdict, detail)

    if args.case == "cursor":
        template = cv2.imread(str(CURSOR_TEMPLATE))
        cursors = [(at, cursor_x(cv2.imread(str(path)), template)) for at, path in shots]
        cursors = [(at, x) for at, x in cursors if x is not None]
        if len(cursors) >= 10 and epochs and (out / "truth.csv").exists():
            truth = list(csv.DictReader(open(out / "truth.csv")))
            times = np.array([float(r["epoch_s"]) for r in truth])
            best = (0.0, 0.0)
            for lag in np.arange(0.0, 2.01, 0.25):
                hand = [float(truth[int(np.argmin(abs(times - (epochs[at] - lag))))]["hand_x"])
                        for at, _ in cursors]
                if np.std(hand) > 0.05:
                    corr = float(np.corrcoef(hand, [x for _, x in cursors])[0, 1])
                    if abs(corr) > abs(best[0]):
                        best = (corr, lag)
            span = max(x for _, x in cursors) - min(x for _, x in cursors)
            check("cursor", best[0] > 0.6,
                  f"seen in {len(cursors)} of {len(shots)} screenshots, across {span:.0%} of the "
                  f"screen; cursor x vs the hand's x {best[1]:.2f} s earlier: corr {best[0]:+.2f} "
                  "(+ = moves with the hand, - = mirrored)")
        else:
            check("cursor", False, f"cursor seen in only {len(cursors)} of {len(shots)} screenshots")

    summary = {"case": args.case, "seconds": args.seconds, "screenshots": [str(p) for _, p in shots],
               "checks": results}
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"\n{args.case}: {out}")
    for r in results:
        mark = "INFO" if r["result"] is None else ("PASS" if r["result"] else "FAIL")
        print(f"  {mark:4s}  {r['check']:9s}  {r['detail']}")
    failed = [r for r in results if r["result"] is False]
    print("  RESULT: " + ("PASS" if not failed else f"FAIL ({', '.join(r['check'] for r in failed)})"))
    return not failed


def contact_sheet(out, shots, cols=4, every=15):
    ims = []
    for at, path in shots:
        if at % every:
            continue
        im = cv2.resize(cv2.imread(str(path)), (480, 270))
        cv2.putText(im, f"{at}s", (8, 262), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        ims.append(im)
    if not ims:
        return
    while len(ims) % cols:
        ims.append(np.zeros_like(ims[0]))
    rows = [np.hstack(ims[i:i + cols]) for i in range(0, len(ims), cols)]
    cv2.imwrite(str(out / "sheet.jpg"), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 80])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="synthetic", help="synthetic, or frames:<folder>")
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--shot-every", type=int, default=1)
    ap.add_argument("--settle", type=float, default=60.0, help="synthetic: stand still first (s)")
    ap.add_argument("--gain-x", type=float, default=1.4, help="frames: synth_depth.py --gain-x")
    ap.add_argument("--log-every", type=int, default=3, help="--nui_log_play_frames")
    ap.add_argument("--pipe", default="kinectemu-test")
    ap.add_argument("--xenia", type=Path, default=XENIA)
    ap.add_argument("--iso", type=Path, default=ISO)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--grade-only", action="store_true", help="grade an earlier run in --out")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.grade_only:
        shots = sorted((int(p.stem.split("_")[1]), p) for p in args.out.glob("shot_*.[jp][pn]g"))
    else:
        shots = run(args, args.out)
    contact_sheet(args.out, shots)
    sys.exit(0 if grade(args, args.out, shots) else 1)


if __name__ == "__main__":
    main()
