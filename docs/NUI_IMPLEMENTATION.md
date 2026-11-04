# NUI implementation: skeleton injection (Option B) in Kinect Adventures

Status, 2026-11-04: **the live webcam skeleton drives the game; the mirroring is fixed, and hands-free tests pass. A live feel-check of River Rush is still to do.**
- Xenia finds the skeleton-frame site in title `4D5308ED` by its code, not by fixed addresses. It then binds the site to the host and rewrites every skeleton frame before the title, the HUD or the logs see it.
- On a webcam-synthesised replay, the game accepted a synthetic test figure. It completed its play-space setup in 90 s. Without injection, the same replay was still stuck there.
- **Live (section 6):** `synth_depth.py --live --skeleton-pipe` sends the webcam player's skeleton to xenia every frame. On it alone, the game ran its calibration, play-space setup and guide-gesture tutorial and started River Rush.
- **Not done:** the player left the play space just as River Rush started, and the retry was closed after about 40 s. So steering and jumping on the injected skeleton haven't been checked.

Code: fork `external/ryantimjohn-xenia`, branch `kinectemu-skeleton-inject`:
- `a31aa3f`: the hook and the `test` source.
- `d4cabac`: the `pipe:<name>` source.

The branch is cut from `kinectemu-pipe-backend` @ `d0c7335` and is not pushed. The live source's other half is `tools/phase0/synth_depth.py` in this repo.

## 1. Which image this is

The user's disc and the fork's documentation are **different builds** of `4D5308ED`.

| | user's disc (`E:\Kinect Adventures!\Kinect Adventures!.iso`) | fork `docs/nui.md` |
|---|---|---|
| module hash | `FC152DDFE29E856E` | `FEF1C9FF272B3BD9` ("Rev 2") |
| NUI runtime | 2.0.11775.7 | 2.0.11775.7 |
| runtime state block `g` | `0x83896510` | `0x83896510` |
| 0x2B002 wrapper | `0x831ADFC8` | `0x831aef98` |

The runtime code and its data block agree in places. Much of the code around them is shifted by up to about `0x1000`. **So the fork's addresses can't be used on this disc.** Every address below is from the user's image, and section 4 says how each one is found without it.

How it was read:
- The decrypted image was dumped from guest memory by a throw-away local build. The dump was never committed and stays in the session scratchpad.
- Disassembly used capstone; the image was never modified.
- Runtime claims are marked **observed** (from a xenia run) or **read** (from disassembly).

## 2. Where the skeleton frame is made and handed out

### Data

`g` is the runtime state block at `0x83896510`.

