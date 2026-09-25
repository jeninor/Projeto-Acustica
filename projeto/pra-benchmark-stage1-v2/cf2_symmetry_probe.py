"""
Check whether the decoded CF2 magnitude balloon is left/right symmetric.

Current decoded layout under test:
  base offset: 14232 bytes
  30 frequency slots
  72 rotations: 0..355 deg, step 5 deg
  37 arcs:      0..180 deg, step 5 deg
  float32 little-endian
  storage order: [frequency][rotation][arc]

For the CLF/CF2 reference arc through TOP and BOTTOM, reversing rotation
handedness maps rotation r -> (-r) mod 360.  Therefore this script compares:

    D[f, r, arc]  vs  D[f, (-r) mod 360, arc]

If the balloon is exactly symmetric under that mapping, the choice
top->left versus top->right does not change the decoded magnitude values
for this particular loudspeaker.
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
STEP_DEG = 5.0
DTYPE = np.dtype("<f4")
FLOATS_PER_BAND = N_ROT * N_ARC
BYTES_PER_BAND = FLOATS_PER_BAND * DTYPE.itemsize


def load_all(path, base_offset):
    raw = Path(path).read_bytes()

    needed = (
        base_offset
        + N_FREQ * BYTES_PER_BAND
    )

    if needed > len(raw):
        raise RuntimeError(
            f"File too short. "
            f"file_size={len(raw)}, "
            f"needed={needed}"
        )

    data = np.frombuffer(
        raw,
        dtype=DTYPE,
        count=N_FREQ * FLOATS_PER_BAND,
        offset=base_offset,
    ).copy()

    return data.reshape(
        N_FREQ,
        N_ROT,
        N_ARC,
    )


def mirror_rotation_indices():
    # 0->0, 5->355, 10->350, ..., 180->180
    return np.array(
        [
            (-r) % N_ROT
            for r in range(N_ROT)
        ],
        dtype=int,
    )


def metrics(diff):
    d = np.asarray(diff, dtype=np.float64)
    ad = np.abs(d)

    return {
        "max_abs": float(np.max(ad)),
        "mae": float(np.mean(ad)),
        "rmse": float(
            np.sqrt(
                np.mean(d * d)
            )
        ),
        "nonzero": int(
            np.count_nonzero(d)
        ),
        "total": int(d.size),
    }


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--cf2",
        required=True,
    )

    ap.add_argument(
        "--base",
        type=int,
        default=BASE_OFFSET,
    )

    args = ap.parse_args()

    balloon = load_all(
        args.cf2,
        args.base,
    )

    mirror_idx = mirror_rotation_indices()

    print("=== CF2 left/right symmetry probe ===")
    print("file:", args.cf2)
    print("shape:", balloon.shape)
    print("mapping: rotation r -> (-r) mod 360")
    print()

    global_worst = None
    global_worst_info = None

    print(
        "freq_hz      max_abs_dB       MAE_dB      RMSE_dB   nonzero/total"
    )

    for fi, freq in enumerate(
        FREQUENCIES_HZ
    ):
        band = balloon[fi]

        mirrored = band[
            mirror_idx,
            :
        ]

        diff = (
            band.astype(np.float64)
            -
            mirrored.astype(np.float64)
        )

        m = metrics(diff)

        print(
            f"{freq:7g} "
            f"{m['max_abs']:15.9f} "
            f"{m['mae']:12.9f} "
            f"{m['rmse']:12.9f} "
            f"{m['nonzero']:6d}/{m['total']}"
        )

        flat_index = int(
            np.argmax(
                np.abs(diff)
            )
        )

        ri, ai = np.unravel_index(
            flat_index,
            diff.shape,
        )

        worst = abs(
            float(
                diff[
                    ri,
                    ai,
                ]
            )
        )

        if (
            global_worst is None
            or worst > global_worst
        ):
            global_worst = worst

            mi = int(
                mirror_idx[
                    ri
                ]
            )

            global_worst_info = {
                "frequency_hz": float(freq),
                "rotation_deg": float(
                    ri * STEP_DEG
                ),
                "mirror_rotation_deg": float(
                    mi * STEP_DEG
                ),
                "arc_deg": float(
                    ai * STEP_DEG
                ),
                "value_db": float(
                    band[
                        ri,
                        ai,
                    ]
                ),
                "mirror_value_db": float(
                    band[
                        mi,
                        ai,
                    ]
                ),
                "difference_db": float(
                    diff[
                        ri,
                        ai,
                    ]
                ),
            }

    print()
    print("=== Global worst mirrored pair ===")
    for k, v in global_worst_info.items():
        print(f"{k}: {v}")

    print()
    print("=== Pole consistency over all frequencies ===")

    front_spans = (
        balloon[:, :, 0].max(axis=1)
        -
        balloon[:, :, 0].min(axis=1)
    )

    back_spans = (
        balloon[:, :, -1].max(axis=1)
        -
        balloon[:, :, -1].min(axis=1)
    )

    print(
        "max front-pole rotation span dB:",
        float(
            np.max(
                np.abs(front_spans)
            )
        ),
    )

    print(
        "max back-pole rotation span dB:",
        float(
            np.max(
                np.abs(back_spans)
            )
        ),
    )

    print()
    print("front pole by frequency:")
    for f, v in zip(
        FREQUENCIES_HZ,
        balloon[:, 0, 0],
    ):
        print(
            f"{f:7g} Hz -> "
            f"{float(v):10.4f} dB"
        )

    print()
    print("back pole by frequency:")
    for f, v in zip(
        FREQUENCIES_HZ,
        balloon[:, 0, -1],
    ):
        print(
            f"{f:7g} Hz -> "
            f"{float(v):10.4f} dB"
        )


if __name__ == "__main__":
    main()
