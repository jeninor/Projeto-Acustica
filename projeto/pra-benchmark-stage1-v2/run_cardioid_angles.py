import argparse
import csv
import math
import os
import platform
import resource
import statistics
import time
from pathlib import Path

import numpy as np
import psutil
import pyroomacoustics as pra
from pyroomacoustics.directivities import Cardioid, DirectionVector


ROOM_DIM = np.array([6.0, 5.0, 3.0], dtype=float)
SOURCE_POS = np.array([2.0, 2.5, 1.5], dtype=float)
FS = 16000
MAX_ORDER = 10
ABSORPTION = 0.35
ANGLES_DEG = [0.0, 45.0, 90.0, 180.0]


def mic_grid():
    xs = np.linspace(1.0, 5.0, 5)
    ys = np.linspace(0.75, 4.25, 5)
    z = 1.2
    pts = []
    idx = 0

    for row, y in enumerate(ys):
        for col, x in enumerate(xs):
            pts.append(
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

    return pts


MIC_GRID = mic_grid()


def cardioid_for_angle(angle_deg):
    return Cardioid(
        orientation=DirectionVector(
            azimuth=float(angle_deg),
            colatitude=90.0,
            degrees=True,
        ),
        gain=1.0,
    )


def make_room(angle_deg):
    room = pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=pra.Material(ABSORPTION),
        max_order=MAX_ORDER,
    )

    room.add_source(
        SOURCE_POS,
        directivity=cardioid_for_angle(angle_deg),
    )

    for mic in MIC_GRID:
        room.add_microphone(
            [mic["x"], mic["y"], mic["z"]]
        )

    return room


def direct_path_geometry(mic, angle_deg):
    vec = np.array(
        [
            mic["x"] - SOURCE_POS[0],
            mic["y"] - SOURCE_POS[1],
            mic["z"] - SOURCE_POS[2],
        ],
        dtype=float,
    )

    distance = float(np.linalg.norm(vec))

    # Horizontal azimuth from +X toward +Y.
    azimuth_deg = math.degrees(
        math.atan2(vec[1], vec[0])
    ) % 360.0

    # Angular difference in horizontal plane.
    delta = abs(
        (
            azimuth_deg
            - float(angle_deg)
            + 180.0
        )
        % 360.0
        - 180.0
    )

    # Ideal first-order cardioid amplitude response in horizontal plane.
    ideal_gain = 0.5 * (
        1.0
        +
        math.cos(
            math.radians(delta)
        )
    )

    ideal_gain_db = (
        20.0 * math.log10(ideal_gain)
        if ideal_gain > 0.0
        else float("-inf")
    )

    return {
        "distance_m": distance,
        "receiver_azimuth_deg": azimuth_deg,
        "horizontal_angle_from_axis_deg": delta,
        "ideal_cardioid_direct_gain": ideal_gain,
        "ideal_cardioid_direct_gain_db": ideal_gain_db,
    }


