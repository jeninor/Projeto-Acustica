import argparse
import csv
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


def cpu_model():
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "unknown"


def mic_grid_5x5():
    xs = np.linspace(1.0, 5.0, 5)
    ys = np.linspace(0.75, 4.25, 5)
    z = 1.2
    return np.asarray([[x, y, z] for y in ys for x in xs], dtype=float)


def make_directivity(pattern: str):
    if pattern == "omni":
        return None

    if pattern == "cardioid":
        return Cardioid(
            orientation=DirectionVector(
                azimuth=0.0,
                colatitude=90.0,
                degrees=True,
            ),
            gain=1.0,
        )

    raise ValueError(pattern)


def make_room(pattern: str):
    room = pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=pra.Material(ABSORPTION),
        max_order=MAX_ORDER,
    )

    room.add_source(
        SOURCE_POS,
        directivity=make_directivity(pattern),
    )

    for mic in mic_grid_5x5():
        room.add_microphone(mic)

    return room


def rir_summary(room):
    rirs = [np.asarray(r, dtype=float) for r in room.rir[0]]
    lengths = [len(r) for r in rirs]
    energies = [float(np.sum(r * r)) for r in rirs]
    peaks = [float(np.max(np.abs(r))) if len(r) else 0.0 for r in rirs]

    return {
        "n_mics": len(rirs),
        "rir_len_min": min(lengths),
        "rir_len_max": max(lengths),
        "rir_energy_mean": float(np.mean(energies)),
        "rir_energy_sum": float(np.sum(energies)),
        "rir_peak_mean": float(np.mean(peaks)),
    }


def peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def run_once(pattern: str):
    room = make_room(pattern)
    process = psutil.Process(os.getpid())
    rss_before = process.memory_info().rss / (1024 ** 2)

    t0 = time.perf_counter()
    room.compute_rir()
    elapsed = time.perf_counter() - t0

    rss_after = process.memory_info().rss / (1024 ** 2)

    return {
        "pattern": pattern,
        "elapsed_s": elapsed,
        "rss_before_mb": rss_before,
        "rss_after_mb": rss_after,
        "peak_rss_mb": peak_rss_mb(),
        **rir_summary(room),
    }


def environment_info():
    return {
        "environment": "local-docker",
        "python": platform.python_version(),
        "pyroomacoustics": getattr(pra, "__version__", "unknown"),
        "numpy": np.__version__,
        "platform": platform.platform(),
        "cpu": cpu_model(),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "pra_num_threads": os.getenv("PRA_NUM_THREADS", ""),
        "omp_num_threads": os.getenv("OMP_NUM_THREADS", ""),
        "openblas_num_threads": os.getenv("OPENBLAS_NUM_THREADS", ""),
        "mkl_num_threads": os.getenv("MKL_NUM_THREADS", ""),
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", choices=["omni", "cardioid", "both"], default="both")
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--repetitions", type=int, default=5)
    ap.add_argument("--output", default="/app/results/local_baseline.csv")
    args = ap.parse_args()

    patterns = ["omni", "cardioid"] if args.pattern == "both" else [args.pattern]
    env = environment_info()
    rows = []

    print("=== Pyroomacoustics Stage 1 ===")
    for k, v in env.items():
        print(f"{k}: {v}")

    for pattern in patterns:
        print(f"\\n--- {pattern.upper()} ---")

        for i in range(args.warmup):
            print(f"warm-up {i + 1}/{args.warmup}")
            run_once(pattern)

        measured = []
        for rep in range(1, args.repetitions + 1):
            result = run_once(pattern)
            measured.append(result["elapsed_s"])
            rows.append({**env, "repeat": rep, **result})

            print(
                f"repeat {rep:02d}: {result['elapsed_s']:.6f} s | "
                f"RIR energy mean={result['rir_energy_mean']:.6e}"
            )

        print(
            f"{pattern}: median={statistics.median(measured):.6f} s | "
            f"mean={statistics.mean(measured):.6f} s | "
            f"min={min(measured):.6f} s | max={max(measured):.6f} s"
        )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\\nCSV saved: {out}")


if __name__ == "__main__":
    main()
