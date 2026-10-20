#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
experiment6g_d_fft_length_optimization.py

EXPERIMENTO 6G-D — OPTIMIZACIÓN EXACTA DEL TAMAÑO FFT
======================================================

Parte de 6G-C y ataca sus tres costes dominantes:
    final_ifft             ~30%
    fractional_delay_fft   ~25%
    directivity_fd_lookup  ~19%

En 6G-C:
    convolution max_len = 1274
    FFT length          = 2048

Pyroomacoustics usa una potencia de 2. Sin embargo, para convolución lineal
sólo necesitamos NFFT >= 1274. scipy.fft.next_fast_len(1274, real=True)
da 1280 en el entorno esperado.

6G-D compara:
    SOFA_NATIVE
    SOFA_FD_2048       -> mismo algoritmo 6G-C, sin copy redundante
    SOFA_FD_FASTLEN    -> mismo algoritmo, NFFT = next_fast_len(max_len)

No se modifica:
    - GeneralFIR
    - 2522 direcciones
    - 683 taps
    - magnitud
    - fase
    - fs
    - geometría
    - absorción
    - max_order

Además elimina un copy redundante:
    directivity_fd[index, :] ya devuelve una copia por advanced indexing,
    por lo que no se aplica .copy() una segunda vez.

Uso:
PRA_NUM_THREADS=1 \
OMP_NUM_THREADS=1 \
OPENBLAS_NUM_THREADS=1 \
MKL_NUM_THREADS=1 \
python experiment6g_d_fft_length_optimization.py \
  --general-fir "clfviewer_validation/experiment6b1_general_fir/AXYS4549_GeneralFIR.sofa" \
  --base-script experiment6g_c_frequency_domain_cache.py \
  --warmup-rounds 3 \
  --repetitions 6 \
  --output-dir "clfviewer_validation/experiment6g_d_fft_length"
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.fft import next_fast_len

import pyroomacoustics as pra
from pyroomacoustics import doa, libroom
from pyroomacoustics.directivities.measured import MeasuredDirectivity
from pyroomacoustics.parameters import constants

import importlib