def extract_mic_metrics(room, angle_deg):
    rows = []

    if len(room.rir) != len(MIC_GRID):
        raise RuntimeError(
            f"Expected {len(MIC_GRID)} microphones, "
            f"got {len(room.rir)} RIR entries"
        )

    for mic in MIC_GRID:
        i = mic["mic_index"]
        rir = np.asarray(
            room.rir[i][0],
            dtype=float,
        )

        energy = float(
            np.sum(
                rir * rir
            )
        )

        peak = (
            float(
                np.max(
                    np.abs(rir)
                )
            )
            if len(rir)
            else 0.0
        )

        rows.append(
            {
                "angle_deg": float(angle_deg),
                **mic,
                **direct_path_geometry(
                    mic,
                    angle_deg,
                ),
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

    return rows


def summarize_mics(mic_rows):
    energies = np.asarray(
        [
            r["rir_energy"]
            for r in mic_rows
        ],
        dtype=float,
    )

    peaks = np.asarray(
        [
            r["rir_peak"]
            for r in mic_rows
        ],
        dtype=float,
    )

    lengths = np.asarray(
        [
            r["rir_len"]
            for r in mic_rows
        ],
        dtype=int,
    )

    energy_mean = float(
        energies.mean()
    )

    energy_std = float(
        energies.std(
            ddof=1
        )
    )

    return {
        "n_mics": int(
            len(mic_rows)
        ),
        "rir_len_min": int(
            lengths.min()
        ),
        "rir_len_max": int(
            lengths.max()
        ),
        "rir_energy_mean": energy_mean,
        "rir_energy_std": energy_std,
        "rir_energy_cv": (
            energy_std / energy_mean
            if energy_mean != 0.0
            else float("nan")
        ),
        "rir_energy_min": float(
            energies.min()
        ),
        "rir_energy_max": float(
            energies.max()
        ),
        "rir_energy_sum": float(
            energies.sum()
        ),
        "rir_peak_mean": float(
            peaks.mean()
        ),
        "rir_peak_min": float(
            peaks.min()
        ),
        "rir_peak_max": float(
            peaks.max()
        ),
    }


def run_once(angle_deg):
    room = make_room(
        angle_deg
    )

    proc = psutil.Process(
        os.getpid()
    )

    rss_before = (
        proc.memory_info().rss
        /
        (1024 ** 2)
    )

    t0 = time.perf_counter()

    room.compute_rir()

    elapsed = (
        time.perf_counter()
        -
        t0
    )

    rss_after = (
        proc.memory_info().rss
        /
        (1024 ** 2)
    )

    mic_rows = extract_mic_metrics(
        room,
        angle_deg,
    )

    summary = summarize_mics(
        mic_rows
    )

    return {
        "elapsed_s": elapsed,
        "rss_before_mb": rss_before,
        "rss_after_mb": rss_after,
        "peak_rss_mb": (
            resource.getrusage(
                resource.RUSAGE_SELF
            ).ru_maxrss
            /
            1024.0
        ),
        **summary,
    }, mic_rows


def cpu_model():
    try:
        with open(
            "/proc/cpuinfo",
            "r",
            encoding="utf-8",
        ) as f:
            for line in f:
                if line.lower().startswith(
                    "model name"
                ):
                    return (
                        line.split(
                            ":",
                            1,
                        )[1].strip()
                    )
    except Exception:
        pass

    return (
        platform.processor()
        or
        "unknown"
    )


def environment_info():
    return {
        "environment": "local-docker",
        "python": platform.python_version(),
        "pyroomacoustics": getattr(
            pra,
            "__version__",
            "unknown",
        ),
        "numpy": np.__version__,
        "platform": platform.platform(),
        "cpu": cpu_model(),
        "cpu_count_logical": psutil.cpu_count(
            logical=True
        ),
        "cpu_count_physical": psutil.cpu_count(
            logical=False
        ),
        "pra_num_threads": os.getenv(
            "PRA_NUM_THREADS",
            "",
        ),
        "omp_num_threads": os.getenv(
            "OMP_NUM_THREADS",
            "",
        ),
        "openblas_num_threads": os.getenv(
            "OPENBLAS_NUM_THREADS",
            "",
        ),
        "mkl_num_threads": os.getenv(
            "MKL_NUM_THREADS",
            "",
        ),
        "room_x_m": ROOM_DIM[0],
        "room_y_m": ROOM_DIM[1],
        "room_z_m": ROOM_DIM[2],
        "source_x_m": SOURCE_POS[0],
        "source_y_m": SOURCE_POS[1],
        "source_z_m": SOURCE_POS[2],
        "fs_hz": FS,
        "max_order": MAX_ORDER,
        "absorption": ABSORPTION,
    }


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not rows:
        return

    with path.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(
                rows[0].keys()
            ),
        )
        writer.writeheader()
        writer.writerows(
            rows
        )


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--warmup",
        type=int,
        default=3,
    )

    ap.add_argument(
        "--repetitions",
        type=int,
        default=20,
    )

    ap.add_argument(
        "--output-dir",
        default="/app/results/cardioid_angles",
    )

    args = ap.parse_args()

    output_dir = Path(
        args.output_dir
    )

    env = environment_info()

    print(
        "=== Stage 1 v3: Cardioid orientation benchmark ==="
    )

    for k, v in env.items():
        print(
            f"{k}: {v}"
        )

    print(
        f"configured_mics: {len(MIC_GRID)}"
    )

    print(
        "angles_deg:",
        ", ".join(
            str(int(a))
            for a in ANGLES_DEG
        ),
    )

    run_rows = []
    detail_rows = []
    summary_rows = []

    reference_energy_mean = None

    for angle in ANGLES_DEG:
        print(
            f"\n--- CARDIOID {angle:g} deg ---"
        )

        for i in range(
            args.warmup
        ):
            print(
                f"warm-up "
                f"{i + 1}/"
                f"{args.warmup}"
            )
            run_once(
                angle
            )

        times = []
        first_mic_rows = None
        first_summary = None

        for rep in range(
            1,
            args.repetitions + 1,
        ):
            result, mic_rows = run_once(
                angle
            )

            times.append(
                result[
                    "elapsed_s"
                ]
            )

            if first_mic_rows is None:
                first_mic_rows = mic_rows
                first_summary = result

            run_rows.append(
                {
                    **env,
                    "angle_deg": angle,
                    "repeat": rep,
                    **result,
                }
            )

            print(
                f"repeat {rep:02d}: "
                f"{result['elapsed_s']:.6f} s | "
                f"energy_mean="
                f"{result['rir_energy_mean']:.6e} | "
                f"CV="
                f"{result['rir_energy_cv']:.4f}"
            )

        assert first_mic_rows is not None
        assert first_summary is not None

        if reference_energy_mean is None:
            reference_energy_mean = (
                first_summary[
                    "rir_energy_mean"
                ]
            )

        angle_energy_mean = (
            first_summary[
                "rir_energy_mean"
            ]
        )

        energy_vs_0_db = (
            10.0
            *
            math.log10(
                angle_energy_mean
                /
                reference_energy_mean
            )
        )

        detail_rows.extend(
            first_mic_rows
        )

        time_mean = statistics.mean(
            times
        )

        time_stdev = (
            statistics.stdev(
                times
            )
            if len(times) > 1
            else 0.0
        )

        summary_row = {
            **env,
            "angle_deg": angle,
            "warmup": args.warmup,
            "repetitions": args.repetitions,
            "time_median_s": statistics.median(
                times
            ),
            "time_mean_s": time_mean,
            "time_std_s": time_stdev,
            "time_cv": (
                time_stdev
                /
                time_mean
                if time_mean
                else float("nan")
            ),
            "time_min_s": min(
                times
            ),
            "time_max_s": max(
                times
            ),
            "energy_vs_cardioid_0_db": energy_vs_0_db,
            **{
                k: v
                for k, v in first_summary.items()
                if k
                not in {
                    "elapsed_s",
                    "rss_before_mb",
                    "rss_after_mb",
                    "peak_rss_mb",
                }
            },
        }

        summary_rows.append(
            summary_row
        )

        print(
            f"summary: "
            f"median="
            f"{summary_row['time_median_s']:.6f} s | "
            f"mean="
            f"{summary_row['time_mean_s']:.6f} s | "
            f"energy="
            f"{angle_energy_mean:.6e} | "
            f"vs 0 deg="
            f"{energy_vs_0_db:+.3f} dB"
        )

    write_csv(
        output_dir
        /
        "cardioid_angles_runs.csv",
        run_rows,
    )

    write_csv(
        output_dir
        /
        "cardioid_angles_mics.csv",
        detail_rows,
    )

    write_csv(
        output_dir
        /
        "cardioid_angles_summary.csv",
        summary_rows,
    )

    print(
        "\nSaved:"
    )

    print(
        output_dir
        /
        "cardioid_angles_runs.csv"
    )

    print(
        output_dir
        /
        "cardioid_angles_mics.csv"
    )

    print(
        output_dir
        /
        "cardioid_angles_summary.csv"
    )


if __name__ == "__main__":
    main()
