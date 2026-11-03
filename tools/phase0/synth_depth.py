"""Phase 0 Option A' probe: webcam frames -> synthetic Kinect depth (.xdepth).

For each frame the GPU pose pipeline gives every player 3D world landmarks, 2D
landmarks and a segmentation mask. Per player:
  1. solvePnP of the world landmarks against the 2D ones places the body in
     camera space (metres). MediaPipe's world scale is an average adult, so the
     distance is only as right as that and the intrinsics.
  2. Each mask pixel takes the camera-space Z of its nearest landmark,
     smoothed inside the mask, then is pulled toward the camera by a rounded
     edge profile of radius --relief-mm (a body is not a flat cut-out; the
     4D5308ED tracker wants 50..300 mm torso relief).
  3. Each player is optionally moved straight back by --push-mm (the webcam is
     wider than the Kinect, so a player framed for it stands too close), resampled
     by ray angle into the Kinect depth camera (640x480, f = 583.3 px, the value
     the title uses), and z-buffered.
Background is 0 (no reading) unless --wall-mm is given.

Frames are written in Kinect for Windows orientation (--no-mirror to skip the
horizontal flip of the webcam image); the fork reverses rows again for the guest.

  python synth_depth.py --models ../../models --fixture ../../fixtures/fullbody_01 \
      --out ../../docs/data/synth_depth.json --xdepth fixture.xdepth [--intrinsics ../../docs/data/intrinsics.json]
  python synth_depth.py --models ../../models --live --intrinsics ../../docs/data/intrinsics.json \
      --push-mm 700        # webcam -> xenia --nui=pipe --nui_pipe=kinectemu
"""
import argparse
import json
import math
import statistics
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from gpu_pose_pipeline import Detector, DetectorWorker, GpuPosePipeline
from xdepth_writer import XdepthWriter

KW, KH, KF = 640, 480, 583.3  # Kinect depth camera as the 4D5308ED runtime models it
BODY = 33  # MediaPipe body landmarks; 33, 34 are aux ROI points
# Tracker acceptance limits from PSCAM_CONTRACT.md §8.
LIMITS = {"root_mm": (1800, 2920), "area_m2": (0.2, 2.0)}
# Key in the per-slot state dict that holds the filtered floor height instead.
FLOOR_KEY = "floor_y_mm"
# Kinect pixel offsets from the optical axis, shared by every to_kinect() call.
_GRID_U, _GRID_V = np.meshgrid(np.arange(KW, dtype=np.float32) - (KW - 1) / 2,
                               np.arange(KH, dtype=np.float32) - (KH - 1) / 2)


def camera_matrix(args, w, h):
    if args.intrinsics and Path(args.intrinsics).exists():
        j = json.loads(Path(args.intrinsics).read_text(encoding="utf-8"))
        s = w / j["image_size"]["width"]
        return np.array([[j["fx"] * s, 0, j["cx"] * s], [0, j["fy"] * s, j["cy"] * s], [0, 0, 1]]), "calibrated"
    f = (w / 2) / math.tan(math.radians(args.hfov) / 2)
    return np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]]), f"assumed hfov {args.hfov} deg"


def place(p, K):
    """Camera-space landmark positions (m) from PnP, or None."""
    vis = p["visibility"][:BODY] > 0.5
    if vis.sum() < 8:
        return None
    try:
        ok, rvec, tvec = cv2.solvePnP(p["world"][:BODY][vis].astype(np.float64),
                                      p["xy"][:BODY, :2][vis].astype(np.float64), K, None,
                                      flags=cv2.SOLVEPNP_SQPNP)
    except cv2.error:
        # SQPnP asserts on landmarks with almost no spread, which is what a
        # false detection produces (seen live); that is no player.
        return None
    if not ok or tvec[2, 0] <= 0:
        return None
    R, _ = cv2.Rodrigues(rvec)
    return (p["world"][:BODY] @ R.T + tvec.T), vis


