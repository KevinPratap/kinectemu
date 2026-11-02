"""Phase 0-W display -> camera -> host capture latency, measured without props.

You sit in front of the laptop. A fullscreen window alternates black / white
every ~700 ms (+/-150 ms random jitter) for N flashes. The screen light changes
the brightness of your face / the room, which the webcam sees. A capture thread
timestamps every frame (time.perf_counter() right after cap.read() returns) and
records the mean luma of a central ROI. For each flash we find the first frame
whose ROI luma crosses 50% of the step between the dark and bright levels, and
report latency = that frame's host timestamp - host time the flash was presented.

Flash 1 goes black->white, flash 2 white->black, ... (N edges, alternating).
Both directions are reported separately and together.

LIMITATIONS (read before trusting the absolute number)
  * "Presented" = perf_counter() right after cv2.imshow + cv2.waitKey(1) return.
    The pixels actually reach the panel later: DWM composition + vsync (0-16.7 ms
    at 60 Hz, more at lower refresh) + panel response. So the measurement is
    display-to-host latency, INCLUDING display/compositor delay, and the
    imshow-to-pixel uncertainty is roughly +/-1 refresh period (~17 ms). The
    imshow+waitKey call duration is stored as present_call_ms for reference.
  * Camera auto exposure reacts to the flashes (face brightness changes), which can
    reshape the step; levels are measured per edge from settled windows just before
    each flash to compensate. Needs latency + ~120 ms < flash interval (true for
    latency < ~400 ms at the default timing).
  * Frame timestamps are host arrival times (after USB transport, MJPG decode, driver
    buffering) -- that is the latency a game would see -- but resolution is one
    frame period (33 ms at 30 fps): the first-crossing estimate is biased up by
    0..1 frame (avg +0.5). latency_interp_ms interpolates between the two straddling
    frames and is unbiased on average for a step.
  * Luma step must be >= 8: sit ~40-70 cm from a dim-ish room, face filling the
    central ROI, screen brightness at maximum.
  * The window flashes black/white at ~1.4 Hz for ~30 s. Press q or Esc to abort.

Usage:
  python latency_flash_test.py [--flashes 40] [--interval-ms 700] [--jitter-ms 150]
        [--camera-name "HP Wide Vision HD Camera"] [--countdown 0] [--out ../../docs/data/latency_flash.json]
  (--countdown 0 waits for SPACE in the preview; N > 0 auto-starts after N seconds)
  python latency_flash_test.py --selftest      (offline synthetic check, no camera/window)
"""

import argparse
import collections
import contextlib
import json
import random
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from calibrate_intrinsics import find_camera_index, open_camera

TARGET_NAME = "HP Wide Vision HD Camera"
PREVIEW = "KinectEmu Phase 0 - latency test: framing"
FLASH = "KinectEmu Phase 0 - latency flash"
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "docs" / "data" / "latency_flash.json"
WARN_STEP = 8.0


# ---------------------------------------------------------------- pure analysis

def _window_median(ft, fl, t_end, width):
    m = (ft >= t_end - width) & (ft <= t_end)
    return float(np.median(fl[m])) if int(m.sum()) >= 2 else None


