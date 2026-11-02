"""Phase 0-W webcam intrinsics calibration with a 9x6 inner-corner checkerboard.

Live mode: opens the HP Wide Vision HD Camera (DirectShow, MJPG 1280x720@30, auto
exposure), shows a preview, and auto-captures a view whenever the board is found
by cv2.findChessboardCornersSB, is holding still, and its pose (position, scale,
tilt) differs enough from every earlier capture. On-screen guidance tells you
how to move the board. After 20 views it runs cv2.calibrateCamera and writes
fx, fy, cx, cy, distortion, RMS error, FOV (h/v/diag) and a Kinect v1 FOV
comparison to a JSON file.

Print docs/checkerboard_9x6.png at 100% first (see make_checkerboard.py), mount
it flat, MEASURE one printed square and pass it as --square-mm.

Keys in the preview: SPACE force-capture (board must be detected), u undo last,
c calibrate now (needs >= 10 views), q/Esc abort.

Usage:
  python calibrate_intrinsics.py [--square-mm 25] [--camera-name "HP Wide Vision HD Camera"]
                                 [--out ../../docs/data/intrinsics.json] [--views 20]
  python calibrate_intrinsics.py --selftest      (offline synthetic check, no camera)

Notes: intrinsics are valid for the calibrated image size only (1280x720 here).
k3 is fixed to 0 by default (--free-k3 to estimate it): with ~20 views at 720p it
is poorly constrained and tends to overfit.
"""

import argparse
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

TARGET_NAME = "HP Wide Vision HD Camera"
PATTERN = (9, 6)  # inner corners (cols, rows)
DEFAULT_VIEWS = 20
MIN_VIEWS = 10
KINECT_V1_FOV = {"color": {"h": 62.0, "v": 48.6}, "depth": {"h": 57.0, "v": 43.0}}
WINDOW = "KinectEmu Phase 0 - intrinsics calibration"
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "docs" / "data" / "intrinsics.json"
SB_FLAGS = cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_ACCURACY


# ---------------------------------------------------------------- camera helpers

def find_camera_index(name):
    """Index of the DirectShow video input called `name` (exact, then substring)."""
    from pygrabber.dshow_graph import FilterGraph  # lazy: keeps --selftest COM-free
    devices = FilterGraph().get_input_devices()
    for idx, dev in enumerate(devices):
        if dev == name:
            return idx
    for idx, dev in enumerate(devices):
        if name.lower() in dev.lower():
            return idx
    print(f"[W0-201] camera '{name}' not found; devices: {devices}", file=sys.stderr)
    return None


def open_camera(index, width=1280, height=720, fps=30):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)  # auto; never lock (black frames on this camera)
    return cap


# ---------------------------------------------------------------- detection / pose

