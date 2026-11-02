"""Phase 0-W GPU pose pipeline prototype: RTMO-s person detection + MediaPipe
landmarks (3D world joints + segmentation) per person crop, all on DirectML.

Modes:
  detect-every-frame  RTMO every frame, landmarks for up to 2 people
  track               RTMO only every --detect-interval frames or when a track
                      is lost; otherwise the crop comes from last frame's landmarks

Usage:
  python gpu_pose_pipeline.py --models ../../models --fixture ../../fixtures/fullbody_01 \
      --out ../../docs/data/gpu_pipeline.json [--variant lite] [--device 1]
"""

import argparse
import json
import math
import statistics
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from pose_bench import rtmo_session, stats

MAX_PLAYERS = 2
COCO_SH, COCO_HIP = (5, 6), (11, 12)
MP_SH, MP_HIP = (11, 12), (23, 24)
MP_EDGES = [(11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24),
            (23, 25), (25, 27), (24, 26), (26, 28), (27, 31), (28, 32), (0, 11), (0, 12)]
PLAYER_COLORS = [(0, 200, 0), (200, 120, 0)]


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60.0, 60.0)))


def roi_from_aux(xy):
    """MediaPipe's own tracking ROI: auxiliary landmark 33 is the hip centre and
    34 a point above the head; the crop is a square of side 2.5 * |34 - 33|,
    rotated so 33->34 points up (AlignmentPointsRectsCalculator + scale 1.25)."""
    center, top = xy[33], xy[34]
    v = top - center
    angle = math.atan2(v[1], v[0]) + math.pi / 2
    return center, max(float(np.hypot(*v)) * 1.25, 32.0), angle


def dml_session(path, device):
    so = ort.SessionOptions()
    so.enable_mem_pattern = False
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(str(path), so, providers=[("DmlExecutionProvider", {"device_id": device})])


def roi_from_points(points, scores, fallback_box, sh_idx, hip_idx, thresh=0.3):
    """Square, rotated ROI centred on the hips with 'up' along hips->shoulders,
    the alignment MediaPipe's landmark model was trained on."""
    x1, y1, x2, y2 = fallback_box
    sh_ok = all(scores[i] > thresh for i in sh_idx)
    hip_ok = all(scores[i] > thresh for i in hip_idx)
    center = points[list(hip_idx)].mean(0) if hip_ok else np.array([(x1 + x2) / 2, (y1 + y2) / 2])
    angle = 0.0
    if sh_ok and hip_ok:
        v = points[list(sh_idx)].mean(0) - points[list(hip_idx)].mean(0)
        angle = math.atan2(v[1], v[0]) + math.pi / 2  # rotate so hips->shoulders points up
    corners = np.array([[x1, y1], [x2, y1], [x1, y2], [x2, y2]], np.float32)
    half = np.abs(corners - center).max() * 1.15
    return center, max(half, 32.0), angle


def roi_affine(center, half, angle, size=256):
    """2x3 matrix mapping image pixels into the size x size crop."""
    c, s = math.cos(angle), math.sin(angle)
    src = np.float32([center + half * np.array([-c + s, -s - c]),
                      center + half * np.array([c + s, s - c]),
                      center + half * np.array([-c - s, -s + c])])
    dst = np.float32([[0, 0], [size, 0], [0, size]])
    return cv2.getAffineTransform(src, dst)


class Detector:
    """RTMO-s person detector; returns up to MAX_PLAYERS crop ROIs."""

    def __init__(self, models, device):
        self.rtmo = rtmo_session(models / "rtmo-s" / "end2end.onnx", device)

    def __call__(self, frame, size=640):
        h, w = frame.shape[:2]
        ratio = min(size / h, size / w)
        resized = cv2.resize(frame, (int(w * ratio), int(h * ratio)))
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        canvas[: resized.shape[0], : resized.shape[1]] = resized
        dets, kpts = self.rtmo.run(None, {"input": canvas.transpose(2, 0, 1)[None].astype(np.float32)})
        order = np.argsort(-dets[0, :, 4])
        rois = []
        for i in order[:MAX_PLAYERS]:
            if dets[0, i, 4] < 0.3:
                break
            box = dets[0, i, :4] / ratio
            pts = kpts[0, i, :, :2] / ratio
            rois.append(roi_from_points(pts, kpts[0, i, :, 2], box, COCO_SH, COCO_HIP))
        return rois


