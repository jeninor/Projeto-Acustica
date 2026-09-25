#!/usr/bin/env python3
"""
Pyroomacoustics 0.10.1 — CF2 multiband ISM synthesis closure test.

Purpose
-------
The previous room smoke test showed:
- structurally valid CF2 integration,
- exact direction symmetries,
- but up to ~3 dB discrepancy when the synthesized RIR was re-analyzed
  with room.octave_bands.energy().

This script determines whether that discrepancy comes from:
A) incorrect application of the 7 CF2 gains inside ISM, or
B) octave-band synthesis + re-analysis not being an identity operation.

Method
------
For the same max_order=0 room and six cardinal receivers:

1. Build seven "basis" RIR sets.
   For basis b, the source directivity returns:
       [0,0,...,1 at band b,...,0]
   for every direction.

2. Obtain the exact 7-band CF2 amplitude vector g[:,j] for each receiver j.

3. Predict the CF2 RIR by linear superposition:
       h_pred[j] = sum_b g[b,j] * h_basis[b,j]

4. Run the real CF2SevenBandDirectivity through Pyroomacoustics:
       h_actual[j]

5. Compare h_pred and h_actual sample-by-sample.

Interpretation
--------------
If the closure error is ~ numerical precision, then PRA is applying the
7-band gains correctly. Any discrepancy observed after calling
octave_bands.energy() is due to the analysis/synthesis filter-bank path,
not the CF2 adapter.

This test is ISM-only and uses max_order=0.
"""

import argparse
import math
import time

import numpy as np
import pyroomacoustics as pra

from pyroomacoustics.directivities import Directivity
from pyroomacoustics.soundsource import SoundSource

from cf2_interpolator import DEFAULT_BASE_OFFSET
from cf2_pra_directivity import (
    CF2SevenBandDirectivity,
    PRA_BAND_CENTERS_HZ,
)


FS = 16000
ABSORPTION = 0.35
MAX_ORDER = 0

ROOM_DIM = np.array([12.0, 12.0, 12.0], dtype=float)
SOURCE_POS = np.array([6.0, 6.0, 6.0], dtype=float)
RADIUS_M = 2.0


CASES = [
    ("FRONT",  np.array([+1.0,  0.0,  0.0])),
    ("TOP",    np.array([ 0.0,  0.0, +1.0])),
    ("LEFT",   np.array([ 0.0, +1.0,  0.0])),
    ("RIGHT",  np.array([ 0.0, -1.0,  0.0])),
    ("BOTTOM", np.array([ 0.0,  0.0, -1.0])),
    ("BACK",   np.array([-1.0,  0.0,  0.0])),
]


class FixedSevenBandDirectivity(Directivity):
    """
    Returns the same fixed 7-band AMPLITUDE vector for every direction.
    """

    def __init__(self, gains):
        self.gains = np.asarray(gains, dtype=float)

        if self.gains.shape != (7,):
            raise ValueError(
                f"Expected gains shape (7,), got {self.gains.shape}"
            )

    @property
    def is_impulse_response(self):
        return False

    @property
    def filter_len_ir(self):
        return 1

    def get_response(
        self,
        azimuth,
        colatitude=None,
        magnitude=False,
        degrees=True,
    ):
        az = np.atleast_1d(np.asarray(azimuth, dtype=float))
        n = az.size

        return np.broadcast_to(
            self.gains[:, None],
            (7, n),
        ).copy()

    def sample_rays(self, n_rays, rng=None):
        raise NotImplementedError(
            "Ray tracing is outside this ISM closure test."
        )


def flat_multiband_material():
    return pra.Material(
        energy_absorption={
            "description": "flat 0.35 at PRA octave-band centers",
            "coeffs": [ABSORPTION] * 7,
            "center_freqs": PRA_BAND_CENTERS_HZ.tolist(),
        }
    )


def make_room():
    room = pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=flat_multiband_material(),
        max_order=MAX_ORDER,
    )

    mic_positions = np.column_stack(
        [
            SOURCE_POS + RADIUS_M * vec
            for _, vec in CASES
        ]
    )

    room.add_microphone_array(
        mic_positions
    )

    return room


def run_with_directivity(directivity):
    room = make_room()

    src = SoundSource(
        SOURCE_POS,
        directivity=directivity,
    )

    room.add(src)

    t0 = time.perf_counter()
    room.compute_rir()
    elapsed = time.perf_counter() - t0

    rirs = [
        np.asarray(
            room.rir[j][0],
            dtype=float,
        )
        for j in range(len(CASES))
    ]

    return room, rirs, elapsed


def vector_to_angles_rad(v):
    v = np.asarray(v, dtype=float)
    v = v / np.linalg.norm(v)

    az = math.atan2(v[1], v[0])
    col = math.acos(
        float(
            np.clip(
                v[2],
                -1.0,
                1.0,
            )
        )
    )

    return az, col


def get_cf2_gain_matrix(directivity):
    az = []
    col = []

    for _, vec in CASES:
        a, c = vector_to_angles_rad(vec)
        az.append(a)
        col.append(c)

    gains = directivity.get_response(
        azimuth=np.asarray(az),
        colatitude=np.asarray(col),
        degrees=False,
    )

    expected_shape = (
        len(PRA_BAND_CENTERS_HZ),
        len(CASES),
    )

    if gains.shape != expected_shape:
        raise RuntimeError(
            f"Unexpected CF2 gain matrix shape {gains.shape}; "
            f"expected {expected_shape}"
        )

    return gains