def board_object_points(square_mm):
    objp = np.zeros((PATTERN[0] * PATTERN[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:PATTERN[0], 0:PATTERN[1]].T.reshape(-1, 2) * float(square_mm)
    return objp


def detect_corners(gray, fast=False):
    """Return (54,1,2) float32 corners or None. fast=True gates on a half-res pass first."""
    if fast:
        small = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        ok, _ = cv2.findChessboardCornersSB(small, PATTERN, flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
        if not ok:
            return None
    ok, corners = cv2.findChessboardCornersSB(gray, PATTERN, flags=SB_FLAGS)
    if not ok:
        return None
    return corners.reshape(-1, 1, 2).astype(np.float32)


def pose_features(corners, size):
    """5-vector: centre x/y (frac of image), log scale, pitch tilt, yaw tilt.

    pitch = log(top edge / bottom edge) of the corner grid, yaw = log(left / right):
    zero when the board is fronto-parallel, +/- when one side is nearer the camera.
    """
    w, h = size
    c = corners.reshape(PATTERN[1], PATTERN[0], 2).astype(np.float64)
    length = lambda a, b: float(np.linalg.norm(a - b))
    top, bot = length(c[0, 0], c[0, -1]), length(c[-1, 0], c[-1, -1])
    left, right = length(c[0, 0], c[-1, 0]), length(c[0, -1], c[-1, -1])
    quad = np.array([c[0, 0], c[0, -1], c[-1, -1], c[-1, 0]], np.float32)
    area = abs(cv2.contourArea(quad))
    cen = c.reshape(-1, 2).mean(axis=0)
    return np.array([cen[0] / w, cen[1] / h, math.log(math.sqrt(area / (w * h))),
                     math.log(top / bot), math.log(left / right)])


class PoseGate:
    """Accepts a view only if it differs enough from all accepted ones."""

    SIGMA = np.array([0.12, 0.12, 0.15, 0.06, 0.06])  # 1.0 = "different enough"
    FLAT = 0.04        # tilt magnitude below which a view counts as fronto-parallel
    MAX_FLAT = 4       # a planar target needs tilt: cap near-flat views
    TILTED = 0.08

    def __init__(self):
        self.feats = []

    @staticmethod
    def tilt(f):
        return math.hypot(f[3], f[4])

    def check(self, f):
        """(accept, reason)."""
        if self.feats:
            d = min(float(np.linalg.norm((f - g) / self.SIGMA)) for g in self.feats)
            if d < 1.0:
                return False, "similar"
        if self.tilt(f) < self.FLAT and sum(self.tilt(g) < self.FLAT for g in self.feats) >= self.MAX_FLAT:
            return False, "flat"
        return True, ""

    def add(self, f):
        self.feats.append(f)

    def pop(self):
        if self.feats:
            self.feats.pop()

    def guidance(self, reason=""):
        n = len(self.feats)
        if n == 0:
            return "Hold the board still, facing the camera, to start"
        f = np.array(self.feats)
        tilted = sum(self.tilt(g) > self.TILTED for g in f)
        if reason == "flat" or tilted < min(n, 6) * 0.6:
            pitch, yaw = f[:, 3], f[:, 4]
            wants = [("top edge toward camera", (pitch > self.TILTED).any()),
                     ("top edge away from camera", (pitch < -self.TILTED).any()),
                     ("left edge toward camera", (yaw > self.TILTED).any()),
                     ("right edge toward camera", (yaw < -self.TILTED).any())]
            missing = [t for t, have in wants if not have]
            return "Tilt the board: " + (missing[0] if missing else "any direction") + " (20-40 deg)"
        cells = {(min(2, int(g[0] * 3)), min(2, int(g[1] * 3))) for g in f}
        names = {(0, 0): "top-left", (2, 0): "top-right", (0, 2): "bottom-left", (2, 2): "bottom-right",
                 (1, 0): "top", (1, 2): "bottom", (0, 1): "left", (2, 1): "right", (1, 1): "centre"}
        for c in [(0, 0), (2, 0), (0, 2), (2, 2), (1, 0), (1, 2), (0, 1), (2, 1), (1, 1)]:
            if c not in cells:
                return f"Move the board to the {names[c]} corner" if c in [(0, 0), (2, 0), (0, 2), (2, 2)] \
                    else f"Move the board to the {names[c]} of the frame"
        if f[:, 2].max() - f[:, 2].min() < 0.4:
            return "Vary the distance: move the board closer, then farther"
        return "Change the pose: rotate the board in-plane or tilt it a new way"


# ---------------------------------------------------------------- calibration

def fov_deg(fx, fy, w, h):
    hfov = 2 * math.degrees(math.atan(w / (2 * fx)))
    vfov = 2 * math.degrees(math.atan(h / (2 * fy)))
    dfov = 2 * math.degrees(math.atan(0.5 * math.hypot(w / fx, h / fy)))
    return hfov, vfov, dfov


def kinect_comparison(fx, fy, w, h):
    hfov, vfov, _ = fov_deg(fx, fy, w, h)
    crop_w = min(w, h * 4.0 / 3.0)  # Kinect colour is 4:3: what a centre 4:3 crop of the webcam sees
    h43 = 2 * math.degrees(math.atan(crop_w / (2 * fx)))
    out = {"webcam_fov_deg": {"horizontal": round(hfov, 2), "vertical": round(vfov, 2)},
           "webcam_4x3_center_crop_fov_deg": {"horizontal": round(h43, 2), "vertical": round(vfov, 2)}}
    for name, k in KINECT_V1_FOV.items():
        out[f"kinect_v1_{name}"] = {
            "horizontal_deg": k["h"], "vertical_deg": k["v"],
            "delta_h_deg": round(hfov - k["h"], 2), "delta_v_deg": round(vfov - k["v"], 2),
            "delta_h_4x3_crop_deg": round(h43 - k["h"], 2),
            "webcam_wider_h": bool(hfov > k["h"]), "webcam_wider_v": bool(vfov > k["v"]),
        }
    return out


def run_calibration(corner_sets, size, square_mm, free_k3=False):
    """cv2.calibrateCamera over detected corner sets. size = (width, height)."""
    objp = board_object_points(square_mm)
    obj_pts = [objp] * len(corner_sets)
    flags = 0 if free_k3 else cv2.CALIB_FIX_K3
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(obj_pts, corner_sets, size, None, None, flags=flags)
    per_view = []
    for op, ip, rv, tv in zip(obj_pts, corner_sets, rvecs, tvecs):
        proj, _ = cv2.projectPoints(op, rv, tv, K, dist)
        per_view.append(float(np.sqrt(np.mean(np.sum((proj - ip) ** 2, axis=2)))))
    fx, fy, cx, cy = float(K[0, 0]), float(K[1, 1]), float(K[0, 2]), float(K[1, 2])
    w, h = size
    hf, vf, df = fov_deg(fx, fy, w, h)
    return {
        "image_size": {"width": w, "height": h},
        "square_mm": float(square_mm),
        "pattern_inner_corners": list(PATTERN),
        "num_views": len(corner_sets),
        "rms_reprojection_error_px": round(float(rms), 4),
        "per_view_rms_px": [round(v, 4) for v in per_view],
        "fx": round(fx, 3), "fy": round(fy, 3), "cx": round(cx, 3), "cy": round(cy, 3),
        "camera_matrix": [[float(v) for v in row] for row in K],
        "distortion_coeffs": [float(v) for v in dist.reshape(-1)],
        "distortion_model": "opencv plumb_bob [k1, k2, p1, p2, k3]" + ("" if free_k3 else ", k3 fixed to 0"),
        "fov_deg": {"horizontal": round(hf, 2), "vertical": round(vf, 2), "diagonal": round(df, 2)},
        "kinect_v1_comparison": kinect_comparison(fx, fy, w, h),
    }


# ---------------------------------------------------------------- live mode

def put(img, text, y, color=(255, 255, 255), scale=0.8, thick=2):
    cv2.putText(img, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 3, cv2.LINE_AA)
    cv2.putText(img, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def draw_overlay(frame, corners, n, total, msg, status_color, flash):
    out = frame.copy()
    h, w = out.shape[:2]
    if corners is not None:
        cv2.drawChessboardCorners(out, PATTERN, corners, True)
    cv2.rectangle(out, (0, 0), (w, 92), (0, 0, 0), -1)
    put(out, f"Captured {n}/{total}", 36, (255, 255, 255))
    put(out, msg, 76, status_color, scale=0.85)
    bar_w = int((w - 40) * n / total)
    cv2.rectangle(out, (20, h - 24), (w - 20, h - 10), (60, 60, 60), -1)
    cv2.rectangle(out, (20, h - 24), (20 + bar_w, h - 10), (0, 200, 0), -1)
    if flash:
        cv2.rectangle(out, (0, 0), (w - 1, h - 1), (255, 255, 255), 24)
    return out


def live(args):
    idx = args.camera_index if args.camera_index is not None else find_camera_index(args.camera_name)
    if idx is None:
        return 2
    cap = open_camera(idx)
    if not cap.isOpened():
        print(f"[W0-202] could not open camera index {idx}", file=sys.stderr)
        return 2
    for _ in range(30):  # let auto exposure settle
        cap.read()
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, 1280, 720)
    cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)

    gate, corner_sets = PoseGate(), []
    size, last_corners, last_capture, flash_until, fails = None, None, 0.0, 0.0, 0
    msg, color, aborted, calibrate_now = "Show the board to the camera", (0, 255, 255), False, False
    print(f"[W0-203] camera index {idx}; capturing {args.views} views (square {args.square_mm:g} mm)", flush=True)

    while len(corner_sets) < args.views and not calibrate_now:
        ok, frame = cap.read()
        if not ok:
            fails += 1
            if fails > 200:
                print("[W0-204] camera stopped delivering frames", file=sys.stderr)
                aborted = True
                break
            continue
        size = (frame.shape[1], frame.shape[0])
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners = detect_corners(gray, fast=True)
        now = time.perf_counter()
        force = False
        key = -1

        if corners is None:
            last_corners = None
            msg, color = "Board not found: show all 9x6 inner corners, flat, no glare", (0, 165, 255)
        else:
            feats = pose_features(corners, size)
            moving = last_corners is not None and float(np.mean(np.linalg.norm(
                (corners - last_corners).reshape(-1, 2), axis=1))) > 2.0
            last_corners = corners
            accept, reason = gate.check(feats)
            small = math.exp(feats[2]) < 0.17  # sqrt(area/image area): corner grid too small in frame
            if moving:
                msg, color = "Hold still...", (0, 255, 255)
            elif now - last_capture < 0.8:
                msg, color = "Captured. " + gate.guidance(), (0, 255, 0)
            elif small:
                msg, color = "Board too small: move it closer to the camera", (0, 165, 255)
            elif not accept:
                msg, color = gate.guidance(reason), (0, 255, 255)
            else:
                corner_sets.append(corners.copy())
                gate.add(feats)
                last_capture, flash_until = now, now + 0.18
                print(f"[W0-205] captured view {len(corner_sets)}/{args.views}", flush=True)
                msg, color = "Captured! " + gate.guidance(), (0, 255, 0)

        cv2.imshow(WINDOW, draw_overlay(frame, corners, len(corner_sets), args.views, msg, color,
                                        time.perf_counter() < flash_until))
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            aborted = True
            break
        if key == ord("u") and corner_sets:
            corner_sets.pop()
            gate.pop()
        elif key == ord("c") and len(corner_sets) >= MIN_VIEWS:
            calibrate_now = True
        elif key == ord(" ") and corners is not None and now - last_capture > 0.3:
            corner_sets.append(corners.copy())
            gate.add(pose_features(corners, size))
            last_capture, flash_until = now, now + 0.18
            print(f"[W0-205] force-captured view {len(corner_sets)}/{args.views}", flush=True)

    readback = {"width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                "reported_fps": cap.get(cv2.CAP_PROP_FPS)}
    cap.release()
    cv2.destroyAllWindows()
    if aborted or len(corner_sets) < MIN_VIEWS:
        print(f"[W0-206] aborted/insufficient views ({len(corner_sets)}); nothing written", file=sys.stderr)
        return 1

    result = run_calibration(corner_sets, size, args.square_mm, free_k3=args.free_k3)
    result.update({"camera": args.camera_name, "camera_index": idx, "readback": readback,
                   "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")})
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print_summary(result)
    print(f"[W0-207] wrote {out}")
    if result["rms_reprojection_error_px"] > 1.0:
        print("[W0-208] WARNING: RMS > 1 px: check the board is flat/rigid and --square-mm is measured", flush=True)
    return 0


def print_summary(r):
    f = r["fov_deg"]
    print(f"[W0-210] {r['image_size']['width']}x{r['image_size']['height']}  views={r['num_views']}  "
          f"RMS={r['rms_reprojection_error_px']:.3f}px")
    print(f"[W0-211] fx={r['fx']:.1f} fy={r['fy']:.1f} cx={r['cx']:.1f} cy={r['cy']:.1f} "
          f"dist={[round(v, 4) for v in r['distortion_coeffs']]}")
    print(f"[W0-212] FOV h={f['horizontal']:.1f} v={f['vertical']:.1f} diag={f['diagonal']:.1f} deg  "
          f"(Kinect v1 colour 62.0x48.6, depth 57.0x43.0)")


# ---------------------------------------------------------------- selftest

def _rot(rx, ry, rz):
    cx, sx, cy, sy, cz, sz = (math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz))
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def render_synthetic_view(K, dist, rvec, tvec, size, square_mm, rng, ss=2):
    """Render the 10x7-square board (with a white paper margin) as seen by the known camera."""
    w, h = size
    s = float(square_mm)
    Kss = K.copy()
    Kss[0, 0] *= ss
    Kss[1, 1] *= ss
    Kss[0, 2] = K[0, 2] * ss + (ss - 1) / 2.0  # pixel centres at integer coords (OpenCV convention)
    Kss[1, 2] = K[1, 2] * ss + (ss - 1) / 2.0
    img = np.full((h * ss, w * ss), 70, np.uint8)  # dark table

    def poly(x0, y0, x1, y1, color, n=16):
        # Subdivide edges so lens distortion bends them like the real thing.
        t = np.linspace(0.0, 1.0, n, endpoint=False)
        edge = np.concatenate([
            np.stack([x0 + (x1 - x0) * t, np.full(n, y0)], 1), np.stack([np.full(n, x1), y0 + (y1 - y0) * t], 1),
            np.stack([x1 - (x1 - x0) * t, np.full(n, y1)], 1), np.stack([np.full(n, x0), y1 - (y1 - y0) * t], 1)])
        pts3 = np.concatenate([edge, np.zeros((len(edge), 1))], 1).astype(np.float64)
        px, _ = cv2.projectPoints(pts3, rvec, tvec, Kss, dist)
        cv2.fillPoly(img, [np.round(px.reshape(-1, 2) * 16).astype(np.int32)], color, cv2.LINE_8, 4)

    poly(-2 * s, -2 * s, 10 * s, 7 * s, 235)  # paper: squares span x in [-s,9s], y in [-s,6s]
    for j in range(7):
        for i in range(10):
            if (i + j) % 2 == 0:
                poly((i - 1) * s, (j - 1) * s, i * s, j * s, 25)
    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    img = cv2.GaussianBlur(img, (0, 0), 0.8)
    noisy = img.astype(np.float32) + rng.normal(0, 2.5, img.shape).astype(np.float32)
    return np.clip(noisy, 0, 255).astype(np.uint8)


def synthesize_views(n, K, dist, size, square_mm, seed=1234):
    rng = np.random.default_rng(seed)
    w, h = size
    s = square_mm
    centre = np.array([4 * s, 2.5 * s, 0.0])  # middle of the inner-corner grid
    objp = board_object_points(s).astype(np.float64)
    paper = np.array([[-2 * s, -2 * s, 0], [10 * s, -2 * s, 0], [10 * s, 7 * s, 0], [-2 * s, 7 * s, 0]], np.float64)
    views = []
    while len(views) < n:
        R = _rot(*np.radians([rng.uniform(-35, 35), rng.uniform(-35, 35), rng.uniform(-30, 30)]))
        t_c = np.array([rng.uniform(-260, 260), rng.uniform(-160, 160), rng.uniform(450, 800)])
        tvec = (t_c - R @ centre).reshape(3, 1)
        rvec, _ = cv2.Rodrigues(R)
        pts = np.concatenate([objp, paper])
        if (R @ pts.T + tvec)[2].min() < 100:
            continue
        proj, _ = cv2.projectPoints(pts, rvec, tvec, K, dist)
        proj = proj.reshape(-1, 2)
        if proj[:, 0].min() < 8 or proj[:, 0].max() > w - 8 or proj[:, 1].min() < 8 or proj[:, 1].max() > h - 8:
            continue
        views.append(render_synthetic_view(K, dist, rvec, tvec, size, s, rng))
    return views


def selftest():
    size, sq = (1280, 720), 25.0
    K = np.array([[1000.0, 0, 640.0], [0, 1000.0, 360.0], [0, 0, 1]])
    dist = np.array([-0.1, 0.0, 0.0, 0.0, 0.0])
    t0 = time.perf_counter()
    images = synthesize_views(20, K, dist, size, sq)
    print(f"[W0-220] synthesised {len(images)} views in {time.perf_counter() - t0:.1f}s")

    corner_sets, gate, accepted = [], PoseGate(), 0
    for img in images:
        corners = detect_corners(img)  # same detector as live mode (full-res SB)
        if corners is not None:
            corner_sets.append(corners)
            f = pose_features(corners, size)
            if gate.check(f)[0]:
                gate.add(f)
                accepted += 1
    res = run_calibration(corner_sets, size, sq)
    fx, fy, cx, cy = res["fx"], res["fy"], res["cx"], res["cy"]
    print(f"[W0-221] detected {len(corner_sets)}/{len(images)}; gate accepted {accepted}")
    print(f"[W0-222] recovered fx={fx:.2f} fy={fy:.2f} cx={cx:.2f} cy={cy:.2f} "
          f"dist={[round(v, 4) for v in res['distortion_coeffs']]} RMS={res['rms_reprojection_error_px']:.3f}px")
    print(f"[W0-223] FOV h={res['fov_deg']['horizontal']} v={res['fov_deg']['vertical']} "
          f"diag={res['fov_deg']['diagonal']} deg (truth h={2 * math.degrees(math.atan(640 / 1000)):.2f} "
          f"v={2 * math.degrees(math.atan(360 / 1000)):.2f})")

    # Gate sanity: duplicate of an accepted view is rejected; a fresh gate accepts the first view.
    g2 = PoseGate()
    f0 = pose_features(corner_sets[0], size)
    first_ok = g2.check(f0)[0]
    g2.add(f0)
    dup_rejected = not g2.check(f0 + 0.001)[0]

    checks = [
        ("all 20 views detected", len(corner_sets) == 20),
        ("fx within 2%", abs(fx - 1000) / 1000 < 0.02),
        ("fy within 2%", abs(fy - 1000) / 1000 < 0.02),
        ("cx within 10 px", abs(cx - 640) < 10),
        ("cy within 10 px", abs(cy - 360) < 10),
        ("RMS < 1 px", res["rms_reprojection_error_px"] < 1.0),
        ("gate accepts first / rejects duplicate", first_ok and dup_rejected),
        ("gate accepts >= 10 of the 20 varied views", accepted >= 10),
    ]
    ok = True
    for name, passed in checks:
        print(f"[W0-224] {'PASS' if passed else 'FAIL'}: {name}")
        ok &= bool(passed)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} (calibrate_intrinsics)")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--square-mm", type=float, default=25.0, help="measured printed square size (mm)")
    ap.add_argument("--camera-name", default=TARGET_NAME)
    ap.add_argument("--camera-index", type=int, default=None, help="override name lookup")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--views", type=int, default=DEFAULT_VIEWS)
    ap.add_argument("--free-k3", action="store_true", help="estimate k3 too (default: fixed to 0)")
    ap.add_argument("--selftest", action="store_true", help="offline synthetic check; no camera, writes nothing")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    return live(args)


if __name__ == "__main__":
    sys.exit(main())
