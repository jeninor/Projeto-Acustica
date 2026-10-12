#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
experiment6m_all_formats_quality_benchmark.py

EXPERIMENTO 6M — BENCHMARK COMPLETO DE FORMATOS / RUNTIME / FS
==============================================================

Objetivo
--------
Conservar y comparar TODAS las rutas útiles actuales, sin confundir:

    - formato de entrada;
    - representación runtime;
    - frecuencia de muestreo;
    - motor de simulación.

Casos
-----
16 kHz
~~~~~~
1. CF2_FAST_16K
   CF2 binario -> 7 bandas PRA -> magnitud, sin fase.
   Motor: implementación CF2 vectorizada histórica.

2. SOFA_TF_FAST_16K
   FreeFieldDirectivityTF / DataType=TF
   -> FIR runtime 683 taps aprox. a 16 kHz.
   Runtime ISM-Lite + caches 6G-E.

3. SOFA_FIR_FAST_16K
   GeneralFIR / DataType=FIR
   -> FIR runtime 683 taps aprox. a 16 kHz.
   Runtime ISM-Lite + caches 6G-E.

4. SOFA_FIR_VORONOI_FAST_16K
   MISMO GeneralFIR, pero usando MeasuredDirectivity estándar
   (incluye energías octave-band, Voronoi y ray-sampling setup),
   y luego el mismo motor rápido 6G-E.
   Sirve para aislar el coste de preparación de Voronoi.

5. SOFA_FIR_NATIVE_16K
   GeneralFIR + MeasuredDirectivity estándar + room.compute_rir()
   nativo de Pyroomacoustics. Es la referencia "SOFA_NATIVE".

48 kHz
~~~~~~
6. SOFA_TF_FAST_48K
   FreeFieldDirectivityTF / TF -> FIR 2048 taps a 48 kHz.
   ISM-Lite + 6G-E.

7. SOFA_FIR_FAST_48K
   GeneralFIR original a 48 kHz, 2048 taps.
   ISM-Lite + 6G-E.

8. SOFA_FIR_VORONOI_FAST_48K
   GeneralFIR 48 kHz + MeasuredDirectivity estándar/Voronoi
   + 6G-E.

9. SOFA_FIR_NATIVE_48K
   GeneralFIR original 48 kHz + Pyroomacoustics nativo.

Notas
-----
- CF2_FAST permanece en 16 kHz porque su benchmark validado utiliza las
  7 bandas PRA [125,250,500,1000,2000,4000,8000] Hz.
- A 48 kHz PRA incorpora también la banda de 16 kHz.
- Para las rutas SOFA actuales, el FIR contiene un delay común de
  1024/48000 s = 21.333... ms. Para métricas temporales C50/C80/D50/Ts/RT,
  este script usa un t0 físico = distancia/c + sofa_source_delay_s.
- El nivel calculado a partir de la energía del RIR es RELATIVO. No es SPL
  absoluto porque todavía no se ha aplicado una calibración común de potencia/
  sensibilidad para comparación inter-simulador.

Métricas de calidad guardadas por micrófono
-------------------------------------------
- RIR total energy / relative level dB
- peak
- C50
- C80
- D50
- center time Ts
- EDT
- T20
- T30

Se guardan además los RIR de cada caso en NPZ para poder comparar después
con I-Simpa, Odeon u otros simuladores.

Uso recomendado inicial
-----------------------
PRA_NUM_THREADS=1 \
OMP_NUM_THREADS=1 \
OPENBLAS_NUM_THREADS=1 \
MKL_NUM_THREADS=1 \
python experiment6m_all_formats_quality_benchmark.py \
  --cf2 "speaker_cf2/AXYS4549.CF2" \
  --cf2-fast-module cf2_pra_directivity_fast_declared.py \
  --loader-script sofa_directivity_dual_adapter_v2.py \
  --base-c-script experiment6g_c_frequency_domain_cache.py \
  --base-d-script experiment6g_d_fft_length_optimization.py \
  --base-e-script experiment6g_e_fractional_delay_fd_cache.py \
  --bridge-script experiment6b_pyroomacoustics_bridge.py \
  --freefield-sofa "clfviewer_validation/experiment6a_sofa_AXYS4549/AXYS4549_FreeFieldDirectivityTF.sofa" \
  --general-fir "clfviewer_validation/experiment6b1_general_fir/AXYS4549_GeneralFIR.sofa" \
  --warmup-rounds 1 \
  --repetitions 3 \
  --output-dir "clfviewer_validation/experiment6m_all_formats"
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import platform
import resource
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyroomacoustics as pra
from scipy.fft import next_fast_len

from pyroomacoustics.parameters import constants


PRA_BANDS_BY_FS = {
    16000: np.array(
        [125, 250, 500, 1000, 2000, 4000, 8000],
        dtype=float,
    ),
    48000: np.array(
        [125, 250, 500, 1000, 2000, 4000, 8000, 16000],
        dtype=float,
    ),
}

