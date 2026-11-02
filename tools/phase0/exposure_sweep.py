"""Phase 0-W exposure sweep: find a locked exposure that keeps 30 fps and a usable image.

Usage:
  python exposure_sweep.py --index 0 --out ../../docs/data/exposure_sweep.json [--seconds 5]
"""

import argparse
import json

import cv2

from webcam_probe import measure, open_cam, readback


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=5.0)
    args = ap.parse_args()

    runs = []
    for exposure in range(-13, 1):
        cap = open_cam(args.index, cv2.CAP_DSHOW, 640, 480)
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
        cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
        run = {"requested_exposure": exposure, "readback": readback(cap)}
        run.update(measure(cap, args.seconds))
        cap.release()
        print(json.dumps({k: run.get(k) for k in ("requested_exposure", "fps", "frame_ms_p95", "mean_luma")}),
              "readback_exposure=", run["readback"]["exposure"], flush=True)
        runs.append(run)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"backend": "DSHOW", "mode": "640x480", "runs": runs}, fh, indent=2)


if __name__ == "__main__":
    main()