class GpuPosePipeline:
    def __init__(self, models, variant, device, mask_size=(640, 360)):
        self.detect = Detector(models, device)
        self.lm = dml_session(models / f"mp-pose-{variant}" / f"pose_landmarks_{variant}.onnx", device)
        self.lm_out = [o.name for o in self.lm.get_outputs()]
        self.mask_size = mask_size
        self.tracks = []  # list of (slot, (center, half, angle)); slot = Kinect player index - 1

    def landmarks(self, frame, roi):
        m = roi_affine(*roi)
        crop = cv2.warpAffine(frame, m, (256, 256), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
        inp = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32)[None] / 255.0
        lm, flag, seg, _heat, world = self.lm.run(self.lm_out, {"input_1": inp})
        lm = lm[0].reshape(39, 5)
        inv = cv2.invertAffineTransform(m)
        img_xy = (np.c_[lm[:, :2], np.ones(39)] @ inv.T)
        # Mask back to the (downscaled) frame for the player-index map.
        sx = self.mask_size[0] / frame.shape[1]
        inv_small = np.vstack([inv[0] * sx, inv[1] * sx])
        mask = cv2.warpAffine(sigmoid(seg[0, :, :, 0]).astype(np.float32), inv_small, self.mask_size)
        return {"present": float(sigmoid(flag[0, 0])), "xy": img_xy, "visibility": sigmoid(lm[:, 3]),
                "world": world[0].reshape(39, 3), "mask": mask}

    def merge_detections(self, rois):
        """Give detected people that no current track covers the lowest free slot.
        A tracked player keeps its slot for as long as its track survives."""
        for roi in rois:
            used = {s for s, _ in self.tracks}
            free = [s for s in range(MAX_PLAYERS) if s not in used]
            if not free:
                break
            if all(np.hypot(*(roi[0] - t[0])) > 0.5 * t[1] for _, t in self.tracks):
                self.tracks.append((free[0], roi))

    def process(self, frame, run_detector):
        if run_detector or not self.tracks:
            self.tracks = []
            self.merge_detections(self.detect(frame))
        return self.track(frame)

    def track(self, frame):
        people, next_tracks = [], []
        for slot, roi in self.tracks:
            p = self.landmarks(frame, roi)
            if p["present"] < 0.5:
                continue
            p["slot"] = slot
            people.append(p)
            next_tracks.append((slot, roi_from_aux(p["xy"])))
        self.tracks = next_tracks
        return people


class DetectorWorker:
    """Runs RTMO on its own thread on the frames it is handed; drops a frame if
    still busy (never blocks the per-frame tracking path)."""

    def __init__(self, detector):
        import threading
        self.detector = detector
        self.lock = threading.Lock()
        self.pending, self.result, self.busy_drops = None, None, 0
        self.wake = threading.Event()
        self.stop = False
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def submit(self, frame):
        with self.lock:
            if self.pending is not None:
                self.busy_drops += 1
            self.pending = frame
        self.wake.set()

    def take(self):
        with self.lock:
            r, self.result = self.result, None
        return r

    def loop(self):
        while not self.stop:
            self.wake.wait()
            self.wake.clear()
            with self.lock:
                frame, self.pending = self.pending, None
            if frame is not None:
                rois = self.detector(frame)
                with self.lock:
                    self.result = rois

    def close(self):
        self.stop = True
        self.wake.set()
        self.thread.join()


def draw(frame, people, path):
    out = frame.copy()
    for p in people:
        m = cv2.resize(p["mask"], (frame.shape[1], frame.shape[0])) > 0.5
        out[m] = (0.5 * out[m] + 0.5 * np.array(PLAYER_COLORS[p["slot"]])).astype(np.uint8)
        pts = [tuple(int(v) for v in xy) for xy in p["xy"]]
        for a, b in MP_EDGES:
            cv2.line(out, pts[a], pts[b], (0, 255, 255), 3)
    cv2.imwrite(str(path), out)


def bench(pipe, frames, run_detector_fn, overlay_dir=None, tag=""):
    for i, f in enumerate(frames[:30]):  # warm-up, also brings GPU clocks up
        pipe.process(f, run_detector_fn(i))
    pipe.tracks = []
    times, persons, detector_runs = [], [], 0
    for i, f in enumerate(frames):
        run_det = run_detector_fn(i) or len(pipe.tracks) < MAX_PLAYERS and i % 5 == 0
        detector_runs += bool(run_det or not pipe.tracks)
        t0 = time.perf_counter()
        people = pipe.process(f, run_det)
        times.append((time.perf_counter() - t0) * 1000.0)
        persons.append(len(people))
        if overlay_dir is not None and i in (59, 119, 179):
            draw(f, people, overlay_dir / f"gpu_{tag}_{i:05d}.jpg")
    r = stats(times, persons)
    r["detector_runs"] = detector_runs
    return r


