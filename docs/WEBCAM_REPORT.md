# Phase 0-W — Webcam feasibility report

Machine: HP Victus 15-fa1xxx, i7-12650H, RTX 4050 Laptop (driver 592.82), Intel UHD, 32 GB, on AC power, "High performance" plan.
Date of measurements: 2026-11-02. Raw data: [data/webcam_probe.json](data/webcam_probe.json), [data/exposure_sweep.json](data/exposure_sweep.json), [data/pose_bench.json](data/pose_bench.json).

**Gate verdict: NOT YET PASSED.** Two-person 3D joints plus per-player masks run on the GPU at p95 about 21 ms (§6). Still open: 30 fps capture in this room's light (§5), camera intrinsics, glass-to-skeleton latency and player-slot stability (§7).

## 1. Capture devices (DirectShow enumeration)

| Index | Device | Notes |
|---|---|---|
| 0 | DroidCam Source 3 | virtual, RGB24 640×480 @ 1 fps, ignore |
| **1** | **HP Wide Vision HD Camera** | target |
| 2 | OMEN Cam & Voice | virtual, 0.2 fps, ignore |
| 3 | DroidCam Source 2 | virtual, ignore |
| 4 | OBS Virtual Camera | format enumeration fails, ignore |

"Camo" shows in Device Manager with status Error, and DirectShow doesn't list it. Device index order is not stable once virtual cameras are installed, so the real `WebcamSource` must select by symbolic link or name, as the prompt requires.

HP Wide Vision HD native modes:
- MJPG: 1280×720, 640×480, 640×360, 352×288, 320×240, 176×144, all @ 30
- YUY2: 640×480, 640×360, 352×288, 320×240, 176×144, all @ 30

There is no 1080p mode and no IR/depth stream.

## 2. Delivered frame rate

15 s per run (`webcam_probe.py`) under normal evening room light. Mean luma is the 0–255 grey level; there's no lux meter yet.

| Backend | Mode | Exposure | fps | frame p50 / p95 ms | mean luma |
|---|---|---|---|---|---|
| MSMF | 640×480 | auto | 29.98 | 32.1 / 49.1 | 62 |
| MSMF | 1280×720 | auto | 29.98 | 32.0 / 49.9 | 57 |
| DSHOW | 640×480 YUY2 | auto | 29.92 | 32.0 / 48.0 | 70 |
| DSHOW | 1280×720 MJPG | auto | 29.71 | 32.0 / 48.1 | 72 |

- **30 fps holds under auto exposure** in this room, on both backends and both modes.
- **Arrival jitter is real.** About 9–13% of frames arrive more than 40 ms after the previous one, and the next one follows early. Host arrival time is therefore not a usable timestamp; use the device timestamp (`MFSampleExtension_DeviceTimestamp`), as the prompt already specifies.
- **The MSMF backend in OpenCV didn't honour the MJPG request** (fourcc read back as raw subtype 0x16). The production `WebcamSource` uses `IMFSourceReader` directly, so this is an OpenCV quirk to re-check, not a blocker.

## 3. Exposure lock — measured to be counter-productive on this camera

Sweep at DSHOW 640×480 with manual exposure (`exposure_sweep.py`):

| Exposure value | fps | luma |
|---|---|---|
| −13 … −4 | 29.94 | 1–2 (black) |
| −3 | 23.92 | 2.5 |
| −2 | 19.49 | 3.2 |
| −1, 0 (clamped to −2) | 15.57 | 4.5 |

Adding gain at a locked exposure:

| Exposure | Gain | fps | luma |
|---|---|---|---|
| −5 | 0 / 64 / 128 (max) | 29.9 | 1.8 / 4.4 / 12.7 |
| −4 | 0 | 29.9 | 1.9 |
| −4 | 32–128 | **23.86** | 3.7–16 |

- This reproduces the laptop-webcam fps trap: exposure −3 and longer drops to 24, then 19, then 15 fps.
- The firmware also drops to 24 fps when gain is non-zero at −4. That's an unexpected quirk.
- In this light, **no manual setting gives both 30 fps and a usable image**: the best is luma 12.7.
- Auto mode delivers 30 fps at luma 60–70, so the firmware's auto mode applies processing that manual mode doesn't.

**Decision (changes prompt Phase 0-W/2):** use auto exposure plus an fps watchdog, not an exposure lock. If measured fps drops below 29 for 2 s, raise a "more light needed" warning on the overlay and log `W2-010`. Revisit the lock only if auto mode is seen dropping fps in a dimmer room.

## 4. Pose inference benchmark

150 real webcam frames, 1280×720, with the user seated at the laptop (upper body visible). Pre- and post-processing are included. ONNX Runtime 1.24.4 with DirectML; MediaPipe 1.2.0 on CPU (it has no GPU delegate on Windows).

DXGI adapter order, which is the order of ORT DirectML `device_id`:
- 0: Intel UHD
- 1: RTX 4050 Laptop
- 2: Intel UHD (duplicate entry)
- 3: Microsoft Basic Render Driver

