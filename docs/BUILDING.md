# Building the Xenia fork

KinectEmu's emulator changes are a few commits on top of Xenia Canary. Build the fork once, then use [scripts/run_live.ps1](../scripts/run_live.ps1) and [scripts/run_harness.ps1](../scripts/run_harness.ps1), which expect the result at:

```
external\ryantimjohn-xenia\build\bin\Windows\Release\xenia_canary.exe
```

`external\` is git-ignored. A full build took about 15 minutes on the test laptop (i7-12650H).

## Where the code is

- Fork repository: <https://github.com/KevinPratap/xenia-canary>
  - `kinectemu-pipe-backend`: the depth pipe backend (`--nui=pipe`), commit `d0c7335`.
  - `kinectemu-skeleton-inject`: skeleton injection, commits `a31aa3f` and `d4cabac`. This branch is cut from the one above, so it contains both. **Use this branch.**
- Base: [ryantimjohn/xenia-canary](https://github.com/ryantimjohn/xenia-canary) branch `add-kinect-support` @ `8907f69`, which tracks [xenia-canary/xenia-canary](https://github.com/xenia-canary/xenia-canary) `canary_experimental`.
- The same commits are mirrored in this repository as git patches: [xenia/patches/](../xenia/patches/), described in [xenia/README.md](../xenia/README.md).

## 1. Install the toolchain

Needs Windows 11 x64. The versions below are what built the project on 2026-11-03 (from [STATUS.md](STATUS.md)):

```powershell
winget install --id Microsoft.VisualStudio.2022.Community --override "--add Microsoft.VisualStudio.Workload.NativeDesktop --includeRecommended --passive"
winget install --id LLVM.LLVM
winget install --id KhronosGroup.VulkanSDK
```

What you need, whichever way you install it:

| Part | Used for | Tested version |
|---|---|---|
| Visual Studio 2022 with the "Desktop development with C++" workload | MSVC x64, CMake, Windows SDK | 17.14, SDK 10.0.26100 |
| LLVM | `clang-format` (the build script checks style) | 23.1.4 |
| Vulkan SDK at `C:\VulkanSDK` | compiling shaders | 1.4.363.0 |
| Python 3 and Git | the build script, submodules | Python 3.11 |

**Vulkan SDK gotcha.** Xenia's own `building.md` calls the SDK optional. It is not: without it the build stops at `xenia-ui-vulkan_spirv.stamp` with a `FileNotFoundError`, because shader compilation needs the SDK's tools. Install it at `C:\VulkanSDK`.

If winget package IDs differ on your machine, search with `winget search <name>`.

Open a new terminal after installing, so PATH picks up the tools.

## 2. Clone the fork

From the repository root:

```powershell
git clone https://github.com/KevinPratap/xenia-canary.git external\ryantimjohn-xenia
cd external\ryantimjohn-xenia
git checkout kinectemu-skeleton-inject
git submodule update --init --depth 1 --recursive
```

The folder is named `ryantimjohn-xenia` because the scripts expect that path. To build from the base plus the patches instead of the fork, see "Applying the patches yourself" in [xenia/README.md](../xenia/README.md).

## 3. Generate and build

Still inside `external\ryantimjohn-xenia`:

```powershell
python xenia-build.py premake
python xenia-build.py build --config=release
```

Output: `build\bin\Windows\Release\xenia_canary.exe`. A good first launch logs the build line, for example `Build: canary_experimental@<commit>`.

## 4. First run: the disclaimer dialog

The first launch of any Xenia build shows a one-time disclaimer dialog (`emulator.cc:150`). Until it is answered, the emulator sits there and does nothing, which can look like a successful start. On the project's first build this cost a wrong "it works" note, later corrected in [STATUS.md](STATUS.md). Run `xenia_canary.exe` once by hand and answer the dialog before using the scripts. The harness and `run_live.ps1` cannot answer it for you.

## 5. Unit tests

The fork carries tests for the NUI code. They build with the rest:

```powershell
cd external\ryantimjohn-xenia\build\bin\Windows\Release
.\xenia-nui-tests.exe
```

All cases should pass. [xenia/README.md](../xenia/README.md) gives the count at the time of writing; it grows as tests are added.

## 6. Replay smoke test (no game, no camera)

This checks that the fork starts and that the depth backend delivers frames to the guest device. From the repository root, make a demo recording:

```powershell
tools\.venv\Scripts\python.exe tools\phase0\xdepth_writer.py --demo external\ryantimjohn-xenia\build\bin\Windows\Release\demo.xdepth
```

Then, from `external\ryantimjohn-xenia\build\bin\Windows\Release`:

```powershell
.\xenia_canary.exe --nui=replay --nui_replay=demo.xdepth --flush_log=true --log_file=replay.log
```

Expect `NUI: replaying ... (90 frames)` in `replay.log`. With no game loaded, STATUS.md records a later `NUI: 300 depth frames replayed` line from this kind of run on the fork.

## Xenia flags used by this project

| Flag | Meaning |
|---|---|
| `--nui=pipe` | Depth backend: Xenia serves a local named pipe and takes a live `.xdepth` stream from it. This is the live webcam path |
| `--nui_pipe=<name>` | Name of that pipe: `\\.\pipe\<name>`. Default used by the scripts: `kinectemu` |
| `--nui=replay` | Depth backend: play a recorded `.xdepth` file |
| `--nui_replay=<file>` | The file to play |
| `--nui_skeleton_inject=test` | Skeleton injection from a built-in test figure: 2.2 m away, sways, jumps |
| `--nui_skeleton_inject=pipe:<name>` | Skeleton injection from a second local pipe. 376-byte records, used while under 250 ms old; with no fresh record the game gets its own tracker's frames. Default name in the scripts: `kinectemu-skel` |
| `--nui_tilt_override=0` | Pin the virtual tilt at 0 degrees. The title kept moving the virtual tilt, which the synthetic depth ignores |
| `--nui_log_play_frames=N` | Log the title's skeleton and player mask every N frames. The harness uses this to grade |
| `--flush_log=true --log_file=<path>` | Write the log to a file and flush as it goes, so a crash or a killed process still leaves it |

The harness also passes `--mute=true`.

All of these exist only in the fork. Stock Xenia Canary does not know them.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Build stops at `xenia-ui-vulkan_spirv.stamp`, `FileNotFoundError` | Vulkan SDK missing. Install it at `C:\VulkanSDK` |
| `xenia_canary.exe` starts but nothing happens | First-run disclaimer dialog is waiting for an answer |
| `premake` or `build` cannot find Visual Studio | Install the C++ desktop workload; use a fresh terminal |
| Game starts, `NUI:` lines never appear in the log | Wrong build. Check you are running the fork build, not stock Canary |
| Skeleton injection warns that no site was found | The title's NUI runtime does not match the signature (only 2.0.11775 on one image of Kinect Adventures has been checked). See [NUI_IMPLEMENTATION.md](NUI_IMPLEMENTATION.md) section 4 |
