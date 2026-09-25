#!/usr/bin/env python3
"""
Stage-3 CF2 horizontal orientation sweep.

Purpose
-------
Characterize how the validated vectorized CF2 loudspeaker directivity changes
the spatial acoustic field when only the source azimuth is varied.

This is NOT yet a GA/PSO/Tabu optimization. It is the deterministic landscape
experiment that should precede the optimizers.

Fixed acoustic configuration
----------------------------
Room:        6 x 5 x 3 m
Source:      [2.0, 2.5, 1.5]
Receivers:   5 x 5 grid, 25 mics, z=1.2 m
fs:          16000 Hz
max_order:   10
Absorption:  0.35 at PRA centers [125..8000] Hz
Directivity: real CF2, vectorized adapter
Colatitude:  90 deg (horizontal forward axis)
Roll:        0 deg

Default sweep
-------------
azimuth = 0, 5, 10, ..., 355 deg

Metrics per orientation
-----------------------
For each microphone:
    RIR energy = sum(h[n]^2)
    relative level = 10 log10(RIR energy)

Per orientation:
    mean linear energy
    mean relative level in dB
    std of relative levels in dB
    min/max level
    level range
    p10/p90 and p90-p10 spread
    compute_rir time

Important
---------
The dB values are relative RIR-energy levels, not calibrated SPL dB re 20 uPa.
They are useful for comparing orientation/uniformity under the same simulation.

No octave-band re-analysis is used as an exact inversion of PRA synthesis.
"""

import argparse
import csv
import json
import math
import os
import platform
import time
from pathlib import Path

import numpy as np
import pyroomacoustics as pra
from pyroomacoustics.soundsource import SoundSource

from cf2_interpolator import DEFAULT_BASE_OFFSET
from cf2_pra_directivity_fast import (
    CF2SevenBandDirectivityFast,
    PRA_BAND_CENTERS_HZ,
)


ROOM_DIM = np.array([6.0, 5.0, 3.0], dtype=float)
SOURCE_POS = np.array([2.0, 2.5, 1.5], dtype=float)

FS = 16000
MAX_ORDER = 10
ABSORPTION = 0.35

SOURCE_COLATITUDE_DEG = 90.0
SOURCE_ROLL_DEG = 0.0


def make_mic_grid():
    xs = np.linspace(1.0, 5.0, 5)
    ys = np.linspace(0.75, 4.25, 5)
    z = 1.2

    rows = []
    idx = 0

    for row, y in enumerate(ys):
        for col, x in enumerate(xs):
            rows.append(
                {
                    "mic_index": idx,
                    "row": row,
                    "col": col,
                    "x": float(x),
                    "y": float(y),
                    "z": float(z),
                }
            )
            idx += 1

    return rows


MIC_GRID = make_mic_grid()


def flat_material():
    return pra.Material(
        energy_absorption={
            "description": "flat 0.35 at PRA octave-band centers",
            "coeffs": [ABSORPTION] * 7,
            "center_freqs": PRA_BAND_CENTERS_HZ.tolist(),
        }
    )


def make_directivity(args, azimuth_deg):
    return CF2SevenBandDirectivityFast(
        cf2_path=args.cf2,
        base_offset=args.base,
        speaker_azimuth_deg=float(azimuth_deg),
        speaker_colatitude_deg=SOURCE_COLATITUDE_DEG,
        speaker_roll_deg=SOURCE_ROLL_DEG,
        rotation_handedness=args.handedness,
        interpolation_domain=args.interpolation_domain,
    )


def make_room(directivity):
    room = pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=flat_material(),
        max_order=MAX_ORDER,
    )

    mic_positions = np.array(
        [[m["x"], m["y"], m["z"]] for m in MIC_GRID],
        dtype=float,
    ).T

    room.add_microphone_array(mic_positions)

    src = SoundSource(
        SOURCE_POS,
        directivity=directivity,
    )
    room.add(src)

    if not room.is_multi_band:
        raise RuntimeError("Expected explicit multiband room.")

    return room


