"""Phase 0-W webcam probe: enumerate devices/modes and measure delivered fps.

Measures fps under auto exposure and with exposure locked, per backend and
mode, and writes a JSON result next to the given output path.

Usage:
  python webcam_probe.py --out ../../docs/data/webcam_probe.json [--seconds 20]
"""

import argparse
import json
import statistics
import sys
import time

import cv2
from pygrabber.dshow_graph import FilterGraph

TARGET_NAME = "HP Wide Vision HD Camera"
MODES = [(640, 480), (1280, 720)]
BACKENDS = {"MSMF": cv2.CAP_MSMF, "DSHOW": cv2.CAP_DSHOW}
# DirectShow exposure is log2(seconds): -5 = 1/32 s (fits a 33 ms frame),
# -6 = 1/64 s. Values longer than the frame period force the fps down.
LOCKED_EXPOSURES = [-5, -6]


def list_devices():
    graph = FilterGraph()
    names = graph.get_input_devices()
    devices = []
    for idx, name in enumerate(names):
        entry = {"index": idx, "name": name, "formats": []}
        try:
            g = FilterGraph()
            g.add_video_input_device(idx)
            for f in g.get_input_device().get_formats():
                entry["formats"].append(
                    {
                        "width": f["width"],
                        "height": f["height"],
                        "subtype": f["media_type_str"],
                        "min_fps": round(f["min_framerate"], 2),
                        "max_fps": round(f["max_framerate"], 2),
                    }
                )
        except Exception as exc:  # broken virtual cameras (e.g. Camo) land here
            entry["error"] = repr(exc)
        devices.append(entry)
    return devices


def measure(cap, seconds):
    # Warm up so auto-exposure / stream start-up does not skew the numbers.
    warm_end = time.perf_counter() + 2.0
    while time.perf_counter() < warm_end:
        cap.read()
    stamps, brightness, failures = [], [], 0
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        ok, frame = cap.read()
        now = time.perf_counter()
        if not ok:
            failures += 1
            continue
        stamps.append(now)
        if len(stamps) % 15 == 0:
            brightness.append(float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean()))
    if len(stamps) < 2:
        return {"frames": len(stamps), "read_failures": failures}
    deltas = [(b - a) * 1000.0 for a, b in zip(stamps, stamps[1:])]
    deltas_sorted = sorted(deltas)
    return {
        "frames": len(stamps),
        "read_failures": failures,
        "fps": round((len(stamps) - 1) / (stamps[-1] - stamps[0]), 2),
        "frame_ms_p50": round(statistics.median(deltas), 2),
        "frame_ms_p95": round(deltas_sorted[int(len(deltas_sorted) * 0.95)], 2),
        "frame_ms_max": round(deltas_sorted[-1], 2),
        "late_frames_over_40ms": sum(d > 40.0 for d in deltas),
        "mean_luma": round(statistics.mean(brightness), 1) if brightness else None,
    }


def open_cam(index, backend, width, height):
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, 30)
    return cap


def readback(cap):
    fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
    return {
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "reported_fps": cap.get(cv2.CAP_PROP_FPS),
        "fourcc": "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)),
        "auto_exposure": cap.get(cv2.CAP_PROP_AUTO_EXPOSURE),
        "exposure": cap.get(cv2.CAP_PROP_EXPOSURE),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=20.0)
    args = ap.parse_args()

    devices = list_devices()
    target = next((d for d in devices if d["name"] == TARGET_NAME), None)
    if target is None:
        print(f"[W0-001] target camera '{TARGET_NAME}' not found", file=sys.stderr)
        result = {"devices": devices, "runs": []}
    else:
        runs = []
        for bname, backend in BACKENDS.items():
            for w, h in MODES:
                configs = [("auto", None)] + [(f"locked_{e}", e) for e in LOCKED_EXPOSURES]
                for label, exposure in configs:
                    cap = open_cam(target["index"], backend, w, h)
                    if cap is None:
                        runs.append({"backend": bname, "mode": f"{w}x{h}", "exposure_mode": label,
                                     "error": "[W0-002] open failed"})
                        continue
                    if exposure is None:
                        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)
                    else:
                        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
                        cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
                    run = {"backend": bname, "mode": f"{w}x{h}", "exposure_mode": label,
                           "requested_exposure": exposure, "readback": readback(cap)}
                    run.update(measure(cap, args.seconds))
                    cap.release()
                    print(json.dumps(run), flush=True)
                    runs.append(run)
        result = {"devices": devices, "target": TARGET_NAME, "seconds_per_run": args.seconds,
                  "runs": runs}

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)


if __name__ == "__main__":
    main()
