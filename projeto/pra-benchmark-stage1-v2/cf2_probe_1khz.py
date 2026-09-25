"""
Diagnostic probe for the confirmed CF2 magnitude block layout.

This does NOT yet integrate with Pyroomacoustics.
It reads one frequency slice and prints canonical CF2 directions so that
we can validate:
  1) the frequency-slot mapping,
  2) whether arc=180 is real data or padding,
  3) the positive rotation handedness to use in CF2Directivity.

Default layout currently under test:
  base offset: 14232 bytes
  30 frequency slots
  72 rotations
  37 arc samples
  float32 little-endian
  storage order: [frequency][rotation][arc]
"""

import argparse
from pathlib import Path
import numpy as np


FREQUENCIES_HZ = np.array([
    25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200,
    250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000,
    2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000,
], dtype=float)

DEFAULT_BASE = 14232
N_ROT = 72
N_ARC = 37
STEP_DEG = 5.0
FLOAT_DTYPE = np.dtype("<f4")
FLOATS_PER_BAND = N_ROT * N_ARC
BYTES_PER_BAND = FLOATS_PER_BAND * FLOAT_DTYPE.itemsize


def nearest_frequency_index(freq_hz):
    idx = int(np.argmin(np.abs(FREQUENCIES_HZ - float(freq_hz))))
    return idx, float(FREQUENCIES_HZ[idx])


def load_band(path, freq_hz, base_offset):
    p = Path(path)
    raw = p.read_bytes()

    idx, actual_freq = nearest_frequency_index(freq_hz)

    start = int(base_offset + idx * BYTES_PER_BAND)
    end = int(start + BYTES_PER_BAND)

    if end > len(raw):
        raise RuntimeError(
            f"CF2 file is too short for requested band. "
            f"file_size={len(raw)}, start={start}, end={end}"
        )

    band = np.frombuffer(
        raw,
        dtype=FLOAT_DTYPE,
        count=FLOATS_PER_BAND,
        offset=start,
    ).copy()

    band = band.reshape(N_ROT, N_ARC)

    return {
        "file_size": len(raw),
        "frequency_index": idx,
        "frequency_hz": actual_freq,
        "start": start,
        "end": end,
        "band": band,
    }


def idx_from_deg(deg, modulo=False):
    if modulo:
        deg = float(deg) % 360.0
    return int(round(float(deg) / STEP_DEG))


def value_at(band, rotation_deg, arc_deg):
    r = idx_from_deg(rotation_deg, modulo=True) % N_ROT
    a = idx_from_deg(arc_deg)
    if not (0 <= a < N_ARC):
        raise ValueError(f"Arc out of range: {arc_deg}")
    return float(band[r, a]), r, a


def stats(values):
    x = np.asarray(values, dtype=float)
    return dict(
        min=float(np.min(x)),
        max=float(np.max(x)),
        mean=float(np.mean(x)),
        std=float(np.std(x)),
        zeros=int(np.sum(x == 0.0)),
        finite=int(np.sum(np.isfinite(x))),
    )


def show_point(band, label, rotation_deg, arc_deg):
    val, ri, ai = value_at(
        band,
        rotation_deg,
        arc_deg,
    )
    print(
        f"{label:24s} "
        f"rot={rotation_deg:6.1f} "
        f"arc={arc_deg:6.1f} "
        f"idx=({ri:2d},{ai:2d}) "
        f"value={val:10.4f} dB"
    )
    return val


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--cf2",
        required=True,
        help="Path to the .CF2/.cf2 file inside the container.",
    )

    ap.add_argument(
        "--freq",
        type=float,
        default=1000.0,
        help="Requested frequency; nearest known CF2 slot is used.",
    )

    ap.add_argument(
        "--base",
        type=int,
        default=DEFAULT_BASE,
        help="Magnitude block base byte offset.",
    )

    args = ap.parse_args()

    result = load_band(
        args.cf2,
        args.freq,
        args.base,
    )

    band = result["band"]

    print("=== CF2 magnitude probe ===")
    print("file:", args.cf2)
    print("file_size:", result["file_size"])
    print("base_offset:", args.base)
    print("frequency_index:", result["frequency_index"])
    print("frequency_hz:", result["frequency_hz"])
    print("band_start:", result["start"])
    print("band_end:", result["end"])
    print("shape:", band.shape)
    print("band_min:", float(np.min(band)))
    print("band_max:", float(np.max(band)))
    print("band_mean:", float(np.mean(band)))
    print()

    print("=== Canonical directions ===")

    # Rotation 0 reference arc: top -> front/back plane
    show_point(band, "FRONT", 0, 0)
    show_point(band, "TOP", 0, 90)
    show_point(band, "BOTTOM", 180, 90)
    show_point(band, "BACK", 0, 180)

    print()
    print("--- Rotation handedness candidates at arc=90 ---")

    # If positive rotation is top->left:
    # left=90, right=270.
    show_point(
        band,
        "LEFT if top->left +",
        90,
        90,
    )
    show_point(
        band,
        "RIGHT if top->left +",
        270,
        90,
    )

    # If positive rotation is top->right:
    # right=90, left=270.
    show_point(
        band,
        "RIGHT if top->right +",
        90,
        90,
    )
    show_point(
        band,
        "LEFT if top->right +",
        270,
        90,
    )

    print()
    print("=== Pole consistency ===")

    front = band[:, 0]
    back = band[:, -1]

    front_stats = stats(front)
    back_stats = stats(back)

    print("arc=0 across all 72 rotations:")
    print(front_stats)

    print("arc=180/last-column across all 72 rotations:")
    print(back_stats)

    front_span = front_stats["max"] - front_stats["min"]
    back_span = back_stats["max"] - back_stats["min"]

    print("front_span_dB:", front_span)
    print("back_span_dB:", back_span)

    possible_padding = bool(
        np.all(back == 0.0)
        or (
            np.max(np.abs(back)) < 1e-12
        )
    )

    print("last_arc_all_zero:", bool(np.all(back == 0.0)))
    print("possible_last_arc_padding:", possible_padding)

    print()
    print("=== Selected 5-degree rows at arc=90 ===")
    for rotation_deg in [0, 45, 90, 135, 180, 225, 270, 315]:
        show_point(
            band,
            f"ROT {rotation_deg:3d}",
            rotation_deg,
            90,
        )

    print()
    print("=== First row, all arcs ===")
    row0 = band[0]
    for arc_index, value in enumerate(row0):
        print(
            f"arc={arc_index * STEP_DEG:6.1f} "
            f"idx={arc_index:2d} "
            f"value={float(value):10.4f}"
        )


if __name__ == "__main__":
    main()