def analyze_latency(frame_t, frame_luma, present_t, first_target=1, level_window_s=0.12,
                    guard_s=0.03, max_search_s=0.8, min_step=2.0):
    """Per-flash latency from frame timestamps/luma and present timestamps (same clock, seconds).

    present_t holds N+1 transition times; edge k (0-based) is the change at present_t[k]
    and its settled "after" level is read just before present_t[k+1], so N edges are
    evaluated. Edge k goes to white if (first_target + k) is odd-parity white, i.e.
    targets alternate starting with first_target (1 = white, 0 = black).

    For each edge: pre = median luma in the level window before the flash, post = median in the
    window before the next flash (state fully settled), threshold = midpoint. The latency is
    the first frame (2 consecutive frames past threshold) at/after present - guard.
    """
    ft = np.asarray(frame_t, dtype=np.float64)
    fl = np.asarray(frame_luma, dtype=np.float64)
    order = np.argsort(ft)
    ft, fl = ft[order], fl[order]
    pt = [float(t) for t in present_t]
    flashes = []
    for k in range(len(pt) - 1):
        target = first_target if k % 2 == 0 else 1 - first_target
        sign = 1.0 if target == 1 else -1.0
        rec = {"flash": k + 1, "target": "white" if target else "black", "t_present_s": pt[k],
               "pre_luma": None, "post_luma": None, "step": None,
               "latency_ms": None, "latency_interp_ms": None, "valid": False, "reason": ""}
        flashes.append(rec)
        pre, post = _window_median(ft, fl, pt[k], level_window_s), _window_median(ft, fl, pt[k + 1], level_window_s)
        if pre is None or post is None:
            rec["reason"] = "no frames in level window"
            continue
        step = post - pre
        rec.update(pre_luma=round(pre, 2), post_luma=round(post, 2), step=round(step, 2))
        if step * sign < min_step:
            rec["reason"] = f"no usable luma step ({step:+.1f}, expected sign {sign:+.0f}, min {min_step})"
            continue
        thr = pre + 0.5 * step
        idx = np.nonzero((ft >= pt[k] - guard_s) & (ft <= pt[k] + max_search_s))[0]
        if len(idx) < 2:
            rec["reason"] = "no frames in search window"
            continue
        past = (fl[idx] - thr) * sign >= 0
        if past[0]:
            rec["reason"] = "already past threshold at present time (noise / earlier event)"
            continue
        hit = next((j for j in range(len(idx)) if past[j] and (j + 1 >= len(idx) or past[j + 1])), None)
        if hit is None:
            rec["reason"] = "no crossing within search window"
            continue
        i = idx[hit]
        lat = (ft[i] - pt[k]) * 1000.0
        a, b = fl[i - 1], fl[i]
        t_interp = ft[i - 1] + (thr - a) / (b - a) * (ft[i] - ft[i - 1]) if b != a else ft[i]
        rec.update(latency_ms=round(lat, 2), latency_interp_ms=round((t_interp - pt[k]) * 1000.0, 2))
        if lat < 0:
            rec["reason"] = "negative latency (present-time uncertainty or noise)"
            continue
        rec["valid"] = True

    def stats(vals):
        if not vals:
            return {"n": 0}
        v = np.asarray(vals)
        return {"n": int(len(v)), "p50_ms": round(float(np.percentile(v, 50)), 2),
                "p95_ms": round(float(np.percentile(v, 95)), 2), "mean_ms": round(float(v.mean()), 2),
                "std_ms": round(float(v.std()), 2), "min_ms": round(float(v.min()), 2),
                "max_ms": round(float(v.max()), 2)}

    good = [f for f in flashes if f["valid"]]
    leveled = [f for f in flashes if f["pre_luma"] is not None and f["post_luma"] is not None]
    darks = [min(f["pre_luma"], f["post_luma"]) for f in leveled]
    brights = [max(f["pre_luma"], f["post_luma"]) for f in leveled]
    med_step = float(np.median([abs(f["step"]) for f in leveled])) if leveled else 0.0
    frame_dt = np.diff(ft) * 1000.0 if len(ft) > 1 else np.array([])
    summary = {
        "n_flashes": len(flashes), "n_valid": len(good),
        "latency": stats([f["latency_ms"] for f in good]),
        "latency_interp": stats([f["latency_interp_ms"] for f in good]),
        "latency_rising_black_to_white": stats([f["latency_ms"] for f in good if f["target"] == "white"]),
        "latency_falling_white_to_black": stats([f["latency_ms"] for f in good if f["target"] == "black"]),
        "dark_luma": round(float(np.median(darks)), 2) if darks else None,
        "bright_luma": round(float(np.median(brights)), 2) if brights else None,
        "median_luma_step": round(med_step, 2),
        "frame_period_ms_p50": round(float(np.median(frame_dt)), 2) if len(frame_dt) else None,
        "frame_period_ms_p95": round(float(np.percentile(frame_dt, 95)), 2) if len(frame_dt) else None,
        "effective_fps": round(1000.0 / float(np.median(frame_dt)), 2) if len(frame_dt) else None,
    }
    warnings = []
    if med_step < WARN_STEP:
        warnings.append(f"[W0-301] luma step {med_step:.1f} < {WARN_STEP:g}: room too bright / face too far / ROI "
                        f"not on face; latency estimates are unreliable (move closer, dim room, max screen brightness)")
    if flashes and len(good) < 0.8 * len(flashes):
        warnings.append(f"[W0-302] only {len(good)}/{len(flashes)} flashes produced a valid latency")
    if summary["bright_luma"] is not None and summary["bright_luma"] >= 250:
        warnings.append("[W0-303] bright level near saturation (>=250): step may be clipped")
    if summary["effective_fps"] is not None and summary["effective_fps"] < 25:
        warnings.append(f"[W0-304] effective camera rate {summary['effective_fps']:.1f} fps < 25 "
                        f"(auto exposure lengthening in low light?): resolution is coarser")
    return {"flashes": flashes, "summary": summary, "warnings": warnings}


