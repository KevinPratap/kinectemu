# STATUS

Last update: 2026-11-02. Current phase: **0 (feasibility)**. No feature code exists yet.

## Works on real hardware
- HP Wide Vision HD delivers 30 fps at 640×480 and 1280×720 under auto exposure in normal room light (`docs/data/webcam_probe.json`).
- RTMO-s 2D multi-person pose runs on the RTX 4050 via ONNX Runtime DirectML in about 20 ms steady state, and detects the user (`docs/data/pose_bench.json`).
- Live two-person test: MediaPipe Pose lite (CPU) tracks both people with 3D world landmarks and masks at p50 27.8 / p95 35.4 ms. RTMO-s on the 4050 finds both at p50 27.7 / p95 34.1 ms. Overlays were checked by eye (`docs/data/fullbody_test.json`, fixture `fixtures/fullbody_01`).
- All-GPU pose pipeline (MediaPipe landmarks converted to ONNX, verified against TFLite to 0.004 px): 2 people with 3D joints and separate player masks, p50 14.9 / p95 21.2 ms at a 30 Hz pace. Tracker on the RTX 4050, RTMO detector async on the Intel iGPU (`docs/data/gpu_pipeline.json`, WEBCAM_REPORT §6).
- In the same test the camera dropped to 23.92 fps under auto exposure (evening light, subject at the back of the room). 30 Hz needs more light on the player.

## Mocked / faked / stubbed
- Nothing yet. Only Phase 0 measurement scripts exist (`tools/phase0/`).

## Gate status
| Gate | State | Blocker |
|---|---|---|
| 0-W webcam | partial | GPU 2-person path done. Still needs: a well-lit full-body re-run, checkerboard intrinsics, a latency test, and a player-slot stability log. See WEBCAM_REPORT §7. |
| 0-N NUI layer | not started | Needs the target titles' XEX files (from images of discs you own) and a Xenia checkout. |
| 0-D disc | not applicable yet | The laptop has no optical drive (`Win32_CDROMDrive` is empty). Needs an external Xbox-compatible SATA drive. |
| Build toolchain | **blocked** | Visual Studio 2022 with the "Desktop development with C++" workload is not installed; only the VS Installer is present. clang-format is also missing. Needed before Phase 1. |

## Reproduce the last runs
```
cd tools\phase0
..\.venv\Scripts\python.exe webcam_probe.py --out ..\..\docs\data\webcam_probe.json --seconds 15
..\.venv\Scripts\python.exe exposure_sweep.py --index 1 --out ..\..\docs\data\exposure_sweep.json --seconds 5
..\.venv\Scripts\python.exe pose_bench.py --models ..\..\models --out ..\..\docs\data\pose_bench.json
..\.venv\Scripts\python.exe fullbody_test.py --models ..\..\models --fixture ..\..\fixtures\fullbody_01 --out ..\..\docs\data\fullbody_test.json --countdown 15 --seconds 10
..\.venv-convert\Scripts\python.exe -m tf2onnx.convert --tflite ..\..\models\mp-pose-lite\extracted\pose_landmarks_detector.tflite --output ..\..\models\mp-pose-lite\pose_landmarks_lite.onnx --opset 17
..\.venv-convert\Scripts\python.exe verify_landmark_onnx.py tflite --fixture ..\..\fixtures\fullbody_01 --models ..\..\models --out ..\..\fixtures\landmark_ref.npz
..\.venv\Scripts\python.exe verify_landmark_onnx.py onnx --models ..\..\models --ref ..\..\fixtures\landmark_ref.npz
..\.venv\Scripts\python.exe gpu_pose_pipeline.py --models ..\..\models --fixture ..\..\fixtures\fullbody_01 --out ..\..\docs\data\gpu_pipeline.json
```
Converter venv: `python -m venv tools\.venv-convert`, then `pip install tensorflow-cpu==2.15.1 tf2onnx==1.16.1 "numpy<2" onnx onnxruntime "opencv-python-headless<4.11"`. Extract the `.task` file (it's a zip) into `models\mp-pose-<v>\extracted\`.

Venv: `python -m venv tools\.venv`, then `pip install onnxruntime-directml numpy opencv-contrib-python pygrabber mediapipe tqdm` and `pip install --no-deps rtmlib`.

## Tried and failed (don't retry)
- **Locking exposure to hold 30 fps** on the HP camera: every manual setting that holds 30 fps gives an unusably dark image (luma ≤ 13/255) in this room, and gain at exposure −4 drops the camera to 24 fps. Use auto exposure plus an fps watchdog instead.
- **Running inference on the Intel UHD iGPU:** RTMO-s takes 82 ms p50, too slow for 30 Hz. Use the RTX 4050.
- **MediaPipe Pose on seated close-up frames:** detects 0 persons. It needs full-body framing to benchmark properly.
- **Two threads submitting DirectML work to the same adapter** (RTMO + landmarks, both on the 4050): the process dies with exit code 5 and no traceback. Use one submitting thread per adapter, or put the detector on the iGPU.
- **Building the tracking crop from the landmark bounding box:** the crop collapses onto the face within about 9 frames for a seated person. Use MediaPipe's aux landmarks 33 and 34 (×2.5 square).
- **Running RTMO every frame on the 4050 next to the landmark model:** 43–55 ms p50, over budget. Run it async, at a low rate.
- **OpenCV MSMF backend with an MJPG request:** the fourcc isn't honoured. Irrelevant to the production `IMFSourceReader` path.