def bench_async(pipe, detector, frames, interval, frame_period_s=1 / 30, overlay_dir=None, tag=""):
    """Real-time pacing: one frame per 33 ms, detector on its own thread every
    `interval` frames; only the tracking path is timed (that is the frame cost)."""
    worker = DetectorWorker(detector)
    for f in frames[:30]:
        pipe.track(f)
    pipe.tracks = []
    times, persons, slips, slot_log = [], [], 0, []
    next_t = time.perf_counter()
    for i, f in enumerate(frames):
        if i % interval == 0 or len(pipe.tracks) < MAX_PLAYERS and i % 3 == 0:
            worker.submit(f)
        t0 = time.perf_counter()
        det = worker.take()
        if det is not None:
            pipe.merge_detections(det)
        people = pipe.track(f)
        dt = time.perf_counter() - t0
        times.append(dt * 1000.0)
        persons.append(len(people))
        # Hip-centre x per slot, to check a slot keeps following the same person.
        slot_log.append({p["slot"]: round(float(p["xy"][33][0]), 1) for p in people})
        if overlay_dir is not None and i in (59, 119, 179):
            draw(f, people, overlay_dir / f"gpu_{tag}_{i:05d}.jpg")
        next_t += frame_period_s
        sleep = next_t - time.perf_counter()
        if sleep > 0:
            time.sleep(sleep)
        else:
            slips += 1
    worker.close()
    r = stats(times, persons)
    r.update({"frames_over_budget": slips, "detector_busy_drops": worker.busy_drops})
    r.update(slot_stability(slot_log))
    return r


def slot_stability(slot_log, jump_px=150.0):
    """A slot 'switches person' when its hip x jumps more than jump_px between
    consecutive frames it is present in (players here are ~400 px apart)."""
    first_seen, switches, dropouts, last_x = {}, 0, 0, {}
    for i, frame_slots in enumerate(slot_log):
        for slot, x in frame_slots.items():
            first_seen.setdefault(slot, i)
            if slot in last_x and abs(x - last_x[slot]) > jump_px:
                switches += 1
            last_x[slot] = x
        if i > 0:
            dropouts += len(set(slot_log[i - 1]) - set(frame_slots))
    return {"slot_first_frame": first_seen, "slot_person_switches": switches,
            "slot_dropouts": dropouts,
            "slot_mean_hip_x": {s: round(statistics.mean(f[s] for f in slot_log if s in f), 1)
                                for s in first_seen}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--variant", default="lite", choices=["lite", "full"])
    ap.add_argument("--device", type=int, default=1)
    ap.add_argument("--detect-interval", type=int, default=10)
    ap.add_argument("--detector-device", type=int, default=0, help="DML adapter for the async detector")
    args = ap.parse_args()
    ort.set_default_logger_severity(3)
    fixture = Path(args.fixture)
    frames = [cv2.imread(str(p)) for p in sorted(fixture.glob("[0-9]*.jpg"))]

    # The async detector gets its own adapter: two threads submitting DirectML
    # work to one adapter crashed the process (exit 5, no traceback).
    async_detector = Detector(Path(args.models), args.detector_device)
    results = []
    for variant in ("lite", "full"):
        pipe = GpuPosePipeline(Path(args.models), variant, args.device)
        for mode, fn in (("detect-every-frame", lambda i: True),
                         ("track", lambda i: i % args.detect_interval == 0)):
            r = bench(pipe, frames, fn, overlay_dir=fixture, tag=f"{variant}_{mode}")
            r.update({"variant": variant, "mode": mode, "device": args.device})
            print(json.dumps(r), flush=True)
            results.append(r)
        r = bench_async(pipe, async_detector, frames, args.detect_interval, overlay_dir=fixture,
                        tag=f"{variant}_async")
        r.update({"variant": variant, "mode": "async-detector, 30 Hz paced", "device": args.device,
                  "detector_device": args.detector_device})
        print(json.dumps(r), flush=True)
        results.append(r)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"frames": len(frames), "fixture": str(fixture), "results": results}, fh, indent=2)


if __name__ == "__main__":
    main()
