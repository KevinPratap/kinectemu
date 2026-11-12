# STATUS

Last update: 2026-11-12. Current phase: **0 (feasibility)**. Emulator code exists as four Xenia patches (`xenia/patches/`, see [xenia/README.md](../xenia/README.md)); the rest is Phase 0 tooling under `tools/phase0/`. Repository published 2026-11-12 with README, setup and launch scripts.

## Works on real hardware
- HP Wide Vision HD delivers 30 fps at 640×480 and 1280×720 under auto exposure in normal room light (`docs/data/webcam_probe.json`).
- RTMO-s 2D multi-person pose runs on the RTX 4050 via ONNX Runtime DirectML in about 20 ms steady state, and detects the user (`docs/data/pose_bench.json`).
- Live two-person test: MediaPipe Pose lite (CPU) tracks both people with 3D world landmarks and masks at p50 27.8 / p95 35.4 ms. RTMO-s on the 4050 finds both at p50 27.7 / p95 34.1 ms. Overlays were checked by eye (`docs/data/fullbody_test.json`, fixture `fixtures/fullbody_01`).
- All-GPU pose pipeline (MediaPipe landmarks converted to ONNX, verified against TFLite to 0.004 px): 2 people with 3D joints and separate player masks, p50 14.9 / p95 21.2 ms at a 30 Hz pace. Tracker on the RTX 4050, RTMO detector async on the Intel iGPU (`docs/data/gpu_pipeline.json`, WEBCAM_REPORT §6).
- **Intrinsics calibrated (2026-11-03)**, using a phone-screen checkerboard (20 views, RMS 0.557 px): fx 796.5, fy 792.0, cx 668.2, cy 355.5 at 1280×720. FOV 77.6° × 48.9°, wider than Kinect depth (57° × 43°). Distortion is small (k1 −0.010). File: `docs/data/intrinsics.json`.
- **Display → capture latency (2026-11-03): interpolated p50 186 ms, p95 222 ms, 40/40 flashes valid** (a mirror or white card reflects the screen into the camera; luma step 30). `docs/data/latency_flash.json`.
  - Rising edges average 168 ms and falling 233 ms.
  - This includes the display and compositor delay. Adding about 15–20 ms of inference gives roughly 200 ms from movement to skeleton.
  - Two earlier runs lit only the face (luma steps of 1.1 and 1.9 against the required 8) and are not valid.
- **Kinect Adventures (4D5308ED, media 6D571F26) boots** from an image of the user's own disc in the ryantimjohn fork build. The game sees the emulated Kinect and streams depth from it (`NUI: frame feed started`).
- **The game's own tracker accepts webcam-synthesised depth (first Option A′ evidence, 2026-11-03).**
  - Ellipse demo: it stays on "Is anybody there?" (`docs/data/ka_demo_ellipse_nobody.jpg`).
  - Depth built from the `fullbody_01` webcam fixture by `synth_depth.py`, played via `--nui=replay`: it moves on to its play-space screen ("Move back as far as you can"). A player marker follows the recorded person (`docs/data/ka_synth_player_detected.jpg`, `docs/data/synth_depth.json`).
  - With the old 8 s clip and assumed 70° HFOV it stopped at that screen.
