#!/usr/bin/env python3
"""
End-to-end room smoke test for CF2SevenBandDirectivity.

Pipeline under test
-------------------
raw CF2 magnitude
  -> validated CF2 interpolator
  -> CF2SevenBandDirectivity (7 x n_images)
  -> SoundSource
  -> Room.add()
  -> explicit 7-band Pyroomacoustics room
  -> compute_rir()
  -> octave-band RIR energy

Design
------
- max_order=0: direct path only; reflections are deliberately excluded.
- Six cardinal receivers at exactly the same radius.
- A separate OMNI room provides the reference path for every receiver.
- Both rooms are explicit 7-band rooms with flat absorption=0.35.
- Relative octave-band RIR energy is compared with raw CF2 dB values.

This is an ISM integration smoke test, not the final performance benchmark.
"""

import argparse
import math
import time

import numpy as np
import pyroomacoustics as pra
from pyroomacoustics.soundsource import SoundSource

from cf2_interpolator import (
    FREQUENCIES_HZ,
    DEFAULT_BASE_OFFSET,
)

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


def canonical_cases():
    return [
        {
            "name": "FRONT",
            "vector": np.array([+1.0, 0.0, 0.0]),
            "ri": 0,
            "ai": 0,
        },
        {
            "name": "TOP",
            "vector": np.array([0.0, 0.0, +1.0]),
            "ri": 0,
            "ai": 18,
        },
        {
            "name": "LEFT",
            "vector": np.array([0.0, +1.0, 0.0]),
            "ri": 18,
            "ai": 18,
        },
        {
            "name": "RIGHT",
            "vector": np.array([0.0, -1.0, 0.0]),
            "ri": 54,
            "ai": 18,
        },
        {
            "name": "BOTTOM",
            "vector": np.array([0.0, 0.0, -1.0]),
            "ri": 36,
            "ai": 18,
        },
        {
            "name": "BACK",
            "vector": np.array([-1.0, 0.0, 0.0]),
            "ri": 0,
            "ai": 36,
        },
    ]


CASES = canonical_cases()


def flat_multiband_material():
    return pra.Material(
        energy_absorption={
            "description": "flat 0.35 at PRA octave-band centers",
            "coeffs": [ABSORPTION] * len(PRA_BAND_CENTERS_HZ),
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
            SOURCE_POS + RADIUS_M * case["vector"]
            for case in CASES
        ]
    )

    room.add_microphone_array(mic_positions)

    return room


def run_omni():
    room = make_room()
    room.add_source(SOURCE_POS)

    t0 = time.perf_counter()
    room.compute_rir()
    elapsed = time.perf_counter() - t0

    return room, elapsed


def run_cf2(directivity):
    room = make_room()

    src = SoundSource(
        SOURCE_POS,
        directivity=directivity,
    )

    room.add(src)

    t0 = time.perf_counter()
    room.compute_rir()
    elapsed = time.perf_counter() - t0

    return room, elapsed


def raw_cf2_db(interpolator, frequency_hz, rotation_index, arc_index):
    idx = np.where(
        np.isclose(
            FREQUENCIES_HZ,
            float(frequency_hz),
            rtol=0.0,
            atol=1e-12,
        )
    )[0]

    if len(idx) != 1:
        raise RuntimeError(
            f"{frequency_hz:g} Hz is not an exact CF2 stored frequency."
        )

    fi = int(idx[0])

    return float(
        interpolator.balloon_db[
            fi,
            int(rotation_index),
            int(arc_index),
        ]
    )


def band_energy(room, rir):
    e = np.asarray(
        room.octave_bands.energy(
            np.asarray(rir, dtype=float)
        ),
        dtype=float,
    )

    if e.shape != (7,):
        raise RuntimeError(
            f"Unexpected octave-band energy shape {e.shape}; expected (7,)"
        )

    return e


