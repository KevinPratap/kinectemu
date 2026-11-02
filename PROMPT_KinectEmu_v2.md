# PROMPT — Build "KinectEmu": Xbox 360 emulator with native laptop-webcam Kinect (+ optional disc boot, optional real Kinect)

## Role
You are a senior C++ systems engineer specialising in console emulation, real-time computer vision on GPUs (ONNX Runtime / DirectML), Windows media capture (Media Foundation), and Windows raw optical I/O. You write code that is verified against hardware, never assumed to work.

## Objective
Fork **Xenia Canary** into **KinectEmu** so that Xbox 360 Kinect titles are **playable on a normal laptop with its built-in webcam**, with no Kinect hardware.

- **A. Virtual Kinect from a webcam (primary).** Turn an RGB webcam into a virtual Kinect v1. Produce a 3D skeleton for up to 2 players, plus depth with player index, colour, IR, audio, tilt and accelerometer, all in the formats and timing the guest title expects.
- **B. Real Kinect v1 (optional backend).** Support the same guest interface from a physical Kinect 1414 through libfreenect, behind the same `SensorSource` interface.
- **C. Physical disc boot (optional track).** Run a retail disc from an Xbox-compatible optical drive. Until that track passes its gate, titles load through Xenia's existing image/extracted-folder support, using images of discs the user owns.

## Target machine (develop and measure on exactly this)
| Item | Value |
|---|---|
| Laptop | HP Victus 15-fa1xxx, i7-12650H (10C/16T), 32 GB RAM |
| GPUs | NVIDIA RTX 4050 Laptop (dGPU, Optimus), Intel UHD (iGPU) |
| Camera | HP Wide Vision HD Camera, USB `VID_30C9&PID_0069`. Ignore virtual cameras such as "Camo". |
| Optical | **None built in.** The disc track needs an external drive (see Phase 1-D). |
| OS / tools | Windows 11 x64, VS 2022, C++20, Xenia's `xb` build script unchanged |
| Baseline | `github.com/xenia-canary/xenia-canary`, branch `canary_experimental` |

## Prior art to read before writing code
- `github.com/BrinooTk/PS5X360`: camera-pose → skeleton injection for one title. **This is the closest precedent to the webcam path.** Study its calibration lessons.
- `github.com/weronika-saturday/xenia-canary`: a Kinect driver that compiles but produces no frames.
- Every `XamNui*` stub in Xenia's kernel module, plus Xenia's `vfs::Device`, `DiscImageDevice` and GDFX code.
- Kinect for Windows SDK v1.8 docs: `NUI_SKELETON_FRAME`, `NUI_SKELETON_DATA`, the 20-joint `NUI_SKELETON_POSITION_INDEX`, `vFloorClipPlane`, and depth/player-index packing. Use these as reference for joint semantics. Phase 0 must confirm what the 360 actually uses.

## Non-goals
Kinect v2 · Xbox One/Series · Xbox Live / DLC / achievements · disc ripping tools · rewriting Xenia's CPU/GPU core · training new ML models (use pretrained models only) · any network access at runtime.

## Working rules
1. **No lying stubs.** Every unimplemented path logs once with a stable message ID and returns a documented failure. Never return success from code you did not implement. Never report "no sensor" when the sensor is present but slow.
2. **Phase gates are hard.** A phase starts only after the previous gate passes on this laptop and is recorded in `docs/STATUS.md`. If a gate is unreachable, stop and write the blocker down with evidence. The disc track (1-D) is the only exception: it runs in parallel and never blocks the Kinect phases.
3. **One concern per commit.** New code goes under `src/kinect/` (guest NUI), `src/sensor/` (sources plus the virtual Kinect) and `src/disc_physical/`. Format with clang-format per Xenia style.
4. **Real-time discipline.** No allocation in the frame path, no busy-waits, no unbounded queues. Use SPSC ring buffers and drop late frames rather than block. Inference never runs on a guest-CPU thread.
5. **Every claim needs a log line.** "Works" means a recorded session plus the log excerpt that proves it.
6. **Privacy.** Camera frames never leave the machine. Recording is opt-in and shows an on-screen indicator. Fixtures are stored only in the user's chosen folder.
7. **Model licences.** Use only models whose licence permits this use, such as Apache-2.0 or MIT. Record each model's name, version, licence and SHA-256 in `docs/MODELS.md`. Do not use SMPL/SMPL-X or any non-commercial (CC-BY-NC) weights.

