# PsCamDeviceRequest contract (Kinect v1 camera interface)

Purpose: an implementation-ready spec of the Xbox 360 kernel export `PsCamDeviceRequest`
(xboxkrnl ordinal 0x30D) as implemented by xenia-canary PR #1274, plus the Kinect v1
disparity/registration math it relies on, so a *virtual* (webcam-driven, synthetic-depth)
sensor can be built against it.

## 0. Sources, citation scheme, provenance, licences

Sources read in full for this document:

- PR #1274 "[Kernel] Implement PsCamDeviceRequest with a Kinect v1 over USB" by knuckleslee
  (closed unmerged). Head commit `88e699bd49aff0ed52dc6b48e1db1eeaa2151352` in
  `knuckleslee/xenia-canary`. All PR line numbers below are line numbers of the file *at that
  head commit* (all PR files are newly added, so they equal diff line numbers).
- The PR description and the PR's overview comment (cited as `PR1274:description`).
- libfreenect, `master` at `09a1f098040d00e6070c18174904547ec31d2774` (fetched 2026-11-02).
  Line numbers are for that commit.
- One cross-check: `BrinooTk/PS5X360` at `0a178708ea2138413eea60eb7625496457455137`
  (cited as `PS5X360:`), only for a cautionary example in section 5.7.

Citation shorthands (the full paths are the ones in the PR):

| Shorthand | Full path |
|---|---|
| `PR1274:pscam.cc:N` | `PR1274:src/xenia/kernel/xboxkrnl/xboxkrnl_pscam.cc:N` |
| `PR1274:usb_win.cc:N` | `PR1274:src/xenia/hid/kinect/usb_camera_win.cc:N` |
| `PR1274:usb_camera.h:N` | `PR1274:src/xenia/hid/kinect/usb_camera.h:N` |
| `PR1274:xbox.h` | `PR1274:src/xenia/xbox.h` (adds `X_STATUS_DEVICE_NOT_CONNECTED` 0xC000009D and `X_STATUS_CANCELLED` 0xC0000120) |
| `libfreenect:registration.c:N` | `libfreenect:src/registration.c:N` |
| `libfreenect:cameras.c:N` | `libfreenect:src/cameras.c:N` |
| `libfreenect:registration.h:N` | `libfreenect:include/libfreenect_registration.h:N` |

Provenance legend used in tables:

