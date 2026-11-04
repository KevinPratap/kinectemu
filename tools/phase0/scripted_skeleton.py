"""Scripted player for hands-free tests of xenia's skeleton injection.

A standing figure with a known timeline of sideways steps and jumps, sent to
xenia the way synth_depth.py --live --skeleton-pipe sends a webcam player: its
synthetic depth into the depth pipe (--nui=pipe) and the matching 20-joint
skeleton into the skeleton pipe (--nui_skeleton_inject=pipe:<name>). Nobody
has to stand in front of a camera, and the ground truth is exact.

Coordinates are the title's sensor space: x grows with the column of the
frames written to the depth pipe (and is the player's right side when the
player faces the sensor), y up, z away from the sensor, metres.

Timeline (seconds from the first frame xenia takes), written to --truth:
  0 .. --settle                stand at x = 0 (the title's setup screens)
  then repeating every 30 s:   reach the right arm forward and hold it 4 s
                               (the title's "reach forward and hold to
                               select"), step to -STEP, back to 0, step to
                               +STEP, back to 0, then a jump of JUMP m

  python scripted_skeleton.py --seconds 300 --truth truth.csv
"""
import argparse
import time
from types import SimpleNamespace

import cv2
import numpy as np

from synth_depth import (KF, KH, KW, SKELETON_RECORD, connect_pipe,
                         connect_skeleton_pipe, scene_background)

# Joint offsets from the hip centre (m), standing and facing the sensor, in
# public NUI joint order; the player's right side is +x.
REST = np.array([
    (0.00, 0.00, 0.00), (0.00, 0.10, 0.00), (0.00, 0.45, 0.00), (0.00, 0.62, 0.00),
    (-0.18, 0.42, 0.00), (-0.24, 0.17, 0.02), (-0.27, -0.06, 0.02), (-0.28, -0.13, 0.02),
    (0.18, 0.42, 0.00), (0.24, 0.17, 0.02), (0.27, -0.06, 0.02), (0.28, -0.13, 0.02),
    (-0.09, -0.05, 0.00), (-0.10, -0.45, 0.00), (-0.10, -0.83, 0.00), (-0.10, -0.88, -0.08),
    (0.09, -0.05, 0.00), (0.10, -0.45, 0.00), (0.10, -0.83, 0.00), (0.10, -0.88, -0.08),
])
# Body segments (joint a, joint b, radius m) drawn into the depth silhouette.
SEGMENTS = [(0, 2, 0.16), (2, 3, 0.06), (4, 5, 0.05), (5, 6, 0.045), (6, 7, 0.045),
            (8, 9, 0.05), (9, 10, 0.045), (10, 11, 0.045), (12, 13, 0.08), (13, 14, 0.06),
            (14, 15, 0.05), (16, 17, 0.08), (17, 18, 0.06), (18, 19, 0.05), (4, 8, 0.07)]
HEAD_RADIUS = 0.11
STEP, JUMP, DISTANCE, SENSOR_HEIGHT = 0.35, 0.30, 2.4, 0.95
PERIOD = 30.0
# (start, end, from x, to x) moves inside one period, and the jump's start.
MOVES = [(6.0, 6.5, 0.0, -STEP), (10.0, 10.5, -STEP, 0.0), (14.0, 14.5, 0.0, STEP), (18.0, 18.5, STEP, 0.0)]
JUMP_AT, JUMP_LENGTH = 22.0, 0.6
# The right arm reaching straight forward at shoulder height: elbow, wrist and
# hand relative to the right shoulder, and when in the period it is up.
REACH = np.array([(0.04, -0.02, -0.28), (0.06, -0.03, -0.52), (0.07, -0.03, -0.60)])
REACH_RISE, REACH_HOLD, REACH_FALL = (0.0, 0.5), (0.5, 4.5), (4.5, 5.0)
# --mode cursor: the right hand held up in front of the chest (elbow, wrist,
# hand relative to the right shoulder) and swept sideways around POINT_CENTRE
# by +-POINT_SWEEP every POINT_PERIOD seconds. The title's START screen shows
# a hand cursor and selects what it rests on (seen: hand +0.02 / +0.17 /
# +0.34 m from the hip put the cursor at 31% / 38% / 54% of the screen, and
# START was selected); the sweep stays across the body so the cursor stays in
# the left third, off the START button.
POINT = np.array([(0.04, -0.15, -0.15), (0.06, -0.08, -0.28), (0.07, -0.05, -0.32)])
POINT_CENTRE, POINT_SWEEP, POINT_PERIOD = -0.42, 0.13, 8.0


def pose_at(t, settle):
    """Hip centre x, its lift and how far the right arm reaches forward
    (0..1) at t seconds of the script."""
    if t < settle:
        return 0.0, 0.0, 0.0
    u = (t - settle) % PERIOD
    reach = 0.0
    if REACH_RISE[0] <= u < REACH_RISE[1]:
        reach = (u - REACH_RISE[0]) / (REACH_RISE[1] - REACH_RISE[0])
    elif REACH_HOLD[0] <= u < REACH_HOLD[1]:
        reach = 1.0
    elif REACH_FALL[0] <= u < REACH_FALL[1]:
        reach = 1.0 - (u - REACH_FALL[0]) / (REACH_FALL[1] - REACH_FALL[0])
    x = 0.0
    for a, b, x0, x1 in MOVES:
        if u >= b:
            x = x1
        elif u >= a:
            x = x0 + (x1 - x0) * (u - a) / (b - a)
            break
    lift = 0.0
    if JUMP_AT <= u < JUMP_AT + JUMP_LENGTH:
        v = (u - JUMP_AT) / JUMP_LENGTH
        lift = 4.0 * JUMP * v * (1.0 - v)
    return x, lift, reach


