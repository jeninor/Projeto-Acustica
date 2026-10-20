#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
experiment6g_e_fractional_delay_fd_cache.py

EXPERIMENTO 6G-E — CACHE EXACTA DEL FRACTIONAL DELAY EN FRECUENCIA
==================================================================

Parte del 6G-D validado:
    SOFA_NATIVE        ~8.26 s
    SOFA_FD_2048       ~1.95 s
    SOFA_FD_FASTLEN    ~1.02 s
    NFFT               2048 -> 1280
    RIR closure        PASS

6G-E elimina la FFT dinámica de cada fractional-delay FIR sin cambiar el
modelo acústico.

Pyroomacoustics 0.10.1 construye cada FIR fractional-delay mediante una LUT
sinc de granularidad G=20 y, dentro de cada intervalo, interpolación LINEAL:

    x = (1 - delay_frac) * G
    j = floor(x)
    w = x - j
    h(delay) = h_j + w * (h_{j+1} - h_j)

Como la FFT también es lineal:

    FFT(h(delay))
      = FFT(h_j) + w * (FFT(h_{j+1}) - FFT(h_j))

Por tanto:
- precomputamos con libroom.fractional_delay() los G+1=21 FIR exactos
  correspondientes a los nodos de la LUT;
- precomputamos sus FFT a NFFT=1280;
- durante compute_rir() hacemos sólo lookup + interpolación compleja;
- no construimos frac_delays [n_images,81];
- no ejecutamos rfft(frac_delays) por cada micrófono.

No se modifica:
- GeneralFIR
- 2522 direcciones
- 683 taps
- magnitud/fase
- absorción
- geometría
- max_order
- fractional-delay model/LUT

Casos:
    SOFA_NATIVE
    SOFA_FD_FASTLEN      (6G-D)
    SOFA_FRAC_FD_CACHE   (6G-E)

Uso:
PRA_NUM_THREADS=1 \
OMP_NUM_THREADS=1 \
OPENBLAS_NUM_THREADS=1 \
MKL_NUM_THREADS=1 \
python experiment6g_e_fractional_delay_fd_cache.py \
  --general-fir "clfviewer_validation/experiment6b1_general_fir/AXYS4549_GeneralFIR.sofa" \
  --base-c-script experiment6g_c_frequency_domain_cache.py \
  --base-d-script experiment6g_d_fft_length_optimization.py \
  --warmup-rounds 3 \
  --repetitions 6 \
  --output-dir "clfviewer_validation/experiment6g_e_fractional_delay_cache"