CASES = (
    "SOFA_NATIVE",
    "SOFA_FD_2048",
    "SOFA_FD_FASTLEN",
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            "Could not load module: {}".format(path)
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


class Stats:
    def __init__(self):
        self.time = defaultdict(float)
        self.calls = defaultdict(int)
        self.items = defaultdict(int)

    def add(self, key, elapsed, items=0):
        self.time[key] += float(elapsed)
        self.calls[key] += 1
        self.items[key] += int(items)


class FDCache:
    def __init__(
        self,
        directivity,
        basis_time,
        fdl,
        fft_len,
    ):
        self.directivity = directivity
        self.basis_time = np.asarray(
            basis_time,
            dtype=float,
        )
        self.fdl = int(fdl)

        self.directivity_len = int(
            directivity.filter_len_ir
        )

        self.attenuation_len = int(
            self.basis_time.shape[-1]
        )

        self.max_len = (
            self.directivity_len
            + self.fdl
            - 1
            + self.attenuation_len
            - 1
        )

        self.fft_len = int(fft_len)

        if self.fft_len < self.max_len:
            raise ValueError(
                "fft_len={} < max_len={}".format(
                    self.fft_len,
                    self.max_len,
                )
            )

        t0 = time.perf_counter()

        self.directivity_fd = np.fft.rfft(
            np.asarray(
                directivity._irs,
                dtype=float,
            ),
            axis=-1,
            n=self.fft_len,
        )

        self.directivity_build_s = (
            time.perf_counter()
            - t0
        )

        t0 = time.perf_counter()

        self.basis_fd = np.fft.rfft(
            self.basis_time,
            axis=-1,
            n=self.fft_len,
        )

        self.basis_build_s = (
            time.perf_counter()
            - t0
        )


def make_fast_compute(
    base,
    original_compute_ism_rir,
    cache: FDCache,
    stats: Stats,
):
    def fast_compute_ism_rir(
        src,
        mic,
        mic_dir,
        src_directions,
        is_visible,
        fdl,
        c,
        fs,
        octave_bands,
        min_phase=True,
        air_abs_coeffs=None,
    ):
        if (
            min_phase
            or mic_dir is not None
            or src.directivity is None
            or not isinstance(
                src.directivity,
                MeasuredDirectivity,
            )
            or src.directivity
            is not cache.directivity
            or octave_bands.n_bands <= 1
            or int(fdl) != cache.fdl
            or int(fs) != base.FS
        ):
            return original_compute_ism_rir(
                src,
                mic,
                mic_dir,
                src_directions,
                is_visible,
                fdl,
                c,
                fs,
                octave_bands,
                min_phase=min_phase,
                air_abs_coeffs=air_abs_coeffs,
            )

        images = src.images[:, is_visible]
        att = src.damping[:, is_visible]

        dist = np.sqrt(
            np.sum(
                (
                    images
                    - mic[:, None]
                ) ** 2,
                axis=0,
            )
        )

        time_arrival = dist / c

        oct_band_amplitude = (
            att / dist
        )

        if air_abs_coeffs is not None:
            oct_band_amplitude = (
                base.ism.apply_air_aborption(
                    oct_band_amplitude,
                    air_abs_coeffs,
                    dist,
                )
            )

        n_images = int(dist.size)

        # ------------------------------------------------------------
        # Direction lookup.
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        azimuth_s, colatitude_s, _ = (
            doa.cart2spher(
                src_directions[:, is_visible]
            )
        )

        cart = doa.spher2cart(
            azimuth_s,
            colatitude_s,
        ).T

        _, direction_index = (
            cache.directivity._kdtree.query(
                cart
            )
        )

        stats.add(
            "direction_query",
            time.perf_counter() - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Fractional delay.
        # ------------------------------------------------------------
        sample_frac = (
            time_arrival
            * fs
        )

        time_ip = np.floor(
            sample_frac
        ).astype(np.int32)

        time_fp = (
            sample_frac
            - time_ip
        ).astype(np.float32)

        frac_delays = np.zeros(
            (
                time_fp.shape[0],
                fdl,
            ),
            dtype=np.float32,
        )

        t0 = time.perf_counter()

        libroom.fractional_delay(
            frac_delays,
            time_fp,
            constants.get(
                "sinc_lut_granularity"
            ),
            constants.get(
                "num_threads"
            ),
        )

        stats.add(
            "fractional_delay",
            time.perf_counter() - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Advanced indexing already returns a new array.
        # No second .copy().
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        fd = cache.directivity_fd[
            direction_index,
            :
        ]

        stats.add(
            "directivity_fd_lookup",
            time.perf_counter() - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Dynamic fractional-delay FFT.
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        frac_fd = np.fft.rfft(
            frac_delays,
            axis=-1,
            n=cache.fft_len,
        )

        stats.add(
            "fractional_delay_fft",
            time.perf_counter() - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Linear attenuation directly in FD.
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        att_fd = (
            oct_band_amplitude.T
            @ cache.basis_fd
        )

        stats.add(
            "attenuation_fd_matmul",
            time.perf_counter() - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Multiplication in place.
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        np.multiply(
            fd,
            frac_fd,
            out=fd,
        )

        np.multiply(
            fd,
            att_fd,
            out=fd,
        )

        stats.add(
            "frequency_domain_multiply",
            time.perf_counter() - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # IFFT and exact linear-convolution trim.
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        irs = np.fft.irfft(
            fd,
            axis=-1,
            n=cache.fft_len,
        )[
            :,
            :cache.max_len,
        ]

        stats.add(
            "final_ifft",
            time.perf_counter() - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Native delay sum and trim.
        # ------------------------------------------------------------
        n_max = int(
            time_ip.max()
            + irs.shape[1]
        )

        rir = np.zeros(
            n_max,
            dtype=np.float32,
        )

        t0 = time.perf_counter()

        libroom.delay_sum(
            irs.astype(
                np.float32
            ),
            time_ip,
            rir,
            constants.get(
                "num_threads"
            ),
        )

        stats.add(
            "delay_sum",
            time.perf_counter() - t0,
            n_images,
        )

        s = (
            constants.get(
                "octave_bands_n_fft"
            )
            // 2
        )

        return rir[s:]

    return fast_compute_ism_rir


def run_fd(
    base,
    directivity,
    cache,
):
    room = base.build_room(
        directivity
    )

    room_module = importlib.import_module(
        "pyroomacoustics.room"
    )

    original = (
        room_module.compute_ism_rir
    )

    stats = Stats()

    room_module.compute_ism_rir = (
        make_fast_compute(
            base,
            original,
            cache,
            stats,
        )
    )

    try:
        t0 = time.perf_counter()
        room.compute_rir()
        elapsed = (
            time.perf_counter()
            - t0
        )
    finally:
        room_module.compute_ism_rir = (
            original
        )

    rirs = base.flatten_rirs(
        room
    )

    return {
        "compute_rir_s":
            float(elapsed),
        "energy_mean":
            base.energy_mean(
                rirs
            ),
        "rirs":
            rirs,
        "stats":
            stats,
    }


def write_csv(
    path: Path,
    rows,
):
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
        w = csv.DictWriter(
            f,
            fieldnames=fields,
        )
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--general-fir",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--base-script",
        type=Path,
        default=Path(
            "experiment6g_c_frequency_domain_cache.py"
        ),
    )

    ap.add_argument(
        "--warmup-rounds",
        type=int,
        default=3,
    )

    ap.add_argument(
        "--repetitions",
        type=int,
        default=6,
    )

    ap.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )

    args = ap.parse_args()

    args.output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    base = load_module(
        args.base_script,
        "experiment6g_c_base",
    )

    (
        measured_file,
        directivity,
        load_s,
    ) = base.load_native_directivity(
        args.general_fir
    )

    temp_room = base.build_room(
        directivity
    )

    original_synthesis = (
        base.AntoniOctaveFilterBank.synthesis
    )

    basis_time = (
        base.prepare_linear_basis(
            temp_room.octave_bands,
            original_synthesis,
        )
    )

    fdl = int(
        constants.get(
            "frac_delay_length"
        )
    )

    directivity_len = int(
        directivity.filter_len_ir
    )

    attenuation_len = int(
        basis_time.shape[-1]
    )

    max_len = (
        directivity_len
        + fdl
        - 1
        + attenuation_len
        - 1
    )

    fft_len_power2 = int(
        2 ** math.ceil(
            math.log2(
                max_len
            )
        )
    )

    fft_len_fast = int(
        next_fast_len(
            max_len,
            real=True,
        )
    )

    cache_2048 = FDCache(
        directivity,
        basis_time,
        fdl,
        fft_len_power2,
    )

    cache_fast = FDCache(
        directivity,
        basis_time,
        fdl,
        fft_len_fast,
    )

    print(
        "=" * 112
    )
    print(
        "EXPERIMENT 6G-D - FFT LENGTH OPTIMIZATION"
    )
    print(
        "=" * 112
    )

    print(
        "GeneralFIR                  :",
        args.general_fir,
    )

    print(
        "IR shape                    :",
        np.asarray(
            measured_file.impulse_responses
        ).shape,
    )

    print(
        "Convolution max_len         :",
        max_len,
    )

    print(
        "Power-of-two FFT length     :",
        fft_len_power2,
    )

    print(
        "next_fast_len(real=True)    :",
        fft_len_fast,
    )

    print(
        "FFT-bin reduction           : {:.2f}%".format(
            (
                1.0
                - (
                    fft_len_fast // 2
                    + 1
                )
                / (
                    fft_len_power2 // 2
                    + 1
                )
            )
            * 100.0
        )
    )

    print(
        "FD cache 2048 shape         :",
        cache_2048.directivity_fd.shape,
    )

    print(
        "FD cache fast shape         :",
        cache_fast.directivity_fd.shape,
    )

    # ------------------------------------------------------------
    # Cache equivalence closure in time domain for the raw FIR bank.
    # ------------------------------------------------------------
    rec_2048 = np.fft.irfft(
        cache_2048.directivity_fd,
        n=cache_2048.fft_len,
        axis=-1,
    )[
        :,
        :directivity_len,
    ]

    rec_fast = np.fft.irfft(
        cache_fast.directivity_fd,
        n=cache_fast.fft_len,
        axis=-1,
    )[
        :,
        :directivity_len,
    ]

    original_ir = np.asarray(
        directivity._irs,
        dtype=float,
    )

    raw_cache_error_2048 = float(
        np.max(
            np.abs(
                rec_2048
                - original_ir
            )
        )
    )

    raw_cache_error_fast = float(
        np.max(
            np.abs(
                rec_fast
                - original_ir
            )
        )
    )

    print()
    print(
        "Directivity FFT roundtrip"
    )

    print(
        "  power2 max abs            : {:.3e}".format(
            raw_cache_error_2048
        )
    )

    print(
        "  fastlen max abs           : {:.3e}".format(
            raw_cache_error_fast
        )
    )

    # ------------------------------------------------------------
    # Warm-up.
    # ------------------------------------------------------------
    def execute(name):
        if name == "SOFA_NATIVE":
            return base.run_native(
                directivity
            )

        if name == "SOFA_FD_2048":
            return run_fd(
                base,
                directivity,
                cache_2048,
            )

        return run_fd(
            base,
            directivity,
            cache_fast,
        )

    print()
    print(
        "=== Warm-up ==="
    )

    for wr in range(
        args.warmup_rounds
    ):
        shift = (
            wr
            % len(CASES)
        )

        order = (
            CASES[shift:]
            + CASES[:shift]
        )

        print(
            "warmup {} order={}".format(
                wr + 1,
                " -> ".join(order),
            )
        )

        for name in order:
            result = execute(
                name
            )

            print(
                "  {:18s} {:.6f}s".format(
                    name,
                    result[
                        "compute_rir_s"
                    ],
                )
            )

    # ------------------------------------------------------------
    # Measured balanced rounds.
    # ------------------------------------------------------------
    runs = []
    closures = []
    components = []

    print()
    print(
        "=== Measured balanced rounds ==="
    )

    for rep in range(
        args.repetitions
    ):
        shift = (
            rep
            % len(CASES)
        )

        order = (
            CASES[shift:]
            + CASES[:shift]
        )

        print()
        print(
            "round {}/{} order={}".format(
                rep + 1,
                args.repetitions,
                " -> ".join(order),
            )
        )

        round_results = {}

        for pos, name in enumerate(
            order,
            start=1,
        ):
            result = execute(
                name
            )

            round_results[
                name
            ] = result

            runs.append({
                "round":
                    rep + 1,
                "position_in_round":
                    pos,
                "case":
                    name,
                "compute_rir_s":
                    result[
                        "compute_rir_s"
                    ],
                "energy_mean":
                    result[
                        "energy_mean"
                    ],
            })

            print(
                "  {:18s} time={:.6f}s energy={:.9e}".format(
                    name,
                    result[
                        "compute_rir_s"
                    ],
                    result[
                        "energy_mean"
                    ],
                )
            )

            if name != "SOFA_NATIVE":
                stats = result[
                    "stats"
                ]

                for key in sorted(
                    stats.time
                ):
                    components.append({
                        "round":
                            rep + 1,
                        "case":
                            name,
                        "component":
                            key,
                        "time_s":
                            float(
                                stats.time[key]
                            ),
                    })

        native = round_results[
            "SOFA_NATIVE"
        ][
            "rirs"
        ]

        for candidate in (
            "SOFA_FD_2048",
            "SOFA_FD_FASTLEN",
        ):
            closure = base.compare_rirs(
                native,
                round_results[
                    candidate
                ][
                    "rirs"
                ],
            )

            closures.append({
                "round":
                    rep + 1,
                "candidate":
                    candidate,
                **closure,
            })

            print(
                "  closure {:18s} max_abs={:.3e} "
                "energy_RMSE={:.3e}dB".format(
                    candidate,
                    closure[
                        "rir_max_abs_error"
                    ],
                    closure[
                        "energy_delta_db_rmse"
                    ],
                )
            )

    # ------------------------------------------------------------
    # Aggregate.
    # ------------------------------------------------------------
    summary_rows = []
    medians = {}

    for name in CASES:
        rows = [
            x
            for x in runs
            if x[
                "case"
            ] == name
        ]

        times = [
            x[
                "compute_rir_s"
            ]
            for x in rows
        ]

        median_t = float(
            statistics.median(
                times
            )
        )

        medians[
            name
        ] = median_t

        summary_rows.append({
            "case":
                name,
            "median_s":
                median_t,
            "mean_s":
                float(
                    statistics.mean(times)
                ),
            "std_s":
                float(
                    statistics.stdev(times)
                    if len(times) > 1
                    else 0.0
                ),
            "min_s":
                float(min(times)),
            "max_s":
                float(max(times)),
        })

    native_t = medians[
        "SOFA_NATIVE"
    ]

    fd2048_t = medians[
        "SOFA_FD_2048"
    ]

    fast_t = medians[
        "SOFA_FD_FASTLEN"
    ]

    max_fast_rir_error = max(
        x[
            "rir_max_abs_error"
        ]
        for x in closures
        if x[
            "candidate"
        ] == "SOFA_FD_FASTLEN"
    )

    max_fast_energy_rmse = max(
        x[
            "energy_delta_db_rmse"
        ]
        for x in closures
        if x[
            "candidate"
        ] == "SOFA_FD_FASTLEN"
    )

    overall = (
        max_fast_rir_error
        <= 1e-6
        and max_fast_energy_rmse
        <= 1e-6
    )

    component_summary = []

    keys = sorted(
        set(
            (
                x[
                    "case"
                ],
                x[
                    "component"
                ],
            )
            for x in components
        )
    )

    for case_name, component in keys:
        vals = [
            x[
                "time_s"
            ]
            for x in components
            if (
                x[
                    "case"
                ] == case_name
                and x[
                    "component"
                ] == component
            )
        ]

        component_summary.append({
            "case":
                case_name,
            "component":
                component,
            "median_s":
                float(
                    statistics.median(vals)
                ),
            "share_of_case_pct":
                float(
                    100.0
                    * statistics.median(vals)
                    / medians[case_name]
                ),
        })

    write_csv(
        args.output_dir
        / "runs.csv",
        runs,
    )

    write_csv(
        args.output_dir
        / "closure.csv",
        closures,
    )

    write_csv(
        args.output_dir
        / "components.csv",
        components,
    )

    write_csv(
        args.output_dir
        / "summary.csv",
        summary_rows,
    )

    report = {
        "experiment":
            "6G-D exact FFT-length optimization",
        "status":
            (
                "PASS"
                if overall
                else "FAIL"
            ),
        "configuration": {
            "convolution_max_len":
                max_len,
            "power2_fft_len":
                fft_len_power2,
            "next_fast_fft_len":
                fft_len_fast,
            "directivity_len":
                directivity_len,
            "attenuation_len":
                attenuation_len,
            "fractional_delay_len":
                fdl,
        },
        "cache_roundtrip": {
            "power2_max_abs":
                raw_cache_error_2048,
            "fastlen_max_abs":
                raw_cache_error_fast,
        },
        "timing": {
            "native_median_s":
                native_t,
            "fd2048_median_s":
                fd2048_t,
            "fastlen_median_s":
                fast_t,
            "fd2048_speedup_vs_native_x":
                native_t
                / fd2048_t,
            "fastlen_speedup_vs_native_x":
                native_t
                / fast_t,
            "fastlen_speedup_vs_fd2048_x":
                fd2048_t
                / fast_t,
            "fastlen_reduction_vs_native_pct":
                (
                    1.0
                    - fast_t
                    / native_t
                )
                * 100.0,
            "fastlen_incremental_reduction_vs_fd2048_pct":
                (
                    1.0
                    - fast_t
                    / fd2048_t
                )
                * 100.0,
        },
        "closure": {
            "fastlen_max_rir_abs_error":
                float(
                    max_fast_rir_error
                ),
            "fastlen_max_energy_rmse_db":
                float(
                    max_fast_energy_rmse
                ),
        },
        "summary":
            summary_rows,
        "component_summary":
            component_summary,
        "method_note":
            (
                "No acoustic representation is changed. The optimization "
                "uses the smallest efficient real FFT length >= the exact "
                "linear-convolution length and removes a redundant copy "
                "after advanced indexing."
            ),
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
        "=" * 112
    )
    print(
        "FINAL 6G-D SUMMARY"
    )
    print(
        "=" * 112
    )

    for row in summary_rows:
        print(
            "{:18s} median={:.6f}s mean={:.6f}s std={:.6f}s".format(
                row[
                    "case"
                ],
                row[
                    "median_s"
                ],
                row[
                    "mean_s"
                ],
                row[
                    "std_s"
                ],
            )
        )

    print()
    print(
        "2048 speedup/native         : {:.3f}x".format(
            native_t / fd2048_t
        )
    )

    print(
        "FASTLEN speedup/native      : {:.3f}x".format(
            native_t / fast_t
        )
    )

    print(
        "FASTLEN speedup/2048        : {:.3f}x".format(
            fd2048_t / fast_t
        )
    )

    print(
        "FASTLEN reduction/native    : {:.2f}%".format(
            (
                1.0
                - fast_t
                / native_t
            )
            * 100.0
        )
    )

    print(
        "FASTLEN incr. reduction     : {:.2f}%".format(
            (
                1.0
                - fast_t
                / fd2048_t
            )
            * 100.0
        )
    )

    print(
        "RIR closure max abs         : {:.3e}".format(
            max_fast_rir_error
        )
    )

    print(
        "Energy closure RMSE         : {:.3e} dB".format(
            max_fast_energy_rmse
        )
    )

    print()
    print(
        "OVERALL                     :",
        (
            "PASS"
            if overall
            else "FAIL"
        ),
    )

    print(
        "Outputs                     :",
        args.output_dir,
    )


if __name__ == "__main__":
    main()