def player_depth(p, cam, vis, mask_scale, fx_mask, relief_mm):
    """Depth (mm) over the player's mask at mask resolution, 0 outside."""
    full = p["mask"] > 0.5
    ys, xs = np.nonzero(full)
    if not ys.size:
        return None
    # Work in the silhouette's box only, with room for the blur.
    pad = 20
    y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad + 1, full.shape[0])
    x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad + 1, full.shape[1])
    m = full[y0:y1, x0:x1]
    pts = p["xy"][:BODY, :2][vis] * mask_scale - (x0, y0)
    px = np.clip(np.round(pts[:, 0]).astype(int), 0, x1 - x0 - 1)
    py = np.clip(np.round(pts[:, 1]).astype(int), 0, y1 - y0 - 1)
    z = (cam[vis, 2] * 1000.0).astype(np.float32)
    # Nearest landmark per pixel: a distance transform to the landmark pixels
    # labels each pixel with its nearest seed (a Voronoi map), in C.
    seeds = np.full(m.shape, 255, np.uint8)
    seeds[py, px] = 0
    _, labels = cv2.distanceTransformWithLabels(seeds, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    lut = np.zeros(labels.max() + 1, np.float32)
    lut[labels[py, px]] = z
    zmap = np.where(m, lut[labels], 0).astype(np.float32)
    # Normalised blur keeps the smoothing inside the silhouette.
    mf = m.astype(np.float32)
    zmap = cv2.GaussianBlur(zmap, (0, 0), 6) / np.maximum(cv2.GaussianBlur(mf, (0, 0), 6), 1e-3)
    edge_px = cv2.distanceTransform(m.astype(np.uint8), cv2.DIST_L2, 5)
    edge_mm = np.minimum(edge_px * zmap / fx_mask, relief_mm)
    depth = zmap - np.sqrt(np.maximum(2 * relief_mm * edge_mm - edge_mm ** 2, 0))
    out = np.zeros(full.shape, np.float32)
    out[y0:y1, x0:x1] = np.where(m, depth, 0)
    return out


def to_kinect(depth, Km, mirror, scale=1.0):
    """Resample a mask-resolution depth map into the Kinect depth camera by ray angle.
    scale < 1 shrinks the image about the optical axis: a body moved straight
    back from Z to Z' keeps its lateral metres, so its pixels scale by Z / Z'."""
    mx = Km[0, 2] + _GRID_U * np.float32(Km[0, 0] / (KF * scale))
    my = Km[1, 2] + _GRID_V * np.float32(Km[1, 1] / (KF * scale))
    out = cv2.remap(depth, mx.astype(np.float32), my.astype(np.float32), cv2.INTER_NEAREST,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return np.fliplr(out) if mirror else out


def synth_frame(people, K, Km, mask_scale, args, smooth):
    """Tracked people of one frame -> (640x480 uint16 Kinect depth, per-player stats).
    smooth carries each slot's filtered distance from frame to frame: a fresh PnP
    distance per frame jitters by tens of cm, and the tracker loses a player
    whose depth jumps. Also returns the slots rejected as not a player."""
    kin = np.zeros((KH, KW), np.float32)
    kl = np.zeros_like(kin)
    rec, rejected = [], set()
    for p in people:
        placed = place(p, K)
        if placed is None:
            continue
        cam, vis = placed
        d = player_depth(p, cam, vis, mask_scale, Km[0, 0], args.relief_mm)
        if d is None:
            continue
        raw = float(cam[[23, 24], 2].mean() * 1000)
        if raw > args.max_raw_mm:
            # Nobody plays from there with a laptop webcam; this is a detection
            # of something person-shaped in the room (seen at 7-9 m live).
            rejected.add(p["slot"])
            continue
        feet = [i for i in range(27, 33) if p["visibility"][i] > 0.5]
        if feet:
            # Floor height below the camera (camera y points down), from the
            # lowest visible foot point, filtered slowly: the floor does not move.
            # PnP's y scales with its noisy distance; rescale to the filtered one.
            y = float(cam[feet, 1].max() * 1000) * (smooth.get(p["slot"], raw) / raw) + 30.0
            prev_floor = smooth.get(FLOOR_KEY, args.floor_y_mm)
            smooth[FLOOR_KEY] = prev_floor + 0.05 * (y - prev_floor)
        prev = smooth.get(p["slot"])
        # One wild PnP reading can only move the filtered distance so far.
        step = 0.0 if prev is None else float(np.clip(raw - prev, -args.max_step_mm, args.max_step_mm))
        root = raw if prev is None else prev + args.smooth * step
        smooth[p["slot"]] = root
        # Each player is moved back on their own (push, then kept inside the
        # tracker's range), then z-buffered in Kinect space. The silhouette is
        # scaled by the filtered distance, not this frame's PnP one: scaling by
        # the raw value made the body jump in size and position every frame
        # (placed hip x spanned 6.7 m in a session where the player moved ~1 m).
        target = float(np.clip(root + args.push_mm, *args.clamp_mm))
        d = np.where(d > 0, d + (target - raw), 0).astype(np.float32)
        dk = to_kinect(d, Km, not args.no_mirror, root / target)
        dk, shift = amplify_motion(dk, p["slot"], smooth, args)
        nearer = (dk > 0) & ((kin == 0) | (dk < kin))
        kin[nearer] = dk[nearer]
        kl[nearer] = p["slot"] + 1
        # Body position as placed in the virtual Kinect's space (m, y up): a push
        # moves the body straight back, so x and y stay what the webcam saw.
        hip = cam[[23, 24]].mean(axis=0)
        rec.append({"slot": p["slot"], "root_mm": round(target), "raw_mm": round(raw),
                    "shift_x": shift[0], "shift_y": shift[1],
                    "hip_x": round(float(hip[0]), 3), "hip_y": round(float(-hip[1]), 3),
                    "head_x": round(float(cam[0, 0]), 3), "head_y": round(float(-cam[0, 1]), 3)})
    present = {p["slot"] for p in people}
    for gone in [k for k in smooth if isinstance(k, int) and k not in present]:
        del smooth[gone]
        smooth.pop(f"base{gone}", None)
        smooth.pop(f"rise{gone}", None)
        smooth.pop(f"home{gone}", None)
    for r in rec:
        sel = kl == r["slot"] + 1
        px = kin[sel]
        r["area_m2"] = round(float(((px / 1000.0 / KF) ** 2).sum()), 3)
        r["pixels"] = int(px.size)
        if px.size:
            # Where the silhouette actually sits in the Kinect image, in metres
            # at its distance (y up): what the title's tracker gets to see.
            ys, xs = np.nonzero(sel)
            m_per_px = r["root_mm"] / 1000.0 / KF
            r["sil_x"] = round(float((xs.mean() - (KW - 1) / 2) * m_per_px), 3)
            r["sil_top"] = round(float(((KH - 1) / 2 - ys.min()) * m_per_px), 3)
            r["sil_bottom"] = round(float(((KH - 1) / 2 - ys.max()) * m_per_px), 3)
    background = scene_background(smooth.get(FLOOR_KEY, args.floor_y_mm), args)
    out = np.clip(np.where(kin > 0, kin, background), 0, 65535).astype(np.uint16)
    return out, rec, rejected


def amplify_motion(dk, slot, state, args):
    """Exaggerates a player's sideways position and upward excursions in the
    Kinect image. Kinect Adventures' tracker passed on about 0.43 of a sideways
    move and 0.6-0.7 of a jump that our silhouette made, so leans and jumps
    barely registered. Sideways is scaled about the player's usual spot; up is scaled
    from the player's standing level, learnt slowly from where their feet
    usually are (crouching is left alone). The body is kept inside the frame.
    Returns the shifted image and the (dx, dy) shift in pixels."""
    if args.gain_x == 1.0 and args.gain_y == 1.0:
        return dk, (0, 0)
    ys, xs = np.nonzero(dk > 0)
    if not ys.size:
        return dk, (0, 0)
    # Sideways: the player's usual spot (learnt over ~15 s) goes to the image
    # centre, as if the Kinect were aimed at them, and only moves away from it
    # are amplified. Amplifying about the image centre pushed a player who
    # stood 0.6-1.5 m off the webcam's axis against the frame edge, where the
    # title could not track them.
    cx = float(xs.mean())
    home_key = f"home{slot}"
    home = state.get(home_key, cx)
    state[home_key] = home + 0.002 * (cx - home)
    # Capped: Kinect Adventures pauses when the player leaves its play area, and
    # with gain 2.0 a normal step reached +-1.1 m and paused River Rush.
    m_per_px = state.get(slot, 2500.0) / 1000.0 / KF
    reach = args.max_lateral_mm / 1000.0 / m_per_px
    dx = (KW - 1) / 2 + float(np.clip(args.gain_x * (cx - home), -reach, reach)) - cx
    dx = float(np.clip(dx, -xs.min(), KW - 1 - xs.max()))
    feet = float(ys.max())
    key, count_key = f"base{slot}", f"rise{slot}"
    base = state.get(key, feet)
    rise = base - feet  # px, positive = feet above their standing level
    # Feet also move up the image when the player steps back, so only a short
    # rise is a jump: a jump lands within ~0.5 s. The standing level holds
    # during a rise of up to 15 frames, then catches up quickly.
    frames_up = state.get(count_key, 0) + 1 if rise >= 10 else 0
    state[count_key] = frames_up
    alpha = 0.05 if frames_up == 0 else (0.0 if frames_up <= 15 else 0.3)
    state[key] = base + alpha * (feet - base)
    dy = -(args.gain_y - 1.0) * max(rise, 0.0) if frames_up <= 15 else 0.0
    dy = float(np.clip(dy, -ys.min(), 0.0))
    if abs(dx) < 0.5 and abs(dy) < 0.5:
        return dk, (0, 0)
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    shifted = cv2.warpAffine(dk, m, (KW, KH), flags=cv2.INTER_NEAREST,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return shifted, (round(dx), round(dy))


def scene_background(floor_y_mm, args):
    """What a Kinect sees behind the players: the floor, then a back wall. With
    nothing but the players in its depth the title found no floor plane (it
    logged floor (0, 0, 0, 0)) and kept losing the player. The virtual Kinect
    sits where the webcam is, level, so a floor floor_y_mm below it is at depth
    floor_y * f / (row - centre) on rows below the centre."""
    rows = np.arange(KH, dtype=np.float32) - (KH - 1) / 2
    far = args.wall_mm if args.wall_mm else 0.0
    with np.errstate(divide="ignore"):
        z = np.where(rows > 0, floor_y_mm * KF / np.maximum(rows, 1e-6), np.inf)
    if args.no_floor:
        z = np.full_like(z, np.inf)
    # Beyond the wall (or beyond what the sensor reads, without one) is the wall
    # or no reading.
    z = np.where(z < (far if far else 3999.0), z, far)
    return np.broadcast_to(z[:, None], (KH, KW)).astype(np.float32)


def drop_duplicates(people, overlap_limit=0.5):
    """Two tracks on one body: a detection of part of a tracked player starts a
    second track, which then converges on the same person (seen live with one
    person in view). Overlap is measured against the smaller mask, so a
    part-body track inside a whole-body one counts. Keeps the lower slot.
    Returns (kept, dropped slots)."""
    kept, dropped = [], set()
    for p in sorted(people, key=lambda q: q["slot"]):
        m = p["mask"] > 0.5
        clash = False
        for k in kept:
            km = k["mask"] > 0.5
            smaller = min(np.count_nonzero(m), np.count_nonzero(km))
            if smaller and np.count_nonzero(m & km) / smaller > overlap_limit:
                clash = True
                break
        if clash:
            dropped.add(p["slot"])
        else:
            kept.append(p)
    return kept, dropped


def open_camera(index):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class LatestFrame:
    """Reads the camera on its own thread and keeps only the newest frame. A
    consumer slower than the camera then skips frames instead of falling
    further behind the driver's queue."""

    def __init__(self, cap):
        self.cap = cap
        self.cond = threading.Condition()
        self.frame, self.t_capture, self.seq, self.taken, self.skipped = None, 0.0, 0, 0, 0
        self.stop = False
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop:
            ok, frame = self.cap.read()
            t = time.perf_counter()
            with self.cond:
                if not ok:
                    self.stop = True
                elif self.seq != self.taken:
                    self.skipped += 1
                if ok:
                    self.frame, self.t_capture = frame, t
                    self.seq += 1
                self.cond.notify()

    def next(self, timeout=2.0):
        """The newest frame not yet handed out and its arrival time, or None."""
        with self.cond:
            self.cond.wait_for(lambda: self.seq != self.taken or self.stop, timeout)
            if self.seq == self.taken:
                return None
            self.taken = self.seq
            return self.frame, self.t_capture

    def close(self):
        self.stop = True
        self.thread.join(timeout=2.0)


def connect_pipe(name):
    """Waits for xenia (--nui=pipe --nui_pipe=<name>) to serve the pipe."""
    path = rf"\\.\pipe\{name}"
    print(f"waiting for xenia on {path} ...", flush=True)
    while True:
        try:
            return XdepthWriter(path)
        except OSError:
            time.sleep(1.0)


def run_live(args):
    """Webcam -> synthetic depth -> xenia's depth pipe, until q or Ctrl-C.
    Frames stay on this machine: the pipe only takes local clients."""
    cap = open_camera(args.camera)
    ok, frame = cap.read()
    if not ok:
        raise SystemExit(f"camera {args.camera} gave no frame")
    h, w = frame.shape[:2]
    K, k_src = camera_matrix(args, w, h)
    pipe = GpuPosePipeline(Path(args.models), "lite", args.device)
    mask_scale = pipe.mask_size[0] / w
    Km = K.copy()
    Km[:2] *= mask_scale
    print(f"camera {args.camera} {w}x{h}, intrinsics {k_src}, push {args.push_mm} mm", flush=True)

    # The person detector runs on its own adapter and thread, so it never stalls
    # the frame path, and it only adds people: it does not reset the tracks.
    detector = DetectorWorker(Detector(Path(args.models), args.detector_device, args.min_detect_score))
    wr = connect_pipe(args.pipe)
    print("connected; q in the preview window or Ctrl-C to stop", flush=True)
    grabber = LatestFrame(cap)
    trace = open(args.trace, "w", encoding="utf-8") if args.trace else None
    if trace:
        trace.write("t_s,frame,slot,hip_x,hip_y,head_x,head_y,root_mm,raw_mm,sil_x,sil_top,sil_bottom,"
                    "shift_x,shift_y,held\n")
    i, t_last, synth_ms, age_ms, smooth = 0, time.perf_counter(), [], [], {}
    last_out, held, empty, held_total = None, 0, 0, 0
    try:
        while True:
            got = grabber.next()
            if got is None:
                print("camera stopped delivering frames; stopping", flush=True)
                break
            frame, t_capture = got
            t0 = time.perf_counter()
            # Search hard only while nobody is tracked; otherwise once a second is
            # enough to pick up a second player and keeps the detector's adapter
            # (the one xenia renders on) mostly free.
            if (not pipe.tracks and i % 3 == 0) or i % args.detect_every == 0:
                detector.submit(frame)
            found = detector.take()
            if found is not None:
                pipe.merge_detections(found)
            people, rejected = drop_duplicates(pipe.track(frame))
            out, rec, far = synth_frame(people, K, Km, mask_scale, args, smooth)
            rejected |= far
            if rejected:
                # Free the slot, or the phantom keeps it and a real second
                # player can never be added.
                pipe.tracks = [t for t in pipe.tracks if t[0] not in rejected]
            # Hold-over: when the landmark model loses everyone, the async
            # detector needs a few frames to find them again. An empty frame in
            # between makes the title drop the player, so the last body is
            # repeated for up to --hold-frames frames.
            if rec:
                last_out, held = out, 0
            elif last_out is not None and held < args.hold_frames:
                out, held = last_out, held + 1
                held_total += 1
            else:
                empty += 1
            if trace:
                now_s = time.perf_counter()
                for r in rec or [{"slot": -1}]:
                    trace.write(",".join(str(v) for v in (
                        f"{now_s:.4f}", i, r["slot"], *(r.get(k, "") for k in (
                            "hip_x", "hip_y", "head_x", "head_y", "root_mm", "raw_mm",
                            "sil_x", "sil_top", "sil_bottom", "shift_x", "shift_y")), held)) + "\n")
            synth_ms.append((time.perf_counter() - t0) * 1000)
            try:
                wr.write(out, time.perf_counter_ns() // 1000)
                wr.flush()
            except OSError:
                print("xenia closed the pipe; reconnecting", flush=True)
                wr = connect_pipe(args.pipe)
                continue
            # Arrival of the frame in this process -> depth handed to xenia.
            age_ms.append((time.perf_counter() - t_capture) * 1000)
            i += 1
            if i % 30 == 0:
                now = time.perf_counter()
                players = ", ".join(f"P{r['slot'] + 1} {r['root_mm']} mm (seen {r['raw_mm']})"
                                    for r in rec) or "nobody"
                print(f"{time.strftime('%H:%M:%S')}  {30 / (now - t_last):5.1f} fps  "
                      f"synth p50 {statistics.median(synth_ms):5.1f} ms  "
                      f"age p50 {statistics.median(age_ms):5.1f} ms  skipped {grabber.skipped}  "
                      f"held {held_total}  empty {empty}  {players}", flush=True)
                t_last, synth_ms, age_ms, held_total, empty = now, [], [], 0, 0
            if not args.no_window:
                view = cv2.applyColorMap(cv2.convertScaleAbs(out, alpha=255 / 4000), cv2.COLORMAP_TURBO)
                cv2.imshow("synthetic Kinect depth (q quits)", view)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        grabber.close()
        if trace:
            trace.close()
        detector.close()
        cap.release()
        cv2.destroyAllWindows()
        try:
            wr.close()
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--fixture", help="recorded frames (fixture mode)")
    ap.add_argument("--out", help="summary JSON (fixture mode)")
    ap.add_argument("--xdepth", help="output recording (fixture mode)")
    ap.add_argument("--live", action="store_true", help="webcam -> xenia --nui=pipe instead of a fixture")
    ap.add_argument("--camera", type=int, default=1)
    ap.add_argument("--pipe", default="kinectemu", help="pipe name xenia serves (--nui_pipe)")
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--trace", help="live: per-frame CSV of each player's placed hip and head position")
    ap.add_argument("--hold-frames", type=int, default=10,
                    help="live: repeat the last body this many frames when tracking briefly loses everyone")
    ap.add_argument("--intrinsics", help="calibrate_intrinsics.py output; else --hfov is assumed")
    ap.add_argument("--hfov", type=float, default=70.0)
    ap.add_argument("--relief-mm", type=float, default=100.0)
    ap.add_argument("--wall-mm", type=int, default=3600, help="back wall depth; 0 = no reading behind")
    ap.add_argument("--no-floor", action="store_true", help="leave the floor out of the background")
    ap.add_argument("--floor-y-mm", type=float, default=800.0,
                    help="floor height below the camera until the feet give an estimate")
    ap.add_argument("--push-mm", type=float, default=0.0,
                    help="move every player this far back (depth and silhouette scale)")
    ap.add_argument("--smooth", type=float, default=0.2,
                    help="per-frame weight of a new distance reading (1 = no smoothing)")
    ap.add_argument("--gain-x", type=float, default=1.0,
                    help="exaggerate sideways position about the image centre (title passes on ~0.43)")
    ap.add_argument("--max-lateral-mm", type=float, default=500.0,
                    help="cap on the amplified sideways offset from the player's usual spot")
    ap.add_argument("--gain-y", type=float, default=1.0,
                    help="exaggerate upward moves such as jumps above standing level (title passes on ~0.6-0.7)")
    ap.add_argument("--max-step-mm", type=float, default=150.0,
                    help="largest change one frame's distance reading may feed into the filter")
    ap.add_argument("--clamp-mm", type=lambda s: tuple(float(v) for v in s.split(",")),
                    default=(1900.0, 2900.0), help="keep players' distance within lo,hi (mm)")
    ap.add_argument("--max-raw-mm", type=float, default=5000.0,
                    help="ignore people whose estimated distance is beyond this (false detections)")
    ap.add_argument("--min-detect-score", type=float, default=0.5,
                    help="live: person detector confidence needed to start tracking someone")
    ap.add_argument("--no-mirror", action="store_true")
    # Landmarks on the iGPU (10 ms p50) leave the RTX 4050 to xenia: sharing it
    # dropped the title's tracking to 16-28 Hz in a live session.
    ap.add_argument("--device", type=int, default=0, help="DML adapter for the landmark model")
    ap.add_argument("--detector-device", type=int, default=1,
                    help="DML adapter for the live async person detector (two threads on one adapter crash)")
    ap.add_argument("--detect-every", type=int, default=30,
                    help="live: run the person detector every N frames while someone is tracked")
    ap.add_argument("--preview", help="write a colour-mapped depth strip of a few frames here")
    args = ap.parse_args()
    ort.set_default_logger_severity(3)
    if args.live:
        run_live(args)
        return
    if not (args.fixture and args.out and args.xdepth):
        ap.error("fixture mode needs --fixture, --out and --xdepth (or use --live)")

    paths = sorted(Path(args.fixture).glob("[0-9]*.jpg"))
    h, w = cv2.imread(str(paths[0])).shape[:2]
    K, k_src = camera_matrix(args, w, h)
    pipe = GpuPosePipeline(Path(args.models), "lite", args.device)
    mask_scale = pipe.mask_size[0] / w
    Km = K.copy()
    Km[:2] *= mask_scale

    wr = XdepthWriter(args.xdepth)
    per_frame, previews, smooth = [], [], {}
    for i, path in enumerate(paths):
        people = pipe.process(cv2.imread(str(path)), i % 10 == 0)
        out, rec, _ = synth_frame(people, K, Km, mask_scale, args, smooth)
        wr.write(out, i * 33333)
        per_frame.append(rec)
        if args.preview and i in (30, 120, 210):
            previews.append(cv2.applyColorMap(cv2.convertScaleAbs(out, alpha=255 / 4000), cv2.COLORMAP_TURBO))
    wr.close()

    players = [r for f in per_frame for r in f]
    def within(key):
        lo, hi = LIMITS[key]
        return round(sum(lo <= r[key] <= hi for r in players) / max(len(players), 1), 3)
    summary = {
        "fixture": str(args.fixture), "frames": len(paths), "xdepth": str(args.xdepth),
        "intrinsics": k_src, "fx_px": round(float(K[0, 0]), 1), "relief_mm": args.relief_mm,
        "push_mm": args.push_mm,
        "mirror": not args.no_mirror, "wall_mm": args.wall_mm,
        "frames_with_players": sum(bool(f) for f in per_frame),
        "player_frames": len(players),
        "root_mm": {"min": min((r["root_mm"] for r in players), default=None),
                    "max": max((r["root_mm"] for r in players), default=None)},
        "area_m2": {"min": min((r["area_m2"] for r in players), default=None),
                    "max": max((r["area_m2"] for r in players), default=None)},
        "fraction_within_limits": {k: within(k) for k in LIMITS},
        "limits": LIMITS,
        "per_frame": per_frame,
    }
    Path(args.out).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    if previews:
        cv2.imwrite(args.preview, np.hstack(previews))
    print(json.dumps({k: v for k, v in summary.items() if k != "per_frame"}, indent=1))


if __name__ == "__main__":
    main()