| what | where | notes |
|---|---|---|
| frame slots | `g+0xA70`, two slots of `0xAE0` bytes | Each slot holds: `+0x000` state (1 = new frame, 2 = HUD has the player, 0 = consumed); `+0x020` `NUI_SKELETON_FRAME` (0xAB0 bytes); `+0xAD0` timebase at publish. |
| slot being written | `g+0xA5C` | Always `g+0xA60 ^ 1`. |
| slot published | `g+0xA60` | Set from `g+0xA5C` at publish. |
| "frame ready" events | `g+0xA44` (runtime), `g+0xA54` (title's `hNextSkeletonEvent`, if given) | Both signalled at publish. |
| optional frame callback | `g+0x28EC` = `0x83898DFC` | A function pointer. It was null in every run (**observed**), and no code in the image writes it (**read**). |
| HUD copy of the frame | `*(g+0x2168)`, 0xAB0 bytes | Copied after publish when `g+0x2174 == 1`. |
| tracking-id renumbering | `g+0x27AC` (base), `g+0x27B0` (max) | Each slot id is the tracker's raw id plus this base. |

### `NUI_SKELETON_FRAME` (0xAB0 bytes, big-endian)

Header (offsets from the frame start):

| offset | field |
|---|---|
| `+0x00` | u64 timestamp: the depth frame's stamp from PsCam code 5 |
| `+0x08` | u32 frame number |
| `+0x0C` | u32 flags (bit 1 set by the producer) |
| `+0x10` | floor clip plane: unit normal, then d in **metres** |
| `+0x20` | normal to gravity (from the tilt status block) |
| `+0x30` | six `NUI_SKELETON_DATA` of 0x1C0 bytes each |

Each `NUI_SKELETON_DATA` (offsets from the skeleton's start):

| offset | field |
|---|---|
| `+0x000` | state: 0 = not tracked, 1 = position only, 2 = tracked |
| `+0x004` | tracking id |
| `+0x008` | enrollment index |
| `+0x00C` | user index |
| `+0x010` | position |
| `+0x020` | 20 joints × float4 (x, y, z, 1), in metres |
| `+0x160` | 20 joint states (0 / 1 / 2) |
| `+0x1B0` | quality flags |

- Joint order is the public NUI order: hip centre, spine, shoulder centre, head, left arm ×4, right arm ×4, left leg ×4, right leg ×4.
- The enrollment and user index are written by the identity pass. On this disc that is −1 and `0xFE` in every slot (**observed**). This corrects the fork's comment that they are always 0.
- Space is the sensor's camera space: x toward a facing player's right hand, y up, z away from the sensor.

### Functions

| function | address | role |
|---|---|---|
| frame producer | `0x82D21F20` | Called per processed depth frame. See the sequence below. |
| skeletal tracker fit | `0x82D488A0` | Called from the producer. Outputs the tracker's skeletons on the stack, plus the floor vector. |
| fill skeleton slots | `0x82D25FE8` | Clears the six slots, then: <br>• position-only bodies → state 1, position, id + base<br>• two tracked players → state 2, 20 joints, joint states, quality |
| identity pass | `0x82D313F8` | Runs if `g+0x2148`. Writes enrollment/user index per slot (`stw 8(r31)`, `stw 0xC(r31)`, stride 0x1C0). |
| ETX log of the frame | `0x82D263E0` | `li 0xAB0`, `0x2DFF`. |
| **frame callback thunk** | **`0x82D263C0`** | `if (*(g+0x28EC)) (*(g+0x28EC))(frame)`. One caller: `0x82D222C8`. **This is the hook.** |
| per-frame joint statistics | `0x82D26958` | Counts tracked/inferred joints. |
| publish | `0x82D262A8` | Under critical section `0x8381E2DC`: sets `g+0xA60 = g+0xA5C`, then signals `g+0xA44` and `g+0xA54`. |
| HUD message | `0x82D34A70` → `0x82D34988` | Called at `0x82D22640`, later in the producer, with the same slot's frame. `0x82D34988` builds the 0xAE0-byte 0x2B002 buffer: `{5, 0x2DFF}`, a copy of the frame, the timestamp, and the depth and player-index texture pointers. |
| `NuiSkeletonGetNextFrame` | `0x82D26768` | Waits on `g+0xA44` (8000 ms cap), then under the critical section copies 0xAB0 bytes from slot `g+0xA60` and sets that slot's state to 0. Returns `0x8301000B` if the slot state is 2 (HUD took the player), else `0x8007048F` or `0x83010001`. |
| `NuiTransformSmooth` | `0x82D26190` | Holt smoothing over tracked slots, 0x500 bytes of state per slot. Default parameters 0.5 / 0.5 / 0.5 / 0.05 / 0.04. |
| `NuiImageStreamGetNextFrame` / `ReleaseFrame` | `0x82D28FB8` / `0x82D292C8` | |

The producer `0x82D21F20` runs these steps, in order, per frame:
1. Fit the skeletons (`0x82D488A0`).
2. Write the floor plane (`frame+0x10`, mm → m) and gravity (`0x82D2C490`).
3. Fill the slots (`0x82D25FE8`); write the timestamp, frame number and slot state.
4. Run identity (`0x82D313F8`), then the ETX log (`0x82D263E0`).
5. **Call the frame callback thunk (`0x82D263C0`).**
6. Count statistics (`0x82D26958`).
7. Publish (`0x82D262A8`).
8. Make the HUD copy, then the HUD message (`0x82D34A70`).

### What the game reads

- **Skeleton:** the Kinect manager (`0x82A55118`, object at `0x83AB9F90`) calls `NuiSkeletonGetNextFrame` from **one** call site, `0x82A5531C`, into its own copy at `+0x17D00`. It then calls `NuiTransformSmooth` (`0x82A558C0`).
- **Image streams:** it reads two image streams with `NuiImageStreamGetNextFrame` (`0x82A54308`, `0x82A54624`) and releases both each frame (`0x82A55914`). With `NuiInitialize` flags `0x849`, these are colour and depth plus player index.
- **Total:** 27 runtime functions between `0x82D23E30` and `0x82D2DB60` are called from game code. Among them, the two above are the frame readers identified so far. The list is in the session notes (`nui_runtime_calls.txt`).

## 3. What a hook replaces, and what the title still needs from depth

### What the hook replaces

**The hook replaces the thunk `0x82D263C0`.** It writes `sc 2; blr` at the thunk and puts a host extern on the function, the way xenia binds imports. Each frame, the handler:
1. Rewrites the six skeleton slots of the frame passed in `r3`:
   - The injected body goes in slot 0 as tracked, with a stable id.
   - The other slots are emptied.
   - Each slot keeps its enrollment and user index words.
2. Calls the runtime's callback if `g+0x28EC` is set.

Effects:
- **It runs before publish, so every consumer sees the same injected frame:** the game (through `NuiSkeletonGetNextFrame` and its smoothing), the HUD message 0x2B002, xenia's Kinect Guide recogniser and `--nui_log_play_frames`.
- **The runtime keeps its own timestamps, frame numbers, slot state (including the HUD-engaged state 2), events and pacing.**

**Why here and not at `NuiSkeletonGetNextFrame`, as PS5X360 did:**
- Replacing the getter would give the game one skeleton and the HUD/guide another.
- The host would also have to re-implement the getter's wait, its slot-state semantics and the `0x8301000B` "HUD has the player" answer.
- The thunk is the one point that sees the finished frame before anyone else.
- PS5X360's getter signature is still used, but only to find `g` and the slot geometry.

### What still comes from the depth path

1. **Depth frames must keep flowing.** The producer runs once per depth frame, so skeleton frames are paced by depth delivery. If no depth arrives, no skeleton frames are made. The live pipeline still needs `--nui=pipe` with depth, or some depth, at 30 Hz.
2. **Floor plane:** the tracker's fit to the depth image. The prototype leaves it alone.
   - Seen with the test figure: d varied 0.49–1.42 m and the normal tilted by up to 0.15. The synthetic floor in `synth_depth.py` is what it fits.
   - The hook could write the webcam pipeline's own floor plane instead (`frame+0x10`). Not done yet.
3. **Gravity vector:** from the tilt status block (xenia's accelerometer model), as before. It stays consistent with `--nui_tilt_override=0`.
4. **Player-index mask:** the 320×240 and 80×60 textures. They are still the tracker's segmentation, **numbered by the tracker's own slots**.
   - **Observed:** with the test figure in slot 0 (index 1), the mask held index 5, 3 or 1 from frame to frame.
   - The mask goes to the HUD (silhouette) and, through the depth-plus-player-index stream, to the game.
   - **Open:** whether gameplay matches mask pixels to skeleton slots, as photos or cut-outs would. If it does, the hook should put the injected body in the slot whose index dominates the mask, or the depth writer should be made to segment one player. Both are possible.
5. **Enrollment and user index:** the identity pass's values are kept. It runs on the tracker's bodies, so with injection it only ever saw "not recognised" (−1 / `0xFE`).
6. **The tracker keeps running.** Its GPU classification of each depth frame is wasted work once its skeletons are replaced. Starving it of depth would also stop the frames (point 1), so it stays.

## 4. Locating the site without addresses

Implemented in `src/xenia/nui/nui_skeleton_site.cc` (`FindSkeletonFrameSite`) and run once per title, at message 0x2B003. **Every step must match exactly once, or the title keeps its own skeletons and a warning is logged.**

**Step 1: find `NuiSkeletonGetNextFrame`** (PS5X360's signature). It is the function after an `mflr r12` prologue that has all four of:
- `li r5, 0xAB0`
- `cmplwi crN, r3, 0x1F40`
- `mulli rX, rY, -10000`
- `ori rX, rY, 0x48F`

Whole user image: **1 match** (`0x82D26768`). Nine other `li r5, 0xAB0` sites fail the rest.

**Step 2: read its constants** from the instructions:

| constant | read from | value |
|---|---|---|
| `g` | `lis rA, hi` + `addi rG, rA, lo` | `0x83896510` |
| ring offset and stride | the `addi rR, rG, ring` just before `mulli rI, rI, stride` | `0xA70`, `0xAE0` |
| frame offset in the slot | `addi r4, rS, off` after `li r5, 0xAB0` | `0x20` |

**Step 3: find the frame callback thunk.** It is the six-word sequence:
`lis rX, hi; lwz rY, lo(rX); cmplwi crN, rY, 0; beqlr crN; mtctr rY; bctr`
whose pointer lies within `g … g+0x10000`.

Whole image: **1 match**. That six-word thunk shape occurs only once in the image.

**Step 4: count calls.** Exactly one `bl` to the thunk: `0x82D222C8`, inside the producer.

**Step 5: check every frame at run time.** The handler only rewrites a frame whose address is one of the two slot frames (`g + 0xA70 + k·0xAE0 + 0x20`, k ∈ {0, 1}).

What this does and doesn't cover:
- It was written for runtime 2.0.11775 and checked on one image.
- Another Kinect title linking the same SDK should match steps 1–4 if its runtime has the same producer.
- That is untested. Our other targets (Dance Central, Joy Ride, Sports) need their XEX first, as in [NUI_LAYER.md](NUI_LAYER.md).

## 5. Prototype and evidence

The source is selected with `--nui_skeleton_inject`:
- `test`: a synthetic figure 2.2 m away that sways ±0.35 m over 6 s and jumps 0.25 m every 4 s.
- Empty (the default): off.

Unit tests: `xenia-nui-tests` pass (86 cases, 37 259 assertions; 5 new cases cover the finder and the frame writer).

Run setup:
- `Kinect Adventures!.iso`, `--nui=replay`, using a recording made by `synth_depth.py --fixture fixtures/fullbody_01 --push-mm 700`. The recording stays local.
- Flags: `--nui_tilt_override=0 --nui_log_play_frames=30`, 90–150 s per run.

Bind and play-frame log with `--nui_skeleton_inject=test` (**observed**):

```
NUI: skeleton injection (source "test"): NuiSkeletonGetNextFrame at 82D26768, runtime state at 83896510, frame slots at +A70 every AE0 with the frame at +20, frame callback thunk at 82D263C0 (pointer at 83898DFC) called from 82D222C8, now bound to the host.
NUI: skeleton injection frame 900 (runtime frame 1638): replaced tracker slots [0:1/402/-1/254 1:1/403/-1/254 2:0/0/-1/254 ...]
NUI: play frame 120: id 1 position (0.291, 0.106, 2.200) hip (0.291, 0.106, 2.200) head (0.291, 0.726, 2.200) hand L (-0.059, -0.014, 2.200) R (0.641, -0.014, 2.200) ...
NUI: play frame 120: mask 320x240 pitch 768: 0 pixels, no centroid
```

At play frame 120 the figure is mid-jump: hip y is 0.106 against 0.000 at rest.

| same replay, every 30th frame logged | without injection (one run, about 90 s) | `--nui_skeleton_inject=test` (two runs, about 100 s and 150 s) |
|---|---|---|
| logged frames with a tracked skeleton | 61 of 81 (84 skeleton lines; some frames had two) | 93 of 93 and 101 of 101 |
| distinct tracking ids | 25 (1, 59, 145, 172, …, 1065) | 1 |
| what the tracker had in the replaced frame (one sample about every 900 frames, 8 samples) | n/a | 5 samples empty; 3 had two position-only bodies (402/403, 655/670, 1247/1230); none had a tracked body |
| game screen at 90 s | "MOVE BACK AS FAR AS YOU CAN.", dwell ring nearly empty ([baseline](data/ka_baseline_90s_move_back.jpg)) | "YOU HAVE SELECTED: GOOD SPACE!" ([injected](data/ka_inject_test_90s_good_space.jpg)) |

**What this shows:** the game binds and keeps a player built entirely from an injected skeleton. It walks its play-space setup on its own.

**An open oddity:** with injection on, the tracker's own frame never held a tracked body in the 8 samples. Without injection, 61 of 81 logged frames did. The samples are few, and xenia did not feed anything back to the tracker. Whether the tracker or the game reacts to the published frame in a way that changes what the tracker finds is unknown. It doesn't matter while injection is on, but it would if injection were switched off mid-session.

**What this doesn't show yet:**
- **Gameplay control:** River Rush steering and jumping on an injected skeleton has not been tried.
- **Latency:** end-to-end latency has not been measured.

One injected run ended about 100 s in through xenia's normal window-close path (`Cheap-skate exit!` from `OnDestroy`). Nothing was logged before it, and the earlier 150 s injected run ran to the end. The cause wasn't determined.

## 6. Live webcam source

### How it works

```
xenia_canary.exe "<Kinect Adventures!.iso>" --nui=pipe --nui_pipe=kinectemu --nui_skeleton_inject=pipe:kinectemu-skel --nui_tilt_override=0
tools\.venv\Scripts\python.exe synth_depth.py --models ..\..\models --live --intrinsics ..\..\docs\data\intrinsics.json --push-mm 700 --skeleton-pipe kinectemu-skel
```

(Run the second from `tools\phase0`.)

**Two pipes.** Depth keeps flowing on `kinectemu`, because the producer runs once per depth frame and the game still reads the image streams. Each frame, `synth_depth.py` also writes one 376-byte record into `kinectemu-skel`:
- the magic `XNUISKL1`;
- a tracking id;
- 20 joints in metres, with their states;
- the floor plane.

The format is `nui_skeleton_pipe.h` in the fork.

**What xenia does with a record:**
- It drops any record whose body is not in front of the sensor (finite joints, 0.3–6 m away).
- It injects the newest record while it is under 250 ms old.
- With no fresh record, frames go to the game as its tracker made them.
- A record's floor plane replaces the tracker's.

**Joints:** 20 joints come from MediaPipe's 33 landmarks:
- hip centre: the mean of the two hips;
- spine: one third of the way up the trunk;
- shoulder centre: the mean of the two shoulders;
- head: between the ears;
- hands: the mean of wrist, pinky and index finger;
- feet: the foot-index landmarks.

A joint is tracked when its landmarks are visible, otherwise inferred.

**Axes, measured against the title:**
- **x:** the title's skeleton x grows with the column of the frames `synth_depth.py` writes (mirrored by default), so it is the webcam's x negated. Two measurements:
  - On the A′ replay, the title's own hip followed its player mask as `column ≈ 160 + 290·x/z` (correlation +0.89).
  - In the first live run, an injected skeleton that used the webcam's x unnegated followed the same mask with correlation −0.91, and the player saw everything mirrored. With x negated, the run's injected hips fall within 10 px of the mask centroid (85 px off before).
  - An earlier derivation from the fork's row reversal got this sign wrong. Don't re-derive it; the measurements stand.
- **y** is up, and **z** is moved back by the same push as the depth.
- **Same place as the depth:** over the fixture, the skeleton hip and the silhouette's position in the frame the title gets differ by a median of 4 mm (90% within 47 mm). With `--gain-x 1.4` it is 7 mm (90% within 64 mm). The gain's sideways shift moves both.

**Sides:** on our depth, the title's own tracker labelled as "right" the hand with the larger x in 97% of frames. With x negated, that is the player's anatomical right, so MediaPipe's names are used as they are. `--skeleton-sides` only matters with `--no-mirror`.

**Floor:** a level plane at the floor height `synth_depth.py` already estimates from the feet. It is the same plane its synthetic depth floor uses.

### First live test (2026-11-04)

The user played; the pipeline ran at 15–30 fps. Screens every 20 s are in [the contact sheet](data/ka_live_inject_sheet.jpg).

| time (pipeline clock) | what the webcam saw | what the game did |
|---|---|---|
| 0–40 s | the player within 0.7–1.2 m (depth clamped to 1.9 m) | calibration, "let's figure out how much room you have" |
| 45–130 s | the player standing about 2.2 m back, head 0.42 m above the camera, hip x +0.03…+0.19 m, one skeleton id | play-space token and dwell ring, guide-gesture tutorial, River Rush intro, the raft on the river (~140 s) |
| ~135 s on | the player at the laptop, 0.65–1.0 m. MediaPipe put the hip **above** the head (an unseen lower body), and the skeleton id changed twice | "GAME PAUSED", JOIN IN |
| retry | back at 2.2 m | "IDENTIFYING" and the hand cursor back on screen; then the xenia window was closed, about 40 s in |

**What it shows:**
- The game takes the injected live skeleton as its player through every setup screen and into River Rush.
- With the webcam player standing still, the skeleton was steady: one id for 90 s.
- The pause came from the player leaving the play space, not from the injection.

**What it doesn't show yet:** steering and jumping in River Rush, and latency.

**Fixed after the run** (in `synth_depth.py`, not yet run live):
- **Stable id:** the single player keeps one tracking id while anyone stays in view, even when the webcam tracker moves them to another slot. A new id comes only after more than `--hold-frames` frames with nobody worth sending.
- **Implausible bodies:** a body with its hip above its shoulder centre is not sent.

### Second live test: mirrored, and leaning did nothing

The user reported left and right mirrored, leaning doing nothing, and only jumps registering. The logs confirm the mirroring: the injected x had the wrong sign (see "Axes" above), now fixed. Leaning moved the head only about ±0.1 m, and steps moved the hip about 0.2 m, with the player standing about 0.2 m off the camera's axis. `--gain-x 1.4` (from A′) recentres the player's usual spot and amplifies sideways moves. Depth and skeleton move together.

### Hands-free tests

So the user doesn't have to stand up for every change, `tools/phase0/skeleton_harness.py` runs the title in xenia on a source that needs no camera. It takes a screenshot every second, then grades the title's logs and prints PASS/FAIL. The sources:
- `--case synthetic`: `scripted_skeleton.py`, a scripted figure with exact ground truth. Its synthetic depth and matching skeleton go into the same two pipes. After 60 s standing still, every 30 s it does this:
  - reaches the right arm forward and holds it 4 s (the title's "reach forward and hold to select");
  - steps to −0.35 m and back, then to +0.35 m and back;
  - jumps 0.30 m.
- `--case cursor`: the same figure holds its right hand up and sweeps it sideways, for the title's hand cursor.
- `--case frames:<folder>`: `synth_depth.py --live --live-frames` replaying recorded webcam frames.

`synth_depth.py --record-frames DIR` saves a live run as such a folder: frames plus `timestamps.json`, replayed at the recorded pace. Keep these under `fixtures/`, which is not committed.

The checks:

| check | passes when |
|---|---|
| bound | the hook found and bound the site |
| fresh | injection never fell back to the title's tracker |
| tracking | one tracking id, and a body in ≥ 95% of logged frames |
| direction | the injected hip agrees with the player mask the title's own tracker cut from our depth (correlation > 0.7, error < 40 px). This is the mirror check against the title itself |
| lateral | synthetic only: steps reach the scripted x on the scripted side |
| jumps | synthetic only: every scripted jump shows as a hip rise > 0.15 m |
| paused | no "GAME PAUSED" in any screenshot (template match) |
| cursor | cursor case only: the hand cursor moves across the screen with the hand |

Two more are reported but never fail the run:
- **raft:** the raft's screen x against the player's x. The camera follows the raft, so this is a weak signal.
- **lateral and jumps on recorded frames:** reported, since there is no ground truth.

Results:
- **Synthetic, 300 s:** everything passed.
- **Synthetic, 420 s and 480 s:** everything passed.
  - The game reached River Rush on its own at about 135–155 s, selecting START by the scripted reach.
  - One tracking id; a body in 100% of frames.
  - Direction: correlation +1.00, 1 px error.
  - Steps at −0.35…+0.35 m on the scripted side; one rise per scripted jump plus one; no pause screens.
  - The raft's screen position showed no relation to the steps (best correlation −0.16 over 0–4 s lags).
- **Cursor, first run:** on the START screen, the hand cursor followed the scripted right hand the way a player expects. Hand +0.02 / +0.17 / +0.34 m from the hip put the cursor at 31% / 38% / 54% of the screen width: the player's right hand going right moves the cursor right. Then the cursor rested on START, selected it, and River Rush began.
- **Cursor, second run** (sweep kept off the button): passed. The cursor was found in 37 screenshots and followed the hand with correlation **+0.90** at a 0.75 s lag. On screen, the injected skeleton is not mirrored.
- **Recorded webcam (`fullbody_02`, `--gain-x 1.4`, 240 s):** passed.
  - One tracking id; a body in 100% of frames.
  - Direction: correlation +0.99, 3 px error (mirrored would be 69 px).
  - Hip x −0.70…+0.65 m. That is past the ±0.5 m `--max-lateral-mm` cap, because the cap was converted to pixels at the player's raw distance instead of the pushed distance the body is drawn at. Fixed.
  - Rerun (180 s): hip x −0.49…+0.48 m, everything passed.
  - The game stays on the START screen here: the recording has no forward reach.

## 7. Next steps

1. **One live feel-check, with recording on.** The automated checks pass. Next, the user plays River Rush about 2.2 m back with `--gain-x 1.4 --record-frames ../../fixtures/live_<date>`, judging steering, jumps and delay. The saved frames then become a harness case (`--case frames:...`), so later changes are tested against the user's own play.
   - **Harness before any live run:**
     ```
     python skeleton_harness.py --case synthetic --seconds 420 --out ../../runs/<name>
     python skeleton_harness.py --case cursor --seconds 170 --out ../../runs/<name>
     ```
2. **Make the mask agree:** put the injected body in the tracker's dominant mask index, or emit depth for one player only. Then check that the game's use of the player-index stream is unaffected.
3. **Latency:** the skeleton no longer waits for the tracker's GPU fit, but it still waits for the depth frame that paces the producer and for the game's `NuiTransformSmooth`. Measure end to end with `latency_flash_test.py`. If smoothing dominates, the game's `NuiTransformSmooth` call (`0x82D26190`) could be a second hook.
