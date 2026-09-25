#!/usr/bin/env python3
"""
Stage-2 local benchmark: OMNI vs CARDIOID vs real CF2 directivity
using Pyroomacoustics 0.10.1 ISM in the SAME explicit 7-band room.

Scientific purpose
------------------
Measure the computational cost of source directivity while keeping fixed:
- room geometry
- source position
- microphone positions
- absorption
- sampling rate
- ISM max_order
- number of threads
- Pyroomacoustics version

Cases
-----
1. OMNI
2. CARDIOID, main axis +X
3. CF2 real loudspeaker directivity via vectorized CF2SevenBandDirectivityFast

Important methodological choices
--------------------------------
- Explicit 7-band room for ALL cases:
    125, 250, 500, 1000, 2000, 4000, 8000 Hz
    absorption = 0.35 at every band
- fs = 16000 Hz
- max_order = 10
- 25 microphone grid
- CF2 object is built ONCE before timing.
- Room construction is OUTSIDE compute_rir() timing.
- Warm-up rounds are excluded.
- Measured rounds use a deterministic balanced cyclic case order:
    round 1: OMNI -> CARDIOID -> CF2
    round 2: CARDIOID -> CF2 -> OMNI
    round 3: CF2 -> OMNI -> CARDIOID
    repeat...
- Acoustic output uses time-domain RIR metrics.
  We deliberately do NOT treat octave_bands.energy() as an exact inverse
  of PRA's multiband synthesis, because the closure experiments showed that
  synthesis/re-analysis is not identity even though CF2 gains are applied
  correctly inside ISM.

This runner is intended to be reused later in Colab and on the UFV cluster
with the same code and only a different --environment-label.
"""

import argparse
import csv
import json
import math
import os
import platform
import resource
import statistics
import time
from pathlib import Path

import numpy as np
import pyroomacoustics as pra

from pyroomacoustics.directivities import (
    Cardioid,
    DirectionVector,
)
from pyroomacoustics.soundsource import SoundSource

from cf2_interpolator import DEFAULT_BASE_OFFSET
from cf2_pra_directivity_fast import (
    CF2SevenBandDirectivityFast,
    PRA_BAND_CENTERS_HZ,
)


# ======================================================================================
# Fixed benchmark configuration
# ======================================================================================

ROOM_DIM = np.array([6.0, 5.0, 3.0], dtype=float)
SOURCE_POS = np.array([2.0, 2.5, 1.5], dtype=float)

FS = 16000
MAX_ORDER = 10
ABSORPTION = 0.35

SOURCE_AZIMUTH_DEG = 0.0
SOURCE_COLATITUDE_DEG = 90.0
SOURCE_ROLL_DEG = 0.0

CASES = ("OMNI", "CARDIOID", "CF2")


def make_mic_grid():
    """
    Exact Stage-1 5x5 grid.

    x = 1,2,3,4,5 m
    y = 0.75,1.625,2.5,3.375,4.25 m
    z = 1.2 m

    Ordering is row-major in y, then x.
    """
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


# ======================================================================================
# Environment
# ======================================================================================

def cpu_model():
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass

    return platform.processor() or "unknown"


def cpu_affinity_count():
    try:
        return len(os.sched_getaffinity(0))
    except Exception:
        return None


def peak_rss_mb():
    """
    Linux ru_maxrss is KiB.
    Colab and the UFV cluster are also expected to be Linux.
    """
    return float(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    )


def environment_info(label):
    return {
        "environment": label,
        "python": platform.python_version(),
        "pyroomacoustics": getattr(pra, "__version__", "unknown"),
        "numpy": np.__version__,
        "platform": platform.platform(),
        "cpu": cpu_model(),
        "cpu_count_os": os.cpu_count(),
        "cpu_affinity_count": cpu_affinity_count(),
        "pra_num_threads": os.getenv("PRA_NUM_THREADS", ""),
        "omp_num_threads": os.getenv("OMP_NUM_THREADS", ""),
        "openblas_num_threads": os.getenv("OPENBLAS_NUM_THREADS", ""),
        "mkl_num_threads": os.getenv("MKL_NUM_THREADS", ""),
    }


