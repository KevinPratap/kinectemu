"""Phase 0-W printable calibration target: 9x6 inner-corner checkerboard (10x7 squares).

Renders an A4-landscape PNG at 300 dpi (3508x2480 px) with the squares at their
true physical size, a caption stating the intended square size, and a 100 mm
scale bar so you can verify the print scale. Print at 100% / "actual size"
(NOT "fit to page"), stick it on something rigid and flat, then MEASURE one
printed square and pass that value to calibrate_intrinsics.py --square-mm.

Usage:
  python make_checkerboard.py [--out ../../docs/checkerboard_9x6.png] [--square-mm 25]
"""

import argparse
import struct
import sys
import zlib
from pathlib import Path

import cv2
import numpy as np

COLS_SQ, ROWS_SQ = 10, 7           # squares -> 9x6 inner corners
INNER = (COLS_SQ - 1, ROWS_SQ - 1)
DPI = 300
A4_MM = (297.0, 210.0)             # landscape
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "docs" / "checkerboard_9x6.png"


def add_phys_chunk(png_bytes, dpi):
    """Insert a pHYs chunk so printers/viewers know the PNG is `dpi` dots per inch."""
    ppm = int(round(dpi / 0.0254))
    data = struct.pack(">IIB", ppm, ppm, 1)
    chunk = struct.pack(">I", len(data)) + b"pHYs" + data
    chunk += struct.pack(">I", zlib.crc32(b"pHYs" + data) & 0xFFFFFFFF)
    ihdr_end = 8 + 4 + 4 + 13 + 4  # signature + IHDR chunk (len, type, data, crc)
    return png_bytes[:ihdr_end] + chunk + png_bytes[ihdr_end:]


def fit_text(img, text, org_x, y, max_w, color=0, scale=1.6, thick=3):
    """Draw `text` at (org_x, y), shrinking the scale until it fits max_w px."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    while True:
        (tw, _), _ = cv2.getTextSize(text, font, scale, thick)
        if tw <= max_w or scale < 0.4:
            break
        scale -= 0.05
    cv2.putText(img, text, (org_x, y), font, scale, color, thick, cv2.LINE_AA)


def render(square_mm, dpi=DPI):
    px_per_mm = dpi / 25.4
    w, h = int(round(A4_MM[0] * px_per_mm)), int(round(A4_MM[1] * px_per_mm))
    sq = square_mm * px_per_mm
    board_w, board_h = COLS_SQ * sq, ROWS_SQ * sq
    if board_w > w - 2 * 10 * px_per_mm or board_h > h - 32 * px_per_mm:
        raise ValueError(f"{square_mm} mm squares do not fit on A4 landscape with margins")
    img = np.full((h, w), 255, np.uint8)
    x0 = int(round((w - board_w) / 2))
    y0 = int(round(6 * px_per_mm))
    # Round each edge from the exact physical position so no error accumulates.
    xs = [x0 + int(round(i * sq)) for i in range(COLS_SQ + 1)]
    ys = [y0 + int(round(j * sq)) for j in range(ROWS_SQ + 1)]
    for j in range(ROWS_SQ):
        for i in range(COLS_SQ):
            if (i + j) % 2 == 0:
                img[ys[j]:ys[j + 1], xs[i]:xs[i + 1]] = 0
    left = xs[0]
    text_w = w - 2 * left
    cap_y = ys[-1] + int(round(8 * px_per_mm))
    line = int(round(6 * px_per_mm))
    fit_text(img, f"Checkerboard {INNER[0]}x{INNER[1]} inner corners ({COLS_SQ}x{ROWS_SQ} squares). "
                  f"Intended square size: {square_mm:g} mm.", left, cap_y, text_w)
    fit_text(img, "Print at 100% (actual size), mount flat. Measure your printed square and pass "
                  "--square-mm <value>.", left, cap_y + line, text_w, scale=1.3, thick=2)
    bar_y = cap_y + int(round(2.0 * line))
    bar_len = int(round(100 * px_per_mm))
    img[bar_y:bar_y + int(round(1.2 * px_per_mm)), left:left + bar_len] = 0
    for k in range(11):  # 10 mm ticks
        tx = left + int(round(k * 10 * px_per_mm))
        img[bar_y - int(round(2.5 * px_per_mm)):bar_y, tx:tx + 3] = 0
    fit_text(img, "<- this bar must measure 100 mm", left + bar_len + int(round(5 * px_per_mm)),
             bar_y + int(round(1.5 * px_per_mm)), text_w - bar_len, scale=1.0, thick=2)
    return img, (w, h)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--square-mm", type=float, default=25.0)
    args = ap.parse_args()

    img, (w, h) = render(args.square_mm)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        print("[W0-101] PNG encode failed", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(add_phys_chunk(buf.tobytes(), DPI))
    print(f"[W0-102] wrote {out} ({w}x{h} px, {DPI} dpi, A4 landscape, {args.square_mm:g} mm squares)")

    # Sanity: the exact detector the calibration tool uses must find all 9x6 corners.
    back = cv2.imread(str(out), cv2.IMREAD_GRAYSCALE)
    found, corners = cv2.findChessboardCornersSB(back, INNER, flags=cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_ACCURACY)
    if not found:
        print("[W0-103] FAIL: findChessboardCornersSB did not find the board in the rendered PNG", file=sys.stderr)
        return 1
    pitch = abs(float(np.median(np.diff(corners.reshape(INNER[1], INNER[0], 2)[0, :, 0]))))
    print(f"[W0-104] PASS: detector found {INNER[0]}x{INNER[1]} corners; pitch {pitch:.2f} px "
          f"= {pitch / (DPI / 25.4):.2f} mm at {DPI} dpi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