def pad_to_length(x, n):
    if len(x) == n:
        return x

    if len(x) > n:
        return x[:n]

    out = np.zeros(n, dtype=float)
    out[:len(x)] = x
    return out


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--cf2",
        required=True,
    )

    ap.add_argument(
        "--base",
        type=int,
        default=DEFAULT_BASE_OFFSET,
    )

    ap.add_argument(
        "--handedness",
        choices=["top_to_left", "top_to_right"],
        default="top_to_left",
    )

    ap.add_argument(
        "--interpolation-domain",
        choices=["db", "linear_amplitude"],
        default="db",
    )

    args = ap.parse_args()

    print("=== CF2 multiband ISM synthesis closure test ===")
    print("pyroomacoustics:", pra.__version__)
    print("fs:", FS)
    print("max_order:", MAX_ORDER)
    print("bands_hz:", PRA_BAND_CENTERS_HZ.tolist())
    print("CF2:", args.cf2)

    cf2 = CF2SevenBandDirectivity(
        cf2_path=args.cf2,
        base_offset=args.base,
        speaker_azimuth_deg=0.0,
        speaker_colatitude_deg=90.0,
        speaker_roll_deg=0.0,
        rotation_handedness=args.handedness,
        interpolation_domain=args.interpolation_domain,
    )

    gains = get_cf2_gain_matrix(
        cf2
    )

    print("\n=== CF2 gain matrix ===")
    print("shape:", gains.shape)

    for j, (name, _) in enumerate(CASES):
        gains_db = (
            20.0
            * np.log10(
                np.maximum(
                    gains[:, j],
                    1e-15,
                )
            )
        )

        print(
            f"{name:7s}:",
            np.array2string(
                gains_db,
                precision=4,
                suppress_small=False,
            ),
            "dB",
        )

    print("\n=== Building seven PRA synthesis basis RIR sets ===")

    basis_rirs = []

    for bi, f in enumerate(PRA_BAND_CENTERS_HZ):
        one_hot = np.zeros(7, dtype=float)
        one_hot[bi] = 1.0

        _, rirs, elapsed = run_with_directivity(
            FixedSevenBandDirectivity(
                one_hot
            )
        )

        basis_rirs.append(
            rirs
        )

        print(
            f"basis {bi} ({f:g} Hz): "
            f"compute_rir={elapsed:.6f} s | "
            f"rir_len={len(rirs[0])}"
        )

    print("\n=== Running actual CF2 room ===")

    _, actual_rirs, cf2_elapsed = (
        run_with_directivity(
            cf2
        )
    )

    print(
        "CF2 compute_rir_elapsed_s:",
        cf2_elapsed,
    )

    print("\n=== Linear synthesis closure ===")

    global_max_abs = 0.0
    global_max_rel_l2 = 0.0
    global_max_rmse = 0.0

    for j, (name, _) in enumerate(CASES):
        max_len = max(
            len(actual_rirs[j]),
            *[
                len(
                    basis_rirs[bi][j]
                )
                for bi in range(7)
            ],
        )

        pred = np.zeros(
            max_len,
            dtype=float,
        )

        for bi in range(7):
            pred += (
                gains[bi, j]
                * pad_to_length(
                    basis_rirs[bi][j],
                    max_len,
                )
            )

        actual = pad_to_length(
            actual_rirs[j],
            max_len,
        )

        diff = actual - pred

        max_abs = float(
            np.max(
                np.abs(
                    diff
                )
            )
        )

        rmse = float(
            np.sqrt(
                np.mean(
                    diff * diff
                )
            )
        )

        denom = float(
            np.linalg.norm(
                actual
            )
        )

        rel_l2 = (
            float(
                np.linalg.norm(
                    diff
                )
                / denom
            )
            if denom > 0.0
            else float("nan")
        )

        energy_actual = float(
            np.sum(
                actual * actual
            )
        )

        energy_pred = float(
            np.sum(
                pred * pred
            )
        )

        energy_ratio_db = (
            10.0
            * math.log10(
                energy_actual
                / energy_pred
            )
            if (
                energy_actual > 0.0
                and energy_pred > 0.0
            )
            else float("nan")
        )

        global_max_abs = max(
            global_max_abs,
            max_abs,
        )

        global_max_rmse = max(
            global_max_rmse,
            rmse,
        )

        if math.isfinite(rel_l2):
            global_max_rel_l2 = max(
                global_max_rel_l2,
                rel_l2,
            )

        print(
            f"{name:7s} "
            f"max_abs={max_abs:.3e} | "
            f"rmse={rmse:.3e} | "
            f"rel_l2={rel_l2:.3e} | "
            f"actual_vs_pred_energy={energy_ratio_db:+.6e} dB"
        )

    print("\n=== Global closure summary ===")
    print(
        "global_max_abs_sample_error:",
        global_max_abs,
    )
    print(
        "global_max_rmse:",
        global_max_rmse,
    )
    print(
        "global_max_relative_l2_error:",
        global_max_rel_l2,
    )

    print("\n=== Interpretation ===")

    tol = 1e-10

    if (
        global_max_abs < tol
        and global_max_rel_l2 < tol
    ):
        print("PASS")
        print(
            "The actual CF2 RIR is exactly reproduced by linear combination "
            "of Pyroomacoustics' seven band-basis RIRs."
        )
        print(
            "Therefore the CF2 7-band gains are being applied correctly "
            "inside the ISM synthesis path."
        )
        print(
            "The larger dB discrepancies observed after "
            "room.octave_bands.energy() come from band synthesis/re-analysis "
            "behavior, not from the CF2 adapter or coordinate mapping."
        )
    else:
        print("FAIL")
        print(
            "The actual CF2 RIR does not close against the seven synthesis "
            "basis RIRs. Inspect ISM reconstruction before benchmarking."
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