# ======================================================================================
# Room / directivity construction
# ======================================================================================

def flat_multiband_material():
    return pra.Material(
        energy_absorption={
            "description": "flat 0.35 at PRA octave-band centers",
            "coeffs": [ABSORPTION] * len(PRA_BAND_CENTERS_HZ),
            "center_freqs": PRA_BAND_CENTERS_HZ.tolist(),
        }
    )


def cardioid_front_x():
    return Cardioid(
        orientation=DirectionVector(
            azimuth=SOURCE_AZIMUTH_DEG,
            colatitude=SOURCE_COLATITUDE_DEG,
            degrees=True,
        ),
        gain=1.0,
    )


def make_room(case_name, cf2_directivity):
    room = pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=flat_multiband_material(),
        max_order=MAX_ORDER,
    )

    mic_positions = np.array(
        [
            [m["x"], m["y"], m["z"]]
            for m in MIC_GRID
        ],
        dtype=float,
    ).T

    room.add_microphone_array(
        mic_positions
    )

    if case_name == "OMNI":
        room.add_source(
            SOURCE_POS
        )

    elif case_name == "CARDIOID":
        room.add_source(
            SOURCE_POS,
            directivity=cardioid_front_x(),
        )

    elif case_name == "CF2":
        src = SoundSource(
            SOURCE_POS,
            directivity=cf2_directivity,
        )
        room.add(src)

    else:
        raise ValueError(
            f"Unknown benchmark case: {case_name}"
        )

    if not room.is_multi_band:
        raise RuntimeError(
            "Benchmark room must be explicit multi-band."
        )

    if len(room.walls[0].absorption) != 7:
        raise RuntimeError(
            "Expected 7-band wall absorption."
        )

    return room


# ======================================================================================
# Acoustic metrics
# ======================================================================================

def extract_rir_metrics(room, case_name):
    if len(room.rir) != len(MIC_GRID):
        raise RuntimeError(
            f"{case_name}: expected {len(MIC_GRID)} microphones, "
            f"got {len(room.rir)}"
        )

    detail = []

    for mic in MIC_GRID:
        i = mic["mic_index"]

        rir = np.asarray(
            room.rir[i][0],
            dtype=float,
        )

        if rir.size == 0:
            raise RuntimeError(
                f"{case_name}: empty RIR at microphone {i}"
            )

        if not np.all(np.isfinite(rir)):
            raise RuntimeError(
                f"{case_name}: non-finite RIR at microphone {i}"
            )

        energy = float(
            np.sum(
                rir * rir
            )
        )

        peak = float(
            np.max(
                np.abs(
                    rir
                )
            )
        )

        detail.append(
            {
                "case": case_name,
                **mic,
                "rir_len": int(len(rir)),
                "rir_energy": energy,
                "rir_energy_db": (
                    10.0 * math.log10(energy)
                    if energy > 0.0
                    else float("-inf")
                ),
                "rir_peak": peak,
            }
        )

    energies = np.asarray(
        [r["rir_energy"] for r in detail],
        dtype=float,
    )

    peaks = np.asarray(
        [r["rir_peak"] for r in detail],
        dtype=float,
    )

    lengths = np.asarray(
        [r["rir_len"] for r in detail],
        dtype=int,
    )

    energy_mean = float(
        np.mean(energies)
    )

    energy_std = float(
        np.std(
            energies,
            ddof=1,
        )
    )

    summary = {
        "n_mics": int(len(detail)),
        "rir_len_min": int(np.min(lengths)),
        "rir_len_max": int(np.max(lengths)),
        "rir_energy_mean": energy_mean,
        "rir_energy_std": energy_std,
        "rir_energy_cv": (
            energy_std / energy_mean
            if energy_mean != 0.0
            else float("nan")
        ),
        "rir_energy_min": float(np.min(energies)),
        "rir_energy_max": float(np.max(energies)),
        "rir_energy_sum": float(np.sum(energies)),
        "rir_peak_mean": float(np.mean(peaks)),
        "rir_peak_min": float(np.min(peaks)),
        "rir_peak_max": float(np.max(peaks)),
    }

    return summary, detail


