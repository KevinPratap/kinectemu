"""Phase 0-W full-body test: live framing countdown, short recording, pose benchmark.

Shows a preview window so the player can step back, records N seconds
(with an on-screen REC indicator), saves the frames as a replay fixture,
then runs MediaPipe Pose (3D + masks) and RTMO-s on the recorded frames.

Usage:
  python fullbody_test.py --models ../../models --fixture ../../fixtures/fullbody_01 \
      --out ../../docs/data/fullbody_test.json [--countdown 15] [--seconds 10]
"""

import argparse
import json
import statistics
import time
from pathlib import Path

import cv2
import numpy as np

from pose_bench import rtmo_run, rtmo_session, stats

WINDOW = "KinectEmu Phase 0 - full-body test"
ANKLES = (27, 28)  # MediaPipe landmark indices
MP_EDGES = [(11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24),
            (23, 25), (25, 27), (24, 26), (26, 28), (27, 31), (28, 32), (0, 11), (0, 12)]


def open_camera(index):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)
    return cap


def banner(frame, lines, color):
    out = frame.copy()
    cv2.rectangle(out, (0, 0), (out.shape[1], 40 + 40 * len(lines)), (0, 0, 0), -1)
    for i, text in enumerate(lines):
        cv2.putText(out, text, (20, 45 + 40 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
    return out


def countdown_and_record(cap, countdown, seconds):
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, 960, 540)
    cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
    end = time.perf_counter() + countdown
    while True:
        left = end - time.perf_counter()
        if left <= 0:
            break
        ok, frame = cap.read()
        if ok:
            cv2.imshow(WINDOW, banner(cv2.flip(frame, 1), [
                f"Step back 2-3 m: whole body, head to feet, in view. Recording in {int(left) + 1}s",
                "Then move around: arms up, step side to side, a little dance."], (0, 255, 255)))
        cv2.waitKey(1)
    frames, stamps = [], []
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        ok, frame = cap.read()
        if not ok:
            continue
        stamps.append(time.perf_counter())
        frames.append(frame)
        shown = banner(cv2.flip(frame, 1), [f"REC  {end - time.perf_counter():4.1f}s  - keep moving"], (0, 0, 255))
        cv2.circle(shown, (shown.shape[1] - 40, 30), 14, (0, 0, 255), -1)
        cv2.imshow(WINDOW, shown)
        cv2.waitKey(1)
    cv2.imshow(WINDOW, banner(np.zeros((540, 960, 3), np.uint8), ["Done - analysing. You can come back."], (0, 255, 0)))
    cv2.waitKey(1500)
    cv2.destroyAllWindows()
    return frames, stamps


def save_fixture(folder, frames, stamps):
    folder.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(frames):
        cv2.imwrite(str(folder / f"{i:05d}.jpg"), f, [cv2.IMWRITE_JPEG_QUALITY, 92])
    t0 = stamps[0]
    (folder / "timestamps.json").write_text(json.dumps(
        {"source": "HP Wide Vision HD Camera", "size": "1280x720", "clock": "host perf_counter",
         "t_ms": [round((t - t0) * 1000.0, 3) for t in stamps]}))


def run_mediapipe(task_path, frames, overlay_dir=None):
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python import vision

    opts = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(task_path)),
        running_mode=vision.RunningMode.VIDEO, num_poses=2, output_segmentation_masks=True)
    times, persons, full_body, masks, heights = [], [], 0, 0, []
    with vision.PoseLandmarker.create_from_options(opts) as lm:
        for i, f in enumerate(frames):
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
            t0 = time.perf_counter()
            res = lm.detect_for_video(image, i * 33 + 33)
            times.append((time.perf_counter() - t0) * 1000.0)
            persons.append(len(res.pose_landmarks))
            if res.segmentation_masks:
                masks += 1
            for p2d, p3d in zip(res.pose_landmarks, res.pose_world_landmarks):
                if all(p2d[a].visibility > 0.5 for a in ANKLES):
                    full_body += 1
                    # World landmarks are metres, hip-centred: head-to-ankle span ~ body height.
                    heights.append(max(p.y for p in p3d) - min(p.y for p in p3d))
            if overlay_dir is not None and i in (len(frames) // 4, len(frames) // 2, 3 * len(frames) // 4):
                draw = f.copy()
                if res.segmentation_masks:
                    m = res.segmentation_masks[0].numpy_view()[..., 0] > 0.5
                    draw[m] = (0.5 * draw[m] + (0, 90, 0)).astype(np.uint8)
                for pose in res.pose_landmarks:
                    pts = [(int(p.x * f.shape[1]), int(p.y * f.shape[0])) for p in pose]
                    for a, b in MP_EDGES:
                        cv2.line(draw, pts[a], pts[b], (0, 255, 255), 3)
                cv2.imwrite(str(overlay_dir / f"overlay_{i:05d}.jpg"), draw)
    r = stats(times, persons)
    r.update({"frames_with_mask": masks, "full_body_detections": full_body,
              "landmark_span_m_median": round(statistics.median(heights), 3) if heights else None})
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--camera", type=int, default=1)
    ap.add_argument("--countdown", type=float, default=15.0)
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args()
    models, fixture = Path(args.models), Path(args.fixture)

    cap = open_camera(args.camera)
    for _ in range(30):
        cap.read()
    frames, stamps = countdown_and_record(cap, args.countdown, args.seconds)
    cap.release()
    save_fixture(fixture, frames, stamps)
    fps = (len(stamps) - 1) / (stamps[-1] - stamps[0])
    print(f"recorded {len(frames)} frames at {fps:.2f} fps -> {fixture}", flush=True)

    results = []
    for name in ("mp-pose-lite", "mp-pose-full", "mp-pose-heavy"):
        r = run_mediapipe(next((models / name).glob("*.task")), frames,
                          overlay_dir=fixture if name == "mp-pose-full" else None)
        r.update({"model": name, "device": "cpu (mediapipe tflite)"})
        print(json.dumps(r), flush=True)
        results.append(r)

    session = rtmo_session(models / "rtmo-s" / "end2end.onnx", 1)
    for f in frames[:30]:
        rtmo_run(session, f)
    times, persons = [], []
    for f in frames:
        t0 = time.perf_counter()
        persons.append(rtmo_run(session, f))
        times.append((time.perf_counter() - t0) * 1000.0)
    r = stats(times, persons)
    r.update({"model": "rtmo-s", "device": "RTX 4050 (DML device 1)"})
    print(json.dumps(r), flush=True)
    results.append(r)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"frames": len(frames), "capture_fps": round(fps, 2), "frame_size": "1280x720",
                   "fixture": str(fixture), "results": results}, fh, indent=2)


if __name__ == "__main__":
    main()