---

## Phase 0 — Feasibility gate (no feature code until done)

**0-W `docs/WEBCAM_REPORT.md` (gates everything)**
- Enumerate every capture device through Media Foundation. List all native modes (resolution × fps × subtype) for the HP camera.
- Measure under auto exposure first. Lock exposure only if auto exposure drops fps. On the HP camera, every locked setting that holds 30 fps gives a black image (see `docs/WEBCAM_REPORT.md` §3). Keep an fps watchdog that warns when more light is needed. Then **measure** the delivered fps over 5 minutes at 640×480 and 1280×720 under normal room light. Laptop webcams often fall to 15 fps when auto-exposure lengthens the shutter. Report whether 30 fps holds, and at what light level it fails.
- Calibrate intrinsics with a printed checkerboard (OpenCV). Report horizontal and vertical FOV and distortion, and compare the FOV to Kinect v1 colour (~62°×48.6°) and depth (~57°×43°).
- **Inference benchmark:** for each candidate model stack, measure p50/p95 latency for 1 and 2 people on (a) the RTX 4050 via ONNX Runtime DirectML or CUDA EP, (b) the Intel UHD via DirectML, and (c) the CPU. Evaluate at least two of:
  - BlazePose GHUM 3D (MediaPipe Pose Landmarker, as ONNX): 3D world landmarks plus a segmentation mask per person
  - RTMO or RTMPose (2D, multi-person) plus a 2D→3D lifter
  - Optionally, Depth Anything V2 **Small** (Apache-2.0) for monocular depth
- **Glass-to-skeleton latency:** measure the delay from a real movement to the joint update. For example, film a hand hitting a key that flashes the screen. Report p50/p95 in ms.
- State plainly: **can this camera plus this GPU deliver a 30 Hz, 2-person 3D skeleton within a 50 ms processing budget, yes or no.** If no, name the bottleneck.

**0-N `docs/NUI_LAYER.md`**
For Dance Central 1, Kinect Adventures, Kinect Joy Ride and Kinect Sports, inspect the XEX: imported modules, `XamNui*` and other NUI imports, any statically linked Kinect runtime (look for the depth-to-body-part classifier and skeleton solver), device paths, and USB usage. Answer per title: **does skeletal tracking run in system code or in code linked inside the title?** Also list which streams the title actually consumes: skeleton, depth, player index, colour, audio. A webcam can't produce real depth, so this tells you how much depth the title really depends on.

**0-D `docs/DISC_REPORT.md` (only when an external drive is attached)**
Same as the original prompt: drive model and firmware, `IOCTL_CDROM_READ_TOC_EX` and `IOCTL_SCSI_PASS_THROUGH_DIRECT` results, a tail read past the DVD-9 boundary, and a plain yes/no on whether it can read the whole disc. State clearly that stock DVD drives expose only the video partition of an XGD disc. Reading the game partition needs a SATA drive with Xbox-compatible firmware (e.g. Kreon/0800-class) on a USB-SATA adapter. Report which class is attached.

## Phase 1 — Game loading
- **1-I (gate for Kinect work):** Confirm that each target title boots to its title screen from Xenia's existing image or extracted path, using an image of a disc the user owns. Log the title ID and media ID.
- **1-D (parallel, optional):** Add a `PhysicalDiscDevice` that implements `vfs::Device`, so GDFX/STFS/metadata work unchanged. Use raw `\\.\X:` access with `READ(12)` via SCSI pass-through and security-sector remapping, with the offset math documented in comments. Add bounded retries, a 1–2 GB streaming sector cache in the user cache dir, and a game-list entry built from metadata read off the disc.
  - **Acceptance:** `--disc X:` reaches the title screen with no image files on disk, and the log shows the title ID and sector count.
  - This track never blocks Phases 2–5.