| Model (2D, multi-person) | Device | p50 ms | p95 ms | persons found |
|---|---|---|---|---|
| RTMO-s | CPU | 132.6 | 807.9 | 1.01 |
| RTMO-s | Intel UHD (DML) | 82.1 | 109.5 | 1.01 |
| RTMO-s | **RTX 4050 (DML)** | **37.9** cold / **~20 steady** | 42.0 cold / 17.3 run-only | 1.01 |
| RTMO-m | CPU | 1230.7 | 1389.0 | 1.0 |
| RTMO-m | Intel UHD (DML) | 150.6 | 153.3 | 1.0 |
| RTMO-m | RTX 4050 (DML) | 41.7 | 44.1 | 1.0 |

| Model (3D world landmarks + masks) | Device | p50 ms | persons found |
|---|---|---|---|
| MediaPipe Pose lite / full / heavy | CPU | 24.6 / 24.6 / 24.7 | **0** — invalid run |

- **The Intel iGPU is too slow (82–150 ms).** The prompt's plan to default inference to the iGPU is wrong for this laptop. Inference must run on the **RTX 4050**, sharing it with Xenia's GPU emulation. Measuring that contention is now a Phase 3 requirement.
- **"Cold" vs "steady" on the 4050.** In the benchmark loop the GPU sat at idle clocks (P8, 210 MHz) between frames, which gave 38 ms. A dedicated steady-state loop gave 3.6 ms prep plus 16.3 ms inference (p95 17.3 ms), about 20 ms. With Xenia keeping the GPU busy, clocks should stay up, but that must be measured, not assumed.
- **Profiling note.** Almost everything runs on DML; one `Gather` falls back to CPU (about 0.9 ms). `MemcpyToHost`'s 13 ms is mostly GPU-sync wait, not copy cost.
- **The MediaPipe run is invalid.** It found 0 persons in all three variants, and the identical timings mean only the person detector ran. Its detector needs more of the body than a seated close-up shows. **Re-run with a full-body subject at 2–3 m.**
- **The 3D route isn't benchmarked yet.** A 2D→3D lifter for the RTMO path is still to be chosen.

## 5. Live two-person test (`fullbody_test.py`)

Raw data: [data/fullbody_test.json](data/fullbody_test.json). The fixture (239 frames, 1280×720 JPEG plus timestamps) is in `fixtures/fullbody_01/`, local only and git-ignored.

Scene: two real people. One was seated about 1 m from the laptop (upper body only). The other stood at the back of the room with arms raised, feet at the bottom edge of the frame. The room had evening indoor light and a bright white wall on the right.

| Model | Device | p50 ms | p95 ms | persons/frame | notes |
|---|---|---|---|---|---|
| MediaPipe Pose lite | CPU | 27.8 | 35.4 | 2.0 | mask on 239/239 frames; full-body (both ankles visible) 94 |
| MediaPipe Pose full | CPU | 35.6 | 44.8 | 2.0 | full-body 88 |
| MediaPipe Pose heavy | CPU | 115.3 | 135.5 | 2.0 | full-body 108; too slow |
| RTMO-s (2D) | RTX 4050 DML | 27.7 | 34.1 | 2.04 | |

- **Overlays checked by eye** (`overlay_00059/00119/00179.jpg`). Both skeletons sit on the correct bodies, and the segmentation mask follows the standing person's silhouette including raised hands.
  - The script only tints mask #0. Per-person masks are returned but weren't visualised.
  - "Full-body" frames are about 37–45% of the standing person's frames, because their ankles were at the bottom edge. This is a framing problem, not a model failure, and it's exactly what the framing assistant must catch.
- **The fps trap does happen under auto exposure.** Capture ran at **23.92 fps** during this test, versus 29.9–30 in §2 with the camera aimed at a close, seated subject. 23.92 matches the camera's 24 fps long-exposure step in §3 exactly.
  - So the §3 decision stands: auto exposure plus a watchdog.
  - The watchdog **will** fire in an ordinary evening room. Playing at 30 Hz needs more light on the player.
- **Monocular scale is not trustworthy.** MediaPipe's world-landmark span (ankle to top landmark) gave a median of 1.38–1.50 m depending on model. The models disagree, and none of them can know the real height. This confirms the per-player height or T-pose calibration in Phase 3.
- **Budget, 2 persons.**
  - MediaPipe lite (CPU) and RTMO-s (GPU) both hit p50 ≈ 28 ms but p95 ≈ 34–35 ms, slightly over the 33 ms frame period. Both are well inside the 50 ms processing budget the gate asks about.
  - MediaPipe on CPU competes with Xenia's guest-CPU threads. RTMO is 2D only.
  - **Next step:** convert the MediaPipe landmark model (the TFLite inside the `.task`) to ONNX and run it on the RTX 4050 through DirectML, per person crop. Use RTMO-s as the GPU person detector and tracker in front of it. This keeps 3D landmarks and masks off the CPU.