# ======================================================================================
# Benchmark execution
# ======================================================================================

def run_once(case_name, cf2_directivity):
    """
    Construct room outside timer, time compute_rir only.
    """
    room = make_room(
        case_name,
        cf2_directivity,
    )

    rss_before = peak_rss_mb()

    t0 = time.perf_counter()
    room.compute_rir()
    elapsed = time.perf_counter() - t0

    rss_after = peak_rss_mb()

    acoustic_summary, detail = (
        extract_rir_metrics(
            room,
            case_name,
        )
    )

    return {
        "case": case_name,
        "compute_rir_s": float(elapsed),
        "peak_rss_before_mb": rss_before,
        "peak_rss_after_mb": rss_after,
        **acoustic_summary,
    }, detail


def cyclic_order(round_index):
    """
    Deterministic Latin-style cyclic order.

    round_index is zero based.
    """
    shift = round_index % len(CASES)

    return (
        CASES[shift:]
        + CASES[:shift]
    )


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not rows:
        return

    # Union all keys while preserving first-seen ordering.
    fields = []

    for row in rows:
        for key in row.keys():
            if key not in fields:
                fields.append(key)

    with path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )
        writer.writeheader()
        writer.writerows(rows)


def summarize_case(
    case_name,
    case_rows,
    first_acoustic,
    env,
    warmup_rounds,
    repetitions,
    cf2_build_s,
):
    times = [
        r["compute_rir_s"]
        for r in case_rows
    ]

    mean_t = statistics.mean(times)

    std_t = (
        statistics.stdev(times)
        if len(times) > 1
        else 0.0
    )

    max_peak_rss = max(
        r["peak_rss_after_mb"]
        for r in case_rows
    )

    return {
        **env,
        "case": case_name,
        "warmup_rounds": warmup_rounds,
        "repetitions": repetitions,
        "time_median_s": statistics.median(times),
        "time_mean_s": mean_t,
        "time_std_s": std_t,
        "time_cv": (
            std_t / mean_t
            if mean_t != 0.0
            else float("nan")
        ),
        "time_min_s": min(times),
        "time_max_s": max(times),
        "peak_rss_max_mb": max_peak_rss,
        "cf2_build_elapsed_s": (
            cf2_build_s
            if case_name == "CF2"
            else ""
        ),
        **first_acoustic,
    }