## Phase 2 — Sensor sources (host side)
Define `SensorSource` with one output contract: timestamped `SensorFrame`s (color, optional native depth, optional host skeleton, audio, imu, health) delivered through an SPSC ring.

- **`WebcamSource` (primary).** Media Foundation `IMFSourceReader` on a dedicated capture thread. Use the mode chosen in 0-W with exposure locked, and use hardware timestamps (`MFSampleExtension_DeviceTimestamp`, falling back to the QPC sample time). Pick the camera by stable symbolic link, not by index. Handle camera loss and Windows privacy-denied with a clear diagnostic.
- **`FreenectSource` (optional).** Kinect v1 through libfreenect on a USB 2.0 controller (prove which one). This laptop has no spare EHCI, so document what was used.
- **`ReplaySource`.** Plays recorded fixtures: raw MJPG/NV12 frames plus timestamps, or Kinect depth and colour. Every later tuning change is measured against the replay fixtures.
- **Health overlay:** device, mode, measured fps, dropped frames, exposure state, inference latency, and which GPU inference is using.
- **Acceptance (webcam):** 30 ± 1 Hz sustained for 10 minutes, zero ring overruns, and a 30-second fixture recorded and replayed bit-identically.

## Phase 3 — Virtual Kinect (webcam → Kinect streams)
Runs on its own thread, consumes `WebcamSource` frames and produces Kinect-shaped output. Inference uses ONNX Runtime with an explicitly selected DirectML device.
- **Default: the RTX 4050**, on its own DirectML queue. Measured in 0-W: the Intel UHD takes 82–150 ms per frame, which is too slow.
- Measure and report the cost to Xenia's frame time, and the inference latency while Xenia is loading the GPU. Don't assume steady-state clocks.

1. **Pose plus masks.** Run multi-person 3D pose (up to 2 players) with a per-person segmentation mask. Assign stable player IDs across frames: use a Hungarian match on the torso box and joint distance, with a hold-over window so a player who leaves the frame for a moment keeps their slot.
2. **Metric placement.** A monocular camera can't tell size from distance, so solve it explicitly:
   - Fit each player's root translation in camera space with PnP of the 2D keypoints against the 3D pose, using calibrated intrinsics and per-player bone lengths.
   - Bone lengths come from a one-time calibration: the player enters their height, or holds a 3-second T-pose.
   - Estimate the floor plane from ankle positions over time, and emit it as the frame's floor clip plane.
3. **Skeleton retarget.** Map the model's joints onto the 20-joint Kinect v1 skeleton:
   - Orientation: sensor origin, metres, +Y up, +Z away from the camera.
   - Synthesised joints: HipCenter, Spine, ShoulderCenter, Head, Hand and Foot. Write each formula down.
   - Per-joint state: Tracked, Inferred or NotTracked, derived from model visibility and confidence.
   - Kinect's hip and shoulder joints sit inside the anatomical landmarks. Apply documented offsets and keep them configurable per title profile.
4. **Synthetic depth plus player index.** Render a body proxy for each player on the GPU: capsules posed by the skeleton, scaled by the calibrated bone lengths, depth-tested in Kinect depth intrinsics.
   - Each pixel gets depth in mm and player index bits, in the exact packing Phase 0-N finds. Expected: NUI-style `depth_mm << 3 | player_index` at 320×240 or 640×480. Verify it.
   - Clip the proxy with the segmentation mask so silhouettes match the real body.
   - Background: a constant back plane at a configurable distance, or, if 0-W shows budget, Depth Anything V2 Small rescaled to match the solved player depth.
   - Optionally add Kinect-like noise and shadow holes, but only if Phase 4 shows the title's tracker needs them.
5. **Other streams.**
   - Colour: undistort, crop and resample to Kinect colour FOV at 640×480, registered to the synthetic depth.
   - IR: luminance-derived and flagged as synthetic.
   - Audio: laptop mic array through WASAPI, up-mixed to the 4-channel layout and flagged.
   - Tilt motor: virtual. Shift the crop window vertically, report the commanded angle, and keep the accelerometer gravity vector consistent with that angle.
   - LED: logged only.