def run_orientation(args, azimuth_deg):
    directivity = make_directivity(args, azimuth_deg)
    room = make_room(directivity)

    t0 = time.perf_counter()
    room.compute_rir()
    elapsed = time.perf_counter() - t0

    mic_rows = []
    levels_db = []
    energies = []

    for mic in MIC_GRID:
        i = mic["mic_index"]
        rir = np.asarray(room.rir[i][0], dtype=float)

        if rir.size == 0 or not np.all(np.isfinite(rir)):
            raise RuntimeError(
                f"Invalid RIR at azimuth={azimuth_deg}, mic={i}"
            )

        energy = float(np.sum(rir * rir))
        level_db = (
            10.0 * math.log10(energy)
            if energy > 0.0
            else float("-inf")
        )

        energies.append(energy)
        levels_db.append(level_db)

        mic_rows.append(
            {
                "azimuth_deg": float(azimuth_deg),
                **mic,
                "rir_len": int(len(rir)),
                "rir_energy": energy,
                "relative_level_db": level_db,
                "rir_peak": float(np.max(np.abs(rir))),
            }
        )

    energies = np.asarray(energies, dtype=float)
    levels_db = np.asarray(levels_db, dtype=float)

    summary = {
        "azimuth_deg": float(azimuth_deg),
        "compute_rir_s": float(elapsed),
        "mean_energy": float(np.mean(energies)),
        "sum_energy": float(np.sum(energies)),
        "mean_level_db": float(np.mean(levels_db)),
        "std_level_db": float(np.std(levels_db, ddof=0)),
        "min_level_db": float(np.min(levels_db)),
        "max_level_db": float(np.max(levels_db)),
        "range_level_db": float(np.ptp(levels_db)),
        "p10_level_db": float(np.percentile(levels_db, 10)),
        "p90_level_db": float(np.percentile(levels_db, 90)),
        "p90_p10_db": float(
            np.percentile(levels_db, 90)
            - np.percentile(levels_db, 10)
        ),
        "n_mics": len(MIC_GRID),
    }

    return summary, mic_rows


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(rows[0].keys()),
        )
        writer.writeheader()
        writer.writerows(rows)


