# Prior art (read 2026-11-02)

Local read-only clones are in `external/` (git-ignored). PR and issue state came from the GitHub API on 2026-11-02.

## 1. Upstream Xenia Canary (`canary_experimental` @ 96ce1ac, 2026-11-01)

- **XAM NUI exports** (`src/xenia/kernel/xam/xam_nui.cc`) are all high-level: device status, tilt, HUD/engagement, identity, depth calibration, best-skeleton index. **XAM has no export that delivers skeleton, depth or colour frames.**
- **Kernel camera exports** exist only as table entries with no implementation: `PsCamDeviceRequest` (0x30D), `McaDeviceRequest` (0x30E, mic array), `DetroitDeviceRequest` (0x30F, tilt), `UsbdGetNatalHub*` (0x372–0x374).
  - When a title imports one of these, `xex_module.cc` sets up an extern with no handler, and a call ends in `UndefinedImport`.
- `NUIController::NuiInitialized()` is just the xconfig `KinectInitialized` retail flag; there's no device behind it.
- XAM message `0x0002C009` (Kinect identity callback registration) is accepted (`xam_app.cc:203`, PR #1278, merged).

## 2. knuckleslee's upstream Kinect PR series (Oct 2026), the most relevant work

A real Kinect v1 drives **title 5345084D's own NUI library** inside Xenia, and "the title tracks a player". The approach is the title's SDK running on camera data from `PsCamDeviceRequest`, i.e. our Option A′.

| PR | What | State |
|---|---|---|
| #1264 | Fetch from wide 1D textures at large coordinates (the tracker's 8192-wide tree tables) | merged |
| #1265 | PM4_WAIT_IB_PFD_COMPLETE | merged |
| #1266 | Resolve copy command 2 (kConvertTo1111) + k_DXT3A_AS_1_1_1_1 bit order | merged |
| #1272 | ObReferenceObjectByHandle object types | merged |
| #1273 | XamShowNuiMessageBoxUI / DeviceSelectorUI | merged |
| #1278 | Accept identity callback 0x2C009 | merged |
| #1279 | Shader-interpreter texture fetches | closed, not merged; included as the first commit of #1280 |
| **#1280** | **Resolves without vertices in vf0**: the tracker's multi-rectangle copy draws, its vertex shader run on the CPU interpreter | **open** |
| #1275 | LDI (LZX) decompression exports | open |
| #1276 | XamContentOpenFile for packages on guest devices (the title's NUI data packages) | open |
| #1277 | Level camera from XamNuiCameraTiltGetStatus | closed, not merged |
| **#1274** | **PsCamDeviceRequest with a Kinect v1 over USB** (libusb + UsbDk/WinUSB, +1339 lines) | **closed, not merged.** The maintainer called it disorganized and asked for a refactor. It was not rejected on technique. |

**The `PsCamDeviceRequest` contract from #1274** (worked out from 5345084D, not from console documentation). This is exactly what a virtual sensor must emulate:
- **Setup:** needs state 4 from code 0, then success from 27, 14 (or 11 when code 13 reports a version below 5.1) and 25.
- **Code 5, frame read:** asynchronous. Requests are queued and completed with the newest frame.
  - Depth: **640×480 raw 11-bit disparity in 16-bit words**. Missing depth is passed as **0, not 2047**, because 5345084D takes the maximum over depth blocks.
  - Colour: **640×480 Bayer**.
  - Colour frames carry the generation of the last completed settings request; the title drops older ones.
- **Code 24:** raw-disparity-to-mm table. **Code 14:** depth↔colour registration. Both come from the device calibration, using libfreenect math.
- **Code 2:** stop. Queued reads complete with X_STATUS_CANCELLED, and a later read restarts streaming.
- **Codes 35/36:** camera settings, not applied in #1274.
- Status codes for absent camera, cancelled read and bad range were chosen by the author, not taken from the console.
- Also found: 5345084D's race steering reads the **depth image's player pixels**, so depth and player index matter even with skeleton injection.

## 3. ryantimjohn/xenia-canary `add-kinect-support` (head `8907f698`, cloned to `external/ryantimjohn-xenia`)

- Issue #1290 (opened 2026-11-02) claims "Kinect support completely operational" and promises 11 stacked PRs. None are open yet; the owner's #1291 and #1295 are unrelated and merged.
- Compare against upstream: 152 files, +19,873/−413, 9 commits ahead and 88 behind. About 8k lines are tests. `third_party/` is unchanged and the code is BSD.
  - Avoid its other branch `codex/dc3-kinect-integration-20261023`: it has commits marked "[Private] SDK-derived".
- **Architecture: the same guest path as knuckleslee, a different host side.** The data path:
  1. `PsCamDeviceRequest` (`kernel/xboxkrnl/xboxkrnl_nui.cc:25`, verified)
  2. `NuiState::HandleCameraRequest`
  3. `NuiCamera` (`kernel/xam/nui_camera.cc`)
  4. The `NuiCamera` feed thread converts the newest host depth frame through the code-24 LUT into the guest ring.
  5. The title's own SDK tracks on the emulated GPU.

  There's no skeleton injection.
- **Host plug-in point: abstract `xe::nui::NuiSystem`** (`nui/nui_system.h:39`, verified).
  - A backend calls the protected `PublishFrame(std::shared_ptr<DepthFrame>)`.
  - `DepthFrame` holds **`uint16` depth in millimetres**, 640×480, with 0 meaning no reading (`nui/depth_frame.h:33,44`, verified).
  - Optional overrides: tilt and gravity (`GetTiltDegrees`, `SetTiltDegrees`, `GetGravityVector`, `IsTiltMotorMoving`). The title's tracker uses gravity (fork `docs/nui.md:199`).
  - Backends: `kinect` (Kinect for Windows SDK 1.8, `Kinect10.dll` at runtime, depth only), `replay` (recorded depth, about 340 lines, the simplest template) and `nop`. Registered in `app/xenia_main.cc:494-510`.
- **Colour is a constant black image** (`nui_camera.cc:874`, verified). Mic array, `McaDeviceRequest` and `DetroitDeviceRequest` aren't implemented.
- **Evidence:**
  - Its docs claim only title `4D5308ED`: boots, tracks a player, plays all five activities, which matches Kinect Adventures. The obstacle activity needs an external patch.
  - Comments on #1290 also mention Dance Central 3 scoring, and one user reports about 15 fps on an RTX 3050.
  - No logs are committed.
- **Overlap with knuckleslee:** it has its own LDI and XamContentOpenFile. Its GPU resolve change infers the rectangle from destination extents (`gpu/draw_util.cc:1394-1420`, marked TODO) instead of #1280's CPU vertex-shader approach. Upstream will have to reconcile the two.

**Built and tested here (2026-11-03):**
- **Build:** release with `--build-tests`, 22 minutes at 6 parallel jobs. 16 jobs ran the compiler out of heap space (C1060).
- **One local build-script fix:** `xenia-build.py` calls `tools/vswhere/vswhere.exe` with forward slashes, which `CreateProcess` can't run. Upstream uses backslashes. Patched in our ignored clone only.
- **Tests:** `xenia-nui-tests` all passed (81 cases, 37,142 assertions), covering depth conversion, `NuiSystem` publishing and recording, the replay backend and tilt. `xenia-kernel-tests` 178 cases and `xenia-gpu-tests` 17 cases also passed.
- **Independent check:** recomputed in Python, the code-14 parameters give raw 2000 → 4000 mm and 3999 → 399.4 mm, as documented.
- **Replay backend:** `--nui=replay --nui_replay=<file.xdepth>` plays a recorded host depth stream into the guest. `tools/phase0/xdepth_writer.py` writes that format, and its self-test passes.
  - This means the Python pipeline can drive Option A′ on a real title with no C++: webcam → synthetic mm depth → `.xdepth` → replay.
  - **Runtime check passed, with no game loaded.** Command: `xenia_canary.exe --nui=replay --nui_replay=demo.xdepth --flush_log=true`, using `xdepth_writer.py --demo`, 90 frames.
    - Setup ran through Memory, Processor, Graphics, HID, VFS and Kernel.
    - The NUI lines appeared in order: `NUI: system Depth replay ready`, then device status initializing → ready, which notifies the guest. Next came `NUI: replaying ...demo.xdepth (90 frames)` and `NUI: 300 depth frames replayed`, so the file loops.
    - Whether a title's tracker accepts these frames still needs a game image.
- **Its encoding differs from #1274's libfreenect curve.** Details and the tracker's acceptance limits (player at 1800..2920 mm and so on) are in [PSCAM_CONTRACT.md §8](PSCAM_CONTRACT.md).

**Consequence for us:** a webcam backend fits this design as one `NuiSystem` subclass that publishes synthetic depth in millimetres plus gravity. The fork handles the guest LUT, PsCam state machine and ring buffers. This is the shortest route to Option A′.

## 4. weronika-saturday/xenia-canary: why it "compiles but produces no frames"

- The host side is fine: Kinect for Windows SDK 1.8 (`Kinect10.dll`) on Windows, OpenNI2/NiTE2 elsewhere, and a synthetic T-pose fallback. It maps NiTE2's 15 joints to the 20 Kinect joints.
- **Nothing reaches the guest.**
  - It adds a host `NuiSkeletonGetNextFrame`, but the 360 XAM table has no such export, so the title's statically linked SDK never calls it.
  - Its `PsCamDeviceRequest` / `McaDeviceRequest` / `DetroitDeviceRequest` return **success with 0 bytes**. The title believes the camera opened, then never gets a frame. This is the "lying stub" the prompt bans.
- Lessons: the delivery point must be `PsCamDeviceRequest` or the title's own linked SDK function. Never report success without data.

## 5. BrinooTk/PS5X360 (MIT; bundles GPL-3 parts, so check per file before reuse)

This is a Xenia port to PS5, with experimental Kinect from a phone or PC webcam over a relay. Target: Dragon Ball Z for Kinect (`4E4D084E`). Source: `docs/KINECT-INTEGRATION.md`.

**Confirmed by tracing the title:**
- It imports `PsCamDeviceRequest` and contains the Kinect SDK statically linked.
- Init needs `XMsgInProcessCall(0xFE, 0x0002C009)` accepted. After that, the title configures the camera (requests 27, 35, 24, 33, 13, 14, 25) and reads depth and colour with request 5.
- Its body tracking runs **on the guest GPU**:
  - depth as a 320×240 `k_16` texture
  - decision-tree tables as 8192-wide 1D textures
  - 160×120 `k_16_16` targets
  - eight resolves with copy command 2 plus a 600-rectangle copy draw per sensor frame
  - This failed in their Xenia base; it's what #1264, #1266 and #1280 fix upstream.

**Their decision was the skeleton-level adapter (our Option B):**
- Find the title's `NuiSkeletonGetNextFrame` by an opcode signature: `mflr r12` prolog, `li r5,0xAB0`, `cmplwi r3,0x1F40`, `mulli ..,-10000`, `ori ..,0x48F`. Exactly one match is required.
- Bind it to a host handler.
- **Frame layout:** `NUI_SKELETON_FRAME` is 0xAB0 bytes, big-endian.
  - Header: timestamp at +0x00, frame number at +0x08, floor clip plane at +0x10, normal-to-gravity at +0x20.
  - Six 0x1C0-byte skeletons from +0x30. Within a skeleton: state +0x00, tracking ID +0x04, enrolment index +0x08 (−1 if unknown), user index +0x0C (0xFE = none), position +0x10, 20 joints × 16 B from +0x20, 20 joint states × 4 B from +0x160.
- **Result:** the title took 1,381 frames with a body in 240 s. Player acceptance and gameplay are **not** confirmed.
- **Their open issues, which our pipeline already covers:**
  - The pose is hip-relative, so the body never moves in the room. We do metric placement from height calibration.
  - Only one skeleton. We track two, with stable slots.
  - Colour is grey. We pass the webcam's colour.
  - Their depth z sign is suspect.
- **Warning from them:** titles where Kinect is optional (Forza Horizon) hung when camera motion was enabled. Kinect must be opt-in per title.

## What we take from this

1. The **`PsCamDeviceRequest` virtual sensor is needed on both paths.** Titles read depth and player pixels even when the skeleton is injected (5345084D steering, DBZ image streams). So the first guest-side component is a PsCam driver implementing the #1274 contract, fed by our Virtual Kinect instead of USB.
2. **Depth must be served as raw 11-bit Kinect disparity, not millimetres.** The virtual sensor converts its synthetic mm depth with the inverse of the table it reports on code 24. **Colour must be re-mosaiced to Bayer.** Both are new requirements for Phase 3.
3. **Option A′** (the title's own tracker on our synthetic depth) is now feasible inside Xenia **once #1280 lands**. The risk that remains is whether the title's decision forest accepts synthetic depth.
4. **Option B** (PS5X360's signature hook of the title's `NuiSkeletonGetNextFrame`) is the per-title fallback, and its frame layout is documented above.
5. **Coordinate with upstream rather than fork away.** Rebase on knuckleslee's and ryantimjohn's PRs as they land, and contribute the webcam backend as a `SensorSource` behind the PsCam driver.
