#!/usr/bin/env python3
"""Download and verify the model files used by tools/phase0 (stdlib only).

The hash table below mirrors docs/MODELS.md. Keep the two in sync.

Usage:
    python tools/fetch_models.py                 # download missing, verify all
    python tools/fetch_models.py --check         # verify existing files only, no network
    python tools/fetch_models.py --only rtmo-s   # one model
    python tools/fetch_models.py --models DIR    # alternate models directory

Hash semantics: for RTMO the table hash is of the extracted end2end.onnx (the
zip itself has no published hash); for MediaPipe it is of the .task file.
On a hash mismatch nothing is deleted; the script prints both hashes and exits 1.
"""
import argparse
import hashlib
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

OPENMMLAB = "https://download.openmmlab.com/mmpose/v1/projects/rtmo/onnx_sdk/"
MEDIAPIPE = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_{v}/float16/latest/pose_landmarker_{v}.task"
)

# Table mirrors docs/MODELS.md.
# kind "rtmo": download a zip, extract end2end.onnx; sha256 is of end2end.onnx.
# kind "task": download a .task (zip); sha256 is of the .task; extract into extracted/.
MODELS = {
    "rtmo-s": dict(
        kind="rtmo",
        url=OPENMMLAB + "rtmo-s_8xb32-600e_body7-640x640-dac2bf74_20231211.zip",
        file="rtmo-s_8xb32-600e_body7-640x640-dac2bf74_20231211.zip",
        sha256="D0703D40D19F3921DA51AE725402D5FDAE4D2478C7442072D3101BD396F370D8",
    ),
    "rtmo-m": dict(
        kind="rtmo",
        url=OPENMMLAB + "rtmo-m_16xb16-600e_body7-640x640-39e78cc4_20231211.zip",
        file="rtmo-m_16xb16-600e_body7-640x640-39e78cc4_20231211.zip",
        sha256="750D872151FF4652F02C738EFC3A547E112CE9B688FE920BC17F948E8C3AFDAC",
    ),
    "mp-pose-lite": dict(
        kind="task",
        url=MEDIAPIPE.format(v="lite"),
        file="pose_landmarker_lite.task",
        sha256="59929E1D1EE95287735DDD833B19CF4AC46D29BC7AFDDBBF6753C459690D574A",
    ),
    "mp-pose-full": dict(
        kind="task",
        url=MEDIAPIPE.format(v="full"),
        file="pose_landmarker_full.task",
        sha256="4EAA5EB7A98365221087693FCC286334CF0858E2EB6E15B506AA4A7ECDCEC4AD",
    ),
    "mp-pose-heavy": dict(
        kind="task",
        url=MEDIAPIPE.format(v="heavy"),
        file="pose_landmarker_heavy.task",
        sha256="64437AF838A65D18E5BA7A0D39B465540069BC8AAE8308DE3E318AAD31FCBC7B",
    ),
}

# Derived locally (see docs/MODELS.md): folder -> (onnx file, sha256)
CONVERTED = {
    "mp-pose-lite": ("pose_landmarks_lite.onnx",
                     "D227DA716233676A5C190A5A133A82D53332106983A6E5C4F37602EE41C3F943"),
    "mp-pose-full": ("pose_landmarks_full.onnx",
                     "DA6D0A1BF955A2132E17BBA8F53A557232F37E1333079F78A0DD59FFDDD34E4A"),
}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.replace(dest)


def extract_member(zpath, member, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath) as z, z.open(member) as src, open(dest, "wb") as out:
        shutil.copyfileobj(src, out)


def mismatch(name, what, expected, actual):
    print(f"[{name}] HASH MISMATCH for {what}\n    expected {expected}\n    actual   {actual}")


def process(name, spec, root, check_only):
    """Return a status string: ok, downloaded, missing, mismatch."""
    folder = root / name
    if spec["kind"] == "rtmo":
        onnx = folder / "end2end.onnx"
        if onnx.is_file():
            actual = sha256_of(onnx)
            if actual != spec["sha256"]:
                mismatch(name, str(onnx), spec["sha256"], actual)
                return "mismatch"
            return "ok"
        if check_only:
            return "missing"
        zpath = folder / spec["file"]
        if not zpath.is_file():
            print(f"[{name}] downloading {spec['url']}")
            download(spec["url"], zpath)
        extract_member(zpath, "end2end.onnx", onnx)
        actual = sha256_of(onnx)
        if actual != spec["sha256"]:
            mismatch(name, str(onnx), spec["sha256"], actual)
            return "mismatch"
        return "downloaded"

    task = folder / spec["file"]
    status = "ok"
    if not task.is_file():
        if check_only:
            return "missing"
        print(f"[{name}] downloading {spec['url']}")
        download(spec["url"], task)
        status = "downloaded"
    actual = sha256_of(task)
    if actual != spec["sha256"]:
        mismatch(name, str(task), spec["sha256"], actual)
        return "mismatch"
    if not check_only:
        ext = folder / "extracted"
        with zipfile.ZipFile(task) as z:
            members = z.namelist()
        for m in members:
            if not (ext / m).is_file():
                extract_member(task, m, ext / m)
    return status


def converted_report(root, only):
    bad = False
    for name, (fname, sha) in CONVERTED.items():
        if only and name != only:
            continue
        p = root / name / fname
        if not p.is_file():
            tflite = root / name / "extracted" / "pose_landmarks_detector.tflite"
            print(f"[{name}] converted {fname}: missing. Build it in tools/.venv-convert with:\n"
                  f'    python -m tf2onnx.convert --tflite "{tflite}" --output "{p}" --opset 17')
            continue
        actual = sha256_of(p)
        if actual == sha:
            print(f"[{name}] converted {fname}: ok")
        else:
            mismatch(name, str(p), sha, actual)
            bad = True
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description="Download and verify model files (see docs/MODELS.md).")
    ap.add_argument("--models", type=Path, default=REPO / "models",
                    help="models directory (default: <repo>/models)")
    ap.add_argument("--check", action="store_true",
                    help="verify existing files only, no network; exit 1 if any model is missing")
    ap.add_argument("--only", choices=sorted(MODELS), help="process a single model")
    args = ap.parse_args(argv)

    failed = False
    for name, spec in MODELS.items():
        if args.only and name != args.only:
            continue
        try:
            status = process(name, spec, args.models, args.check)
        except OSError as e:
            print(f"[{name}] download/extract failed: {e}")
            status = "error"
        print(f"{name}: {status}")
        if status in ("missing", "mismatch", "error"):
            failed = True
    if converted_report(args.models, args.only):
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
