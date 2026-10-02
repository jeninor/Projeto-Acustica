#!/usr/bin/env python3
"""
Correctness + performance comparison:
legacy scalar CF2 adapter vs vectorized CF2 adapter.

The test must pass acoustic equivalence before its speed result is accepted.
"""

import argparse
import math
import statistics
import time

import numpy as np
import pyroomacoustics as pra
from pyroomacoustics.soundsource import SoundSource

from cf2_interpolator import DEFAULT_BASE_OFFSET
from cf2_pra_directivity import CF2SevenBandDirectivity
from cf2_pra_directivity_fast import (
    CF2SevenBandDirectivityFast,
    PRA_BAND_CENTERS_HZ,
)


FS = 16000
ROOM_DIM = np.array([6.0, 5.0, 3.0], dtype=float)
SOURCE_POS = np.array([2.0, 2.5, 1.5], dtype=float)
ABSORPTION = 0.35
MAX_ORDER = 10


def mic_grid():
    xs = np.linspace(1.0, 5.0, 5)
    ys = np.linspace(0.75, 4.25, 5)
    z = 1.2

    return np.array(
        [
            [x, y, z]
            for y in ys
            for x in xs
        ],
        dtype=float,
    ).T


MIC_POSITIONS = mic_grid()


def flat_material():
    return pra.Material(
        energy_absorption={
            "description": "flat 0.35",
            "coeffs": [ABSORPTION] * 7,
            "center_freqs": PRA_BAND_CENTERS_HZ.tolist(),
        }
    )


def make_room(directivity):
    room = pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=flat_material(),
        max_order=MAX_ORDER,
    )

    room.add_microphone_array(
        MIC_POSITIONS
    )

    src = SoundSource(
        SOURCE_POS,
        directivity=directivity,
    )

    room.add(src)

    return room


def run_room(directivity):
    room = make_room(
        directivity
    )

    t0 = time.perf_counter()
    room.compute_rir()
    elapsed = time.perf_counter() - t0

    rirs = [
        np.asarray(
            room.rir[i][0],
            dtype=float,
        )
        for i in range(
            MIC_POSITIONS.shape[1]
        )
    ]

    return room, rirs, elapsed


def random_directions(n, seed=12345):
    rng = np.random.default_rng(seed)

    # Uniform sphere.
    z = rng.uniform(
        -1.0,
        1.0,
        size=n,
    )

    az = rng.uniform(
        0.0,
        2.0 * np.pi,
        size=n,
    )

    col = np.arccos(z)

    # Add canonical/pole directions to exercise edge cases.
    az_extra = np.deg2rad(
        np.array(
            [0, 0, 90, 270, 180, 0],
            dtype=float,
        )
    )

    col_extra = np.deg2rad(
        np.array(
            [90, 0, 90, 90, 90, 180],
            dtype=float,
        )
    )

    return (
        np.concatenate(
            [az, az_extra]
        ),
        np.concatenate(
            [col, col_extra]
        ),
    )


def benchmark_get_response(obj, az, col, repetitions):
    times = []
    result = None

    # Warm-up.
    obj.get_response(
        az,
        col,
        degrees=False,
    )

    for _ in range(repetitions):
        t0 = time.perf_counter()

        result = obj.get_response(
            az,
            col,
            degrees=False,
        )

        times.append(
            time.perf_counter()
            - t0
        )

    return result, times