def schedule_gaps(n_transitions, interval_s, jitter_s, rng):
    """Gaps (s) after each transition: interval +/- uniform jitter."""
    return [interval_s + rng.uniform(-jitter_s, jitter_s) for _ in range(n_transitions)]


# ---------------------------------------------------------------- capture thread

def roi_luma(frame, frac):
    h, w = frame.shape[:2]
    y0, y1 = int(h * (1 - frac) / 2), int(h * (1 + frac) / 2)
    x0, x1 = int(w * (1 - frac) / 2), int(w * (1 + frac) / 2)
    roi = frame[y0:y1:2, x0:x1:2]
    if roi.ndim == 3:
        roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    return float(roi.mean())


class CaptureThread(threading.Thread):
    """Reads frames as fast as the camera delivers them and never blocks the main thread.

    samples: bounded deque of (perf_counter_s, roi_luma) -- oldest dropped if it ever fills.
    latest:  single slot holding the newest frame for the preview (drop-late: overwritten).
    """

    def __init__(self, cap, roi_frac=0.5, maxlen=8192):
        super().__init__(daemon=True)
        self.cap, self.roi_frac = cap, roi_frac
        self.samples = collections.deque(maxlen=maxlen)
        self.latest = None
        self.read_failures = 0
        self.error = None
        self._halt = threading.Event()

    def run(self):
        try:
            while not self._halt.is_set():
                ok, frame = self.cap.read()
                t = time.perf_counter()
                if not ok or frame is None:
                    self.read_failures += 1
                    time.sleep(0.002)
                    continue
                self.samples.append((t, roi_luma(frame, self.roi_frac)))
                self.latest = frame
        except Exception as exc:  # surface in main thread instead of dying silently
            self.error = repr(exc)

    def stop(self):
        self._halt.set()
        self.join(timeout=2.0)


@contextlib.contextmanager
def timer_resolution(ms=1):
    """Ask Windows for 1 ms timers so waitKey/sleep don't quantise to ~15.6 ms."""
    winmm = None
    try:
        import ctypes
        winmm = ctypes.windll.winmm
        winmm.timeBeginPeriod(ms)
    except Exception:
        winmm = None
    try:
        yield
    finally:
        if winmm is not None:
            winmm.timeEndPeriod(ms)


# ---------------------------------------------------------------- live mode

def screen_size():
    try:
        import ctypes
        u = ctypes.windll.user32
        w, h = int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return 1920, 1080