CASES = (
    "CF2_FAST_16K",
    "SOFA_TF_FAST_16K",
    "SOFA_FIR_FAST_16K",
    "SOFA_FIR_VORONOI_FAST_16K",
    "SOFA_FIR_NATIVE_16K",
    "SOFA_TF_FAST_48K",
    "SOFA_FIR_FAST_48K",
    "SOFA_FIR_VORONOI_FAST_48K",
    "SOFA_FIR_NATIVE_48K",
)


@dataclass
class Case:
    name: str
    fs: int
    family: str
    engine: str
    directivity: object
    preparation_s: float
    source_delay_s: float
    runtime: dict | None = None


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(
        name,
        str(path),
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module

    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise

    return module


def write_csv(path: Path, rows):
    rows = list(rows)
    if not rows:
        return

    fields = []
    seen = set()

    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
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


def peak_rss_mb():
    # Linux ru_maxrss is KiB.
    return float(
        resource.getrusage(
            resource.RUSAGE_SELF
        ).ru_maxrss
        / 1024.0
    )


def configure_base_c(base_c, fs):
    fs = int(fs)

    if fs not in PRA_BANDS_BY_FS:
        raise ValueError(
            f"Unsupported benchmark fs={fs}; "
            f"supported={list(PRA_BANDS_BY_FS)}"
        )

    base_c.FS = fs
    base_c.PRA_BANDS = PRA_BANDS_BY_FS[fs].copy()


def build_fast_runtime(
    base_c,
    base_d,
    base_e,
    directivity,
    fs,
):
    configure_base_c(
        base_c,
        fs,
    )

    room = base_c.build_room(
        directivity
    )

    original_synthesis = (
        base_c
        .AntoniOctaveFilterBank
        .synthesis
    )

    t0 = time.perf_counter()

    basis_time = base_c.prepare_linear_basis(
        room.octave_bands,
        original_synthesis,
    )

    basis_build_s = (
        time.perf_counter()
        - t0
    )

    fdl = int(
        constants.get(
            "frac_delay_length"
        )
    )

    granularity = int(
        constants.get(
            "sinc_lut_granularity"
        )
    )

    max_len = (
        int(
            directivity.filter_len_ir
        )
        + fdl
        - 1
        + int(
            basis_time.shape[-1]
        )
        - 1
    )

    fft_len = int(
        next_fast_len(
            max_len,
            real=True,
        )
    )

    t0 = time.perf_counter()

    fd_cache = base_d.FDCache(
        directivity,
        basis_time,
        fdl,
        fft_len,
    )

    frac_cache = base_e.FractionalDelayFDCache(
        fdl=fdl,
        granularity=granularity,
        fft_len=fft_len,
    )

    cache_build_s = (
        time.perf_counter()
        - t0
    )

    return {
        "basis_build_s":
            float(
                basis_build_s
            ),
        "cache_build_s":
            float(
                cache_build_s
            ),
        "fft_len":
            int(
                fft_len
            ),
        "fdl":
            int(
                fdl
            ),
        "granularity":
            int(
                granularity
            ),
        "fd_cache":
            fd_cache,
        "frac_cache":
            frac_cache,
    }


def run_fast_case(
    case,
    base_c,
    base_e,
):
    configure_base_c(
        base_c,
        case.fs,
    )

    rss_before = peak_rss_mb()

    result = base_e.run_frac_cached(
        base_c,
        case.directivity,
        case.runtime[
            "fd_cache"
        ],
        case.runtime[
            "frac_cache"
        ],
    )

    rss_after = peak_rss_mb()

    return {
        "compute_rir_s":
            float(
                result[
                    "compute_rir_s"
                ]
            ),
        "energy_mean":
            float(
                result[
                    "energy_mean"
                ]
            ),
        "peak_rss_before_mb":
            rss_before,
        "peak_rss_after_mb":
            rss_after,
        "rirs":
            [
                np.asarray(
                    h,
                    dtype=float,
                )
                for h in result[
                    "rirs"
                ]
            ],
    }


def run_native_case(
    case,
    base_c,
):
    configure_base_c(
        base_c,
        case.fs,
    )

    room = base_c.build_room(
        case.directivity
    )

    rss_before = peak_rss_mb()

    t0 = time.perf_counter()
    room.compute_rir()
    elapsed = (
        time.perf_counter()
        - t0
    )

    rss_after = peak_rss_mb()

    rirs = [
        np.asarray(
            h,
            dtype=float,
        )
        for h in base_c.flatten_rirs(
            room
        )
    ]

    energies = np.asarray(
        [
            float(
                np.sum(
                    h * h
                )
            )
            for h in rirs
        ],
        dtype=float,
    )

    return {
        "compute_rir_s":
            float(
                elapsed
            ),
        "energy_mean":
            float(
                np.mean(
                    energies
                )
            ),
        "peak_rss_before_mb":
            rss_before,
        "peak_rss_after_mb":
            rss_after,
        "rirs":
            rirs,
    }


def run_cf2_case(
    case,
    base_c,
):
    # CF2_FAST is intentionally kept on the historical validated 16 kHz,
    # 7-band room.
    return run_native_case(
        case,
        base_c,
    )


def execute_case(
    case,
    base_c,
    base_e,
):
    if case.engine == "6G-E_FAST":
        return run_fast_case(
            case,
            base_c,
            base_e,
        )

    if case.engine in (
        "PRA_NATIVE",
        "CF2_FAST_NATIVE_ISM",
    ):
        return run_native_case(
            case,
            base_c,
        )

    raise ValueError(
        f"Unknown engine: {case.engine}"
    )


def pad_rirs(rirs):
    lengths = np.asarray(
        [
            len(h)
            for h in rirs
        ],
        dtype=np.int64,
    )

    n = int(
        np.max(
            lengths
        )
    )

    arr = np.zeros(
        (
            len(rirs),
            n,
        ),
        dtype=np.float32,
    )

    for i, h in enumerate(rirs):
        arr[
            i,
            :len(h),
        ] = np.asarray(
            h,
            dtype=np.float32,
        )

    return arr, lengths


def save_rirs(
    path,
    rirs,
    fs,
    case_name,
    source_delay_s,
):
    arr, lengths = pad_rirs(
        rirs
    )

    np.savez_compressed(
        path,
        rirs=arr,
        lengths=lengths,
        fs=np.asarray(
            [int(fs)],
            dtype=np.int64,
        ),
        case=np.asarray(
            [str(case_name)]
        ),
        source_delay_s=np.asarray(
            [float(source_delay_s)],
            dtype=float,
        ),
    )


def safe_db_ratio(
    a,
    b,
):
    if (
        a <= 0.0
        or b <= 0.0
    ):
        return float("nan")

    return float(
        10.0
        * math.log10(
            a / b
        )
    )


def regression_decay_time(
    rir_segment,
    fs,
    db_lo,
    db_hi,
):
    """
    Schroeder backward integration and linear regression.

    db_lo, db_hi are negative dB bounds, e.g.:
        EDT:   0 .. -10
        T20:  -5 .. -25
        T30:  -5 .. -35

    Returns extrapolated T60 in seconds.
    """
    x = np.asarray(
        rir_segment,
        dtype=float,
    )

    if x.size < 8:
        return float("nan")

    e = x * x

    total = float(
        np.sum(
            e
        )
    )

    if total <= 0.0:
        return float("nan")

    edc = np.cumsum(
        e[
            ::-1
        ]
    )[
        ::-1
    ]

    edc /= max(
        float(
            edc[
                0
            ]
        ),
        np.finfo(
            float
        ).tiny,
    )

    edc_db = (
        10.0
        * np.log10(
            np.maximum(
                edc,
                np.finfo(
                    float
                ).tiny,
            )
        )
    )

    lo = min(
        float(
            db_lo
        ),
        float(
            db_hi
        ),
    )

    hi = max(
        float(
            db_lo
        ),
        float(
            db_hi
        ),
    )

    mask = (
        (edc_db >= lo)
        & (edc_db <= hi)
    )

    idx = np.flatnonzero(
        mask
    )

    if idx.size < 8:
        return float("nan")

    t = (
        idx.astype(
            float
        )
        / float(
            fs
        )
    )

    y = edc_db[
        idx
    ]

    slope, intercept = np.polyfit(
        t,
        y,
        1,
    )

    if (
        not np.isfinite(
            slope
        )
        or slope >= 0.0
    ):
        return float("nan")

    return float(
        -60.0
        / slope
    )


def quality_metrics_for_case(
    case,
    rirs,
    base_c,
):
    """
    Compute onset-relative room-acoustic descriptors.

    t0 uses known geometric source->mic propagation plus the common source FIR
    delay for SOFA routes. This avoids interpreting the 21.333 ms bridge delay
    as room acoustics.
    """
    c = float(
        constants.get(
            "c"
        )
    )

    source = np.asarray(
        base_c.SOURCE_POS,
        dtype=float,
    )

    mics = np.asarray(
        base_c.MIC_MATRIX,
        dtype=float,
    )

    rows = []

    for mic_idx, h in enumerate(
        rirs
    ):
        h = np.asarray(
            h,
            dtype=float,
        )

        mic = mics[
            :,
            mic_idx,
        ]

        distance_m = float(
            np.linalg.norm(
                mic - source
            )
        )

        t0_s = (
            distance_m
            / c
            + float(
                case.source_delay_s
            )
        )

        t0 = int(
            round(
                t0_s
                * case.fs
            )
        )

        t0 = int(
            np.clip(
                t0,
                0,
                max(
                    h.size - 1,
                    0,
                ),
            )
        )

        x = h[
            t0:
        ]

        if x.size == 0:
            continue

        e = x * x
        total = float(
            np.sum(
                e
            )
        )

        n50 = int(
            round(
                0.050
                * case.fs
            )
        )

        n80 = int(
            round(
                0.080
                * case.fs
            )
        )

        e50 = float(
            np.sum(
                e[
                    :min(
                        n50,
                        e.size,
                    )
                ]
            )
        )

        late50 = max(
            total - e50,
            0.0,
        )

        e80 = float(
            np.sum(
                e[
                    :min(
                        n80,
                        e.size,
                    )
                ]
            )
        )

        late80 = max(
            total - e80,
            0.0,
        )

        if total > 0.0:
            t = (
                np.arange(
                    e.size,
                    dtype=float,
                )
                / float(
                    case.fs
                )
            )

            ts_s = float(
                np.sum(
                    t * e
                )
                / total
            )

            d50_pct = float(
                100.0
                * e50
                / total
            )

            relative_level_db = float(
                10.0
                * np.log10(
                    total
                )
            )

        else:
            ts_s = float("nan")
            d50_pct = float("nan")
            relative_level_db = float("-inf")

        rows.append({
            "case":
                case.name,
            "fs_hz":
                int(
                    case.fs
                ),
            "mic_index":
                int(
                    mic_idx
                ),
            "distance_m":
                distance_m,
            "analysis_t0_s":
                float(
                    t0_s
                ),
            "analysis_t0_sample":
                int(
                    t0
                ),
            "rir_len":
                int(
                    h.size
                ),
            "peak_abs":
                float(
                    np.max(
                        np.abs(
                            h
                        )
                    )
                ),
            "total_energy_from_t0":
                total,
            "relative_level_db":
                relative_level_db,
            "C50_db":
                safe_db_ratio(
                    e50,
                    late50,
                ),
            "C80_db":
                safe_db_ratio(
                    e80,
                    late80,
                ),
            "D50_pct":
                d50_pct,
            "Ts_s":
                ts_s,
            "EDT_s":
                regression_decay_time(
                    x,
                    case.fs,
                    -10.0,
                    0.0,
                ),
            "T20_s":
                regression_decay_time(
                    x,
                    case.fs,
                    -25.0,
                    -5.0,
                ),
            "T30_s":
                regression_decay_time(
                    x,
                    case.fs,
                    -35.0,
                    -5.0,
                ),
        })

    return rows


def nan_summary(
    values,
):
    x = np.asarray(
        values,
        dtype=float,
    )

    finite = x[
        np.isfinite(
            x
        )
    ]

    if finite.size == 0:
        return {
            "mean":
                float("nan"),
            "std":
                float("nan"),
            "min":
                float("nan"),
            "max":
                float("nan"),
        }

    return {
        "mean":
            float(
                np.mean(
                    finite
                )
            ),
        "std":
            float(
                np.std(
                    finite,
                    ddof=1,
                )
                if finite.size > 1
                else 0.0
            ),
        "min":
            float(
                np.min(
                    finite
                )
            ),
        "max":
            float(
                np.max(
                    finite
                )
            ),
    }


def summarize_quality(
    case_name,
    rows,
):
    selected = [
        r
        for r in rows
        if r[
            "case"
        ] == case_name
    ]

    out = {
        "case":
            case_name,
        "n_mics":
            len(
                selected
            ),
    }

    for field in (
        "relative_level_db",
        "C50_db",
        "C80_db",
        "D50_pct",
        "Ts_s",
        "EDT_s",
        "T20_s",
        "T30_s",
    ):
        stats = nan_summary(
            [
                r[
                    field
                ]
                for r in selected
            ]
        )

        for key, value in stats.items():
            out[
                f"{field}_{key}"
            ] = value

    return out


def compare_rirs(
    reference,
    candidate,
):
    if len(
        reference
    ) != len(
        candidate
    ):
        raise ValueError(
            "RIR count mismatch."
        )

    max_abs = 0.0
    sum_sq = 0.0
    n_total = 0

    energy_db = []

    for a, b in zip(
        reference,
        candidate,
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

        aa[
            :len(a)
        ] = a

        bb[
            :len(b)
        ] = b

        d = bb - aa

        max_abs = max(
            max_abs,
            float(
                np.max(
                    np.abs(
                        d
                    )
                )
            ),
        )

        sum_sq += float(
            np.sum(
                d * d
            )
        )

        n_total += int(
            n
        )

        ea = float(
            np.sum(
                aa * aa
            )
        )

        eb = float(
            np.sum(
                bb * bb
            )
        )

        if (
            ea > 0.0
            and eb > 0.0
        ):
            energy_db.append(
                10.0
                * math.log10(
                    eb / ea
                )
            )

    ed = np.asarray(
        energy_db,
        dtype=float,
    )

    return {
        "rir_max_abs_error":
            float(
                max_abs
            ),
        "rir_rmse":
            float(
                math.sqrt(
                    sum_sq
                    / max(
                        n_total,
                        1,
                    )
                )
            ),
        "energy_delta_db_rmse":
            float(
                np.sqrt(
                    np.mean(
                        ed * ed
                    )
                )
            )
            if ed.size
            else float("nan"),
        "energy_delta_db_max_abs":
            float(
                np.max(
                    np.abs(
                        ed
                    )
                )
            )
            if ed.size
            else float("nan"),
    }


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--cf2",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--cf2-fast-module",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--loader-script",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--base-c-script",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--base-d-script",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--base-e-script",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--bridge-script",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--freefield-sofa",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--general-fir",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--warmup-rounds",
        type=int,
        default=1,
    )

    ap.add_argument(
        "--repetitions",
        type=int,
        default=3,
    )

    ap.add_argument(
        "--resample-backend",
        default="scipy",
    )

    ap.add_argument(
        "--render-fs",
        type=int,
        default=48000,
    )

    ap.add_argument(
        "--render-n-fft",
        type=int,
        default=2048,
    )

    ap.add_argument(
        "--sofa-source-delay-s",
        type=float,
        default=1024.0 / 48000.0,
        help=(
            "Embedded source FIR delay used only for onset-relative "
            "quality metrics. Default matches the validated AXYS bridge."
        ),
    )

    ap.add_argument(
        "--output-dir",
        type=Path,
        required=True,
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

    args.output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    loader_mod = load_module(
        args.loader_script,
        "sofa_loader_6m",
    )

    base_c = load_module(
        args.base_c_script,
        "base_c_6m",
    )

    base_d = load_module(
        args.base_d_script,
        "base_d_6m",
    )

    base_e = load_module(
        args.base_e_script,
        "base_e_6m",
    )

    cf2_mod = load_module(
        args.cf2_fast_module,
        "cf2_fast_6m",
    )

    print(
        "=" * 124
    )
    print(
        "EXPERIMENT 6M - ALL FORMATS / 16K+48K / FAST+VORONOI+NATIVE / QUALITY"
    )
    print(
        "=" * 124
    )
    print(
        "Python                       :",
        platform.python_version(),
    )
    print(
        "Pyroomacoustics              :",
        pra.__version__,
    )
    print(
        "NumPy                       :",
        np.__version__,
    )
    print(
        "Threads                      : PRA={} OMP={} OPENBLAS={} MKL={}".format(
            os.getenv(
                "PRA_NUM_THREADS",
                "",
            ),
            os.getenv(
                "OMP_NUM_THREADS",
                "",
            ),
            os.getenv(
                "OPENBLAS_NUM_THREADS",
                "",
            ),
            os.getenv(
                "MKL_NUM_THREADS",
                "",
            ),
        )
    )
    print(
        "SOFA quality source delay    : {:.9f}s".format(
            args.sofa_source_delay_s
        )
    )

    # ------------------------------------------------------------------
    # Build/load directivities.
    # ------------------------------------------------------------------
    cases = {}

    # CF2 FAST — historical 16 kHz / 7-band path.
    t0 = time.perf_counter()

    cf2_directivity = cf2_mod.CF2SevenBandDirectivityFast(
        cf2_path=args.cf2,
        speaker_azimuth_deg=0.0,
        speaker_colatitude_deg=90.0,
        speaker_roll_deg=0.0,
        rotation_handedness="top_to_left",
        interpolation_domain="db",
    )

    cf2_prep_s = (
        time.perf_counter()
        - t0
    )

    cases[
        "CF2_FAST_16K"
    ] = Case(
        name="CF2_FAST_16K",
        fs=16000,
        family="CF2_MAGNITUDE_7_BAND",
        engine="CF2_FAST_NATIVE_ISM",
        directivity=cf2_directivity,
        preparation_s=float(
            cf2_prep_s
        ),
        source_delay_s=0.0,
    )

    def make_sofa_factory(
        fs,
        runtime_mode,
    ):
        return loader_mod.SOFADirectivityFactory(
            room_fs=int(
                fs
            ),
            runtime_mode=runtime_mode,
            render_fs=int(
                args.render_fs
            ),
            render_n_fft=int(
                args.render_n_fft
            ),
            outside_mode="edge-hold",
            resample_backend=args.resample_backend,
            bridge_path=args.bridge_script,
            source_id=0,
        )

    # 16 kHz lite.
    f16_lite = make_sofa_factory(
        16000,
        "ism-lite",
    )

    tf16 = f16_lite.load(
        args.freefield_sofa
    )

    fir16 = f16_lite.load(
        args.general_fir
    )

    # 16 kHz standard/Voronoi.
    f16_std = make_sofa_factory(
        16000,
        "standard",
    )

    fir16_std = f16_std.load(
        args.general_fir
    )

    # 48 kHz lite.
    f48_lite = make_sofa_factory(
        48000,
        "ism-lite",
    )

    tf48 = f48_lite.load(
        args.freefield_sofa
    )

    fir48 = f48_lite.load(
        args.general_fir
    )

    # 48 kHz standard/Voronoi.
    f48_std = make_sofa_factory(
        48000,
        "standard",
    )

    fir48_std = f48_std.load(
        args.general_fir
    )

    def add_sofa_case(
        name,
        fs,
        family,
        engine,
        loaded,
    ):
        cases[
            name
        ] = Case(
            name=name,
            fs=int(
                fs
            ),
            family=family,
            engine=engine,
            directivity=loaded.directivity,
            preparation_s=float(
                loaded.preparation_s
            ),
            source_delay_s=float(
                args.sofa_source_delay_s
            ),
        )

    add_sofa_case(
        "SOFA_TF_FAST_16K",
        16000,
        "FreeFieldDirectivityTF_TF",
        "6G-E_FAST",
        tf16,
    )

    add_sofa_case(
        "SOFA_FIR_FAST_16K",
        16000,
        "GeneralFIR_FIR_ISM_LITE",
        "6G-E_FAST",
        fir16,
    )

    add_sofa_case(
        "SOFA_FIR_VORONOI_FAST_16K",
        16000,
        "GeneralFIR_FIR_STANDARD_VORONOI",
        "6G-E_FAST",
        fir16_std,
    )

    add_sofa_case(
        "SOFA_FIR_NATIVE_16K",
        16000,
        "GeneralFIR_FIR_STANDARD_VORONOI",
        "PRA_NATIVE",
        fir16_std,
    )

    add_sofa_case(
        "SOFA_TF_FAST_48K",
        48000,
        "FreeFieldDirectivityTF_TF",
        "6G-E_FAST",
        tf48,
    )

    add_sofa_case(
        "SOFA_FIR_FAST_48K",
        48000,
        "GeneralFIR_FIR_ISM_LITE",
        "6G-E_FAST",
        fir48,
    )

    add_sofa_case(
        "SOFA_FIR_VORONOI_FAST_48K",
        48000,
        "GeneralFIR_FIR_STANDARD_VORONOI",
        "6G-E_FAST",
        fir48_std,
    )

    add_sofa_case(
        "SOFA_FIR_NATIVE_48K",
        48000,
        "GeneralFIR_FIR_STANDARD_VORONOI",
        "PRA_NATIVE",
        fir48_std,
    )

    # ------------------------------------------------------------------
    # Build fast caches for all 6G-E cases.
    # ------------------------------------------------------------------
    for name in CASES:
        case = cases[
            name
        ]

        if case.engine == "6G-E_FAST":
            case.runtime = build_fast_runtime(
                base_c,
                base_d,
                base_e,
                case.directivity,
                case.fs,
            )

    print()
    print(
        "=== Case matrix ==="
    )

    setup_rows = []

    for name in CASES:
        case = cases[
            name
        ]

        ir_shape = getattr(
            case.directivity,
            "_irs",
            None,
        )

        setup = {
            "case":
                name,
            "fs_hz":
                int(
                    case.fs
                ),
            "family":
                case.family,
            "engine":
                case.engine,
            "preparation_s":
                float(
                    case.preparation_s
                ),
            "source_delay_s_for_quality":
                float(
                    case.source_delay_s
                ),
            "filter_len":
                int(
                    case.directivity.filter_len_ir
                )
                if hasattr(
                    case.directivity,
                    "filter_len_ir",
                )
                else 1,
            "runtime_ir_shape":
                str(
                    tuple(
                        ir_shape.shape
                    )
                )
                if ir_shape
                is not None
                else "",
            "fast_fft_len":
                int(
                    case.runtime[
                        "fft_len"
                    ]
                )
                if case.runtime
                is not None
                else "",
            "basis_build_s":
                float(
                    case.runtime[
                        "basis_build_s"
                    ]
                )
                if case.runtime
                is not None
                else "",
            "cache_build_s":
                float(
                    case.runtime[
                        "cache_build_s"
                    ]
                )
                if case.runtime
                is not None
                else "",
        }

        setup_rows.append(
            setup
        )

        print(
            "{:29s} fs={:5d} engine={:18s} prep={:8.4f}s filter={:4d} FFT={}".format(
                name,
                case.fs,
                case.engine,
                case.preparation_s,
                setup[
                    "filter_len"
                ],
                setup[
                    "fast_fft_len"
                ],
            )
        )

    write_csv(
        args.output_dir
        / "case_setup.csv",
        setup_rows,
    )

    # ------------------------------------------------------------------
    # Smoke run each case and save deterministic RIRs.
    # ------------------------------------------------------------------
    print()
    print(
        "=== Smoke + RIR capture ==="
    )

    smoke = {}
    quality_rows = []

    for name in CASES:
        case = cases[
            name
        ]

        print(
            "running {:29s} ...".format(
                name
            ),
            flush=True,
        )

        result = execute_case(
            case,
            base_c,
            base_e,
        )

        smoke[
            name
        ] = result

        save_rirs(
            args.output_dir
            / f"rirs_{name}.npz",
            result[
                "rirs"
            ],
            case.fs,
            name,
            case.source_delay_s,
        )

        q = quality_metrics_for_case(
            case,
            result[
                "rirs"
            ],
            base_c,
        )

        quality_rows.extend(
            q
        )

        print(
            "  time={:.6f}s energy={:.9e} rss={:.1f} MiB".format(
                result[
                    "compute_rir_s"
                ],
                result[
                    "energy_mean"
                ],
                result[
                    "peak_rss_after_mb"
                ],
            )
        )

    write_csv(
        args.output_dir
        / "quality_per_mic.csv",
        quality_rows,
    )

    quality_summary = [
        summarize_quality(
            name,
            quality_rows,
        )
        for name in CASES
    ]

    write_csv(
        args.output_dir
        / "quality_summary.csv",
        quality_summary,
    )

    # ------------------------------------------------------------------
    # Mandatory internal closures where equivalence is expected.
    # ------------------------------------------------------------------
    closures = []

    def add_closure(
        reference_name,
        candidate_name,
        mandatory=True,
        max_abs_tol=1e-6,
        energy_tol_db=1e-6,
    ):
        cmp = compare_rirs(
            smoke[
                reference_name
            ][
                "rirs"
            ],
            smoke[
                candidate_name
            ][
                "rirs"
            ],
        )

        row = {
            "reference":
                reference_name,
            "candidate":
                candidate_name,
            "mandatory":
                bool(
                    mandatory
                ),
            **cmp,
        }

        closures.append(
            row
        )

        print(
            "{} vs {} : max_abs={:.3e}, energy_RMSE={:.3e} dB{}".format(
                reference_name,
                candidate_name,
                cmp[
                    "rir_max_abs_error"
                ],
                cmp[
                    "energy_delta_db_rmse"
                ],
                " [GATE]"
                if mandatory
                else "",
            )
        )

        if mandatory:
            if (
                cmp[
                    "rir_max_abs_error"
                ]
                > max_abs_tol
                or cmp[
                    "energy_delta_db_rmse"
                ]
                > energy_tol_db
            ):
                raise RuntimeError(
                    "Mandatory closure failed: "
                    f"{reference_name} vs {candidate_name}"
                )

    print()
    print(
        "=== Internal closures ==="
    )

    # Lite vs standard/Voronoi: same FIR, same 6G-E, must be identical.
    add_closure(
        "SOFA_FIR_FAST_16K",
        "SOFA_FIR_VORONOI_FAST_16K",
        mandatory=True,
        max_abs_tol=1e-12,
        energy_tol_db=1e-12,
    )

    add_closure(
        "SOFA_FIR_FAST_48K",
        "SOFA_FIR_VORONOI_FAST_48K",
        mandatory=True,
        max_abs_tol=1e-12,
        energy_tol_db=1e-12,
    )

    # 6G-E vs PRA native: numerically equivalent implementation target.
    add_closure(
        "SOFA_FIR_NATIVE_16K",
        "SOFA_FIR_VORONOI_FAST_16K",
        mandatory=True,
        max_abs_tol=1e-6,
        energy_tol_db=1e-6,
    )

    add_closure(
        "SOFA_FIR_NATIVE_48K",
        "SOFA_FIR_VORONOI_FAST_48K",
        mandatory=True,
        max_abs_tol=1e-6,
        energy_tol_db=1e-6,
    )

    # Current AXYS TF/FIR pair comes from the same dataset. Characterization
    # only; external TF and FIR files are not required to be equivalent.
    add_closure(
        "SOFA_TF_FAST_16K",
        "SOFA_FIR_FAST_16K",
        mandatory=False,
    )

    add_closure(
        "SOFA_TF_FAST_48K",
        "SOFA_FIR_FAST_48K",
        mandatory=False,
    )

    write_csv(
        args.output_dir
        / "internal_closures.csv",
        closures,
    )

    # ------------------------------------------------------------------
    # Warmup.
    # ------------------------------------------------------------------
    print()
    print(
        "=== Warm-up ==="
    )

    for wr in range(
        args.warmup_rounds
    ):
        shift = (
            wr
            % len(
                CASES
            )
        )

        order = (
            CASES[
                shift:
            ]
            + CASES[
                :shift
            ]
        )

        print(
            "warmup {} order={}".format(
                wr + 1,
                " -> ".join(
                    order
                ),
            )
        )

        for name in order:
            result = execute_case(
                cases[
                    name
                ],
                base_c,
                base_e,
            )

            print(
                "  {:29s} {:.6f}s".format(
                    name,
                    result[
                        "compute_rir_s"
                    ],
                )
            )

    # ------------------------------------------------------------------
    # Balanced timing rounds.
    # ------------------------------------------------------------------
    timing_rows = []

    print()
    print(
        "=== Measured balanced rounds ==="
    )

    for rep in range(
        args.repetitions
    ):
        shift = (
            rep
            % len(
                CASES
            )
        )

        order = (
            CASES[
                shift:
            ]
            + CASES[
                :shift
            ]
        )

        print()
        print(
            "round {}/{} order={}".format(
                rep + 1,
                args.repetitions,
                " -> ".join(
                    order
                ),
            )
        )

        for pos, name in enumerate(
            order,
            start=1,
        ):
            case = cases[
                name
            ]

            result = execute_case(
                case,
                base_c,
                base_e,
            )

            timing_rows.append({
                "round":
                    rep + 1,
                "position_in_round":
                    pos,
                "case":
                    name,
                "fs_hz":
                    int(
                        case.fs
                    ),
                "engine":
                    case.engine,
                "compute_rir_s":
                    float(
                        result[
                            "compute_rir_s"
                        ]
                    ),
                "energy_mean":
                    float(
                        result[
                            "energy_mean"
                        ]
                    ),
                "peak_rss_mb":
                    float(
                        result[
                            "peak_rss_after_mb"
                        ]
                    ),
            })

            print(
                "  {:29s} time={:.6f}s energy={:.9e}".format(
                    name,
                    result[
                        "compute_rir_s"
                    ],
                    result[
                        "energy_mean"
                    ],
                )
            )

    write_csv(
        args.output_dir
        / "timing_runs.csv",
        timing_rows,
    )

    timing_summary = []

    for name in CASES:
        selected = [
            r
            for r in timing_rows
            if r[
                "case"
            ] == name
        ]

        times = [
            r[
                "compute_rir_s"
            ]
            for r in selected
        ]

        energies = [
            r[
                "energy_mean"
            ]
            for r in selected
        ]

        timing_summary.append({
            "case":
                name,
            "fs_hz":
                int(
                    cases[
                        name
                    ].fs
                ),
            "family":
                cases[
                    name
                ].family,
            "engine":
                cases[
                    name
                ].engine,
            "preparation_s":
                float(
                    cases[
                        name
                    ].preparation_s
                ),
            "median_compute_rir_s":
                float(
                    statistics.median(
                        times
                    )
                ),
            "mean_compute_rir_s":
                float(
                    statistics.mean(
                        times
                    )
                ),
            "std_compute_rir_s":
                float(
                    statistics.stdev(
                        times
                    )
                    if len(
                        times
                    ) > 1
                    else 0.0
                ),
            "energy_mean":
                float(
                    statistics.mean(
                        energies
                    )
                ),
            "energy_span":
                float(
                    max(
                        energies
                    )
                    - min(
                        energies
                    )
                ),
        })

    write_csv(
        args.output_dir
        / "timing_summary.csv",
        timing_summary,
    )

    report = {
        "experiment":
            "6M all formats / 16k+48k / fast+voronoi+native / quality",
        "status":
            "PASS",
        "principles": {
            "CF2_FAST_16K":
                (
                    "Magnitude-only 7-band historical fast path."
                ),
            "SOFA_TF_FAST":
                (
                    "FreeFieldDirectivityTF accepted directly; converted only "
                    "to an in-memory runtime FIR required by PRA 0.10.1."
                ),
            "SOFA_FIR_FAST":
                (
                    "GeneralFIR input, ISM-Lite directivity object, 6G-E engine."
                ),
            "SOFA_FIR_VORONOI_FAST":
                (
                    "GeneralFIR input, standard MeasuredDirectivity including "
                    "Voronoi/ray setup, but 6G-E engine."
                ),
            "SOFA_FIR_NATIVE":
                (
                    "GeneralFIR + standard MeasuredDirectivity + unmodified "
                    "Pyroomacoustics compute_rir."
                ),
            "quality_level_is_absolute_spl":
                False,
        },
        "environment": {
            "python":
                platform.python_version(),
            "pyroomacoustics":
                pra.__version__,
            "numpy":
                np.__version__,
            "threads": {
                "PRA_NUM_THREADS":
                    os.getenv(
                        "PRA_NUM_THREADS",
                        "",
                    ),
                "OMP_NUM_THREADS":
                    os.getenv(
                        "OMP_NUM_THREADS",
                        "",
                    ),
                "OPENBLAS_NUM_THREADS":
                    os.getenv(
                        "OPENBLAS_NUM_THREADS",
                        "",
                    ),
                "MKL_NUM_THREADS":
                    os.getenv(
                        "MKL_NUM_THREADS",
                        "",
                    ),
            },
        },
        "room": {
            "dimensions_m":
                np.asarray(
                    base_c.ROOM_DIM,
                    dtype=float,
                ).tolist(),
            "source_m":
                np.asarray(
                    base_c.SOURCE_POS,
                    dtype=float,
                ).tolist(),
            "n_mics":
                int(
                    base_c.MIC_MATRIX.shape[
                        1
                    ]
                ),
            "max_order":
                int(
                    base_c.MAX_ORDER
                ),
            "absorption":
                float(
                    base_c.ABSORPTION
                ),
            "bands_16k_hz":
                PRA_BANDS_BY_FS[
                    16000
                ].tolist(),
            "bands_48k_hz":
                PRA_BANDS_BY_FS[
                    48000
                ].tolist(),
        },
        "quality_analysis": {
            "sofa_source_delay_s":
                float(
                    args.sofa_source_delay_s
                ),
            "onset_definition":
                (
                    "distance/c + source FIR embedded delay for SOFA; "
                    "distance/c for CF2_FAST"
                ),
            "relative_level_note":
                (
                    "RIR-energy level is relative; absolute SPL requires a "
                    "common source calibration before inter-simulator claims."
                ),
        },
        "case_setup":
            setup_rows,
        "timing_summary":
            timing_summary,
        "quality_summary":
            quality_summary,
        "internal_closures":
            closures,
    }

    (
        args.output_dir
        / "summary.json"
    ).write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print(
        "=" * 124
    )
    print(
        "FINAL 6M TIMING SUMMARY"
    )
    print(
        "=" * 124
    )

    for row in timing_summary:
        print(
            "{:29s} fs={:5d} median={:9.4f}s mean={:9.4f}s std={:8.4f}s".format(
                row[
                    "case"
                ],
                row[
                    "fs_hz"
                ],
                row[
                    "median_compute_rir_s"
                ],
                row[
                    "mean_compute_rir_s"
                ],
                row[
                    "std_compute_rir_s"
                ],
            )
        )

    print()
    print(
        "Quality outputs              : quality_per_mic.csv, quality_summary.csv"
    )
    print(
        "RIR outputs                  : rirs_<CASE>.npz"
    )
    print(
        "Absolute SPL calibrated      : NO"
    )
    print(
        "OVERALL                      : PASS"
    )
    print(
        "Outputs                      :",
        args.output_dir,
    )


if __name__ == "__main__":
    main()