def rir_matrix(room):
    result = []

    for mic_index in range(len(CASES)):
        rir = np.asarray(
            room.rir[mic_index][0],
            dtype=float,
        )

        if rir.size == 0:
            raise RuntimeError(
                f"Empty RIR for mic {mic_index}"
            )

        if not np.all(np.isfinite(rir)):
            raise RuntimeError(
                f"Non-finite RIR for mic {mic_index}"
            )

        result.append(rir)

    return result


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument("--cf2", required=True)

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

    print("=== CF2 7-band Room ISM smoke test ===")
    print("pyroomacoustics:", pra.__version__)
    print("fs:", FS)
    print("room_dim_m:", ROOM_DIM.tolist())
    print("source_pos_m:", SOURCE_POS.tolist())
    print("receiver_radius_m:", RADIUS_M)
    print("max_order:", MAX_ORDER)
    print("PRA_band_centers_hz:", PRA_BAND_CENTERS_HZ.tolist())
    print("CF2:", args.cf2)

    directivity = CF2SevenBandDirectivity(
        cf2_path=args.cf2,
        base_offset=args.base,
        speaker_azimuth_deg=0.0,
        speaker_colatitude_deg=90.0,
        speaker_roll_deg=0.0,
        rotation_handedness=args.handedness,
        interpolation_domain=args.interpolation_domain,
    )

    print(
        "valid_cf2_frequencies_hz:",
        directivity.interpolator.valid_frequencies_hz.tolist(),
    )

    print("\n=== OMNI reference room ===")
    omni_room, omni_time = run_omni()
    print("room.is_multi_band:", omni_room.is_multi_band)
    print("compute_rir_elapsed_s:", omni_time)

    print("\n=== CF2 room ===")
    cf2_room, cf2_time = run_cf2(directivity)
    print("room.is_multi_band:", cf2_room.is_multi_band)
    print("compute_rir_elapsed_s:", cf2_time)
    print(
        "stored_directivity_type:",
        type(cf2_room.sources[0].directivity),
    )

    omni_rirs = rir_matrix(omni_room)
    cf2_rirs = rir_matrix(cf2_room)

    # Structural control: equal-distance direct paths in OMNI must have
    # identical RIRs (or numerically indistinguishable).
    omni_ref = omni_rirs[0]
    omni_max_sample_diff = max(
        float(np.max(np.abs(rir - omni_ref)))
        for rir in omni_rirs
    )

    print("\n=== OMNI equal-distance structural control ===")
    print("omni_max_abs_sample_difference:", omni_max_sample_diff)

    print("\n=== End-to-end octave-band comparison ===")

    max_abs_error_db = 0.0
    errors_by_band = {float(f): [] for f in PRA_BAND_CENTERS_HZ}

    omni_band_energies = [
        band_energy(omni_room, rir)
        for rir in omni_rirs
    ]

    cf2_band_energies = [
        band_energy(cf2_room, rir)
        for rir in cf2_rirs
    ]

    for j, case in enumerate(CASES):
        print(f"\n{case['name']}")

        e_omni = omni_band_energies[j]
        e_cf2 = cf2_band_energies[j]

        for bi, f in enumerate(PRA_BAND_CENTERS_HZ):
            if e_omni[bi] <= 0.0 or e_cf2[bi] <= 0.0:
                measured_db = float("-inf")
            else:
                measured_db = 10.0 * math.log10(
                    e_cf2[bi] / e_omni[bi]
                )

            expected_db = raw_cf2_db(
                directivity.interpolator,
                f,
                case["ri"],
                case["ai"],
            )

            error_db = measured_db - expected_db

            if math.isfinite(error_db):
                max_abs_error_db = max(
                    max_abs_error_db,
                    abs(error_db),
                )
                errors_by_band[float(f)].append(
                    abs(error_db)
                )

            print(
                f"  {f:7g} Hz "
                f"expected={expected_db:9.4f} dB | "
                f"RIR_band={measured_db:9.4f} dB | "
                f"error={error_db:+9.4f} dB"
            )

    print("\n=== Error summary ===")
    print("max_abs_room_error_db:", max_abs_error_db)

    for f in PRA_BAND_CENTERS_HZ:
        errs = errors_by_band[float(f)]
        if errs:
            print(
                f"{f:7g} Hz "
                f"mean_abs_error_db={np.mean(errs):.6f} | "
                f"max_abs_error_db={np.max(errs):.6f}"
            )

    print("\n=== Interpretation ===")

    structural_pass = (
        omni_max_sample_diff < 1e-12
        and len(cf2_rirs) == 6
        and all(np.all(np.isfinite(r)) for r in cf2_rirs)
    )

    if structural_pass:
        print("PASS: room-level CF2 ISM integration is structurally valid.")
    else:
        print("FAIL: structural room-level integration check failed.")
        raise SystemExit(1)

    print(
        "The dB errors above are an end-to-end diagnostic after PRA octave-band "
        "RIR synthesis and re-analysis; they are not parser/interpolator errors."
    )
    print(
        "Do not apply a strict acoustic tolerance until we observe this first "
        "room-level result."
    )
    print(
        "If the errors are acceptably small and systematic, the next step is "
        "the 25-microphone Stage-1 benchmark with OMNI/CARDIOID/CF2."
    )


if __name__ == "__main__":
    main()
