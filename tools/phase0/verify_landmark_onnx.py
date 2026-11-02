"""Check converted MediaPipe landmark ONNX models against the original TFLite.

Two steps, because TFLite lives in the converter venv and DirectML in the main venv:
  tools/.venv-convert:  python verify_landmark_onnx.py tflite --fixture ../../fixtures/fullbody_01 --models ../../models --out ref.npz
  tools/.venv:          python verify_landmark_onnx.py onnx --models ../../models --ref ref.npz
"""

import argparse
import sys
from pathlib import Path

import numpy as np

OUTPUTS = ["landmarks", "pose_flag", "segmentation", "heatmap", "world_landmarks"]
# Square crops (x0, y0, size) around each person in fixture frames 59/119/179.
CROPS = [(59, 590, 120, 600), (119, 570, 130, 600), (179, 620, 150, 570),
         (119, 160, 300, 420), (179, 180, 300, 420)]


def load_crops(fixture):
    import cv2
    batch = []
    for idx, x0, y0, size in CROPS:
        img = cv2.imread(str(Path(fixture) / f"{idx:05d}.jpg"))
        img = cv2.copyMakeBorder(img, 0, size, 0, size, cv2.BORDER_CONSTANT)
        crop = cv2.resize(img[y0:y0 + size, x0:x0 + size], (256, 256))
        batch.append(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0)
    return np.stack(batch)


def run_tflite(args):
    import tensorflow as tf
    crops = load_crops(args.fixture)
    ref = {"input": crops}
    for v in ("lite", "full"):
        interp = tf.lite.Interpreter(model_path=str(Path(args.models) / f"mp-pose-{v}/extracted/pose_landmarks_detector.tflite"))
        interp.allocate_tensors()
        inp = interp.get_input_details()[0]["index"]
        outs = sorted(interp.get_output_details(), key=lambda d: d["name"])
        per = {k: [] for k in OUTPUTS}
        for c in crops:
            interp.set_tensor(inp, c[None])
            interp.invoke()
            by_size = {int(np.prod(d["shape"])): interp.get_tensor(d["index"]) for d in outs}
            for k, size in zip(OUTPUTS, (195, 1, 256 * 256, 64 * 64 * 39, 117)):
                per[k].append(by_size[size][0])
        for k in OUTPUTS:
            ref[f"{v}_{k}"] = np.stack(per[k])
    np.savez(args.out, **ref)
    print("saved", args.out, "pose_flag lite:", ref["lite_pose_flag"].ravel().round(3))


def run_onnx(args):
    import onnxruntime as ort
    ort.set_default_logger_severity(3)
    ref = np.load(args.ref)
    crops = ref["input"]
    ok = True
    for v in ("lite", "full"):
        path = str(Path(args.models) / f"mp-pose-{v}/pose_landmarks_{v}.onnx")
        for dev in ("cpu", 1):
            so = ort.SessionOptions()
            if dev == "cpu":
                providers = ["CPUExecutionProvider"]
            else:
                so.enable_mem_pattern = False
                so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                providers = [("DmlExecutionProvider", {"device_id": dev})]
            s = ort.InferenceSession(path, so, providers=providers)
            names = [o.name for o in s.get_outputs()]  # Identity .. Identity_4, same order as OUTPUTS
            res = {k: [] for k in OUTPUTS}
            for c in crops:
                for k, val in zip(OUTPUTS, s.run(names, {"input_1": c[None]})):
                    res[k].append(val[0])
            # Landmarks are in 256-px crop units; world landmarks in metres; flag/seg are logits.
            lm = np.abs(np.stack(res["landmarks"]).reshape(-1, 39, 5)[..., :3] - ref[f"{v}_landmarks"].reshape(-1, 39, 5)[..., :3]).max()
            wl = np.abs(np.stack(res["world_landmarks"]) - ref[f"{v}_world_landmarks"]).max()
            fl = np.abs(np.stack(res["pose_flag"]) - ref[f"{v}_pose_flag"]).max()
            sg = np.abs(np.stack(res["segmentation"]) - ref[f"{v}_segmentation"]).mean()
            line_ok = lm < 1.0 and wl < 0.01 and fl < 0.05
            ok &= line_ok
            label = "cpu" if dev == "cpu" else "dml:4050"
            print(f"{v:4s} {label:8s} max|d landmark xyz| {lm:.4f} px  max|d world| {wl * 1000:.2f} mm  "
                  f"max|d flag| {fl:.4f}  mean|d seg logit| {sg:.4f}  {'OK' if line_ok else 'MISMATCH'}")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["tflite", "onnx"])
    ap.add_argument("--fixture")
    ap.add_argument("--models", required=True)
    ap.add_argument("--out")
    ap.add_argument("--ref")
    args = ap.parse_args()
    run_tflite(args) if args.mode == "tflite" else run_onnx(args)


if __name__ == "__main__":
    main()