"""

from __future__ import annotations

import argparse
import csv
import importlib
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


CASES = (
    "SOFA_NATIVE",
    "SOFA_FD_FASTLEN",
    "SOFA_FRAC_FD_CACHE",
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

    def add(self, name, elapsed, items=0):
        self.time[name] += float(elapsed)
        self.calls[name] += 1
        self.items[name] += int(items)


class FractionalDelayFDCache:
    """
    Frequency-domain representation of the exact PRA sinc-LUT interpolation
    nodes for one (fdl, granularity, fft_len) configuration.
    """

    def __init__(
        self,
        fdl: int,
        granularity: int,
        fft_len: int,
    ):
        self.fdl = int(fdl)
        self.granularity = int(
            granularity
        )
        self.fft_len = int(
            fft_len
        )

        # Node j corresponds to:
        #   x_off_frac = j
        #   time_fp = 1 - j/G
        #
        # We intentionally generate the node FIRs with the SAME compiled
        # libroom function used by native PRA instead of reimplementing sinc.
        node_j = np.arange(
            self.granularity + 1,
            dtype=np.float32,
        )

        node_time_fp = (
            np.float32(1.0)
            - node_j
            / np.float32(
                self.granularity
            )
        ).astype(
            np.float32
        )

        node_fir = np.zeros(
            (
                self.granularity + 1,
                self.fdl,
            ),
            dtype=np.float32,
        )

        t0 = time.perf_counter()

        libroom.fractional_delay(
            node_fir,
            node_time_fp,
            self.granularity,
            constants.get(
                "num_threads"
            ),
        )

        self.node_fir_build_s = (
            time.perf_counter()
            - t0
        )

        self.node_time_fp = (
            node_time_fp
        )

        self.node_fir = (
            node_fir
        )

        t0 = time.perf_counter()

        self.node_fd = np.fft.rfft(
            node_fir,
            axis=-1,
            n=self.fft_len,
        )

        self.node_fd_build_s = (
            time.perf_counter()
            - t0
        )

    def evaluate(
        self,
        time_fp,
    ):
        """
        Reproduce PRA's LUT interpolation directly in the frequency domain.
        """
        t = np.asarray(
            time_fp,
            dtype=np.float32,
        )

        # Match the C++ arithmetic as closely as practical for T=float.
        xf = (
            (
                np.float32(1.0)
                - t
            )
            * np.float32(
                self.granularity
            )
        ).astype(
            np.float32
        )

        j = np.floor(
            xf
        ).astype(
            np.int64
        )

        # time_fp should be in [0,1), but protect exact endpoint arithmetic.
        j = np.clip(
            j,
            0,
            self.granularity,
        )

        w = (
            xf
            - j.astype(
                np.float32
            )
        ).astype(
            np.float32
        )

        j1 = np.minimum(
            j + 1,
            self.granularity,
        )

        a = self.node_fd[
            j,
            :
        ]

        b = self.node_fd[
            j1,
            :
        ]

        # At j==G native has w==0.  The j1 clamp is therefore harmless.
        return (
            a
            + w[
                :,
                None,
            ]
            * (
                b
                - a
            )
        )


def validate_fractional_cache(
    frac_cache: FractionalDelayFDCache,
):
    """
    Compare cached/interpolated frequency responses against the exact native
    path libroom.fractional_delay -> rfft for random and boundary delays.
    """
    rng = np.random.default_rng(
        123456
    )

    random_t = rng.random(
        4096
    ).astype(
        np.float32
    )

    # Include exact/near LUT boundaries and endpoints.
    g = frac_cache.granularity

    boundary = []

    for j in range(
        g + 1
    ):
        t = np.float32(
            1.0
            - j / g
        )

        boundary.append(
            t
        )

        if (
            t > 0.0
            and t < 1.0
        ):
            boundary.append(
                np.nextafter(
                    t,
                    np.float32(0.0),
                    dtype=np.float32,
                )
            )

            boundary.append(
                np.nextafter(
                    t,
                    np.float32(1.0),
                    dtype=np.float32,
                )
            )

    test_t = np.concatenate([
        random_t,
        np.asarray(
            boundary,
            dtype=np.float32,
        ),
    ])

    # Native compute_ism_rir uses fractional parts in [0,1).
    test_t = np.clip(
        test_t,
        np.float32(0.0),
        np.nextafter(
            np.float32(1.0),
            np.float32(0.0),
            dtype=np.float32,
        ),
    )

    native_fir = np.zeros(
        (
            test_t.size,
            frac_cache.fdl,
        ),
        dtype=np.float32,
    )

    libroom.fractional_delay(
        native_fir,
        test_t,
        frac_cache.granularity,
        constants.get(
            "num_threads"
        ),
    )

    native_fd = np.fft.rfft(
        native_fir,
        axis=-1,
        n=frac_cache.fft_len,
    )

    cached_fd = frac_cache.evaluate(
        test_t
    )

    diff = (
        cached_fd
        - native_fd
    )

    return {
        "n_test_delays":
            int(
                test_t.size
            ),
        "max_abs_error":
            float(
                np.max(
                    np.abs(
                        diff
                    )
                )
            ),
        "rmse":
            float(
                np.sqrt(
                    np.mean(
                        np.abs(
                            diff
                        ) ** 2
                    )
                )
            ),
    }


def make_frac_cached_compute(
    base_c,
    original_compute_ism_rir,
    fd_cache,
    frac_cache,
    stats,
):
    """
    6G-D fast-length path, replacing:
        libroom.fractional_delay + np.fft.rfft(frac_delays)
    by:
        frac_cache.evaluate(time_fp)
    """

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
            is not fd_cache.directivity
            or octave_bands.n_bands <= 1
            or int(fdl)
            != fd_cache.fdl
            or int(fs)
            != base_c.FS
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

        images = src.images[
            :,
            is_visible,
        ]

        att = src.damping[
            :,
            is_visible,
        ]

        dist = np.sqrt(
            np.sum(
                (
                    images
                    - mic[
                        :,
                        None,
                    ]
                ) ** 2,
                axis=0,
            )
        )

        time_arrival = (
            dist
            / c
        )

        oct_band_amplitude = (
            att
            / dist
        )

        if air_abs_coeffs is not None:
            oct_band_amplitude = (
                base_c.ism.apply_air_aborption(
                    oct_band_amplitude,
                    air_abs_coeffs,
                    dist,
                )
            )

        n_images = int(
            dist.size
        )

        # ------------------------------------------------------------
        # Direction lookup.
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        azimuth_s, colatitude_s, _ = (
            doa.cart2spher(
                src_directions[
                    :,
                    is_visible,
                ]
            )
        )

        cart = doa.spher2cart(
            azimuth_s,
            colatitude_s,
        ).T

        _, direction_index = (
            fd_cache
            .directivity
            ._kdtree
            .query(
                cart
            )
        )

        stats.add(
            "direction_query",
            time.perf_counter()
            - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Integer/fractional sample delay.
        # ------------------------------------------------------------
        sample_frac = (
            time_arrival
            * fs
        )

        time_ip = np.floor(
            sample_frac
        ).astype(
            np.int32
        )

        time_fp = (
            sample_frac
            - time_ip
        ).astype(
            np.float32
        )

        # ------------------------------------------------------------
        # NEW 6G-E:
        # exact PRA LUT interpolation directly in frequency domain.
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        frac_fd = (
            frac_cache.evaluate(
                time_fp
            )
        )

        stats.add(
            "fractional_delay_fd_cache",
            time.perf_counter()
            - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Directivity FD lookup.
        # Advanced indexing gives writable output.
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        fd = fd_cache.directivity_fd[
            direction_index,
            :
        ]

        stats.add(
            "directivity_fd_lookup",
            time.perf_counter()
            - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Attenuation directly in frequency domain.
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        att_fd = (
            oct_band_amplitude.T
            @ fd_cache.basis_fd
        )

        stats.add(
            "attenuation_fd_matmul",
            time.perf_counter()
            - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Frequency-domain products.
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
            time.perf_counter()
            - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # IFFT.
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        irs = np.fft.irfft(
            fd,
            axis=-1,
            n=fd_cache.fft_len,
        )[
            :,
            :fd_cache.max_len,
        ]

        stats.add(
            "final_ifft",
            time.perf_counter()
            - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Native overlap-add.
        # ------------------------------------------------------------
        n_max = int(
            time_ip.max()
            + irs.shape[
                1
            ]
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
            time.perf_counter()
            - t0,
            n_images,
        )

        s = (
            constants.get(
                "octave_bands_n_fft"
            )
            // 2
        )

        return rir[
            s:
        ]

    return fast_compute_ism_rir


def run_frac_cached(
    base_c,
    directivity,
    fd_cache,
    frac_cache,
):
    room = base_c.build_room(
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
        make_frac_cached_compute(
            base_c,
            original,
            fd_cache,
            frac_cache,
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

    rirs = base_c.flatten_rirs(
        room
    )

    return {
        "compute_rir_s":
            float(
                elapsed
            ),
        "energy_mean":
            base_c.energy_mean(
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
    rows = list(
        rows
    )

    if not rows:
        return

    fields = []
    seen = set()

    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(
                    key
                )
                fields.append(
                    key
                )

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
        w.writerows(
            rows
        )


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--general-fir",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--base-c-script",
        type=Path,
        default=Path(
            "experiment6g_c_frequency_domain_cache.py"
        ),
    )

    ap.add_argument(
        "--base-d-script",
        type=Path,
        default=Path(
            "experiment6g_d_fft_length_optimization.py"
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

    base_c = load_module(
        args.base_c_script,
        "experiment6g_c_base_e",
    )

    base_d = load_module(
        args.base_d_script,
        "experiment6g_d_base_e",
    )

    (
        measured_file,
        directivity,
        load_s,
    ) = base_c.load_native_directivity(
        args.general_fir
    )

    temp_room = base_c.build_room(
        directivity
    )

    original_synthesis = (
        base_c
        .AntoniOctaveFilterBank
        .synthesis
    )

    basis_time = (
        base_c.prepare_linear_basis(
            temp_room.octave_bands,
            original_synthesis,
        )
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

    directivity_len = int(
        directivity.filter_len_ir
    )

    attenuation_len = int(
        basis_time.shape[
            -1
        ]
    )

    max_len = (
        directivity_len
        + fdl
        - 1
        + attenuation_len
        - 1
    )

    fft_len = int(
        next_fast_len(
            max_len,
            real=True,
        )
    )

    fd_cache = (
        base_d.FDCache(
            directivity,
            basis_time,
            fdl,
            fft_len,
        )
    )

    frac_cache = (
        FractionalDelayFDCache(
            fdl=fdl,
            granularity=granularity,
            fft_len=fft_len,
        )
    )

    frac_closure = (
        validate_fractional_cache(
            frac_cache
        )
    )

    print(
        "="
        * 112
    )
    print(
        "EXPERIMENT 6G-E - FRACTIONAL-DELAY FD CACHE"
    )
    print(
        "="
        * 112
    )

    print(
        "Pyroomacoustics              :",
        pra.__version__,
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
        "FFT length                  :",
        fft_len,
    )

    print(
        "Fractional delay taps       :",
        fdl,
    )

    print(
        "sinc LUT granularity        :",
        granularity,
    )

    print(
        "Fractional FD nodes         :",
        frac_cache.node_fd.shape,
    )

    print(
        "Frac FIR node build excluded: {:.6f}s".format(
            frac_cache.node_fir_build_s
        )
    )

    print(
        "Frac FFT node build excluded: {:.6f}s".format(
            frac_cache.node_fd_build_s
        )
    )

    print()
    print(
        "Fractional-delay FD closure"
    )

    print(
        "  test delays               :",
        frac_closure[
            "n_test_delays"
        ],
    )

    print(
        "  max abs error             : {:.3e}".format(
            frac_closure[
                "max_abs_error"
            ]
        )
    )

    print(
        "  RMSE                      : {:.3e}".format(
            frac_closure[
                "rmse"
            ]
        )
    )

    if (
        frac_closure[
            "max_abs_error"
        ]
        > 1e-5
    ):
        raise RuntimeError(
            "Fractional-delay FD closure failed before room benchmark."
        )

    # ------------------------------------------------------------
    # Case execution.
    # ------------------------------------------------------------
    def execute(
        name,
    ):
        if name == "SOFA_NATIVE":
            return base_c.run_native(
                directivity
            )

        if name == "SOFA_FD_FASTLEN":
            return base_d.run_fd(
                base_c,
                directivity,
                fd_cache,
            )

        return run_frac_cached(
            base_c,
            directivity,
            fd_cache,
            frac_cache,
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
            result = execute(
                name
            )

            print(
                "  {:20s} {:.6f}s".format(
                    name,
                    result[
                        "compute_rir_s"
                    ],
                )
            )

    print()
    print(
        "=== Measured balanced rounds ==="
    )

    runs = []
    closures = []
    components = []

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
                "  {:20s} time={:.6f}s energy={:.9e}".format(
                    name,
                    result[
                        "compute_rir_s"
                    ],
                    result[
                        "energy_mean"
                    ],
                )
            )

            if name == "SOFA_FRAC_FD_CACHE":
                stats = result[
                    "stats"
                ]

                for key in sorted(
                    stats.time
                ):
                    components.append({
                        "round":
                            rep + 1,
                        "component":
                            key,
                        "time_s":
                            float(
                                stats.time[
                                    key
                                ]
                            ),
                        "calls":
                            int(
                                stats.calls[
                                    key
                                ]
                            ),
                        "items":
                            int(
                                stats.items[
                                    key
                                ]
                            ),
                    })

        native = round_results[
            "SOFA_NATIVE"
        ][
            "rirs"
        ]

        for candidate in (
            "SOFA_FD_FASTLEN",
            "SOFA_FRAC_FD_CACHE",
        ):
            closure = (
                base_c.compare_rirs(
                    native,
                    round_results[
                        candidate
                    ][
                        "rirs"
                    ],
                )
            )

            closures.append({
                "round":
                    rep + 1,
                "candidate":
                    candidate,
                **closure,
            })

            print(
                "  closure {:20s} max_abs={:.3e} "
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

        med = float(
            statistics.median(
                times
            )
        )

        medians[
            name
        ] = med

        summary_rows.append({
            "case":
                name,
            "median_s":
                med,
            "mean_s":
                float(
                    statistics.mean(
                        times
                    )
                ),
            "std_s":
                float(
                    statistics.stdev(
                        times
                    )
                    if len(times) > 1
                    else 0.0
                ),
            "min_s":
                float(
                    min(
                        times
                    )
                ),
            "max_s":
                float(
                    max(
                        times
                    )
                ),
        })

    native_t = medians[
        "SOFA_NATIVE"
    ]

    d_t = medians[
        "SOFA_FD_FASTLEN"
    ]

    e_t = medians[
        "SOFA_FRAC_FD_CACHE"
    ]

    max_e_rir_error = max(
        x[
            "rir_max_abs_error"
        ]
        for x in closures
        if x[
            "candidate"
        ] == "SOFA_FRAC_FD_CACHE"
    )

    max_e_energy_rmse = max(
        x[
            "energy_delta_db_rmse"
        ]
        for x in closures
        if x[
            "candidate"
        ] == "SOFA_FRAC_FD_CACHE"
    )

    overall = (
        max_e_rir_error
        <= 1e-6
        and max_e_energy_rmse
        <= 1e-6
    )

    component_summary = []

    for key in sorted(
        set(
            x[
                "component"
            ]
            for x in components
        )
    ):
        vals = [
            x[
                "time_s"
            ]
            for x in components
            if x[
                "component"
            ] == key
        ]

        component_summary.append({
            "component":
                key,
            "median_s":
                float(
                    statistics.median(
                        vals
                    )
                ),
            "share_of_6ge_pct":
                float(
                    100.0
                    * statistics.median(
                        vals
                    )
                    / e_t
                ),
        })

    component_summary.sort(
        key=lambda x: x[
            "median_s"
        ],
        reverse=True,
    )

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
            "6G-E fractional-delay frequency-domain cache",
        "status":
            (
                "PASS"
                if overall
                else "FAIL"
            ),
        "fractional_delay_fd_closure":
            frac_closure,
        "configuration": {
            "fft_len":
                fft_len,
            "convolution_max_len":
                max_len,
            "fdl":
                fdl,
            "sinc_lut_granularity":
                granularity,
            "fractional_fd_node_shape":
                list(
                    frac_cache.node_fd.shape
                ),
        },
        "precompute_excluded": {
            "fractional_node_fir_build_s":
                frac_cache.node_fir_build_s,
            "fractional_node_fd_build_s":
                frac_cache.node_fd_build_s,
            "fd_cache_build_s":
                fd_cache.directivity_build_s,
            "basis_fd_build_s":
                fd_cache.basis_build_s,
        },
        "timing": {
            "native_median_s":
                native_t,
            "6gd_fastlen_median_s":
                d_t,
            "6ge_frac_cache_median_s":
                e_t,
            "6ge_speedup_vs_native_x":
                native_t
                / e_t,
            "6ge_speedup_vs_6gd_x":
                d_t
                / e_t,
            "6ge_reduction_vs_native_pct":
                (
                    1.0
                    - e_t
                    / native_t
                )
                * 100.0,
            "6ge_incremental_reduction_vs_6gd_pct":
                (
                    1.0
                    - e_t
                    / d_t
                )
                * 100.0,
        },
        "closure": {
            "max_rir_abs_error":
                float(
                    max_e_rir_error
                ),
            "max_energy_rmse_db":
                float(
                    max_e_energy_rmse
                ),
        },
        "component_summary":
            component_summary,
        "summary":
            summary_rows,
        "method_note":
            (
                "The compiled PRA fractional-delay model is preserved. "
                "The optimization exploits the exact linear interpolation "
                "structure of the sinc LUT and FFT linearity, caching the "
                "frequency responses of the LUT nodes."
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
        "="
        * 112
    )
    print(
        "FINAL 6G-E SUMMARY"
    )
    print(
        "="
        * 112
    )

    for row in summary_rows:
        print(
            "{:20s} median={:.6f}s mean={:.6f}s std={:.6f}s".format(
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
        "6G-E speedup/native         : {:.3f}x".format(
            native_t
            / e_t
        )
    )

    print(
        "6G-E speedup/6G-D           : {:.3f}x".format(
            d_t
            / e_t
        )
    )

    print(
        "6G-E reduction/native       : {:.2f}%".format(
            (
                1.0
                - e_t
                / native_t
            )
            * 100.0
        )
    )

    print(
        "6G-E incr. reduction/6G-D   : {:.2f}%".format(
            (
                1.0
                - e_t
                / d_t
            )
            * 100.0
        )
    )

    print(
        "RIR closure max abs         : {:.3e}".format(
            max_e_rir_error
        )
    )

    print(
        "Energy closure RMSE         : {:.3e} dB".format(
            max_e_energy_rmse
        )
    )

    print()
    print(
        "6G-E component medians"
    )

    for x in component_summary:
        print(
            "  {:30s} {:8.4f}s {:6.2f}%".format(
                x[
                    "component"
                ],
                x[
                    "median_s"
                ],
                x[
                    "share_of_6ge_pct"
                ],
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