- **T** = "worked out from the title's calls" (PR description: "the codes and reply formats were worked
  out from 5345084D's calls"). The title is 5345084D, a Kinect racing game steered by leaning. This is
  *observed title behaviour against a real Kinect over USB*, never a real console.
- **C** = chosen by the PR author (the PR says so explicitly, or the value is a host-side constant).
- **U** = the PR does not use or implement it; unknown.
- **(inferred)** = my inference from reading code, not stated in a source.

Important global caveat: **nothing in PR #1274 was measured on a real Xbox 360 console.** No console
trace exists in the PR. Everything is either title-observed behaviour (T), the author's choice (C), or
libfreenect behaviour of a real Kinect over USB. (`PR1274:description`, Open questions.)

Licences: Xenia is BSD-licensed (`PR1274:pscam.cc:6-7`, "Released under the BSD license");
libfreenect is Apache-2.0 (dual Apache-2.0/GPL-2.0, `libfreenect:registration.c:7-12`). The PR
reimplements libfreenect's math (`PR1274:pscam.cc:10-12`, `PR1274:usb_win.cc:10-11`). This document
describes both in its own words and reproduces only short formulas, field tables and constants. Anyone
re-implementing the formulas in code should retain the Apache-2.0 attribution for libfreenect
(Copyright (c) 2011 OpenKinect contributors) and the BSD notice for Xenia-derived material.

---

## 1. Function signature and request-block layout

### 1.1 Export

- Name `PsCamDeviceRequest`, xboxkrnl ordinal `0x30D`. The PR registers it with
  `DECLARE_XBOXKRNL_EXPORT1(PsCamDeviceRequest, kNone, kSketchy)` (`PR1274:pscam.cc:637-643`); the
  ordinal comes from xenia-canary master `src/xenia/kernel/xboxkrnl/xboxkrnl_table.inc:795`
  (`XE_EXPORT(xboxkrnl, 0x0000030D, PsCamDeviceRequest, kFunction)`), not from the PR diff.
- Signature as implemented: `X_STATUS PsCamDeviceRequest(void* request_block)`. One argument
  (guest pointer in r3), return value is an `X_STATUS` (`PR1274:pscam.cc:637-642`).
  A null pointer returns `X_STATUS_INVALID_PARAMETER` (0xC000000D) immediately.
- The request code is *not* a separate argument: it is the first field of the request block
  (big-endian u32 at +0x00) (`PR1274:pscam.cc:239`, `PR1274:pscam.cc:101-107`).

### 1.2 Common request header (0x18 bytes, all fields big-endian)

| Offset | Size | Field (PR name) | Meaning |
|---|---|---|---|
| +0x00 | u32 | `code` | request code, see section 2 |
| +0x04 | 3 x u32 | `unknown_04[3]` | not used by the PR |
| +0x10 | u32 | `context` | not used by the PR (presumably title context for its completion routine, (inferred)) |
| +0x14 | u32 | `completion_routine` | guest function address; 0 or 0xFFFFFFFF = none |

Source: `PR1274:pscam.cc:100-108` (`static_assert_size(X_PSCAM_REQUEST, 0x18)`).
Header provenance: T/C (layout "worked out from the title"; "context" naming is the author's).

Every request block must be at least 0x18 bytes and the first and last byte of the per-code size
(section 2) must be mapped guest memory, otherwise the call returns `X_STATUS_INVALID_PARAMETER`
(`PR1274:pscam.cc:235-242`, `PR1274:pscam.cc:323-361`). Provenance C ("The status codes ... for unmapped
request ranges ... were chosen here", `PR1274:description`).

Codes 0, 13, 14, 24 and 25 use a *different overlay* on the same block (an 8 to 0x10B0 byte struct
starting at +0x00 with `code`), and codes 14 and 25 overwrite +0x08 onwards, which overlaps the
`context`/`completion_routine` fields (`PR1274:pscam.cc:100`, comment "Codes 14 and 25 reuse +0x08
onwards for their replies"). Consequence: **the implementation must read `completion_routine` (+0x14)
before it writes any reply** (`PR1274:pscam.cc:243-247`, `PR1274:pscam.cc:185-186`,
`PR1274:description` "The completion routine and settings generation are taken when the request is
made, since some codes overwrite the request block").

### 1.3 Completion routine and async semantics

- A request with a completion routine is *queued*. The call returns `X_STATUS_PENDING` (0x103) and the
  completion routine is called later from the emulator's "Camera Worker" host thread
  (`PR1274:pscam.cc:211-220`, `PR1274:pscam.cc:307-311`).
- Calling convention of the completion routine (guest function executed on the worker's thread state):
  `completion(request_ptr, status, bytes)`, i.e. r3 = request block address, r4 = final `X_STATUS`,
  r5 = byte count (frame size for code 5, 0 otherwise). Source: `PR1274:pscam.cc:526-533`.
- For every code except 5, the handler *runs immediately in the caller's thread* (so reply fields are
  already written when `PsCamDeviceRequest` returns), and only the completion callback is deferred.
  Source: `PR1274:pscam.cc:280-311`. For code 5 nothing is written until the frame arrives.
- If there is no completion routine, the handler's status is returned directly (synchronous), except
  code 5 which requires a completion routine (`PR1274:pscam.cc:262`, `PR1274:pscam.cc:305-311`).
- The completion routine is not re-read from the block when it fires: the title may have reused or
  overwritten the block by then (`PR1274:pscam.cc:183-189`, `PR1274:pscam.cc:525`).
- Completions are delivered in this worker order each loop iteration: queued non-frame completions
  (with any cancelled frame reads first), then at most one depth read and one colour read
  (`PR1274:pscam.cc:539-589`). The worker wakes on new work or a new camera frame, with a 100 ms
  poll timeout (`PR1274:pscam.cc:88`, `PR1274:pscam.cc:545`).
- Whether the real console runs the routine at DPC/APC level, on which thread, or with what stack
  guarantees is unknown (U).

### 1.4 Status codes used

| Status | Value | Where | Provenance |
|---|---|---|---|
| `X_STATUS_SUCCESS` | 0x00000000 | normal result | T |
| `X_STATUS_PENDING` | 0x00000103 | queued request | T/C |
| `X_STATUS_INVALID_PARAMETER` | 0xC000000D | null/unmapped block, bad stream, missing completion on code 5, bad table size/range, too-small frame buffer | C |
| `X_STATUS_DEVICE_NOT_CONNECTED` | 0xC000009D | any code except 0 when no camera | C |
| `X_STATUS_CANCELLED` | 0xC0000120 | queued frame reads cancelled by code 2 | C |
| `X_STATUS_NOT_SUPPORTED` | 0xC00000BB | any code the PR does not handle (also logs an error) | C |

Sources: `PR1274:xbox.h`, `PR1274:pscam.cc:255`, `PR1274:pscam.cc:262-265`, `PR1274:pscam.cc:409-412`,
`PR1274:description` ("These three status codes were chosen here, the console's values aren't known").
(The PR text says "three" and lists DEVICE_NOT_CONNECTED, CANCELLED, INVALID_PARAMETER; NOT_SUPPORTED
is a fourth author choice, `PR1274:pscam.cc:409-412`.)

---

## 2. Request codes handled by the PR

The PR handles exactly these codes (`PR1274:pscam.cc:53-66`): 0, 2, 5, 13, 14, 15, 24, 25, 27, 33, 35, 36.
**Code 11 is mentioned in the PR description (older registration request used when code 13 reports a
version below 5.1) but is not implemented**: it falls into the default case and returns
`X_STATUS_NOT_SUPPORTED` (`PR1274:pscam.cc:409-412`). The PR avoids it by always reporting version 5.1 via
code 13. A virtual sensor must do the same (section 6).

Another project (PS5X360) reportedly saw Dragon Ball Z for Kinect issue 27, 35, 24, 33, 13, 14, 25 then
5 (task brief; not verified here). That ordering is consistent with the PR's setup description but
differs from the order listed in the PR description ("27, 14 (or 11) and 25"), so treat the init order as
title-dependent (section 3).

All reply fields are big-endian in guest memory. "Sync/async" below means: with completion routine
the call returns PENDING and calls the routine later; without it the status is returned directly
(section 1.3). `PR` column = what the PR returns when a camera is present.

| Code | PR name / purpose | Input layout | Output layout | Sync/async | PR returns | Provenance |
|---|---|---|---|---|---|---|
| 0 | `kGetState`: query device state. | `+0x00 code` | `+0x04 u32 state` (0 absent, 2 streaming, 4 ready) | Sync (handler); completion honoured only if camera present | SUCCESS | Layout T; "state 4 needed" T (`PR1274:description`); values 0 and 2 are the author's (C) |
| 2 | `kStop`: stop streaming. | header only | none. Cancels all queued code-5 reads (they complete with `X_STATUS_CANCELLED` via their completion routines, before the stop's own completion) | Handler sync; cancellations asynchronous | SUCCESS; PENDING if completion given | Behaviour "tested by forcing a stop" (PR description, T); cancel status and ordering C |
| 5 | `kReadFrame`: asynchronous depth/colour frame read. | see 2.1 | frame data into guest buffer, frame counter, colour settings byte | Always async | PENDING (INVALID_PARAMETER for bad stream/no completion/bad counter range) | Layout T; statuses C |
| 11 | older registration request (version < 5.1). | n/a | n/a | n/a | NOT_SUPPORTED (not implemented) | U |
| 13 | `kVersion`: driver version. | `+0x00 code` | `+0x04 u8 major` = 5, `+0x05 u8 minor` = 1 | Sync | SUCCESS | Version value C ("5.1 or later selects the registration request", `PR1274:pscam.cc:89-91`) |
| 14 | `kRegistration`: depth-to-colour registration data. | `+0x00 code` | see 2.3 | Sync (handler) | SUCCESS | Layout T; values come from camera + libfreenect |
| 15 | `kTimeSync`: time synchronisation. | layout unknown | none | Sync | SUCCESS (no-op) | U (comment in `PR1274:pscam.cc:177` only says time_sync 0 disables it) |
| 24 | `kDepthTable`: raw-disparity to millimetre table. | `+0x08 u32 table_ptr`, `+0x0C u32 entry_count` (must be 8192) | 8192 big-endian u16 at `table_ptr` | Sync | SUCCESS; INVALID_PARAMETER if count != 8192 or range unmapped | Layout T; bounds C |
| 25 | `kTiming`: frame timing. | `+0x00 code` | see 2.4 | Sync | SUCCESS | Layout T; period value C ((inferred) from 30 fps) |
| 27 | `kConfigure`. Purpose unknown. | not read by the PR | none | Sync | SUCCESS (no-op) | Needed for setup success (`PR1274:description`); layout U |
| 33 | `kUnknown33`. Purpose unknown. | not read | none | Sync | SUCCESS (no-op) | U |
| 35 | `kSetControls`: camera settings (exposure etc., (inferred)). | `+0x18` is output; input fields U | `+0x18 u32 settings_generation` | Sync handler; generation applied when its completion runs | SUCCESS | Layout T; settings not applied to sensor (`PR1274:description`) |
| 36 | `kGetControls`: read camera settings. | `+0x18` field | nothing written | Sync | SUCCESS (writes nothing) | U |
| other | any unlisted code | n/a | n/a | n/a | NOT_SUPPORTED | C |

Sources for the table: dispatch `PR1274:pscam.cc:366-414`; sizes `PR1274:pscam.cc:332-361`; structs
`PR1274:pscam.cc:110-181`.

Required request-block sizes (the PR validates these as mapped memory, `PR1274:pscam.cc:332-361`):
code 0: 0x18 (struct 8, clamped up to 0x18); code 5: 0x30; code 13: 0x18; code 14: 0x10B0; code 24: 0x18
(struct 0x10); code 25: 0x3C; codes 35 and 36: 0x1C; all others 0x18.

### 2.1 Code 5 (frame read) input layout, size 0x30

| Offset | Size | Field | Meaning |
|---|---|---|---|
| +0x00..0x17 | 0x18 | header | code = 5; `completion_routine` at +0x14 mandatory |
| +0x18 | u32 | `stream` | 2 = colour, 4 = depth; anything else is INVALID_PARAMETER |
| +0x1C | u32 | `buffer_ptr` | guest destination buffer |
| +0x20 | u32 | `buffer_size` | must be at least the frame size (614400 depth, 307200 colour) |
| +0x24 | u32 | `unknown_24` | not used |
| +0x28 | u32 | `frame_counter_ptr` | optional (0 = none); receives a big-endian u32 frame sequence number |
| +0x2C | u8 | `settings_generation` | OUT, colour frames only (section 3.3) |
| +0x2D | 3 x u8 | unknown | not used |

Source: `PR1274:pscam.cc:116-127`, `PR1274:pscam.cc:258-278`, `PR1274:pscam.cc:490-523`.

Semantics (all read from code):

- At call time only `stream`, the presence of a completion routine, and the range of `frame_counter_ptr`
  (4 bytes) are validated. The call then returns PENDING (`PR1274:pscam.cc:262-277`).
- Setting a read pending also marks the device as streaming (code 0 then reports 2)
  (`PR1274:pscam.cc:272`, `PR1274:pscam.cc:371-373`).
- Reads are queued per stream in FIFO order. For each stream the worker completes the front read only
  when the camera has a frame *newer than the last one delivered on that stream*; it copies the
  newest frame (older frames are dropped, never queued). A frame is never delivered twice
  (`PR1274:pscam.cc:490-499`, `PR1274:usb_win.cc:191-198`). A virtual sensor therefore must bump the
  sequence for each new frame.
- `buffer_size` and the buffer range are validated at *completion* time, not call time. If too small
  or unmapped, the read completes with `X_STATUS_INVALID_PARAMETER` and bytes = 0 (the frame is
  consumed) (`PR1274:pscam.cc:503-509`).
- On success: frame bytes written, then `frame_counter_ptr` (if non-zero) receives the host-side frame
  sequence (a u32 counter starting at 1, incremented per assembled frame, so gaps appear when frames are
  dropped, `PR1274:usb_win.cc:177-183`, `PR1274:pscam.cc:516-519`), then the completion routine is
  called with status SUCCESS and bytes = frame size (`PR1274:pscam.cc:520-521`).
- Depth and colour reads are independent; no pairing between a depth and a colour frame is enforced.
- Depth reads never write `settings_generation`.

### 2.2 Code 0 (get state) layout, 8 bytes

`+0x00 code`, `+0x04 u32 state` (OUT). State values and the state machine are in section 3
(`PR1274:pscam.cc:110-114`, `PR1274:pscam.cc:73-76`, `PR1274:pscam.cc:369-374`).

When no camera is present (cvar off, or camera cannot be opened) code 0 alone is answered
(state = 0, SUCCESS) and **its completion routine is not invoked even if one was supplied**; every other
code returns `X_STATUS_DEVICE_NOT_CONNECTED` without calling a completion routine
(`PR1274:pscam.cc:248-256`). The PR author notes the console's behaviour here is unknown
(`PR1274:pscam.cc:253-254`).

### 2.3 Code 14 (registration) reply layout, request size 0x10B0

Offsets are derived from the struct field order and sizes (they sum to the 0x10B0 asserted at
`PR1274:pscam.cc:159`; no padding assumed) (inferred from struct):

| Offset | Size | Field | Value written by the PR |
|---|---|---|---|
| +0x00 | u32 | `code` | (input) 14 |
| +0x04 | u32 | `unknown_04` | untouched |
| +0x08 | 29 x u32 | `registration[29]` (116 bytes) | the camera's registration record, each little-endian word from the device stored as a big-endian u32 (value preserved); see 5.3 |
| +0x7C | 0x100C bytes | `unknown_7C[0x100C]` | untouched (purpose unknown) |
| +0x1088 | f64 | `reference_distance` | camera zero-plane reference distance (mm) |
| +0x1090 | f64 | `reference_pixel_size` | camera zero-plane reference pixel size (mm) |
| +0x1098 | u16 | `frame_width` | 640 |
| +0x109A | u16 | `shift_to_depth_pixel_const` | 10 |
| +0x109C | 4 bytes | unknown | untouched |
| +0x10A0 | f64 | `shift_to_depth_const_offset` | 0.375 |
| +0x10A8 | f64 | `dcmos_rcmos_distance` | camera depth-to-RGB camera distance (see 5.4) |

Source: `PR1274:pscam.cc:143-159`, `PR1274:pscam.cc:416-432`. Comments in the struct: the shift parameters
are "used like libfreenect's freenect_init_depth_to_rgb"; for `frame_width`: "The title divides by this;
libfreenect's equivalent factor is 1" (`PR1274:pscam.cc:148-153`). Provenance: layout T. The `+0x7C`
region (4108 bytes) is not written by the PR, so it is presumably title-owned output space (inferred).

### 2.4 Code 25 (timing) reply layout, size 0x3C

| Offset | Size | Field | Value written by the PR |
|---|---|---|---|
| +0x00 | u32 | `code` | (input) 25 |
| +0x04 | u32 | `depth_frame_period` | 33333 (microseconds) |
| +0x08 | 4 x u32 | unknown | 0 |
| +0x18 | u32 | `color_frame_period` | 33333 (microseconds) |
| +0x1C | 2 x u32 | unknown | 0 |
| +0x24 | u32 | `time_sync` | 0 ("0 disables time sync (code 15)") |
| +0x28 | 5 x u32 | unknown | 0 |

Source: `PR1274:pscam.cc:169-181`, `PR1274:pscam.cc:399-408`, `PR1274:pscam.cc:84-85`.
The 33333 us value is a 30 fps frame period; it is the author's constant (C).

---

## 3. Init sequence and state machine

### 3.1 Required init sequence (what the PR says the title needs)

From `PR1274:description`: setup requires

1. code 0 returning **state 4**;
2. success from code 27;
3. success from code 14 (or code 11 if code 13 reports a version below 5.1);
4. success from code 25.

Observed order of other titles' calls (PS5X360 project, not verified here): 27, 35, 24, 33, 13, 14, 25,
then repeated code 5 reads. The PR also implements 13, 15, 24, 33, 35 and 36 because the title issues
them (`PR1274:pscam.cc:53-66`). The exact order the PR's title uses is not stated in the PR. A virtual sensor
should accept any order and answer every listed code synchronously and consistently.

### 3.2 States (code 0)

| State | Value | When the PR reports it |
|---|---|---|
| Absent | 0 | no camera (cvar `allow_kinect_camera` false or open failed) |
| Streaming | 2 | after the first frame read has been queued, until code 2 |
| Ready | 4 | camera present and not streaming (initial) |

Source: `PR1274:pscam.cc:73-76`, `PR1274:pscam.cc:369-374`, `PR1274:pscam.cc:272`, `PR1274:pscam.cc:302`.
Only "state 4 is required for setup" is documented as title-driven (`PR1274:description`). Whether
the console reports 2 while streaming, or what other state values exist, is unknown (U).

### 3.3 Streaming start/stop

- **Start:** implicit. The first code 5 read puts the device in "streaming" (`PR1274:pscam.cc:272`). The
  host camera is already streaming from open time (`PR1274:usb_win.cc:255-274`); there is no
  per-code start/stop of the USB streams.
- **Stop (code 2):** under the PR mutex, all queued depth and colour reads are moved to the stop
  request's completion record, state returns to Ready, and a stop counter increments
  (`PR1274:pscam.cc:292-304`). The worker completes each such read with `X_STATUS_CANCELLED` (bytes 0),
  then the stop's own completion, so cancellations arrive asynchronously, even after a synchronous
  stop has returned (`PR1274:pscam.cc:560-568`; `PR1274:description` "Testing"). A read already taken out
  of the queue by the worker is cancelled too if the stop counter changed while it waited for a frame
  (`PR1274:pscam.cc:569-587`); one already being copied still completes normally. The host USB stream
  is not actually stopped (no API for it, `PR1274:usb_camera.h:36-56`).
- **Restart:** "a later frame read starts it again" (`PR1274:description`).
- The console's ordering of cancellations vs the stop's completion is unknown (`PR1274:description`).

### 3.4 Settings generation (colour frames)

Purpose per the PR: "5345084D drops frames older than settings it has recorded", i.e. colour frames
must carry a generation number at least as new as the last settings request the title made
(`PR1274:description`, `PR1274:pscam.cc:190`, `PR1274:pscam.cc:607-610`).

Algorithm as implemented:

1. Code 35 (set controls): increment `requested_generation_` (u8, wraps at 256) and write it as a
   big-endian u32 into the request block at +0x18 (`PR1274:pscam.cc:375-381`, `PR1274:pscam.cc:129-133`).
2. When that request's completion routine has run, `settings_generation_ = requested generation at
   post time` (`PR1274:pscam.cc:287-289`, `PR1274:pscam.cc:526-537`).
3. Each colour frame sets byte +0x2C of its code 5 request block to the current `settings_generation_`
   (`PR1274:pscam.cc:514`).

Edge cases from the code (inferred by reading): (a) if code 35 is issued *without* a completion routine,
the generation is never published (the update lives in the completion path, `PR1274:pscam.cc:305-308`,
`PR1274:pscam.cc:534-536`); (b) the generation published is the value at request time even if more
code 35 calls are issued before the first completion runs; (c) the settings the title sends are ignored
(`PR1274:pscam.cc:378`); (d) code 36 returns success and writes nothing.

---

## 4. Frame formats

### 4.1 Depth (stream 4)

| Property | Value | Source |
|---|---|---|
| Resolution | 640 x 480, row-major, top row first | `PR1274:pscam.cc:78-79` |
| Pixel type | big-endian u16 per pixel (guest endianness) | `PR1274:pscam.cc:486` |
| Frame size | 640*480*2 = **614400 bytes** | `PR1274:pscam.cc:80` |
| Meaningful bits | low 11 bits (raw disparity 0..2046); upper 5 bits are always 0 | `PR1274:pscam.cc:480` (mask 0x7FF) |
| No-depth value | **0** (the camera's 2047 is rewritten to 0) | `PR1274:pscam.cc:481-485`, `PR1274:pscam.cc:83` |
| Source packing | camera sends 11-bit samples packed MSB-first, 8 pixels per 11 bytes: 640*480*11/8 = 422400 bytes | `PR1274:usb_camera.h:38`, `libfreenect:registration.c:77-101`, `libfreenect:cameras.c:69` |

Notes:

- Raw 11-bit values are *disparity-like* (not millimetres); they become millimetres only through the
  code 24 table (section 5).
- **Player index: there are no player-index bits in this stream.** The PR masks the value to 11 bits
  and zero-fills the upper 5 (`PR1274:pscam.cc:480`); the real camera does not send any (the
  libfreenect depth modes are pure 11-bit values, `libfreenect:cameras.c:67-69`, `libfreenect:include/libfreenect.h:49-51`).
  Since the title tracked a player with this stream (`PR1274:description`: "the title tracks a player"
  and the skeleton is tracked by statically linked SDK code), player segmentation and skeleton tracking
  must be computed by the title itself from raw depth (inferred; the PR never writes player bits and the
  title tracks regardless).
- 0 vs 2047: the PR replaces 2047 with 0 because "5345084D takes the maximum of depth blocks"
  (`PR1274:description`, `PR1274:pscam.cc:481-482`), meaning a high raw value would dominate. What the real
  console driver returns is unknown (`PR1274:description`, Open questions).
- The title receives no per-frame header in the buffer; USB packet headers (magic 'R','B', flag byte,
  sequence, u32 timestamp) are consumed on the host (`PR1274:usb_win.cc:129-189`).
- The u32 packet timestamp is *discarded*. **No timestamp is delivered to the title.** The only
  per-frame metadata is the frame counter written via `frame_counter_ptr` (and the colour settings
  generation byte). The nominal period reported by code 25 is 33333 us (`PR1274:pscam.cc:84-85`).

### 4.2 Colour (stream 2)

| Property | Value | Source |
|---|---|---|
| Resolution | 640 x 480 | `PR1274:pscam.cc:78-79` |
| Format | raw Bayer, 8 bits per pixel, passed through unchanged (memcpy) | `PR1274:pscam.cc:81`, `PR1274:pscam.cc:513` |
| Frame size | 640*480 = **307200 bytes** | `PR1274:pscam.cc:81` |
| Bayer pattern | camera's native pattern. libfreenect documents it as rows `G R G R...` / `B G B G...`, i.e. **GRBG** (first row G,R; second row B,G) | `libfreenect:cameras.c:456-463` |
| Extra output | byte at request +0x2C = settings generation | `PR1274:pscam.cc:514` |
| Camera registers used | colour format Bayer (0x0C=0), 640x480 (0x0D=1), 30 fps (0x0E=0x1E), start (0x05=1) | `PR1274:usb_win.cc:267-271` |

Bayer order is documented by libfreenect for the real camera; the PR does not state it. A virtual sensor
should generate GRBG (inferred to be the order the title's debayer expects, because the PR passes the
camera bytes unchanged). The title's handling of mirroring is unknown; the PR leaves camera mirroring off
(registers 0x17 and 0x47 = 0, `PR1274:usb_win.cc:262`, `PR1274:usb_win.cc:271`).

### 4.3 USB packet/frame constants (real-camera side, for reference only)

Depth stream flag 0x70, colour 0x80; first packet flag +1, last packet flag +5; header 12 bytes (`magic[2]='R','B'`, pad,
flag, unk1, seq, unk2, unk3, u32 timestamp) (`PR1274:usb_win.cc:48-49`, `PR1274:usb_win.cc:76-78`,
`PR1274:usb_win.cc:129-139`; matches `libfreenect:cameras.c:76-85`). A virtual sensor does not need any
of this.

---

## 5. Calibration

The PR queries the real camera for its calibration and feeds libfreenect's formulas. A virtual sensor
must supply an *internally consistent* set: the same parameters must be used for the code 24 table, the
code 14 block, and the raw depth values it synthesises.

### 5.1 Camera-reported parameters (libfreenect names)

| Name (PR / libfreenect) | Meaning | Unit | How the real camera provides it |
|---|---|---|---|
| `dcmos_emitter_distance` / `dcmos_emitter_dist` | IR camera to IR emitter distance | cm | float at offset 94 of the fixed-params reply (command 0x04) |
| `dcmos_rcmos_distance` / `dcmos_rcmos_dist` | IR camera to RGB camera distance | cm | float at offset 98 |
| `reference_distance` | zero-plane distance (IR camera focal length per libfreenect's comment) | mm | float at offset 102 |
| `reference_pixel_size` | size of one pixel on the zero plane (for the 1280x1024 sensor) | mm | float at offset 106 |
| `const_shift` | constant raw-disparity offset | raw units | u16 at offset 2 of algorithm-parameter reply, param id 0x00 |
| registration record | 29 x i32 | see 5.3 | algorithm-parameter reply, param id 0x40, 116 bytes after a 2-byte prefix |

Sources: `PR1274:usb_win.cc:84-85`, `PR1274:usb_win.cc:424-456`, `PR1274:usb_camera.h:24-32`;
`libfreenect:cameras.c:664-682`, `libfreenect:cameras.c:769-792`, `libfreenect:cameras.c:794-833`,
`libfreenect:registration.h:90-96`. Units are from libfreenect's struct comments (`libfreenect:registration.h:92-95`).

### 5.2 Code 24: raw disparity to millimetres table

Layout: 8192 big-endian u16 entries at `table_ptr` (16384 bytes). `entry_count` in the request must equal
8192 exactly or the PR returns INVALID_PARAMETER (`PR1274:pscam.cc:161-167`, `PR1274:pscam.cc:434-441`,
`PR1274:pscam.cc:82`). Why 8192 (13 bits) rather than 2048 is unknown (section 7).

Content (the PR's loop, `PR1274:pscam.cc:445-464`):

- `table[0] = 0`, and `table[raw] = 0` for `raw >= 2047` (raw 2047 is "no depth").
- For `1 <= raw <= 2046`:

```
fixed_ref_x = (raw - 4*const_shift) / 4 - 0.375
metric      = fixed_ref_x * reference_pixel_size
value       = 10 * ( metric * reference_distance / (dcmos_emitter_distance - metric) + reference_distance )
table[raw]  = (value > 0 and value < 10000) ? (uint16) value (truncated) : 0
```

This is libfreenect's `freenect_raw_to_mm` (`libfreenect:registration.c:314-326`, with the constants
`parameter_coefficient = 4`, `shift_scale = 10`, `pixel_size_factor = 1` at `registration.c:315-317`,
and offset 0.375 = `S2D_CONST_OFFSET` at `registration.c:39`). The PR's constants are at `PR1274:pscam.cc:93-98`
(`kShiftToDepthConstOffset = 0.375`, `kParameterCoefficient = 4`, `kShiftScale = 10`,
`kMaxDepthMillimetres = 10000`). Differences from libfreenect: libfreenect's table has 2048 entries with
only raw 2047 set to "no value" (`registration.c:331-333`) and leaves raw 0 to the formula; the PR
forces raw 0 to 0 and values outside (0, 10000) to 0 (`PR1274:pscam.cc:446-462`).
Units: millimetres, 0 means no depth. Resolution of the mapping is non-uniform (with the suggested defaults of 5.5, one raw step is about 0.7 mm
at 0.5 m, 3 mm at 1 m, 12 mm at 2 m, 47 mm at 4 m and 190 mm at 8 m; computed here, see 5.6).

Note that this mapping is monotonic in raw: higher raw = farther, up to a vertical asymptote at
`raw_inf = 4 * (dcmos_emitter_distance / reference_pixel_size + 0.375 + const_shift)`; beyond that the
formula goes negative and the table holds 0 (computed from the formula; (inferred) behaviour at
`value <= 0`, which the PR code zeroes at `PR1274:pscam.cc:459`).

### 5.3 Code 14: registration data

The registration record (29 x i32, same order as libfreenect's `freenect_reg_info`):

| Index | Field | Index | Field | Index | Field |
|---|---|---|---|---|---|
| 0 | dx_center* | 10 | dy_start | 20 | dydy_start |
| 1 | ax | 11 | dx_beta_start | 21 | dxdxdx_start |
| 2 | bx | 12 | dy_beta_start | 22 | dydxdx_start |
| 3 | cx | 13 | rollout_blank* | 23 | dxdxdy_start |
| 4 | dx | 14 | rollout_size* | 24 | dydxdy_start |
| 5 | dx_start | 15 | dx_beta_inc | 25 | back_comp1* |
| 6 | ay | 16 | dy_beta_inc | 26 | dydydx_start |
| 7 | by | 17 | dxdx_start | 27 | back_comp2* |
| 8 | cy | 18 | dxdy_start | 28 | dydydy_start |
| 9 | dy | 19 | dydx_start | | |

(* "not used by mapping algorithm" per libfreenect.) Source: `libfreenect:registration.h:38-81`; the camera
reply is memcpy'd straight into this struct at `libfreenect:cameras.c:682`, and the PR stores the
same 116 bytes in the same order as 29 words (`PR1274:usb_win.cc:439-446`, `PR1274:pscam.cc:422-425`).

What libfreenect does with it (the title is stated to use it "like libfreenect", `PR1274:pscam.cc:148-149`;
whether the title's own algorithm is identical is (inferred)):

- Build per-pixel x/y shift tables by integrating the polynomial coefficients down rows and columns
  with fixed-point arithmetic (`libfreenect:registration.c:217-285`), then
  `registration_table[i] = (x + dx + 1, y + dy + 1)` with x scaled by 256, and out-of-image entries
  invalidated (`libfreenect:registration.c:287-312`; `DEPTH_X_OFFSET = DEPTH_Y_OFFSET = 1` at lines 49-50).
- A per-depth horizontal shift table (`depth_to_rgb_shift`, 10000 entries, one per millimetre) from
  `reference_distance`, `reference_pixel_size` and `dcmos_rcmos_distance` (`libfreenect:registration.c:59-74`):

```
x_scale                  = 1280 / 640 = 2
pixel_size               = 1 / (reference_pixel_size * x_scale * 10)
pixels_between_cmos      = dcmos_rcmos_distance * pixel_size * 10
reference_distance_px    = reference_distance * pixel_size * 10
for depth d in mm:  depth_px = d * pixel_size
    shift[d] = ( pixels_between_cmos * (depth_px - reference_distance_px) / depth_px + 0.375 ) * 256
```

  The final colour x for a depth pixel is `(registration_table.x + shift[depth_mm]) / 256` and y is
  `registration_table.y` (`libfreenect:registration.c:143-145`). The constants 10, 0.375 are the values
  the PR writes into the request at +0x109A and +0x10A0 (`PR1274:pscam.cc:94-95`, `PR1274:pscam.cc:429-430`).
- camera-to-world back-projection used by libfreenect: factor = `2 * reference_pixel_size * z_mm / reference_distance`;
  `x = (px - 320) * factor`, `y = (py - 240) * factor` (`libfreenect:registration.c:341-353`). Whether the
  title uses this is unknown, but it explains why `reference_distance` and `reference_pixel_size` are
  passed to the title.

The PR writes `reference_distance`, `reference_pixel_size`, `dcmos_rcmos_distance` as doubles converted
from the camera's floats (`PR1274:pscam.cc:426-431`). It does *not* give the title `dcmos_emitter_distance`
or `const_shift`; those only affect code 24.

libfreenect's registration padding info (`start_lines`, `end_lines`, `cropping_lines`, param id 0x41,
`libfreenect:cameras.c:742-767`) is not read or reported by the PR.

### 5.4 The 2.4 vs camera value for `dcmos_rcmos_distance`

libfreenect overrides the camera-reported RGB-to-IR distance with a hard-coded 2.4 cm ("OpenNI seems to use
a hardcoded value of 2.4 instead of 2.3 as reported by Kinect", `libfreenect:cameras.c:835-836`). The PR does
*not* apply that override: it forwards the raw camera value (`PR1274:usb_win.cc:433-434`,
`PR1274:pscam.cc:431`). So the PR's code 14 gives the camera's reported figure (about 2.3 per
libfreenect's comment, an observation from libfreenect, not from the PR).

### 5.5 Typical default values for a virtual sensor

libfreenect itself contains **no default values** for these parameters; they are always read from the
device (the only constant is the 2.4 override above, `libfreenect:cameras.c:836`). The PR hard-codes only
the constants listed in the table below. The numbers in the "suggested" column are *not* from the PR or
libfreenect: they are commonly reported Kinect v1 values from outside sources (inferred, from memory)
and a self-check I ran with the libfreenect formulas (section 5.6). Real devices vary slightly, notably
`const_shift`.

| Parameter | Source value | Suggested for virtual sensor | Notes |
|---|---|---|---|
| `reference_distance` (mm) | device-supplied | **120** | (inferred) typical OpenNI zero-plane distance |
| `reference_pixel_size` (mm) | device-supplied | **0.1042** | (inferred) typical zero-plane pixel size; gives fx = 575.8 px, HFOV 58.1 deg, VFOV 45.3 deg (computed here) |
| `dcmos_emitter_distance` (cm) | device-supplied | **7.5** | (inferred) typical |
| `dcmos_rcmos_distance` (cm) | device-supplied (about 2.3); libfreenect forces 2.4 | **2.4**, or **0** if colour is already pixel-aligned to depth | see 5.7 |
| `const_shift` | device-supplied u16 | **about 200** (device-specific) | places the 1/z singularity near raw 1089; (inferred) |
| `shift_to_depth_pixel_const` | PR hard-coded | 10 | `PR1274:pscam.cc:94` |
| `shift_to_depth_const_offset` | PR hard-coded | 0.375 | `PR1274:pscam.cc:95` |
| `frame_width` (+0x1098) | PR hard-coded | 640 | `PR1274:pscam.cc:78`, `PR1274:pscam.cc:428` |
| `kParameterCoefficient` | PR / libfreenect | 4 | `PR1274:pscam.cc:96`, `libfreenect:registration.c:315` |
| `kShiftScale` | PR / libfreenect | 10 | `PR1274:pscam.cc:97`, `libfreenect:registration.c:316` |
| max depth (mm) | PR / libfreenect | 10000 | `PR1274:pscam.cc:98`, `libfreenect:include/libfreenect.h:45` |
| raw no-depth | camera | 2047 (PR passes 0 to title) | `PR1274:pscam.cc:83`, `PR1274:pscam.cc:481-485` |
| driver version (code 13) | PR hard-coded | 5.1 | `PR1274:pscam.cc:90-91` |
| frame period (code 25) | PR hard-coded | 33333 us | `PR1274:pscam.cc:85` |

Warning: the unit comments in libfreenect (emitter distance "in cm", reference distance "in mm") do not look
dimensionally consistent with the formula, which mixes `metric` (mm-scale) with the emitter distance. The
formula works as written with the typical values (section 5.6, matching real-sensor depth readings to
about 5 percent), so use the formula literally and do not "fix" units.

### 5.6 Inverse: desired depth in mm to raw 11-bit disparity

Derivation from the forward formula in 5.2. Let `Z = depth_mm / 10`, `R = reference_distance`,
`D = dcmos_emitter_distance`, `s = reference_pixel_size`, `c = const_shift`.
Forward: `Z = metric*R/(D - metric) + R = R*D/(D - metric)`. Solving for `metric`:

```
metric = (Z - R) * D / Z
raw    = 4 * ( metric / s + 0.375 + c )        # then round to nearest integer, clamp to 1..2046
```

(Derived by me from `libfreenect:registration.c:320-326`; equivalent forms: `raw = 4*c + 4*(metric/s + 0.375)`.)
Rules for a synthesiser:

1. Compute `raw` per pixel, round to nearest. The forward table truncates, so verify with
   `table[raw]`; a +/- 1 raw rounding error is harmless.
2. Valid depths are those with `0 < depth < 10000` mm (the table range) and `raw` in [1, 2046]. Anything
   else (no measurement, occlusion holes, too near/far) must be written as **0** (the PR/title convention,
   section 4.1).
3. `raw` increases with depth and diverges at `raw_inf = 4*(D/s + 0.375 + c)`. With the suggested defaults
   `raw_inf = 1089.4`.
4. Use the same `R, D, s, c` for the table (code 24) and for this inverse.

Computed sanity table with `R=120, s=0.1042, D=7.5, c=200` (computed here using the formulas above; forward
column is `table[raw]` after truncation):

| depth mm | raw (rounded) | table[raw] mm |
|---|---|---|
| 400 | 226 | 400 |
| 500 | 398 | 499 |
| 800 | 658 | 800 |
| 1000 | 744 | 1000 |
| 1500 | 859 | 1499 |
| 2000 | 917 | 2003 |
| 3000 | 974 | 2993 |
| 4000 | 1003 | 3998 |
| 6000 | 1032 | 6018 |
| 8000 | 1046 | 7959 |

Quantisation is therefore coarse at range (about 47 mm per raw step at 4 m, about 190 mm at 8 m; computed). Raw 226 for 400 mm is lower than a
physical Kinect (which starts near 0.5 m at raw about 430, (inferred) from commonly quoted fits; these differ by
the device `const_shift`); it does not matter as long as table and inverse agree. Cross-check: compared with
the widely used empirical fit `1000/(-0.0030711*raw + 3.3309)` (from memory, (inferred)) the formula
with these defaults agrees to within about 5 percent (for example raw 1000: 3864 vs 3848 mm).

### 5.7 Pitfalls and a registration-neutral setup

- Pitfall example: the PS5X360 virtual camera uses `const_shift=200`, `reference_distance=120`,
  `reference_pixel_size=120/1050`, `dcmos_emitter_distance=7.5` but converts metres to raw as
  `metres*250` (`PS5X360:include/xbox360ps5/virtual_depth_camera.hpp:13-15`,
  `PS5X360:include/xbox360ps5/virtual_depth_camera.hpp:24-26`). With libfreenect's formula those raw values
  map to 386 mm for 1 m, 558 mm for 2 m and 1003 mm for 3 m (computed here), i.e. the synthesiser and the table
  would not agree unless the table is generated from a different mapping. Always derive raw values with the inverse in 5.6.
- A registration-neutral calibration, if the synthetic colour image is already pixel-aligned with the
  synthetic depth (inferred; assumes the title's math mirrors libfreenect): set all 29 registration words to 0
  and `dcmos_rcmos_distance = 0`. Then in libfreenect's math all dx/dy shift tables are 0, so
  `registration_table = (x+1, y+1)` (`libfreenect:registration.c:300-307`) and the depth-dependent shift is the
  constant `0.375*256 = 96` (`libfreenect:registration.c:72`), giving colour pixel `(x+1, y+1)` for depth pixel
  `(x, y)`, i.e. an identity mapping with the library's fixed 1-pixel offset. Whether the title tolerates this
  (e.g. it may reject all-zero registration or divide by a zero distance) is untested (U).

---

## 6. Implications for a virtual sensor (checklist)

A synthetic sensor must, to satisfy this contract (all items from sections 1 to 5):

1. Implement `PsCamDeviceRequest(request_ptr)` (ordinal 0x30D): single pointer arg in r3; read the code from
   the big-endian u32 at +0x00; return an `X_STATUS`.
2. Capture `completion_routine` (+0x14; 0 and 0xFFFFFFFF mean none) *before* writing any reply, because
   codes 14/25 overwrite it. Validate that the request block range for the code is mapped.
3. For requests with a completion routine: perform the handler synchronously, return `X_STATUS_PENDING`, and
   later invoke the routine as `completion(request_ptr, status, bytes)` on a host thread that can run guest code.
4. Answer code 0 with state 4 until a frame read is queued, then 2 (and back to 4 after code 2). Do not
   report 0 (absent).
5. Answer code 13 with major.minor of at least 5.1 (use 5.1), so the title uses code 14 and never code 11
   (which is unimplemented).
6. Answer codes 27, 33, 15 and 2 with success and no side effects beyond the stop semantics; answer 36 with
   success; handle 35 by writing an incrementing u8 generation (as a BE u32) at +0x18 and publishing it
   after the completion routine runs.
7. Answer code 25 with 33333 us (30 fps) depth and colour periods and time sync 0.
8. Answer code 24 with exactly 8192 BE u16 entries: 0 for raw 0 and raw >= 2047; libfreenect `raw_to_mm`
   (5.2) otherwise, 0 if the value is outside (0, 10000). Reject `entry_count != 8192`.
9. Answer code 14 with: 29 BE i32 registration words (zeros are acceptable, see 5.7), reference distance and
   pixel size (BE f64), `frame_width = 640`, shift constant 10 (BE u16), offset 0.375 (BE f64), RGB-IR
   distance (BE f64), at the offsets in 2.3. Keep these identical to what code 24 and the synthetic depth use.
10. Produce depth frames at about 30 fps: 640x480, BE u16 per pixel, low 11 bits = raw disparity from the
    inverse formula (5.6), **0 for no-depth**, no player-index bits, 614400 bytes.
11. Produce colour frames at about 30 fps: 640x480 8-bit raw Bayer (GRBG), 307200 bytes. A webcam frame
    must be resampled to 640x480 and mosaiced to GRBG; the title debayers.
12. Handle code 5 as queued reads per stream (FIFO). Complete a read only when a frame newer than the last one
    delivered on that stream exists; write the BE u32 frame counter to `frame_counter_ptr` if non-zero;
    write the settings-generation byte at +0x2C for colour reads; validate `buffer_size` (614400 or
    307200 minimum) and range; call the completion routine with the frame size.
13. On code 2: cancel all queued reads with `X_STATUS_CANCELLED` (bytes 0) via their completion routines.
14. Return `X_STATUS_NOT_SUPPORTED` for unknown codes; keep a log of every unseen code and request block (the
    PR logs "unsupported code").
15. Keep depth and colour frame counters strictly increasing per delivered frame so the title never sees the
    same frame twice.
16. Remember that nothing here supplies skeleton or player data: the title's own SDK derives it from the
    synthetic depth, so the synthetic depth must look like a plausible human silhouette at plausible
    distances (see section 7).

---

## 7. Open questions and unknowns

1. **No console ground truth.** Statuses for absent camera, cancelled reads and unmapped ranges, the
   cancel ordering relative to the stop completion, and whether the console validates request ranges
   at all are unknown (`PR1274:description`, Open questions).
2. Console value for "no depth": 0 (PR) vs 2047 (camera). The title takes the maximum over depth blocks
   (`PR1274:description`); a different driver value could change its behaviour.
3. Why `entry_count` for code 24 is 8192 when only raw 0..2046 are meaningful. A 13-bit index suggests
   the title's depth values may carry more than 11 bits on the console, e.g. extra bits (player index?)
   (inferred, speculative; the PR writes only 11-bit values and the title tracks fine).
4. Purpose and input layout of codes 27, 33 and 15, and the unwritten fields of code 36; the PR ignores
   all of them and still passes setup. Input fields of code 35 (what exposure/white balance values the
   title sends) are not decoded.
5. What lives in the 4108-byte `unknown_7C` region of the code 14 block, the unused header fields
   (`unknown_04[3]`, `context`), `unknown_24` of code 5, and the 4 bytes at +0x109C.
6. Code 11 (older registration request) layout and when a title uses it; not implemented.
7. Why `frame_width` (+0x1098) is set to 640 while the comment says libfreenect's equivalent factor is 1
   (`PR1274:pscam.cc:152-153`); what the title does with it.
8. Whether the title's registration code matches libfreenect exactly (including the registration padding
   lines, which the PR does not provide) and whether all-zero registration is accepted (5.7).
9. Whether the title uses the colour image for anything that depends on registration (the PR does not say
   whether skeleton tracking needs colour); how strict the title's tracker is
   about silhouette realism, depth noise, 11-bit range, hole patterns, frame timing and depth/colour sync.
   Skeleton tracking success was shown only with a real Kinect (`PR1274:description`).
10. Whether DPC/APC context and thread-affinity of the completion routine matter to titles
    (the PR runs it on a host thread inside the system process, `PR1274:pscam.cc:211-217`).
11. Tilt motor, accelerometer, audio and NUI identity are *not* part of this export (the overview comment lists
    them as separate work: "Tilt motor and accelerometer: a level, stationary camera is reported" via a
    separate XAM PR #1277, `PR1274:description`).
12. Title-level order of init requests: the PR description (27, 14/11, 25) and the PS5X360 observation
    (27, 35, 24, 33, 13, 14, 25, 5) agree on membership but not necessarily on a hard dependency order.
13. Real-device `const_shift`, zero-plane values and registration words are not published in the sources read; the
    defaults in 5.5 are plausible rather than authoritative.
