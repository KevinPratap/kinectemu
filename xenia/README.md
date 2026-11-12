# Xenia-side changes

KinectEmu's emulator code is a small set of commits on top of
[ryantimjohn/xenia-canary](https://github.com/ryantimjohn/xenia-canary), branch
`add-kinect-support` @ `8907f69` (which tracks
[xenia-canary/xenia-canary](https://github.com/xenia-canary/xenia-canary) `canary_experimental`
and adds the guest `PsCamDeviceRequest` depth path, `xe::nui::NuiSystem`).

The commits live on the fork branch `kinectemu-skeleton-inject` at
<https://github.com/KevinPratap/xenia-canary>. They are mirrored here as
`git format-patch` files so this repository is self-contained.

| Patch | What it adds |
|---|---|
| `0001-NUI-Add-pipe-backend-…` | `--nui=pipe --nui_pipe=<name>`: Xenia serves `\.\pipe\<name>` (local clients only) and takes a live `.xdepth` depth stream from it. `src/xenia/nui/pipe/`. |
| `0002-NUI-Skeleton-injection-at-the-title-runtime-s-frame-…` | `--nui_skeleton_inject=test`: finds the title runtime's skeleton-frame callback thunk by code signature (never by fixed address), binds it to the host and rewrites every `NUI_SKELETON_FRAME` before the title, HUD or logs see it. Includes the site finder, frame writer and 5 unit tests. `src/xenia/nui/nui_skeleton_site.*`, `nui_skeleton_inject.*`. |
| `0003-NUI-Skeleton-injection-from-a-local-pipe` | `--nui_skeleton_inject=pipe:<name>`: 376-byte `XNUISKL1` records (tracking id, 20 joints in metres with states, floor plane) from a second named pipe, injected while under 250 ms old. `src/xenia/nui/nui_skeleton_pipe.*`. |
| `0004-build-use-backslash-path-for-vswhere-…` | `xenia-build.py` fix so the build script finds `vswhere.exe` when run from cmd/PowerShell. |

Design notes and the evidence behind each step: [docs/NUI_IMPLEMENTATION.md](../docs/NUI_IMPLEMENTATION.md).
Build steps: [docs/BUILDING.md](../docs/BUILDING.md).

## Applying the patches yourself

```
git clone https://github.com/ryantimjohn/xenia-canary.git external/ryantimjohn-xenia
cd external/ryantimjohn-xenia
git checkout -b kinectemu 8907f69
git am ../../xenia/patches/*.patch
git submodule update --init --depth 1 --recursive
python xenia-build.py premake
python xenia-build.py build --config=release
```

Unit tests: `build\bin\Windows\Release\xenia-nui-tests.exe` (88 cases, 37,279 assertions pass as of 2026-11-12).

## Licence

These patches modify Xenia and are distributed under Xenia's BSD-3-Clause licence,
not this repository's MIT licence.