- **Kinect Adventures completes its Kinect setup on webcam-only depth (2026-11-03).** Source: the `fullbody_02` clip (30 s, 29.91 fps) with calibrated intrinsics.
  - Played as-is, the game says "Too close!" (`ka_fb02_too_close_nopush.jpg`). The webcam is wider than the Kinect (77.6° vs 57°), so a player framed for it stands too near and their feet fall off the Kinect frame.
  - With `--push-mm 700`, the player moves back to 2.1–3.1 m (96% within the tracker's range). The game then reaches "Best space! One or two players" (`ka_fb02_best_space.jpg`) and its title screen with a PLAYING player badge and the Kinect hand cursor (`ka_fb02_title_playing.jpg`).
- **Live webcam → game works (2026-11-03).** This is the first emulator code, started early at the user's request, before the 0-W two-player re-run.
  - New `--nui=pipe` backend on the fork branch `kinectemu-pipe-backend` (`src/xenia/nui/pipe/`). Xenia serves `\\.\pipe\<nui_pipe>`, local clients only, and takes a live .xdepth stream.
  - `synth_depth.py --live` feeds it from the webcam.
  - Fork NUI tests still pass (81 cases, 37,142 assertions).
  - Pipe check: 5,100 recorded frames streamed through it; the game reached its title with PLAYING. The writer's disconnect and the wait for the next writer were logged.
  - Live: 24–29 fps, synth p50 26–35 ms per frame (pose plus the depth render, on the capture thread; below 30 Hz).
  - **Live join works, but it is not playable (user-reported).** Standing about 1.8 m back, the user went from JOIN IN to PLAYING, but called the result "not playable at all" and the emulation "midish" speed. The log shows about 4,500 live frames through the pipe (9,600 in the session). No screenshot: Xenia was closed first. The user reported two symptoms: a big delay, and tracking that jumped or dropped them.
  - **Fixes:**
    - Camera read on its own thread, keeping only the newest frame. At 25 fps the driver queue had been growing.
    - Person detector moved to a background thread on the iGPU. It no longer resets tracks every 10 frames; its synchronous run had been a 62 ms p95 stall.
    - Per-player distance smoothed (EMA 0.2) and clamped to 1.9–2.9 m. Raw PnP distance had jumped 2.2 → 4.1 m between seconds, and depth over 3999 mm reads as "no reading".
    - Faster depth render: a Voronoi map via distance transform within the silhouette box, and a cached reprojection grid. Depth went from 22.8 to 3.9 ms, reprojection from 7.3 to 3.4 ms.
  - **Second live run:** 30 fps, synth p50 16–18 ms, and a frame reaches xenia 17–19 ms after the camera delivers it (one player). With two people tracked: 27–29 fps, about 37 ms.
  - The game moved on to its two-player hint, "Give each other elbow room!" (`docs/data/ka_live_two_player_hint.jpg`). Our pipeline reported a second person with a raw PnP distance of 7–9 m. Not yet known whether that is a real person or a false detection.
  - Fixture check after the fixes: `fullbody_02` is 100% within the tracker's distance and area limits.
  - **The second player was a phantom; the user was alone.** Three fixes:
    - Two tracks were converging on one body. Tracks whose masks overlap with IoU > 0.4 are dropped (`drop_duplicates`).
    - People seen beyond 5 m are rejected, and their slot is freed.
    - The live detector confidence threshold is 0.5. SQPnP's assertion on degenerate landmarks (which crashed one run) is now treated as no player.
  - **Third live run:** one player only, 25–30 fps. The user got into River Rush ("Curvy Creek").
    - Tracking still drops at times (raw distance 2.8–4.2 m, "nobody" for some intervals), and the game pauses with JOIN IN.
    - The fork's Kinect Guide overlay also opened, from its arm gesture (`docs/data/ka_live_guide_overlay.jpg`).
  - **Diagnosis: our feed was not losing the player; the game's tracker was.** Fourth run, with the game's own skeleton log (`--nui_log_play_frames`):
    - Our feed held 30 fps with no empty frames.
    - The title logged floor (0, 0, 0, 0), so it found no floor plane.
    - It kept moving the virtual tilt (12°, then 20°), which our depth ignored.
    - Its tracking ID changed repeatedly (85 → 90; player index 1 → 6).
    - The player sat at the edge of its view (mask centroid x 288/320).
  - **Fixes:**
    - The depth now has a floor plane (height from the visible feet, EMA 0.05) and a back wall at 3.6 m.
    - Xenia runs with `--nui_tilt_override=0`.
    - Duplicate tracks are detected by overlap with the smaller mask.
    - Hold-over repeats the last body for up to 10 frames.
  - **Fifth run: tracking holds.**
    - The title now fits a floor: (0, 1, 0, 0.80) at first, drifting to 1.30.
    - One tracking ID (185) lasted from frame 2,790 to 5,505, about 90 s unbroken. Before the fixes, IDs changed every few seconds (4, 26, 27, 36, 66, 110, 172 in the first 1,215 frames).
    - Still open: when the player came within 1.5 m, the feed slowed to 17–21 fps (synth 30–43 ms), and a duplicate P2 sometimes reappeared. The floor height estimate drifts.
  - **User: "jump barely registers, lean/move not registered."** Measured with a scripted routine, comparing per-frame traces (`--trace`) against the title's per-frame skeleton log.
    - **Bug found and fixed:** the silhouette was scaled by each frame's raw PnP distance. Placed hip x spanned 6.7 m while the player moved about 1 m. It now uses the filtered distance, with a 150 mm per-frame step limit. While standing, the silhouette moves a median 3–7 mm per frame.
    - **After the fix, lateral motion reaches the title:** correlation 0.84 (mirrored), gain about 0.43.
    - **Jumps reach the title:** our silhouette top rose 0.25–0.35 m on each of the 3 jumps; the title's head rose 0.15–0.27 m at the same rhythm.
    - **The title's tracker runs slowly:** 16–28 Hz (155–285 frames per 10 s of pipe input), falling during gameplay.
  - **GPU contention is the limit on this laptop.** Landmarks on the iGPU take 10 ms p50 idle, but during gameplay our feed fell to 12–22 fps (synth 30–70 ms). Under Optimus the iGPU also composites xenia's output to the laptop panel. The title's rate stayed at 20–28 Hz.
  - The player stood 0.7–1.4 m from the camera, too close: the silhouette is large and the clamp hides depth motion.
  - End-to-end delay is estimated at 300 ms or more (186 ms display→capture, plus feed, plus the title's tracker at 20–28 Hz), against about 100 ms for a real Kinect. **The live webcam path is not playable yet.**
  - **Quick fixes chosen by the user** (Option B skeleton injection and the hardware setup were the alternatives offered):
    - **Motion gain** `--gain-x` / `--gain-y`. Sideways is scaled about the image centre; upward moves are scaled from a learnt standing level, and only for up to 15 frames, so stepping back is not read as a jump. On `fullbody_02` at 2.0/1.8: shifts up to ±125 px sideways; only 2 frames lifted by more than 10 px.
    - **Landmarks on the CPU** (`--device -1`): 24 ms p50 and 38 ms per frame even with xenia idle, so the iGPU stays the default.
    - **Person detector** runs only every 30 frames while someone is tracked.
  - **Live test:** `--push-mm 500 --gain-x 2.0 --gain-y 1.8`, player at 2–2.5 m.
    - **Measured:** the title's tracking rate rose to 27–30 Hz (270–300 frames per 10 s, up from 16–28 Hz), and its lateral position std rose from 0.18 to 0.33 m.
    - **The user still calls it "still bad".**
  - **Likely cause of "left/right not working": mirror orientation.** The fork's `docs/nui.md:1430` records that with rows in the wrong orientation the hand cursor moved against the hand and River Rush "answered jumps but not a sideways lean", which is exactly the user's symptom. `synth_depth.py` flips the webcam image by default, on the unverified assumption that Kinect for Windows host orientation is mirrored relative to a webcam. Being retested with `--no-mirror`.
  - **The `--no-mirror` run did not test the mirror.** The player stood 0.6–1.5 m left of the webcam axis, and `--gain-x` (scaled about the image centre) pushed the silhouette against the frame edge (shifts up to −81 px, clamped). The title logged a skeleton only about 10 times. Sideways gain now works about the player's usual spot (EMA over about 15 s), which is moved to the image centre. Retesting with the hand-cursor check to settle the orientation.
  - **Orientation settled: the default flip is correct.** With `--no-mirror`, the user's right hand moving to their right moved the cursor the opposite way, which is the wrong-orientation symptom in `nui.md`. So the mirror was not why steering failed. The likelier causes are the off-centre player and the title's low lateral gain. Retesting mirrored, with re-centring and gain.
  - **Mirrored run with re-centring and gain:**
    - The title tracked one skeleton (ID 202) with a large lateral range: pos_x −1.12..+0.60, std 0.40 m.
    - The user again reported no steering, but a screenshot showed River Rush **paused** (GAME PAUSED, player slot JOIN IN, `docs/data/ka_live_river_rush_paused.jpg`). The "not steering" reports in this and probably earlier runs may have come from a paused activity. It most likely paused after a brief tracking loss.
    - Still to verify: steering after selecting CONTINUE.
  - **Restart, then River Rush paused again (JOIN IN)** after the raft had moved part-way (screenshot not kept).
    - The title's pos_x spanned −1.11..+1.16 m (std 0.45), at 27–30 Hz. Tracking IDs changed 35 → 36 → 162.
    - **Likely cause:** `--gain-x 2.0` carried normal steps out of the play area, so the title lost the joined player, paused, and saw a new person when they came back.
    - Sideways gain is now 1.4, and the amplified offset is capped (`--max-lateral-mm`, default 500).
  - **With gain 1.4, capped:** pos_x stayed within −0.61..+0.58 m, and the raft progressed further (score 6). The title still lost the player 4 times (IDs 26, 116, 155, 156) and paused with JOIN IN (`docs/data/ka_live_river_rush_paused_gain14.jpg`).
  - **Option A′ verdict on this laptop: works, but not playable.** The title boots, sees the sensor, completes its setup, tracks at 27–30 Hz, follows lateral motion and jumps, and River Rush starts. Its depth tracker still drops the player every so often; each drop means a new tracking ID and a pause. End-to-end delay is estimated at 300 ms or more.
    - Likely causes, not isolated: synthetic depth lacks real depth separation (arms in front of the torso) and the silhouette flickers. The plan's fallback is Option B: inject the skeleton at the title runtime's skeleton output and bypass the depth tracker.
  - **Conclusion for Option A′ on this laptop (before the mirror retest):** the title accepts webcam-synthesised depth, keeps the player, and now tracks at near 30 Hz with amplified motion. Play still feels unresponsive. The remaining suspect is end-to-end delay (about 300 ms or more, of which 186 ms is camera/display alone, plus the title's own depth tracker), which more depth tuning cannot remove. Not yet measured end to end. Next candidates:
    - Option B: inject the skeleton at the title's runtime boundary, bypassing its depth tracker.
    - An end-to-end latency measurement, glass to title skeleton.
- In the same test the camera dropped to 23.92 fps under auto exposure (evening light, subject at the back of the room). 30 Hz needs more light on the player.

- **Hands-free harness re-verified (2026-11-12)** on the committed fork build with `skeleton_harness.py --case synthetic --seconds 420`: PASS on every check (bound, fresh with 0 fall-backs, one tracking id in 100% of 4,044 logged frames, direction corr +1.00 at 1 px error, lateral ±0.35 m as scripted, 12 jump rises for 11 scripted, no pause screen). Fork unit tests: 88 cases, 37,279 assertions pass. A first attempt failed only because the harness did not resolve a relative `--out` path (the scripted source crashed on `truth.csv`); fixed.

## Findings that change the plan
- Kinect titles get depth and colour through the kernel export `PsCamDeviceRequest`. Their own statically linked SDK then tracks the skeleton on the guest GPU, and XAM has no frame-delivery export.
- The first guest component is therefore a **virtual `PsCamDeviceRequest` driver**, serving raw 11-bit disparity plus Bayer colour, following the contract documented in Canary PR #1274. Details in PRIOR_ART §2 and NUI_LAYER.
- Upstream Canary is landing Kinect pieces right now:
  - knuckleslee's series: 6 merged, #1280 (the GPU resolves the tracker needs) open, #1274 (PsCam over USB) closed pending a refactor
  - ryantimjohn's fork, issue #1290 opened 2026-11-02
- Plan: rebase on these PRs and contribute the webcam backend, rather than diverge.
- The ryantimjohn fork already has a working guest depth path with a clean host plug-in point (`xe::nui::NuiSystem::PublishFrame`, depth in mm). It's verified on one title, `4D5308ED`, which matches Kinect Adventures. The webcam backend is most likely one `NuiSystem` subclass publishing synthetic mm depth plus gravity. Full guest contract: [PSCAM_CONTRACT.md](PSCAM_CONTRACT.md).

## Mocked / faked / stubbed
- Emulator code so far: the `--nui=pipe` depth backend and the skeleton injection (`--nui_skeleton_inject`), both on the fork branch `kinectemu-skeleton-inject` and mirrored as `xenia/patches/0001`–`0004`. Everything else is Phase 0 scripts (`tools/phase0/`).
- The pipe backend reports a ready sensor as soon as the pipe exists. With no writer connected, the title sees a sensor with nobody in front of it.
- `synth_depth.py` synthesises depth by design: PnP placement on MediaPipe's average-adult scale, nearest-landmark Z, and a 100 mm rounded relief. There's no floor or background (0 = no reading).

## Gate status
| Gate | State | Blocker |
|---|---|---|
| 0-W webcam | partial | Done:<br>• GPU 2-person path; slots are stable on the clip (0 switches).<br>• Intrinsics calibrated.<br>• Latency measured: 186 ms display→capture.<br>• Lit full-body re-run held 29.91 fps.<br>Still needs: a re-run with **two** players crossing (`fullbody_02` averages 1.19 people per frame). Latency is well over the 50 ms processing budget once glass-to-capture is counted. See WEBCAM_REPORT §7. |
| 0-N NUI layer | partial | Xenia side and prior art done ([NUI_LAYER.md](NUI_LAYER.md), [PRIOR_ART.md](PRIOR_ART.md)).<br>• **Kinect Adventures:** tracking runs in the title's own linked runtime, fed by `PsCamDeviceRequest` depth; its tracker detects a player in synthetic depth.<br>• **Dance Central 1, Joy Ride, Kinect Sports:** still need images. |
| 0-D disc | not applicable yet | The laptop has no optical drive (`Win32_CDROMDrive` is empty). Needs an external Xbox-compatible SATA drive. |
| Build toolchain | **pass** (2026-11-03) | Installed via winget:<br>• VS 2022 Community 17.14 with the C++ desktop workload (MSVC x64, CMake, Windows SDK 10.0.26100)<br>• LLVM 23.1.4 (clang-format)<br>• Vulkan SDK 1.4.363.0<br>Unmodified Canary `96ce1ac` builds in release in about 15 minutes total. `xenia_canary.exe` starts and logs `Build: canary_experimental@96ce1ac`. **Correction (2026-11-03):** that first launch was actually stuck at Xenia's one-time first-run disclaimer dialog (`emulator.cc:150`). The dialog is now answered (No, with the user's OK).<br>With it out of the way, the ryantimjohn fork build initialises the full core, and its replay backend streams `xdepth_writer.py --demo` frames to the guest device, logging `NUI: 300 depth frames replayed` (PRIOR_ART "Built and tested here"). |

## Waiting on the user (about 10 minutes, tooling ready)
Run these from the project root, using the venv's `python.exe`:
1. Print `docs\checkerboard_9x6.png` at 100% and measure one square in mm. Then run `tools\phase0\calibrate_intrinsics.py --square-mm <measured>`, which writes `docs\data\intrinsics.json`. Keys: SPACE capture, u undo, c calibrate, q quit.
2. Run `tools\phase0\latency_flash_test.py` with a small mirror or white card held about 15 cm in front of the laptop, so the camera sees the screen's light. Lighting your face is too weak in a lit room. The screen flashes black and white for about 30 s; it writes `docs\data\latency_flash.json`.
   - This measures display → camera → capture latency, including compositor and panel delay, ±1 refresh.
   - Use `latency_interp_ms`, which was +7 ms off the simulated truth in self-test.
3. With a lamp on and both players' feet in view, re-run `tools\phase0\fullbody_test.py`, and include the two players crossing paths.

Self-tests (all PASS, re-run 2026-11-02):
- `make_checkerboard.py`: board detects as 9×6 at 24.98 mm pitch.
- `calibrate_intrinsics.py --selftest`: recovers fx/fy within 0.05% on 20 synthetic views.
- `latency_flash_test.py --selftest`: interpolated error +6.8 ms on the main case, +10.8 ms with a threaded fake camera.

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

Xenia build (from `external\xenia-canary`): `git submodule update --init --depth 1 --recursive`, then `python xenia-build.py premake` and `python xenia-build.py build --config=release`. Output: `build\bin\Windows\Release\xenia_canary.exe`.

Replay smoke test (ryantimjohn fork, from `external\ryantimjohn-xenia\build\bin\Windows\Release`; no game loaded):
- Run `tools\phase0\xdepth_writer.py --demo demo.xdepth`.
- Then run `xenia_canary.exe --nui=replay --nui_replay=demo.xdepth --flush_log=true --log_file=replay.log`.
- Expect `NUI: replaying ... (90 frames)` in the log.

Live webcam into Kinect Adventures (fork build on branch `kinectemu-pipe-backend`):
1. Start the game: `xenia_canary.exe "<Kinect Adventures!.iso>" --nui=pipe --nui_pipe=kinectemu`
2. From the project root: `tools\.venv\Scripts\python.exe tools\phase0\synth_depth.py --models models --live --intrinsics docs\data\intrinsics.json --push-mm 700`
3. Stand about 1.8 m from the laptop with your whole body in view.

Option A′ probe (from `tools\phase0`):
- First run `..\.venv\Scripts\python.exe synth_depth.py --models ..\..\models --fixture ..\..\fixtures\fullbody_01 --out ..\..\docs\data\synth_depth.json --xdepth fixture.xdepth`.
- Then run `xenia_canary.exe "<Kinect Adventures!.iso>" --nui=replay --nui_replay=fixture.xdepth`.
- On the fixture, `root_mm` is 1256..3379 (71% within the tracker's 1800..2920) and `area_m2` 0.14..1.09 (96% within 0.2..2).

Venv: `python -m venv tools\.venv`, then `pip install onnxruntime-directml numpy opencv-contrib-python pygrabber mediapipe tqdm` and `pip install --no-deps rtmlib`.

## Tried and failed (don't retry)
- **Locking exposure to hold 30 fps** on the HP camera: every manual setting that holds 30 fps gives an unusably dark image (luma ≤ 13/255) in this room, and gain at exposure −4 drops the camera to 24 fps. Use auto exposure plus an fps watchdog instead.
- **Running inference on the Intel UHD iGPU:** RTMO-s takes 82 ms p50, too slow for 30 Hz. Use the RTX 4050.
- **MediaPipe Pose on seated close-up frames:** detects 0 persons. It needs full-body framing to benchmark properly.
- **Two threads submitting DirectML work to the same adapter** (RTMO + landmarks, both on the 4050): the process dies with exit code 5 and no traceback. Use one submitting thread per adapter, or put the detector on the iGPU.
- **Building the tracking crop from the landmark bounding box:** the crop collapses onto the face within about 9 frames for a seated person. Use MediaPipe's aux landmarks 33 and 34 (×2.5 square).
- **Running RTMO every frame on the 4050 next to the landmark model:** 43–55 ms p50, over budget. Run it async, at a low rate.
- **A host-side `NuiSkeletonGetNextFrame` export, or PsCam/Mca/Detroit stubs that return success with no data** (weronika-saturday fork): titles never call the former, and the latter makes the title wait forever for frames. Deliver frames through `PsCamDeviceRequest`, or hook the title's own SDK function.
- **Building Xenia without the Vulkan SDK:** `building.md` calls it optional, but the build stops at `xenia-ui-vulkan_spirv.stamp` with `FileNotFoundError`, because shader compilation needs the SDK's tools. Install the SDK at `C:\VulkanSDK`.
- **OpenCV MSMF backend with an MJPG request:** the fourcc isn't honoured. Irrelevant to the production `IMFSourceReader` path.
