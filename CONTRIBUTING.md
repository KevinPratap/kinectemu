# Contributing to KinectEmu

KinectEmu is an early research prototype tested on one laptop and one game. Careful, evidenced changes are more useful than large ones. Reports of what happened on other hardware are very welcome, including failures.

## Set up

Windows 11 x64, Python 3.11, Git.

```powershell
git clone https://github.com/KevinPratap/kinectemu.git
cd kinectemu
.\scripts\setup.ps1          # venv, pinned packages, models (hash-checked)
```

Then build the Xenia fork if you need to run a game: [docs/BUILDING.md](docs/BUILDING.md). You need an image of a disc you own. Do not ask for game files, BIOS files or dumps in issues or pull requests, and do not attach them.

If you only change Python tools, `setup.ps1` plus the self-tests below are enough. If you change anything that touches a game, build the fork and run the harness.

## Working rules

These come from the project brief ([PROMPT_KinectEmu_v2.md](PROMPT_KinectEmu_v2.md), "Working rules"). They are why the docs can be trusted.

1. **No lying stubs.** An unimplemented path logs once with a stable message and returns a documented failure. Never return success from code you did not implement. Never report "no sensor" when the sensor is present but slow.
2. **Every claim needs a log line.** "Works" means a recorded run and the log excerpt that shows it. If you did not run it, say "not tested".
3. **Real-time discipline.** No allocation in the frame path, no busy-waits, no unbounded queues. Drop late frames rather than block. Inference never runs on a guest-CPU thread.
4. **Privacy.** Camera frames never leave the machine. No network access at runtime. Recording is opt-in. Do not add telemetry, uploads or remote services.
5. **Model licences.** Only models whose licence allows this use (Apache-2.0, MIT). Record name, version, licence and SHA-256 in [docs/MODELS.md](docs/MODELS.md), and keep [tools/fetch_models.py](tools/fetch_models.py) in sync with it. No SMPL/SMPL-X or non-commercial (CC-BY-NC) weights.
6. **One concern per commit.** Small commits with a message that says what changed and why.
7. **Phase gates.** A phase starts only after the previous gate passes on a real machine and is recorded in [docs/STATUS.md](docs/STATUS.md). If a gate is unreachable, write the blocker down with evidence.

## Evidence goes in the repo

- Measurements: JSON in `docs/data/` (see the existing files), and screenshots there if they prove something.
- Conclusions: [docs/STATUS.md](docs/STATUS.md). Add what works (one line each, with the machine), what is mocked or synthesised, the exact reproduce command, and what failed so nobody retries it.
- Design notes for the Xenia side: [docs/NUI_IMPLEMENTATION.md](docs/NUI_IMPLEMENTATION.md).
- State the hardware: laptop, GPUs, camera, light, distance, player count.

## Never commit

- **Fixtures and recordings.** `--record-frames` saves camera frames of a real person. They go under `fixtures\` (git-ignored) and must not be committed, attached to issues or shared without the person's consent. The same goes for harness output under `runs\`.
- Game images, BIOS, decrypted game memory dumps, or anything derived from game binaries beyond short disassembly facts already in the docs (addresses, instruction patterns).
- Model files (`models\` is git-ignored; they are downloaded).
- `tools\.venv`, `external\`.

## Before you open a pull request

1. Lint and self-tests, the same as CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)):

   ```powershell
   pip install ruff
   ruff check .
   cd tools\phase0
   ..\.venv\Scripts\python.exe calibrate_intrinsics.py --selftest
   ..\.venv\Scripts\python.exe latency_flash_test.py --selftest
   ```

   CI runs `ruff check .`, compiles every script, checks `fetch_models.py --help`, and runs those two self-tests.

2. If your change can affect what a game sees (`synth_depth.py`, `scripted_skeleton.py`, the skeleton code, Xenia flags), run the hands-free harness and attach the PASS/FAIL tail and the contact sheet from `runs\...\sheet.jpg`:

   ```powershell
   .\scripts\run_harness.ps1 -Iso "<your Kinect Adventures image>"
   .\scripts\run_harness.ps1 -Case cursor -Iso "<your Kinect Adventures image>"
   ```

   The harness is documented in [docs/NUI_IMPLEMENTATION.md](docs/NUI_IMPLEMENTATION.md) section 6. If you cannot run it, say so in the PR.

3. If you can, play it live once and write down what you felt: steering, jumps, delay. The harness does not measure that.

4. Update the docs that your change makes wrong.

## Changes to Xenia

Xenia-side code lives in the fork repository, not here:

- Fork: <https://github.com/KevinPratap/xenia-canary>, branches `kinectemu-pipe-backend` and `kinectemu-skeleton-inject`.
- Make the change there, with unit tests in `xenia-nui-tests`.
- Mirror each commit here as a patch under [xenia/patches/](xenia/patches/) (`git format-patch`) and update the table in [xenia/README.md](xenia/README.md).
- The patches are derivative of Xenia and stay under Xenia's BSD-3-Clause licence. Everything else here is MIT. Do not paste code from other projects without checking its licence.

Where it fits, upstream-friendly changes should go to Xenia Canary and the Kinect PR series first. The plan in [docs/STATUS.md](docs/STATUS.md) is to rebase on them, not to diverge.

## Adding another game

Do not guess. First inspect the title's XEX: its imports, whether it links the Kinect runtime statically, and which streams it uses (skeleton, depth, player index, colour, audio). [docs/NUI_LAYER.md](docs/NUI_LAYER.md) has the method and what is known so far. The skeleton injection finds its site by code signature and has been checked on one image of one title (runtime 2.0.11775 in Kinect Adventures). A different title or disc build may not match. The addresses in the fork's own docs did not match the author's disc, so find sites by signature, not by fixed address.

Record the result in [docs/NUI_LAYER.md](docs/NUI_LAYER.md) with the title ID and the module hash of your image.

## Reporting a problem

Open an issue with: Windows version, laptop and GPUs, webcam model, light level and distance, the command you ran, and the last lines of the Xenia log (`--flush_log=true --log_file=...`). Do not attach camera frames of people unless they agree.

## Licence

By contributing you agree your changes to this repository's own code and docs are under the [MIT licence](LICENSE), except patches to Xenia, which stay under BSD-3-Clause.