**Gate answer so far:** 2-person tracking with 3D landmarks and masks within the 50 ms budget is **yes on the bench**: MediaPipe lite on CPU, p95 35 ms. 30 Hz capture is **not guaranteed** in this room's light (24 fps measured). The gate stays open until the GPU 3D path, intrinsics and latency are measured.

## 6. All-GPU pose pipeline (`gpu_pose_pipeline.py`)

Raw data: [data/gpu_pipeline.json](data/gpu_pipeline.json). Same two-person fixture as §5 (239 frames).

**Design.** This is MediaPipe's own two-stage design, with RTMO as the person detector:
1. RTMO-s finds people and gives a crop for each.
2. The MediaPipe landmark model (converted to ONNX, see MODELS.md) runs per person on the RTX 4050. It returns 33 landmarks, 3D world joints in metres, a presence flag and a 256×256 mask, which is warped back to a 640×360 map, one per player.
3. Between detections, each person's crop for the next frame comes from landmarks 33 and 34 (hip centre and above-head point), scaled ×2.5, which is MediaPipe's own rule.
4. The detector re-runs every 10 frames, or every 3 frames while fewer than 2 players are tracked. It runs on its own thread on the **Intel iGPU**, handing new players to the tracker.

| Variant | Mode | p50 ms | p95 ms | max ms | persons/frame |
|---|---|---|---|---|---|
| lite | RTMO every frame, same GPU, serial | 54.9 | 68.7 | 92.1 | 2.0 |
| lite | RTMO every 10th frame, same GPU, serial | 10.3 | 50.4 | 74.0 | 2.0 |
| **lite** | **async detector on iGPU, paced at 30 Hz** | **14.9** | **21.2** | **25.6** | 1.92 |
| full | RTMO every frame, same GPU, serial | 52.9 | 66.2 | 95.6 | 2.0 |
| full | RTMO every 10th frame, same GPU, serial | 11.5 | 53.1 | 90.0 | 2.0 |
| **full** | **async detector on iGPU, paced at 30 Hz** | **17.3** | **21.6** | **29.8** | 1.97 |

Stage breakdown (lite, serial, same GPU):

| Stage | p50 ms | p95 ms |
|---|---|---|
| RTMO-s (pre + run + post) on real frames | 40.6 | 46.6 |
| Landmark model `run` per person | 2.5 | 3.8 |
| Landmark per person incl. warp + mask unwarp | 4.4 | 6.0 |

The GPU sat at P0, 2505 MHz. On real frames RTMO costs about 40 ms, versus about 20 ms on the synthetic frame in §4. The detector is the expensive part, and the per-person landmark stage is cheap.

Findings:
- **The 2-person GPU path with 3D joints and masks fits the frame budget.** With the async detector: lite p95 21.2 ms and max 25.6 ms; full p95 21.6 ms. Both are under 33 ms with the CPU nearly idle.
  - 7 of 239 paced frames slipped the 33 ms tick. Not yet attributed; most likely Windows sleep granularity or start-up.
  - mean persons < 2 reflects the first frames, before the async detector delivers the second player.
- **Overlays checked by eye** (`fixtures/fullbody_01/gpu_*_00059/00119/00179.jpg`): both people have correct skeletons and **separate per-player masks**, which is what the Kinect player-index bits need.
- **My first tracking rule was wrong.** Building the next crop from the landmark bounding box made the seated person's crop collapse onto the chin within about 9 frames, a feedback loop. Using aux landmarks 33 and 34 (MediaPipe's rule) fixed it.
- **Two threads running DirectML on the same adapter crashed the process** (exit code 5, no traceback). RTMO on the iGPU thread plus landmarks on the 4050 is stable. The C++ implementation must either keep one submitting thread per adapter or put the detector on its own adapter or queue. Recorded in STATUS under "don't retry".
- **Python's GIL inflates these numbers.** The tracker and the detector's CPU pre/post share it, so the async p50 (14.9 ms) is above the serial track-only p50 (about 7–10 ms). The C++ version should come in lower.
- **Not yet verified:** that each person keeps the same player slot for the whole run. Slot order differs between runs, as expected. Within-run stability needs a per-frame slot log, which is Phase 3 requirement 1.

**Recommended inference layout for Phase 3:**
- Tracker: MediaPipe landmarks **lite or full** on the **RTX 4050**, every frame.
- Detector: RTMO-s on the **Intel iGPU**, async, about 3 Hz.
- CPU: free for Xenia.
- The remaining unknown is how much Xenia's own GPU load on the 4050 slows the tracker. That gets measured once Xenia builds.

## 7. Still open for this gate

| Item | Needs |
|---|---|
| Re-run the 2-person test with both players fully in frame and the room better lit | Both players standing 2.5–3 m back, feet visible, one more lamp on |
| Intrinsics / FOV / distortion | Printed checkerboard (9×6 inner corners, known square size) |
| Glass-to-skeleton latency | A short live test with the user moving in front of the camera |
| Inference cost under Xenia GPU load | Xenia build (see STATUS: no MSVC toolchain installed) |
| Light level in lux | Phone light meter app reading at the player position |