6. **Framing assistant.** An overlay that tells the player to step back, centre themselves, or tilt the screen. Full-body framing on this camera needs roughly 1.8–3.0 m depending on FOV, so measure and document it. It also warns on low light when the fps or confidence drops.
- **Acceptance:** on replay fixtures, the 20-joint output is deterministic within a documented tolerance. Because GPU EPs can be nondeterministic, fixtures run on the CPU EP for bit-exact checks. Live, it holds 30 Hz with 2 players and p95 processing ≤ 33 ms.

## Phase 4 — Guest-facing NUI
Pick the injection point from 0-N, and keep the alternative behind a compile flag.

- **Option B — NUI boundary injection (default for webcam).** Implement the `XamNui*` exports plus hooks on the title-linked runtime's skeleton-output entry points, located per title by signature or hash, never by fixed address. The title receives skeletons from the Virtual Kinect, while depth, player index and colour come from the synthesised streams.
- **Option A′ — Synthetic-depth feed (experiment).** Feed the synthetic depth into the title's own tracker, through emulated USB or the lowest NUI depth entry point, and let it track as it would on hardware.
  - Run this as a time-boxed experiment in Phase 4 and record whether the title's tracker locks onto synthetic depth.
  - If it locks reliably, it becomes the no-per-title-tuning path. If not, record why and stay on B.
- **`FreenectSource`** feeds both options unchanged, with real depth.
- Document every hooked function in `docs/NUI_IMPLEMENTATION.md`: its signature, what it returns, what is synthesised and what is not.

## Phase 5 — Skeleton fidelity and latency (the hard part)
- Match the joint set and hierarchy, tracking-state and confidence semantics, coordinate space, floor plane and frame cadence the title expects.
- **Latency compensation.** Webcam plus inference adds latency a real Kinect doesn't have. Measure it, then apply per-title compensation: a short-horizon joint predictor such as a constant-velocity or One-Euro-style filter, plus a title-side timing offset where the title allows one. The per-title profile carries it.
- Per-title calibration profile in JSON: smoothing, latency compensation, joint offsets, gesture thresholds, player-slot assignment, depth back-plane distance.
- Use the replay harness for every tuning change.
- **Acceptance:** the title's own calibration or "player detected" screen locks onto the player and holds for 60 seconds, and the recorded skeleton overlay matches the body within a documented tolerance.

## Phase 6 — Per-title bring-up (laptop-native)
Bring titles up one at a time, in this order: **Dance Central 1 → Kinect Adventures (2 players) → Kinect Joy Ride → Kinect Sports.** Each title gets a pass/fail matrix:

| Check | Required |
|---|---|
| Boots (state: disc or owned image) | yes |
| Sensor recognised by title (webcam backend) | yes |
| In-title calibration passes | yes |
| Gameplay tracking holds 10 min | yes |
| Scoring/UI accepts input correctly | yes |
| 30 min session, no crash | yes |
| Room distance, light level (lux or phone reading), player count | state it |
| Measured game FPS and inference p95 on this laptop | state it |

## Performance floor
- Titles must sustain their native 30 FPS on this laptop with the webcam pipeline running and two players tracked, plugged in, in performance mode, with Xenia on the RTX 4050.
- If they don't, report the bottleneck per subsystem: guest CPU/JIT, Xenia GPU, inference device, capture, or tracker. Don't silently lower the target or the model quality.

## Reporting contract
Update `docs/STATUS.md` after every phase:
- What works on real hardware, one line each.
- What is synthesised, mocked or stubbed, and where. The webcam path synthesises depth, IR, tilt and accelerometer by design, so say so.
- Exact reproduce commands.
- The log excerpt proving the gate passed.
- Failed attempts, so they aren't retried.

## Definition of done
- On this laptop, with only the built-in webcam and no Kinect hardware, **Dance Central 1 completes a full song twice in a row**.
- Flashcard moves register and the song finishes with a star rating.
- Logs show the webcam backend, the measured latency and fps, and the boot source (disc or owned image).
- If a real 360 and Kinect are available, also compare scores on the same song and report the difference.
- **Stretch:** the same song booted from a physical disc in a compatible external drive.