def compare_rirs(legacy, fast):
    if len(legacy) != len(fast):
        raise RuntimeError(
            "Different microphone RIR counts."
        )

    global_max_abs = 0.0
    global_max_rel_l2 = 0.0
    worst_mic = None

    for i, (a, b) in enumerate(
        zip(
            legacy,
            fast,
        )
    ):
        n = max(
            len(a),
            len(b),
        )

        aa = np.zeros(
            n,
            dtype=float,
        )

        bb = np.zeros(
            n,
            dtype=float,
        )

        aa[:len(a)] = a
        bb[:len(b)] = b

        diff = aa - bb

        max_abs = float(
            np.max(
                np.abs(diff)
            )
        )

        denom = float(
            np.linalg.norm(aa)
        )

        rel_l2 = (
            float(
                np.linalg.norm(diff)
                / denom
            )
            if denom > 0.0
            else 0.0
        )

        if (
            max_abs > global_max_abs
            or rel_l2 > global_max_rel_l2
        ):
            worst_mic = i

        global_max_abs = max(
            global_max_abs,
            max_abs,
        )

        global_max_rel_l2 = max(
            global_max_rel_l2,
            rel_l2,
        )

    return (
        global_max_abs,
        global_max_rel_l2,
        worst_mic,
    )


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
        choices=[
            "top_to_left",
            "top_to_right",
        ],
        default="top_to_left",
    )

    ap.add_argument(
        "--interpolation-domain",
        choices=[
            "db",
            "linear_amplitude",
        ],
        default="db",
    )

    ap.add_argument(
        "--n-directions",
        type=int,
        default=5000,
    )

    ap.add_argument(
        "--micro-repetitions",
        type=int,
        default=5,
    )

    args = ap.parse_args()

    common = dict(
        cf2_path=args.cf2,
        base_offset=args.base,
        speaker_azimuth_deg=0.0,
        speaker_colatitude_deg=90.0,
        speaker_roll_deg=0.0,
        rotation_handedness=args.handedness,
        interpolation_domain=args.interpolation_domain,
    )

    print(
        "=== Legacy vs vectorized CF2 adapter ==="
    )

    print(
        "pyroomacoustics:",
        pra.__version__,
    )

    print(
        "n_random_directions:",
        args.n_directions,
    )

    legacy = CF2SevenBandDirectivity(
        **common
    )

    fast = CF2SevenBandDirectivityFast(
        **common
    )

    az, col = random_directions(
        args.n_directions
    )

    print()
    print(
        "=== get_response correctness + microbenchmark ==="
    )

    legacy_response, legacy_times = (
        benchmark_get_response(
            legacy,
            az,
            col,
            args.micro_repetitions,
        )
    )

    fast_response, fast_times = (
        benchmark_get_response(
            fast,
            az,
            col,
            args.micro_repetitions,
        )
    )

    diff = (
        fast_response
        - legacy_response
    )

    max_amp_error = float(
        np.max(
            np.abs(
                diff
            )
        )
    )

    mask = (
        (legacy_response > 0.0)
        & (fast_response > 0.0)
    )

    db_error = np.zeros_like(
        diff
    )

    db_error[mask] = (
        20.0
        * np.log10(
            fast_response[mask]
            / legacy_response[mask]
        )
    )

    max_db_error = float(
        np.max(
            np.abs(
                db_error[mask]
            )
        )
    )

    legacy_med = statistics.median(
        legacy_times
    )

    fast_med = statistics.median(
        fast_times
    )

    print(
        "response_shape:",
        legacy_response.shape,
    )

    print(
        "max_abs_amplitude_error:",
        max_amp_error,
    )

    print(
        "max_abs_db_error:",
        max_db_error,
    )

    print(
        "legacy_get_response_median_s:",
        legacy_med,
    )

    print(
        "fast_get_response_median_s:",
        fast_med,
    )

    print(
        "get_response_speedup_x:",
        legacy_med / fast_med,
    )

    amp_tol = 1e-11
    db_tol = 1e-10

    print(
        "amplitude_tolerance:",
        amp_tol,
    )

    print(
        "db_tolerance:",
        db_tol,
    )

    if (
        max_amp_error > amp_tol
        or max_db_error > db_tol
    ):
        print(
            "FAIL: adapter responses differ beyond numerical tolerance."
        )
        raise SystemExit(1)

    print(
        "get_response_equivalence: PASS"
    )

    print()
    print(
        "=== Full 25-mic max_order=10 room comparison ==="
    )

    _, legacy_rirs, legacy_room_s = (
        run_room(
            legacy
        )
    )

    _, fast_rirs, fast_room_s = (
        run_room(
            fast
        )
    )

    (
        max_rir_abs,
        max_rir_rel_l2,
        worst_mic,
    ) = compare_rirs(
        legacy_rirs,
        fast_rirs,
    )

    print(
        "legacy_compute_rir_s:",
        legacy_room_s,
    )

    print(
        "fast_compute_rir_s:",
        fast_room_s,
    )

    print(
        "room_speedup_x:",
        legacy_room_s
        / fast_room_s,
    )

    print(
        "max_abs_RIR_sample_error:",
        max_rir_abs,
    )

    print(
        "max_relative_L2_RIR_error:",
        max_rir_rel_l2,
    )

    print(
        "worst_mic:",
        worst_mic,
    )

    if (
        max_rir_abs < 1e-8
        and max_rir_rel_l2 < 1e-8
    ):
        print()
        print(
            "PASS: vectorized adapter is acoustically equivalent "
            "to the validated legacy adapter."
        )

        print(
            "The reported speedup can be used to decide whether "
            "to replace the scalar adapter before Colab/cluster."
        )
    else:
        print()
        print(
            "FAIL: room-level results differ beyond tolerance."
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