def circular_distance_deg(a, b):
    return abs(((a - b + 180.0) % 360.0) - 180.0)


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

    ap.add_argument(
        "--azimuth-start",
        type=float,
        default=0.0,
    )

    ap.add_argument(
        "--azimuth-stop",
        type=float,
        default=360.0,
        help="Exclusive stop.",
    )

    ap.add_argument(
        "--azimuth-step",
        type=float,
        default=5.0,
    )

    ap.add_argument(
        "--environment-label",
        default="ufv-cluster-orientation-sweep",
    )

    ap.add_argument(
        "--output-dir",
        default="results/cf2_orientation_sweep",
    )

    args = ap.parse_args()

    if args.azimuth_step <= 0.0:
        raise ValueError("--azimuth-step must be positive.")

    azimuths = np.arange(
        args.azimuth_start,
        args.azimuth_stop,
        args.azimuth_step,
        dtype=float,
    )

    if len(azimuths) == 0:
        raise ValueError("Empty azimuth sweep.")

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    print("=== Stage-3 CF2 horizontal orientation sweep ===")
    print("environment:", args.environment_label)
    print("python:", platform.python_version())
    print("pyroomacoustics:", pra.__version__)
    print("numpy:", np.__version__)
    print("threads:", {
        k: os.getenv(k, "")
        for k in [
            "PRA_NUM_THREADS",
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
        ]
    })
    print("CF2:", args.cf2)
    print("room_dim_m:", ROOM_DIM.tolist())
    print("source_pos_m:", SOURCE_POS.tolist())
    print("n_mics:", len(MIC_GRID))
    print("fs:", FS)
    print("max_order:", MAX_ORDER)
    print("azimuth_count:", len(azimuths))
    print("azimuth_step_deg:", args.azimuth_step)

    summary_rows = []
    mic_rows = []

    for idx, az in enumerate(azimuths, start=1):
        summary, detail = run_orientation(args, az)
        summary_rows.append(summary)
        mic_rows.extend(detail)

        print(
            f"[{idx:02d}/{len(azimuths):02d}] "
            f"az={az:7.2f} deg | "
            f"time={summary['compute_rir_s']:.4f} s | "
            f"meanE={summary['mean_energy']:.9e} | "
            f"meanL={summary['mean_level_db']:+.4f} dB | "
            f"stdL={summary['std_level_db']:.4f} dB | "
            f"minL={summary['min_level_db']:+.4f} dB"
        )

    # Relative mean energy referenced to azimuth 0 if present,
    # otherwise to the first tested azimuth.
    ref_row = min(
        summary_rows,
        key=lambda r: circular_distance_deg(
            r["azimuth_deg"],
            0.0,
        ),
    )
    ref_energy = ref_row["mean_energy"]

    for row in summary_rows:
        row["mean_energy_vs_reference_db"] = (
            10.0
            * math.log10(
                row["mean_energy"] / ref_energy
            )
        )
        row["reference_azimuth_deg"] = ref_row["azimuth_deg"]

    write_csv(
        outdir / "orientation_summary.csv",
        summary_rows,
    )
    write_csv(
        outdir / "orientation_mics.csv",
        mic_rows,
    )

    # Descriptive candidates. These are NOT combined into one objective yet.
    lowest_std = min(
        summary_rows,
        key=lambda r: r["std_level_db"],
    )
    highest_min = max(
        summary_rows,
        key=lambda r: r["min_level_db"],
    )
    highest_mean = max(
        summary_rows,
        key=lambda r: r["mean_level_db"],
    )
    smallest_p90p10 = min(
        summary_rows,
        key=lambda r: r["p90_p10_db"],
    )

    # Aggregate symmetry diagnostic for azimuth a vs 360-a.
    by_az = {
        round(r["azimuth_deg"] % 360.0, 10): r
        for r in summary_rows
    }

    symmetry_diffs = []

    for a, r in by_az.items():
        mirror = round((-a) % 360.0, 10)

        if mirror in by_az:
            rr = by_az[mirror]

            symmetry_diffs.append(
                {
                    "a": a,
                    "mirror": mirror,
                    "mean_energy_abs_diff": abs(
                        r["mean_energy"]
                        - rr["mean_energy"]
                    ),
                    "std_level_abs_diff_db": abs(
                        r["std_level_db"]
                        - rr["std_level_db"]
                    ),
                }
            )

    max_sym_energy = (
        max(
            d["mean_energy_abs_diff"]
            for d in symmetry_diffs
        )
        if symmetry_diffs
        else float("nan")
    )
    max_sym_std_db = (
        max(
            d["std_level_abs_diff_db"]
            for d in symmetry_diffs
        )
        if symmetry_diffs
        else float("nan")
    )

    result = {
        "environment_label": args.environment_label,
        "room_dim_m": ROOM_DIM.tolist(),
        "source_pos_m": SOURCE_POS.tolist(),
        "source_colatitude_deg": SOURCE_COLATITUDE_DEG,
        "source_roll_deg": SOURCE_ROLL_DEG,
        "n_mics": len(MIC_GRID),
        "fs_hz": FS,
        "max_order": MAX_ORDER,
        "absorption": ABSORPTION,
        "band_centers_hz": PRA_BAND_CENTERS_HZ.tolist(),
        "azimuth_start_deg": args.azimuth_start,
        "azimuth_stop_deg_exclusive": args.azimuth_stop,
        "azimuth_step_deg": args.azimuth_step,
        "n_orientations": len(summary_rows),
        "reference_azimuth_deg": ref_row["azimuth_deg"],
        "reference_mean_energy": ref_energy,
        "descriptive_candidates": {
            "lowest_std_level_db": lowest_std,
            "highest_min_level_db": highest_min,
            "highest_mean_level_db": highest_mean,
            "smallest_p90_p10_db": smallest_p90p10,
        },
        "symmetry_diagnostic": {
            "max_mean_energy_abs_diff": max_sym_energy,
            "max_std_level_abs_diff_db": max_sym_std_db,
        },
        "metric_note": (
            "relative_level_db = 10*log10(sum(rir^2)); "
            "not calibrated physical SPL."
        ),
    }

    with (outdir / "orientation_config_and_candidates.json").open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            result,
            f,
            indent=2,
            ensure_ascii=False,
        )

    print()
    print("=" * 90)
    print("DESCRIPTIVE ORIENTATION CANDIDATES")
    print("=" * 90)
    print(
        "Lowest level std:",
        f"{lowest_std['azimuth_deg']:.1f} deg",
        f"std={lowest_std['std_level_db']:.4f} dB",
    )
    print(
        "Highest minimum level:",
        f"{highest_min['azimuth_deg']:.1f} deg",
        f"min={highest_min['min_level_db']:+.4f} dB",
    )
    print(
        "Highest mean level:",
        f"{highest_mean['azimuth_deg']:.1f} deg",
        f"mean={highest_mean['mean_level_db']:+.4f} dB",
    )
    print(
        "Smallest p90-p10 spread:",
        f"{smallest_p90p10['azimuth_deg']:.1f} deg",
        f"spread={smallest_p90p10['p90_p10_db']:.4f} dB",
    )

    print()
    print("=== Symmetry diagnostic ===")
    print(
        "max mirrored mean-energy absolute difference:",
        max_sym_energy,
    )
    print(
        "max mirrored std-level difference dB:",
        max_sym_std_db,
    )

    print()
    print("=== Files ===")
    print(outdir / "orientation_summary.csv")
    print(outdir / "orientation_mics.csv")
    print(outdir / "orientation_config_and_candidates.json")


if __name__ == "__main__":
    main()
