"""Phase 0-W inference benchmark: pose models on each DirectML adapter and CPU.

Captures real frames from the webcam once, then times every model/device
combination on the same frames (pre- and post-processing included).

Usage:
  python pose_bench.py --models ../../models --out ../../docs/data/pose_bench.json
"""

import argparse
import ctypes
import json
import statistics
import time
from ctypes import wintypes
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort


# ---- DXGI adapter enumeration (ORT DirectML device_id follows this order) ----

class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [
        ("Description", wintypes.WCHAR * 128),
        ("VendorId", wintypes.UINT),
        ("DeviceId", wintypes.UINT),
        ("SubSysId", wintypes.UINT),
        ("Revision", wintypes.UINT),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", LUID),
        ("Flags", wintypes.UINT),
    ]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


IID_IDXGIFactory1 = GUID(0x770AAE78, 0xF26F, 0x4DBA,
                         (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))


def _vcall(obj, index, restype, *argtypes):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])


def list_adapters():
    factory = ctypes.c_void_p()
    hr = ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(IID_IDXGIFactory1), ctypes.byref(factory))
    if hr != 0:
        return []
    adapters = []
    i = 0
    while True:
        adapter = ctypes.c_void_p()
        # IDXGIFactory1::EnumAdapters1 is vtable slot 12.
        hr = _vcall(factory, 12, ctypes.c_long, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(
            factory, i, ctypes.byref(adapter))
        if hr != 0:
            break
        desc = DXGI_ADAPTER_DESC1()
        # IDXGIAdapter1::GetDesc1 is vtable slot 10.
        _vcall(adapter, 10, ctypes.c_long, ctypes.POINTER(DXGI_ADAPTER_DESC1))(adapter, ctypes.byref(desc))
        adapters.append({"device_id": i, "name": desc.Description,
                         "vram_mb": desc.DedicatedVideoMemory // (1 << 20),
                         "software": bool(desc.Flags & 2)})
        _vcall(adapter, 2, ctypes.c_ulong)(adapter)  # Release
        i += 1
    _vcall(factory, 2, ctypes.c_ulong)(factory)
    return adapters


# ---- Frame capture ----

def capture_frames(index, count):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)
    for _ in range(45):  # let auto exposure settle
        cap.read()
    frames = []
    while len(frames) < count:
        ok, frame = cap.read()
        if ok:
            frames.append(frame)
    cap.release()
    return frames


def stats(times_ms, persons):
    s = sorted(times_ms)
    return {"n": len(s), "p50_ms": round(statistics.median(s), 2),
            "p95_ms": round(s[int(len(s) * 0.95)], 2), "max_ms": round(s[-1], 2),
            "mean_persons": round(statistics.mean(persons), 2)}


# ---- RTMO (one-stage multi-person 2D pose, ONNX) ----

def rtmo_session(path, device):
    so = ort.SessionOptions()
    if device == "cpu":
        providers = ["CPUExecutionProvider"]
    else:
        so.enable_mem_pattern = False
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        providers = [("DmlExecutionProvider", {"device_id": device})]
    return ort.InferenceSession(str(path), so, providers=providers)


def rtmo_run(session, frame, size=640):
    h, w = frame.shape[:2]
    ratio = min(size / h, size / w)
    resized = cv2.resize(frame, (int(w * ratio), int(h * ratio)))
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[: resized.shape[0], : resized.shape[1]] = resized
    blob = canvas.transpose(2, 0, 1)[None].astype(np.float32)
    dets, kpts = session.run(None, {session.get_inputs()[0].name: blob})
    keep = dets[0, :, 4] > 0.3
    return int(keep.sum())


def bench_rtmo(path, device, frames, warmup=10):
    session = rtmo_session(path, device)
    for f in frames[:warmup]:
        rtmo_run(session, f)
    times, persons = [], []
    for f in frames:
        t0 = time.perf_counter()
        persons.append(rtmo_run(session, f))
        times.append((time.perf_counter() - t0) * 1000.0)
    return stats(times, persons)


# ---- MediaPipe Pose Landmarker (3D world landmarks + masks; CPU on Windows) ----

def bench_mediapipe(path, frames, warmup=10):
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python import vision

    opts = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(path)),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=2,
        output_segmentation_masks=True,
    )
    times, persons = [], []
    with vision.PoseLandmarker.create_from_options(opts) as landmarker:
        ts = 0
        for i, f in enumerate(frames[:warmup] + frames):
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
            ts += 33
            t0 = time.perf_counter()
            result = landmarker.detect_for_video(image, ts)
            dt = (time.perf_counter() - t0) * 1000.0
            if i >= warmup:
                times.append(dt)
                persons.append(len(result.pose_world_landmarks))
    return stats(times, persons)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--camera", type=int, default=1)
    ap.add_argument("--frames", type=int, default=150)
    args = ap.parse_args()
    models = Path(args.models)

    adapters = list_adapters()
    print("adapters:", json.dumps(adapters), flush=True)
    frames = capture_frames(args.camera, args.frames)
    print(f"captured {len(frames)} frames", flush=True)

    devices = ["cpu"] + [a["device_id"] for a in adapters if not a["software"]]
    results = []
    for name in ("rtmo-s", "rtmo-m"):
        for device in devices:
            label = "cpu" if device == "cpu" else adapters[device]["name"]
            try:
                r = bench_rtmo(models / name / "end2end.onnx", device, frames)
            except Exception as exc:
                r = {"error": repr(exc)}
            r.update({"model": name, "device": label})
            print(json.dumps(r), flush=True)
            results.append(r)
    for name in ("mp-pose-lite", "mp-pose-full", "mp-pose-heavy"):
        task = next((models / name).glob("*.task"))
        try:
            r = bench_mediapipe(task, frames)
        except Exception as exc:
            r = {"error": repr(exc)}
        r.update({"model": name, "device": "cpu (mediapipe tflite)"})
        print(json.dumps(r), flush=True)
        results.append(r)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"adapters": adapters, "frames": len(frames), "frame_size": "1280x720",
                   "results": results}, fh, indent=2)


if __name__ == "__main__":
    main()
