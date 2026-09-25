"""
Profile all decoded CF2 magnitude bands before interpolation / Pyroomacoustics integration.

Current layout under test:
  base offset: 14232 bytes
  30 frequency slots
  72 rotations x 37 arcs
  float32 little-endian
  order [frequency][rotation][arc]

The script detects:
- all-zero bands
- effective dynamic range
- number of non-zero samples
- number of unique values
- whether front/back poles are rotation-consistent
- similarity to adjacent bands (RMSE / max abs)
"""

import argparse
from pathlib import Path
import numpy as np

FREQUENCIES_HZ = np.array([
    25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200,
    250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000,
    2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000,
], dtype=float)

BASE_OFFSET = 14232
N_FREQ = 30
N_ROT = 72
N_ARC = 37
DTYPE = np.dtype("<f4")
FLOATS_PER_BAND = N_ROT * N_ARC
BYTES_PER_BAND = FLOATS_PER_BAND * DTYPE.itemsize


def load_all(path, base_offset):
    raw = Path(path).read_bytes()
    needed = base_offset + N_FREQ * BYTES_PER_BAND

    if needed > len(raw):
        raise RuntimeError(
            f"File too short: size={len(raw)}, needed={needed}"
        )

    x = np.frombuffer(
        raw,
        dtype=DTYPE,
        count=N_FREQ * FLOATS_PER_BAND,
        offset=base_offset,
    ).copy()

    return x.reshape(N_FREQ, N_ROT, N_ARC)


def diff_metrics(a, b):
    d = a.astype(np.float64) - b.astype(np.float64)
    return (
        float(np.sqrt(np.mean(d * d))),
        float(np.max(np.abs(d))),
    )


def classify_band(band):
    finite = np.isfinite(band)
    if not np.all(finite):
        return "NONFINITE"

    if np.all(band == 0.0):
        return "ALL_ZERO"

    # A directivity band normalized at the front should normally have
    # front pole ~= 0 dB. This is diagnostic, not a strict validity rule.
    front = band[:, 0]

    if np.max(np.abs(front)) < 1e-6:
        return "NONZERO_FRONT0"

    return "NONZERO_FRONT_NOT0"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cf2", required=True)
    ap.add_argument("--base", type=int, default=BASE_OFFSET)
    args = ap.parse_args()

    balloon = load_all(args.cf2, args.base)

    print("=== CF2 band profile ===")
    print("file:", args.cf2)
    print("shape:", balloon.shape)
    print()

    header = (
        "freq      class                 min_dB     max_dB"
        "     mean_dB      std_dB  nonzero unique"
        "  front_span  back_span"
    )
    print(header)

    classes = []

    for fi, freq in enumerate(FREQUENCIES_HZ):
        b = balloon[fi]
        cls = classify_band(b)
        classes.append(cls)

        nonzero = int(np.count_nonzero(b))
        unique = int(np.unique(b).size)

        front = b[:, 0]
        back = b[:, -1]

        front_span = float(front.max() - front.min())
        back_span = float(back.max() - back.min())

        print(
            f"{freq:7g}  "
            f"{cls:22s} "
            f"{float(b.min()):10.4f} "
            f"{float(b.max()):10.4f} "
            f"{float(b.mean()):11.4f} "
            f"{float(b.std()):11.4f} "
            f"{nonzero:8d} "
            f"{unique:6d} "
            f"{front_span:11.6f} "
            f"{back_span:10.6f}"
        )

    print()
    print("=== Adjacent-band differences ===")
    print("from_hz -> to_hz       RMSE_dB    max_abs_dB")

    for fi in range(N_FREQ - 1):
        rmse, max_abs = diff_metrics(
            balloon[fi],
            balloon[fi + 1],
        )

        print(
            f"{FREQUENCIES_HZ[fi]:7g} -> "
            f"{FREQUENCIES_HZ[fi + 1]:7g} "
            f"{rmse:12.6f} "
            f"{max_abs:13.6f}"
        )

    print()
    print("=== Summary ===")

    all_zero_freqs = [
        float(FREQUENCIES_HZ[i])
        for i, cls in enumerate(classes)
        if cls == "ALL_ZERO"
    ]

    nonzero_freqs = [
        float(FREQUENCIES_HZ[i])
        for i, cls in enumerate(classes)
        if cls != "ALL_ZERO"
    ]

    print("all_zero_frequencies_hz:", all_zero_freqs)
    print("nonzero_frequencies_hz:", nonzero_freqs)

    if nonzero_freqs:
        print(
            "first_nonzero_frequency_hz:",
            nonzero_freqs[0],
        )
        print(
            "last_nonzero_frequency_hz:",
            nonzero_freqs[-1],
        )

    # Check whether the non-zero frequencies form one contiguous slot range.
    nonzero_idx = [
        i
        for i, cls in enumerate(classes)
        if cls != "ALL_ZERO"
    ]

    contiguous = (
        nonzero_idx
        == list(
            range(
                min(nonzero_idx),
                max(nonzero_idx) + 1,
            )
        )
        if nonzero_idx
        else True
    )

    print("nonzero_slots_contiguous:", contiguous)


if __name__ == "__main__":
    main()
