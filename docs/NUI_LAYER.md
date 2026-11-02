# Phase 0-N — NUI layer

Status: **partial.** The Xenia side and prior art are done. Per-title XEX inspection of our four targets is blocked: it needs the titles' XEX files, from images of discs the user owns.

## Where frames enter a Kinect title (from the Xenia export tables and prior art)

```
title code ──► statically linked Kinect SDK (NuiApi) ──► xboxkrnl PsCamDeviceRequest  (depth + colour frames, setup, calibration)
                      │                               ├► xboxkrnl McaDeviceRequest    (mic array)
                      │                               └► xboxkrnl DetroitDeviceRequest (tilt motor)
                      ├─ skeleton tracking: runs in the title, on the guest GPU (decision forest)
                      └─ XAM XamNui* : HUD, identity (msg 0x2C009), tilt status, device status — no frame data
```

The evidence is in [PRIOR_ART.md](PRIOR_ART.md):
- Both titles traced so far, `4E4D084E` and `5345084D`, import `PsCamDeviceRequest`, link the SDK, and track on the guest GPU.
- XAM exports no frame-delivery function.

## Per-title answers

| Title | Title ID | Tracking runs in | Streams the title reads | Source |
|---|---|---|---|---|
| Dragon Ball Z for Kinect (not a target) | 4E4D084E | title code, guest GPU | skeleton + 3 image streams | PS5X360 trace |
| (Kinect racing title, name unverified; not a target) | 5345084D | title code, guest GPU | skeleton + depth player pixels (steering) | Canary PR #1274/#1280 traces |
| Dance Central 1 | ? | **needs XEX** | **needs XEX** | — |
| Kinect Adventures | ? | **needs XEX** | **needs XEX** | — |
| Kinect Joy Ride | ? | **needs XEX** | **needs XEX** | — |
| Kinect Sports | ? | **needs XEX** | **needs XEX** | — |

Per target title, check:
1. Imports of `PsCamDeviceRequest`, `McaDeviceRequest`, `DetroitDeviceRequest`, `XUsbcam*` and `XamNui*`.
2. Presence of the SDK, by running the PS5X360 `NuiSkeletonGetNextFrame` signature over the code section and requiring exactly one match.
3. The PsCam request codes seen in a Xenia trace.

## The guest contract and a host plug-in point now exist

- **[PSCAM_CONTRACT.md](PSCAM_CONTRACT.md)** is the request-by-request spec from PR #1274's code plus libfreenect's math. It covers depth as BE u16 with the low 11 bits set and no player-index bits, colour as 8-bit Bayer GRBG, and the code-24 table of 8192 entries. It also gives the inverse mm→raw formula, which I verified: exact at 0.8–1 m and ≤ 10 mm off at 3.5 m, the sensor's own quantization.
- **The ryantimjohn fork's `NuiSystem`** (PRIOR_ART §3) takes depth in **millimetres** from a host backend and does the guest-format conversion itself.
- **Neither path carries player-index bits.** The title's own tracker computes player pixels from depth, so our per-player masks matter only for Option B and for our own synthetic depth rendering.

## Preliminary injection decision (to confirm per title)

1. **Build the `PsCamDeviceRequest` virtual sensor first.** It implements the #1274 contract, fed by the Virtual Kinect:
   - depth as raw 11-bit disparity, with player pixels
   - colour as Bayer 640×480
   - calibration tables consistent with the synthetic depth

   Every path needs this component.
2. **Try Option A′ first:** the title's own tracker on synthetic depth. It needs upstream #1280, which is open, plus a local merge of it. There's no per-title tuning if it works.
3. **Fall back to Option B per title:** hook the title's `NuiSkeletonGetNextFrame`, found by signature, and serve the 0xAB0-byte frame from our tracker. Use this when the title's tracker rejects synthetic depth, or when A′ is too slow on the guest GPU.
4. Kinect stays **off unless enabled per title**. PS5X360 saw optional-Kinect titles hang when it was on.

Why A′ comes first: it reuses the title's own skeleton semantics (joint offsets, tracking states, enrolment), which Option B has to imitate by hand. The prompt's Phase 5 skeleton-fidelity work mostly disappears if A′ works.