# ======================================================================================
# Main
# ======================================================================================

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
        "--warmup-rounds",
        type=int,
        default=3,
        help="Each warm-up round runs all three cases once.",
    )

    ap.add_argument(
        "--repetitions",
        type=int,
        default=20,
        help="Measured runs PER CASE.",
    )

    ap.add_argument(
        "--environment-label",
        default="local-docker",
    )

    ap.add_argument(
        "--output-dir",
        default="/app/results/multiband_directivity_benchmark_fast",
    )

    args = ap.parse_args()

    if args.warmup_rounds < 0:
        raise ValueError(
            "--warmup-rounds must be >= 0"
        )

    if args.repetitions < 1:
        raise ValueError(
            "--repetitions must be >= 1"
        )

    output_dir = Path(
        args.output_dir
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    env = environment_info(
        args.environment_label
    )

    print(
        "=== Stage-2 Pyroomacoustics multiband directivity benchmark — VECTORIZED CF2 ==="
    )

    for k, v in env.items():
        print(f"{k}: {v}")

    print()
    print("=== Fixed benchmark configuration ===")
    print("room_dim_m:", ROOM_DIM.tolist())
    print("source_pos_m:", SOURCE_POS.tolist())
    print("n_mics:", len(MIC_GRID))
    print("fs_hz:", FS)
    print("max_order:", MAX_ORDER)
    print("absorption_per_band:", ABSORPTION)
    print(
        "band_centers_hz:",
        PRA_BAND_CENTERS_HZ.tolist(),
    )
    print(
        "source_orientation_deg:",
        {
            "azimuth": SOURCE_AZIMUTH_DEG,
            "colatitude": SOURCE_COLATITUDE_DEG,
            "roll": SOURCE_ROLL_DEG,
        },
    )
    print("CF2:", args.cf2)
    print(
        "CF2 handedness:",
        args.handedness,
    )
    print(
        "CF2 interpolation_domain:",
        args.interpolation_domain,
    )
    print(
        "CF2 adapter implementation:",
        "vectorized-fast",
    )
    print(
        "warmup_rounds:",
        args.warmup_rounds,
    )
    print(
        "repetitions_per_case:",
        args.repetitions,
    )

    # ------------------------------------------------------------------
    # Build CF2 adapter ONCE and outside all timed benchmark sections.
    # ------------------------------------------------------------------
    print()
    print(
        "=== Building CF2 adapter once (excluded from compute_rir timing) ==="
    )

    t0 = time.perf_counter()

    cf2_directivity = CF2SevenBandDirectivityFast(
        cf2_path=args.cf2,
        base_offset=args.base,
        speaker_azimuth_deg=SOURCE_AZIMUTH_DEG,
        speaker_colatitude_deg=SOURCE_COLATITUDE_DEG,
        speaker_roll_deg=SOURCE_ROLL_DEG,
        rotation_handedness=args.handedness,
        interpolation_domain=args.interpolation_domain,
    )

    cf2_build_elapsed_s = (
        time.perf_counter()
        - t0
    )

    print(
        "cf2_build_elapsed_s:",
        cf2_build_elapsed_s,
    )

    print(
        "valid_cf2_frequencies_hz:",
        cf2_directivity.interpolator.valid_frequencies_hz.tolist(),
    )

    # Preflight room contract.
    preflight = make_room(
        "CF2",
        cf2_directivity,
    )

    print(
        "preflight_room_is_multi_band:",
        preflight.is_multi_band,
    )

    print(
        "preflight_wall_absorption_shape:",
        np.shape(
            preflight.walls[0].absorption
        ),
    )

    print(
        "preflight_cf2_type:",
        type(
            preflight.sources[0].directivity
        ),
    )

    # ------------------------------------------------------------------
    # Warm-up
    # ------------------------------------------------------------------
    print()
    print("=== Warm-up ===")

    for wr in range(
        args.warmup_rounds
    ):
        order = cyclic_order(wr)

        print(
            f"warmup_round={wr + 1} "
            f"order={' -> '.join(order)}"
        )

        for case_name in order:
            result, _ = run_once(
                case_name,
                cf2_directivity,
            )

            print(
                f"  {case_name:8s} "
                f"{result['compute_rir_s']:.6f} s"
            )

    # ------------------------------------------------------------------
    # Measured rounds
    # ------------------------------------------------------------------
    print()
    print("=== Measured balanced rounds ===")

    run_rows = []
    first_detail_by_case = {}
    first_acoustic_by_case = {}

    for round_idx in range(
        args.repetitions
    ):
        order = cyclic_order(
            round_idx
        )

        print(
            f"\nround {round_idx + 1:02d}/{args.repetitions} "
            f"order={' -> '.join(order)}"
        )

        for position_in_round, case_name in enumerate(
            order,
            start=1,
        ):
            result, detail = run_once(
                case_name,
                cf2_directivity,
            )

            run_row = {
                **env,
                "round": round_idx + 1,
                "position_in_round": position_in_round,
                "case": case_name,
                "case_order": "->".join(order),
                "compute_rir_s": result["compute_rir_s"],
                "peak_rss_before_mb": result["peak_rss_before_mb"],
                "peak_rss_after_mb": result["peak_rss_after_mb"],
                "rir_energy_mean": result["rir_energy_mean"],
                "rir_energy_std": result["rir_energy_std"],
                "rir_energy_cv": result["rir_energy_cv"],
                "rir_energy_min": result["rir_energy_min"],
                "rir_energy_max": result["rir_energy_max"],
                "rir_energy_sum": result["rir_energy_sum"],
                "rir_peak_mean": result["rir_peak_mean"],
                "rir_peak_min": result["rir_peak_min"],
                "rir_peak_max": result["rir_peak_max"],
                "rir_len_min": result["rir_len_min"],
                "rir_len_max": result["rir_len_max"],
                "n_mics": result["n_mics"],
            }

            run_rows.append(
                run_row
            )

            if case_name not in first_detail_by_case:
                first_detail_by_case[
                    case_name
                ] = detail

                first_acoustic_by_case[
                    case_name
                ] = {
                    "n_mics": result["n_mics"],
                    "rir_len_min": result["rir_len_min"],
                    "rir_len_max": result["rir_len_max"],
                    "rir_energy_mean": result["rir_energy_mean"],
                    "rir_energy_std": result["rir_energy_std"],
                    "rir_energy_cv": result["rir_energy_cv"],
                    "rir_energy_min": result["rir_energy_min"],
                    "rir_energy_max": result["rir_energy_max"],
                    "rir_energy_sum": result["rir_energy_sum"],
                    "rir_peak_mean": result["rir_peak_mean"],
                    "rir_peak_min": result["rir_peak_min"],
                    "rir_peak_max": result["rir_peak_max"],
                }

            print(
                f"  {case_name:8s} "
                f"time={result['compute_rir_s']:.6f} s | "
                f"energy_mean={result['rir_energy_mean']:.9e} | "
                f"peak_rss={result['peak_rss_after_mb']:.1f} MB"
            )

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------
    summary_rows = []

    for case_name in CASES:
        rows = [
            r
            for r in run_rows
            if r["case"] == case_name
        ]

        summary = summarize_case(
            case_name=case_name,
            case_rows=rows,
            first_acoustic=first_acoustic_by_case[case_name],
            env=env,
            warmup_rounds=args.warmup_rounds,
            repetitions=args.repetitions,
            cf2_build_s=cf2_build_elapsed_s,
        )

        summary_rows.append(
            summary
        )

    summary_by_case = {
        r["case"]: r
        for r in summary_rows
    }

    omni_time = summary_by_case[
        "OMNI"
    ]["time_median_s"]

    omni_energy = summary_by_case[
        "OMNI"
    ]["rir_energy_mean"]

    for row in summary_rows:
        row["median_time_vs_omni_ratio"] = (
            row["time_median_s"]
            / omni_time
        )

        row["median_time_overhead_vs_omni_pct"] = (
            (
                row["time_median_s"]
                / omni_time
            )
            - 1.0
        ) * 100.0

        row["energy_vs_omni_db"] = (
            10.0
            * math.log10(
                row["rir_energy_mean"]
                / omni_energy
            )
            if (
                row["rir_energy_mean"] > 0.0
                and omni_energy > 0.0
            )
            else float("nan")
        )

    # Determinism diagnostics.
    determinism_rows = []

    for case_name in CASES:
        rows = [
            r
            for r in run_rows
            if r["case"] == case_name
        ]

        e = np.asarray(
            [
                r["rir_energy_mean"]
                for r in rows
            ],
            dtype=float,
        )

        determinism_rows.append(
            {
                "case": case_name,
                "energy_mean_min": float(np.min(e)),
                "energy_mean_max": float(np.max(e)),
                "energy_mean_span": float(np.max(e) - np.min(e)),
            }
        )

    # Detail rows from the first measured run of each case.
    mic_rows = []

    for case_name in CASES:
        for row in first_detail_by_case[
            case_name
        ]:
            mic_rows.append(
                {
                    **env,
                    **row,
                }
            )

    # ------------------------------------------------------------------
    # Write artifacts
    # ------------------------------------------------------------------
    write_csv(
        output_dir / "runs.csv",
        run_rows,
    )

    write_csv(
        output_dir / "summary.csv",
        summary_rows,
    )

    write_csv(
        output_dir / "mics_first_run.csv",
        mic_rows,
    )

    write_csv(
        output_dir / "determinism.csv",
        determinism_rows,
    )

    config = {
        "environment": env,
        "room_dim_m": ROOM_DIM.tolist(),
        "source_pos_m": SOURCE_POS.tolist(),
        "n_mics": len(MIC_GRID),
        "microphones": MIC_GRID,
        "fs_hz": FS,
        "max_order": MAX_ORDER,
        "absorption_per_band": ABSORPTION,
        "band_centers_hz": PRA_BAND_CENTERS_HZ.tolist(),
        "source_orientation": {
            "azimuth_deg": SOURCE_AZIMUTH_DEG,
            "colatitude_deg": SOURCE_COLATITUDE_DEG,
            "roll_deg": SOURCE_ROLL_DEG,
        },
        "cases": list(CASES),
        "warmup_rounds": args.warmup_rounds,
        "repetitions_per_case": args.repetitions,
        "balanced_order": [
            list(cyclic_order(i))
            for i in range(3)
        ],
        "cf2": {
            "path_as_invoked": args.cf2,
            "base_offset": args.base,
            "handedness": args.handedness,
            "interpolation_domain": args.interpolation_domain,
            "adapter_implementation": "vectorized-fast",
            "valid_frequencies_hz": (
                cf2_directivity
                .interpolator
                .valid_frequencies_hz
                .tolist()
            ),
            "build_elapsed_s": cf2_build_elapsed_s,
        },
        "timing_scope": (
            "Only room.compute_rir(); room construction and CF2 adapter "
            "construction excluded."
        ),
        "acoustic_metric_note": (
            "Time-domain RIR metrics are reported. PRA octave-band "
            "re-analysis is not used as an exact CF2 validation metric."
        ),
    }

    with (
        output_dir
        / "config.json"
    ).open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            config,
            f,
            indent=2,
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------
    # Console summary
    # ------------------------------------------------------------------
    print()
    print("=" * 100)
    print("FINAL BENCHMARK SUMMARY")
    print("=" * 100)

    for row in summary_rows:
        print(
            f"{row['case']:8s} | "
            f"median={row['time_median_s']:.6f} s | "
            f"mean={row['time_mean_s']:.6f} s | "
            f"std={row['time_std_s']:.6f} s | "
            f"min={row['time_min_s']:.6f} s | "
            f"max={row['time_max_s']:.6f} s | "
            f"vs_omni={row['median_time_vs_omni_ratio']:.3f}x | "
            f"overhead={row['median_time_overhead_vs_omni_pct']:+.1f}% | "
            f"energy_vs_omni={row['energy_vs_omni_db']:+.3f} dB"
        )

    print()
    print("=== Determinism ===")

    for row in determinism_rows:
        print(
            f"{row['case']:8s} "
            f"energy_span={row['energy_mean_span']:.3e}"
        )

    print()
    print("=== Files ===")
    print(output_dir / "runs.csv")
    print(output_dir / "summary.csv")
    print(output_dir / "mics_first_run.csv")
    print(output_dir / "determinism.csv")
    print(output_dir / "config.json")

    print()
    print("=== Interpretation guardrails ===")
    print(
        "- Compare timing only among these Stage-2 multiband runs, "
        "not against the old single-band Stage-1 timing."
    )
    print(
        "- CF2 build time is reported separately and excluded from "
        "compute_rir timing."
    )
    print(
        "- This benchmark measures ISM source-directivity cost at "
        "fs=16 kHz and max_order=10."
    )
    print(
        "- Ray tracing is not part of this benchmark."
    )


if __name__ == "__main__":
    main()