def point_at(t, settle):
    """--mode cursor: the right hand's sideways offset (m) as it sweeps in
    front of the chest, or None while it hangs at the side."""
    if t < settle:
        return None
    return POINT_CENTRE + POINT_SWEEP * np.sin(2 * np.pi * (t - settle) / POINT_PERIOD)


def body_at(x, lift, reach=0.0, point=None):
    """20x3 joints in sensor space for a hip centre at x, lifted by lift, with
    the right arm reaching forward by reach (0..1), or held up in front of
    the chest point metres to the side."""
    hip_y = -SENSOR_HEIGHT + 0.88  # feet on the floor
    pose = REST.copy()
    pose[9:12] += reach * (pose[8] + REACH - pose[9:12])
    if point is not None:
        pose[9:12] = pose[8] + POINT + np.array([[0.4 * point, 0, 0], [point, 0, 0], [point, 0, 0]])
    return pose + np.array([x, hip_y + lift, DISTANCE])


def render_depth(joints, background, relief_mm=100.0):
    """The figure's synthetic depth (mm) over the background, in the frame the
    depth pipe takes: column 319.5 + KF x / z, row 239.5 - KF y / z."""
    def px(p):
        return (int(round((KW - 1) / 2 + KF * p[0] / p[2])),
                int(round((KH - 1) / 2 - KF * p[1] / p[2])))
    mask = np.zeros((KH, KW), np.uint8)
    for a, b, r in SEGMENTS:
        width = max(1, int(round(2 * KF * r / joints[a][2])))
        cv2.line(mask, px(joints[a]), px(joints[b]), 255, width)
    cv2.circle(mask, px(joints[3]), int(round(KF * HEAD_RADIUS / joints[3][2])), 255, -1)
    z_mm = float(joints[0][2]) * 1000.0
    edge_mm = np.minimum(cv2.distanceTransform(mask, cv2.DIST_L2, 5) * z_mm / KF, relief_mm)
    body = z_mm - np.sqrt(np.maximum(2 * relief_mm * edge_mm - edge_mm ** 2, 0))
    return np.clip(np.where(mask > 0, body, background), 0, 65535).astype(np.uint16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipe", default="kinectemu")
    ap.add_argument("--skeleton-pipe", default="kinectemu-skel")
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--settle", type=float, default=60.0,
                    help="stand still this long first, for the title's setup screens")
    ap.add_argument("--truth", help="CSV of the scripted hip per frame")
    ap.add_argument("--mode", choices=("play", "cursor"), default="play",
                    help="play: reach to select, steps and jumps; cursor: stand still and sweep "
                    "the raised right hand sideways (for the title's hand cursor)")
    args = ap.parse_args()

    background = scene_background(SENSOR_HEIGHT * 1000.0,
                                  SimpleNamespace(wall_mm=3600, no_floor=False))
    floor_mm = SENSOR_HEIGHT * 1000.0
    wr = connect_pipe(args.pipe)
    sk = connect_skeleton_pipe(args.skeleton_pipe)
    truth = open(args.truth, "w", encoding="utf-8") if args.truth else None
    if truth:
        truth.write("t_s,epoch_s,hip_x,hip_y,hip_z,lift,reach,hand_x\n")
    seen = np.full(20, 2, np.uint32)
    t0 = time.perf_counter()
    frame = 0
    try:
        while True:
            t = time.perf_counter() - t0
            if t >= args.seconds:
                break
            if args.mode == "cursor":
                x, lift, reach = 0.0, 0.0, 0.0
                joints = body_at(x, lift, point=point_at(t, args.settle))
            else:
                x, lift, reach = pose_at(t, args.settle)
                joints = body_at(x, lift, reach)
            wr.write(render_depth(joints, background), time.perf_counter_ns() // 1000)
            wr.flush()
            sk.write(time.perf_counter_ns() // 1000, (joints, seen), 1, floor_mm)
            if truth:
                truth.write(f"{t:.4f},{time.time():.3f},{joints[0][0]:.4f},{joints[0][1]:.4f},"
                            f"{joints[0][2]:.4f},{lift:.4f},{reach:.3f},"
                            f"{joints[11][0] - joints[0][0]:.4f}\n")
            frame += 1
            time.sleep(max(0.0, t0 + frame / 30.0 - time.perf_counter()))
    except OSError:
        print("xenia closed a pipe; stopping", flush=True)
    finally:
        if truth:
            truth.close()
        for w in (wr, sk):
            try:
                w.close()
            except OSError:
                pass


if __name__ == "__main__":
    assert SKELETON_RECORD.size == 376
    main()