def put(img, text, y, color=(255, 255, 255), scale=0.8):
    cv2.putText(img, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(img, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA)


def preview_phase(cam, roi_frac, countdown):
    """Framing preview with ROI box + live luma. Returns False if the user aborted."""
    cv2.namedWindow(PREVIEW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(PREVIEW, 960, 540)
    cv2.setWindowProperty(PREVIEW, cv2.WND_PROP_TOPMOST, 1)
    t_start = time.perf_counter()
    while True:
        frame = cam.latest
        if cam.error:
            print(f"[W0-310] capture thread error: {cam.error}", file=sys.stderr)
            return False
        if frame is not None:
            view = cv2.flip(frame, 1)
            h, w = view.shape[:2]
            cv2.rectangle(view, (int(w * (1 - roi_frac) / 2), int(h * (1 - roi_frac) / 2)),
                          (int(w * (1 + roi_frac) / 2), int(h * (1 + roi_frac) / 2)), (0, 255, 255), 2)
            luma = cam.samples[-1][1] if cam.samples else float("nan")
            put(view, f"ROI luma {luma:5.1f}", 40, (0, 255, 255))
            put(view, "Sit 40-70 cm away, face filling the yellow box, screen brightness max, room dim.", 80)
            left = countdown - (time.perf_counter() - t_start)
            put(view, "SPACE = start flashing   q/Esc = quit" if countdown <= 0 else f"Starting in {max(left, 0):.0f}s   (q/Esc quits)",
                120, (0, 255, 0))
            cv2.imshow(PREVIEW, view)
        key = cv2.waitKey(15) & 0xFF
        if key in (27, ord("q")):
            return False
        if (countdown <= 0 and key == ord(" ")) or (countdown > 0 and time.perf_counter() - t_start >= countdown):
            cv2.destroyWindow(PREVIEW)
            cv2.waitKey(1)
            return True


def flash_phase(args, cam, rng):
    w, h = screen_size()
    black, white = np.zeros((h, w, 3), np.uint8), np.full((h, w, 3), 255, np.uint8)
    cv2.namedWindow(FLASH, cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty(FLASH, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    cv2.imshow(FLASH, black)
    end = time.perf_counter() + args.settle_s  # settle: auto exposure adapts to the dark screen
    aborted = False
    while time.perf_counter() < end:
        if cv2.waitKey(1) & 0xFF in (27, ord("q")):
            aborted = True
            break
    gaps = schedule_gaps(args.flashes + 1, args.interval_ms / 1000.0, args.jitter_ms / 1000.0, rng)
    presents = []  # (t_before_imshow, t_after_waitKey, target) -- t_after is the recorded present time
    for k in range(args.flashes + 1):
        if aborted:
            break
        target = 1 if k % 2 == 0 else 0
        t0 = time.perf_counter()
        cv2.imshow(FLASH, white if target else black)
        key = cv2.waitKey(1) & 0xFF
        t1 = time.perf_counter()
        presents.append((t0, t1, target))
        if k == args.flashes:
            end = t1 + 0.4  # tail so the last settled window has frames
        else:
            end = t1 + gaps[k]
        while time.perf_counter() < end:
            key = key if key in (27, ord("q")) else cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")) or cv2.getWindowProperty(FLASH, cv2.WND_PROP_VISIBLE) < 1:
                aborted = True
                break
        if key in (27, ord("q")):
            aborted = True
    cv2.destroyAllWindows()
    cv2.waitKey(1)
    return presents, aborted, (w, h)


def live(args):
    idx = args.camera_index if args.camera_index is not None else find_camera_index(args.camera_name)
    if idx is None:
        return 2
    cap = open_camera(idx)
    if not cap.isOpened():
        print(f"[W0-311] could not open camera index {idx}", file=sys.stderr)
        return 2
    readback = {"width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                "reported_fps": cap.get(cv2.CAP_PROP_FPS)}
    for _ in range(30):
        cap.read()
    rng = random.Random(args.seed)
    cam = CaptureThread(cap, args.roi_frac)
    with timer_resolution(1):
        cam.start()
        try:
            started = preview_phase(cam, args.roi_frac, args.countdown)
            if not started:
                print("[W0-312] aborted before flashing; nothing written", file=sys.stderr)
                return 1
            print(f"[W0-313] flashing {args.flashes} times (~{args.interval_ms:g} ms +/-{args.jitter_ms:g}); q/Esc aborts", flush=True)
            presents, aborted, scr = flash_phase(args, cam, rng)
        finally:
            cam.stop()
            cap.release()
            cv2.destroyAllWindows()
    if cam.error:
        print(f"[W0-314] capture thread error: {cam.error}", file=sys.stderr)
    samples = list(cam.samples)
    if len(presents) < 4 or len(samples) < 20:
        print(f"[W0-315] too little data ({len(presents)} presents, {len(samples)} frames); nothing written", file=sys.stderr)
        return 1
    ft, fl = [s[0] for s in samples], [s[1] for s in samples]
    analysis = analyze_latency(ft, fl, [p[1] for p in presents], first_target=1)
    out = report(args, idx, readback, scr, presents, samples, analysis, aborted, cam.read_failures)
    return 0 if analysis["summary"]["n_valid"] > 0 else 1


def report(args, idx, readback, scr, presents, samples, analysis, aborted, read_failures):
    s = analysis["summary"]
    t0 = presents[0][1]
    for f in analysis["flashes"]:
        lat = f"{f['latency_ms']:7.1f} ms" if f["valid"] else f"invalid ({f['reason']})"
        print(f"[W0-320] flash {f['flash']:02d} {f['target']:5s}  step {f['step'] if f['step'] is not None else float('nan'):+6.1f}  {lat}")
        f["t_present_s"] = round(f["t_present_s"] - t0, 4)  # relative to first flash in the JSON
    lat = s["latency"]
    if lat["n"]:
        print(f"[W0-321] latency p50={lat['p50_ms']:.1f} ms  p95={lat['p95_ms']:.1f} ms  mean={lat['mean_ms']:.1f}  "
              f"min={lat['min_ms']:.1f}  max={lat['max_ms']:.1f}  (n={lat['n']}/{s['n_flashes']})")
        print(f"[W0-322] interpolated p50={s['latency_interp']['p50_ms']:.1f} ms   "
              f"rising p50={s['latency_rising_black_to_white'].get('p50_ms', float('nan')):.1f}  "
              f"falling p50={s['latency_falling_white_to_black'].get('p50_ms', float('nan')):.1f}")
    print(f"[W0-323] luma dark={s['dark_luma']} bright={s['bright_luma']} step={s['median_luma_step']}  "
          f"camera {s['effective_fps']} fps (period p50 {s['frame_period_ms_p50']} / p95 {s['frame_period_ms_p95']} ms)")
    for w in analysis["warnings"]:
        print(w, flush=True)
    present_call = [(p[1] - p[0]) * 1000.0 for p in presents]
    result = {
        "camera": args.camera_name, "camera_index": idx, "readback": readback,
        "screen_px": {"width": scr[0], "height": scr[1]},
        "params": {"flashes": args.flashes, "interval_ms": args.interval_ms, "jitter_ms": args.jitter_ms,
                   "settle_s": args.settle_s, "roi_frac": args.roi_frac, "seed": args.seed},
        "aborted": aborted, "frames": len(samples), "read_failures": read_failures,
        "present_call_ms": {"p50": round(float(np.median(present_call)), 2), "max": round(float(np.max(present_call)), 2)},
        "method": "present time = perf_counter() after cv2.imshow+cv2.waitKey(1) returns; frame time = perf_counter() "
                  "after cap.read() returns; luma = mean grey of central ROI; latency = first frame (2 consecutive) "
                  "past 50% of the settled pre/post luma step minus present time",
        "limitations": [
            "includes display/compositor/vsync/panel latency (imshow return != photons emitted; +/-1 refresh uncertainty)",
            "frame timestamps are host arrival times; resolution is one frame period; first-crossing biased up 0..1 frame",
            "auto exposure reshapes the luma step; levels measured per edge from settled windows",
        ],
        "analysis": analysis,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[W0-324] wrote {out}")
    return out


# ---------------------------------------------------------------- selftest

def synth_stream(present_t, delay_s, period_s, step_levels=(100.0, 115.0), noise=0.7, ts_jitter=0.0015,
                 drop=0.03, ramp_s=0.012, tail_s=0.6, seed=7):
    """Camera sees screen state (black=dark level, white=bright level) delay_s late, linear ramp, noise."""
    rng = np.random.default_rng(seed)
    dark, bright = step_levels
    t = present_t[0] - 1.0
    ts, ls = [], []
    while t < present_t[-1] + tail_s:
        t += period_s
        if rng.random() < drop:
            continue
        seen = t - delay_s
        level = dark  # initial screen is black
        for k, pt in enumerate(present_t):
            target_level = bright if k % 2 == 0 else dark
            frac = min(max((seen - pt) / ramp_s, 0.0), 1.0)
            if seen >= pt:
                level = target_level if frac >= 1.0 else (dark if k % 2 == 0 else bright) + (target_level - (dark if k % 2 == 0 else bright)) * frac
        ts.append(t + rng.normal(0, ts_jitter))
        ls.append(level + rng.normal(0, noise))
    return ts, ls


class FakeCap:
    """cap.read() stand-in: ~33 ms frames whose brightness follows a screen that is delay_s late."""

    def __init__(self, events, delay_s, period_s, levels=(100, 115)):
        self.events, self.delay, self.period, self.levels = events, delay_s, period_s, levels
        self.next = time.perf_counter()
        self.rng = np.random.default_rng(3)

    def read(self):
        self.next += self.period
        time.sleep(max(0.0, self.next - time.perf_counter()))
        seen = time.perf_counter() - self.delay
        level = self.levels[0]
        for t_evt, target in list(self.events):
            if t_evt <= seen:
                level = self.levels[target]
        return True, np.full((72, 128, 3), np.uint8(np.clip(level + self.rng.normal(0, 0.7), 0, 255)))


def selftest():
    ok = True

    def check(name, passed, detail=""):
        nonlocal ok
        print(f"[W0-330] {'PASS' if passed else 'FAIL'}: {name} {detail}")
        ok &= bool(passed)

    # A: pure analysis on synthetic stream, truth 85 ms @ 33.3 ms frames, 40 flashes with jitter.
    period, truth_ms = 1 / 30.0, 85.0
    rng = random.Random(11)
    t, present = 5.0, []
    for gap in [0.0] + schedule_gaps(40, 0.7, 0.15, rng):
        t += gap
        present.append(t)
    ft, fl = synth_stream(present, truth_ms / 1000.0, period)
    res = analyze_latency(ft, fl, present)
    s = res["summary"]
    p50, p95, interp = s["latency"]["p50_ms"], s["latency"]["p95_ms"], s["latency_interp"]["p50_ms"]
    print(f"[W0-331] A: n_valid={s['n_valid']}/{s['n_flashes']} p50={p50} p95={p95} interp_p50={interp} "
          f"dark={s['dark_luma']} bright={s['bright_luma']} step={s['median_luma_step']} fps={s['effective_fps']} warnings={res['warnings']}")
    check("A all 40 flashes valid", s["n_valid"] == 40)
    check("A p50 within one frame period of truth", abs(p50 - truth_ms) <= period * 1000, f"(err {p50 - truth_ms:+.1f} ms)")
    check("A p95 within two frame periods of truth", abs(p95 - truth_ms) <= 2 * period * 1000, f"(err {p95 - truth_ms:+.1f} ms)")
    check("A interpolated p50 within half a frame of truth", abs(interp - truth_ms) <= 0.5 * period * 1000, f"(err {interp - truth_ms:+.1f} ms)")
    check("A levels measured", abs(s["dark_luma"] - 100) < 1.5 and abs(s["bright_luma"] - 115) < 1.5)
    check("A no warnings", not res["warnings"])

    # B: weak luma step (room too bright) must raise the warning.
    ft, fl = synth_stream(present, truth_ms / 1000.0, period, step_levels=(100.0, 104.0))
    resb = analyze_latency(ft, fl, present)
    check("B luma step < 8 raises [W0-301]", any("[W0-301]" in w for w in resb["warnings"]),
          f"(step {resb['summary']['median_luma_step']})")

    # C: no step at all -> flashes invalid, no crash.
    ft, fl = synth_stream(present, truth_ms / 1000.0, period, step_levels=(100.0, 100.0))
    resc = analyze_latency(ft, fl, present)
    check("C flat luma -> 0 valid flashes, warns", resc["summary"]["n_valid"] == 0 and bool(resc["warnings"]))

    # D: real CaptureThread + real perf_counter timing against a fake camera (6 quick flashes, truth 85 ms).
    with timer_resolution(1):
        events = []
        cam = CaptureThread(FakeCap(events, truth_ms / 1000.0, period), roi_frac=0.5)
        cam.start()
        time.sleep(0.5)
        pres = []
        rng = random.Random(5)
        for k, gap in enumerate(schedule_gaps(7, 0.4, 0.05, rng)):
            tp = time.perf_counter()
            events.append((tp, 1 if k % 2 == 0 else 0))
            pres.append(tp)
            time.sleep(gap)
        cam.stop()
    samples = list(cam.samples)
    resd = analyze_latency([x[0] for x in samples], [x[1] for x in samples], pres)
    sd = resd["summary"]
    d50 = sd["latency"].get("p50_ms", float("nan"))
    print(f"[W0-332] D: frames={len(samples)} fps={sd['effective_fps']} n_valid={sd['n_valid']}/6 p50={d50} "
          f"(period p95 {sd['frame_period_ms_p95']} ms)")
    check("D capture thread produced frames, no error", len(samples) > 60 and cam.error is None)
    check("D >= 5 of 6 flashes valid", sd["n_valid"] >= 5)
    check("D p50 within 1.5 frame periods of truth", abs(d50 - truth_ms) <= 1.5 * period * 1000, f"(err {d50 - truth_ms:+.1f} ms)")

    print(f"SELFTEST {'PASS' if ok else 'FAIL'} (latency_flash_test)")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--flashes", type=int, default=40)
    ap.add_argument("--interval-ms", type=float, default=700.0)
    ap.add_argument("--jitter-ms", type=float, default=150.0)
    ap.add_argument("--settle-s", type=float, default=2.0, help="dark settle time before the first flash")
    ap.add_argument("--roi-frac", type=float, default=0.5, help="central ROI as a fraction of width/height")
    ap.add_argument("--camera-name", default=TARGET_NAME)
    ap.add_argument("--camera-index", type=int, default=None, help="override name lookup")
    ap.add_argument("--countdown", type=float, default=0.0, help="0 = wait for SPACE; N = auto-start after N s")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--selftest", action="store_true", help="offline synthetic check; no camera/window, writes nothing")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    return live(args)


if __name__ == "__main__":
    sys.exit(main())
