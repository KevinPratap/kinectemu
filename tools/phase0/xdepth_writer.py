"""Writes .xdepth recordings for the ryantimjohn fork's --nui=replay backend.

Format (fork src/xenia/nui/depth_recording.h, little endian):
  header: magic b"XNUIDEP1", u32 width, u32 height, u32 reserved[2]   (24 bytes)
  frame:  u64 sequence, u64 timestamp_us                              (16 bytes)
          width*height u16 depth in mm, row major, 0 = no reading

Frames are in Kinect for Windows orientation; the fork mirrors rows on the
way to the guest. The title's tracker accepts depth 1..3999 mm only.

  python xdepth_writer.py --selftest
  python xdepth_writer.py --demo out.xdepth   # 3 s of a standing blob at 2.3 m
"""
import argparse
import struct
import sys

import numpy as np

MAGIC = b"XNUIDEP1"
W, H = 640, 480


class XdepthWriter:
    def __init__(self, path, width=W, height=H):
        self.f = open(path, "wb")
        self.width, self.height = width, height
        self.seq = 0
        self.f.write(struct.pack("<8sIIII", MAGIC, width, height, 0, 0))

    def write(self, depth_mm, timestamp_us):
        assert depth_mm.shape == (self.height, self.width)
        self.seq += 1
        self.f.write(struct.pack("<QQ", self.seq, int(timestamp_us)))
        self.f.write(np.ascontiguousarray(depth_mm, dtype="<u2").tobytes())

    def close(self):
        self.f.close()


def read_xdepth(path):
    with open(path, "rb") as f:
        magic, w, h, _, _ = struct.unpack("<8sIIII", f.read(24))
        if magic != MAGIC:
            raise ValueError(f"bad magic {magic!r}")
        frames = []
        n = w * h * 2
        while True:
            hdr = f.read(16)
            body = f.read(n)
            if len(hdr) < 16 or len(body) < n:
                break  # a partial trailing frame is ignored, as the fork does
            seq, ts = struct.unpack("<QQ", hdr)
            frames.append((seq, ts, np.frombuffer(body, "<u2").reshape(h, w)))
    return w, h, frames


def demo_frame(t):
    """A person-sized ellipse at 2.3 m swaying left/right, wall at 0 (no reading)."""
    d = np.zeros((H, W), np.uint16)
    yy, xx = np.mgrid[0:H, 0:W]
    cx = W / 2 + 60 * np.sin(2 * np.pi * 0.5 * t)
    body = ((xx - cx) / 70) ** 2 + ((yy - 260) / 190) ** 2 <= 1
    # Torso relief: centre ~100 mm nearer than the edge (tracker wants 50..300).
    r2 = ((xx - cx) / 70) ** 2 + ((yy - 260) / 190) ** 2
    d[body] = (2300 - 100 * (1 - r2[body])).astype(np.uint16)
    return d


def selftest(path):
    wr = XdepthWriter(path)
    for i in range(5):
        wr.write(demo_frame(i / 30), i * 33333)
    wr.close()
    w, h, frames = read_xdepth(path)
    assert (w, h) == (W, H) and len(frames) == 5
    assert [f[0] for f in frames] == [1, 2, 3, 4, 5]
    assert np.array_equal(frames[2][2], demo_frame(2 / 30))
    nz = frames[0][2][frames[0][2] > 0]
    assert 2100 <= nz.min() and nz.max() <= 2300
    # A torn trailing frame must not be read.
    with open(path, "ab") as f:
        f.write(b"\0" * 100)
    assert len(read_xdepth(path)[2]) == 5
    print("selftest PASS")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--demo", metavar="OUT")
    a = ap.parse_args()
    if a.selftest:
        import os, tempfile
        selftest(os.path.join(tempfile.gettempdir(), "xdepth_selftest.xdepth"))
    elif a.demo:
        wr = XdepthWriter(a.demo)
        for i in range(90):
            wr.write(demo_frame(i / 30), i * 33333)
        wr.close()
        print(f"wrote 90 frames to {a.demo}")
    else:
        ap.print_help()
        sys.exit(1)
