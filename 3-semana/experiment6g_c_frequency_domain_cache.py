#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
experiment6g_c_frequency_domain_cache.py

EXPERIMENTO 6G-C — SOFA FULL-PHASE CON CACHE EN DOMINIO FRECUENCIAL
===================================================================

Parte de los resultados validados:

6G-A:
    octave_band_synthesis ~74%
    multi_convolve        ~23% (inclusivo)

6G-B:
    cache lineal de octave synthesis:
        ~8.53 s -> ~2.29 s
        speedup ~3.73x
        RIR closure ~1e-14

6G-C ataca el nuevo cuello de botella SIN modificar el SOFA:
    - 2522 direcciones: igual
    - 683 taps a 16 kHz: igual
    - magnitud: igual
    - fase: igual
    - max_order: igual
    - geometría/materiales: igual

Optimizaciones:
1) FFT de los 2522 FIR de directividad se precomputa UNA vez para el tamaño
   de convolución requerido.
2) El filtro de atenuación por bandas no se materializa en tiempo y luego se
   vuelve a transformar. Debido a linealidad (min_phase=False):
       ir_att = C @ basis_time
       FFT(ir_att) = C @ FFT(basis_time)
   Se usa directamente:
       att_fd = C @ basis_fd
3) Sólo permanece por image source la FFT del fractional-delay, las
   multiplicaciones complejas y la IFFT final.

Casos:
    SOFA_NATIVE
    SOFA_CACHED_SYNTH      (optimización 6G-B)
    SOFA_FD_CACHE          (6G-C)

El script exige cierre numérico contra SOFA_NATIVE.

Uso:
PRA_NUM_THREADS=1 \
OMP_NUM_THREADS=1 \
OPENBLAS_NUM_THREADS=1 \
MKL_NUM_THREADS=1 \
python experiment6g_c_frequency_domain_cache.py \
  --general-fir "clfviewer_validation/experiment6b1_general_fir/AXYS4549_GeneralFIR.sofa" \
  --warmup-rounds 3 \
  --repetitions 6 \
  --output-dir "clfviewer_validation/experiment6g_c_fd_cache"
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import math
import os
import platform
import statistics
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyroomacoustics as pra

import pyroomacoustics.simulation.ism as ism
from pyroomacoustics import doa, libroom
from pyroomacoustics.acoustics import AntoniOctaveFilterBank
from pyroomacoustics.directivities import (
    MeasuredDirectivityFile,
    Rotation3D,
)
from pyroomacoustics.directivities.measured import MeasuredDirectivity
from pyroomacoustics.parameters import constants
from pyroomacoustics.soundsource import SoundSource


ROOM_DIM = np.array(
    [6.0, 5.0, 3.0],
    dtype=float,
)

SOURCE_POS = np.array(
    [2.0, 2.5, 1.5],
    dtype=float,
)

FS = 16000
MAX_ORDER = 10
ABSORPTION = 0.35

PRA_BANDS = np.array(
    [
        125.0,
        250.0,
        500.0,
        1000.0,
        2000.0,
        4000.0,
        8000.0,
    ],
    dtype=float,
)

CASES = (
    "SOFA_NATIVE",
    "SOFA_CACHED_SYNTH",
    "SOFA_FD_CACHE",
)


def make_mic_grid():
    xs = np.linspace(
        1.0,
        5.0,
        5,
    )

    ys = np.linspace(
        0.75,
        4.25,
        5,
    )

    z = 1.2

    rows = []

    for y in ys:
        for x in xs:
            rows.append(
                [
                    float(x),
                    float(y),
                    float(z),
                ]
            )

    return np.asarray(
        rows,
        dtype=float,
    ).T


MIC_MATRIX = make_mic_grid()


def flat_material():
    return pra.Material(
        energy_absorption={
            "description":
                "flat 0.35 at PRA octave centers",
            "coeffs":
                [ABSORPTION]
                * len(PRA_BANDS),
            "center_freqs":
                PRA_BANDS.tolist(),
        }
    )


def load_native_directivity(
    path: Path,
):
    t0 = time.perf_counter()

    measured_file = MeasuredDirectivityFile(
        path=path,
        fs=FS,
        interp_order=None,
    )

    load_s = (
        time.perf_counter()
        - t0
    )

    identity = Rotation3D(
        [
            0.0,
            0.0,
        ],
        "yz",
        degrees=True,
    )

    directivity = (
        measured_file.get_source_directivity(
            0,
            orientation=identity,
        )
    )

    return (
        measured_file,
        directivity,
        float(load_s),
    )


def build_room(
    directivity,
):
    room = pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=flat_material(),
        max_order=MAX_ORDER,
        min_phase=False,
        air_absorption=False,
    )

    room.add_microphone_array(
        MIC_MATRIX
    )

    room.add(
        SoundSource(
            SOURCE_POS,
            directivity=directivity,
        )
    )

    return room


def flatten_rirs(
    room,
):
    return [
        np.asarray(
            room.rir[
                m
            ][
                0
            ],
            dtype=float,
        )
        for m in range(
            len(room.rir)
        )
    ]


def energy_mean(
    rirs,
):
    values = [
        float(
            np.sum(
                x * x
            )
        )
        for x in rirs
    ]

    return float(
        np.mean(values)
    )


def compare_rirs(
    reference,
    candidate,
):
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

        pa = np.zeros(
            n,
            dtype=float,
        )

        pb = np.zeros(
            n,
            dtype=float,
        )

        pa[:len(a)] = a
        pb[:len(b)] = b

        d = pb - pa

        max_abs = max(
            max_abs,
            float(
                np.max(
                    np.abs(d)
                )
            ),
        )

        sum_sq += float(
            np.sum(
                d * d
            )
        )

        n_total += n

        ea = float(
            np.sum(
                pa * pa
            )
        )

        eb = float(
            np.sum(
                pb * pb
            )
        )

        if ea > 0.0 and eb > 0.0:
            energy_db.append(
                10.0
                * math.log10(
                    eb / ea
                )
            )

    e = np.asarray(
        energy_db,
        dtype=float,
    )

    return {
        "rir_max_abs_error":
            float(max_abs),
        "rir_rmse":
            float(
                math.sqrt(
                    sum_sq
                    / n_total
                )
            ),
        "energy_delta_db_rmse":
            float(
                np.sqrt(
                    np.mean(
                        e * e
                    )
                )
            ),
        "energy_delta_db_max_abs":
            float(
                np.max(
                    np.abs(e)
                )
            ),
    }


# ============================================================================
# 6G-B cached synthesis support
# ============================================================================

def prepare_linear_basis(
    octave_bands,
    original_synthesis,
):
    identity = np.eye(
        octave_bands.n_bands,
        dtype=float,
    )

    return np.asarray(
        original_synthesis(
            octave_bands,
            identity,
            min_phase=False,
        ),
        dtype=float,
    )


def install_cached_synthesis(
    basis,
):
    original = (
        AntoniOctaveFilterBank.synthesis
    )

    stats = {
        "fast_calls":
            0,
        "fallback_calls":
            0,
        "fast_time_s":
            0.0,
    }

    def cached(
        obj,
        band_magnitudes,
        min_phase=False,
        filter_length=None,
        oversampling=2,
    ):
        coeffs = np.asarray(
            band_magnitudes
        )

        if (
            min_phase
            or coeffs.shape[-1]
            != basis.shape[0]
            or (
                filter_length is not None
                and int(filter_length)
                != basis.shape[-1]
            )
        ):
            stats[
                "fallback_calls"
            ] += 1

            return original(
                obj,
                band_magnitudes,
                min_phase=min_phase,
                filter_length=filter_length,
                oversampling=oversampling,
            )

        t0 = time.perf_counter()

        out = (
            coeffs
            @ basis
        )

        stats[
            "fast_time_s"
        ] += (
            time.perf_counter()
            - t0
        )

        stats[
            "fast_calls"
        ] += 1

        return out

    AntoniOctaveFilterBank.synthesis = (
        cached
    )

    return original, stats


def restore_synthesis(
    original,
):
    AntoniOctaveFilterBank.synthesis = (
        original
    )


# ============================================================================
# 6G-C frequency-domain cache
# ============================================================================

class FastStats:
    def __init__(self):
        self.time = defaultdict(float)
        self.calls = defaultdict(int)
        self.items = defaultdict(int)

    def add(
        self,
        key,
        elapsed,
        items=0,
    ):
        self.time[key] += float(elapsed)
        self.calls[key] += 1
        self.items[key] += int(items)


class FrequencyDomainCache:
    def __init__(
        self,
        directivity: MeasuredDirectivity,
        basis_time: np.ndarray,
        fdl: int,
    ):
        self.directivity = directivity
        self.basis_time = np.asarray(
            basis_time,
            dtype=float,
        )
        self.fdl = int(
            fdl
        )

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

        self.pow2_len = int(
            2 ** math.ceil(
                math.log2(
                    self.max_len
                )
            )
        )

        t0 = time.perf_counter()

        self.directivity_fd = np.fft.rfft(
            np.asarray(
                directivity._irs,
                dtype=float,
            ),
            axis=-1,
            n=self.pow2_len,
        )

        self.directivity_fft_build_s = (
            time.perf_counter()
            - t0
        )

        t0 = time.perf_counter()

        self.basis_fd = np.fft.rfft(
            self.basis_time,
            axis=-1,
            n=self.pow2_len,
        )

        self.basis_fft_build_s = (
            time.perf_counter()
            - t0
        )


def make_fast_compute_ism_rir(
    original_compute_ism_rir,
    cache: FrequencyDomainCache,
    stats: FastStats,
):
    """
    Specialized optimization for this validated benchmark:
    - measured source directivity
    - no microphone directivity
    - n_bands > 1
    - min_phase=False
    - no air absorption

    Unsupported configurations fall back to PRA native.
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
            is not cache.directivity
            or octave_bands.n_bands <= 1
            or int(fdl)
            != cache.fdl
            or int(fs)
            != FS
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
            dist / c
        )

        oct_band_amplitude = (
            att / dist
        )

        if air_abs_coeffs is not None:
            oct_band_amplitude = (
                ism.apply_air_aborption(
                    oct_band_amplitude,
                    air_abs_coeffs,
                    dist,
                )
            )

        n_images = int(
            dist.size
        )

        # ------------------------------------------------------------
        # Exact same angular route as PRA get_response().
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
        # Fractional-delay filters, unchanged.
        # ------------------------------------------------------------
        sample_frac = (
            time_arrival * fs
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
        # Cached directivity FFT lookup.
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        fd = cache.directivity_fd[
            direction_index,
            :
        ].copy()

        stats.add(
            "directivity_fd_lookup",
            time.perf_counter() - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Fractional-delay FFT: still dynamic.
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        frac_fd = np.fft.rfft(
            frac_delays,
            axis=-1,
            n=cache.pow2_len,
        )

        stats.add(
            "fractional_delay_fft",
            time.perf_counter() - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Exact linear octave synthesis DIRECTLY in frequency domain.
        #
        # Native/cached time route:
        #   ir_att = C @ basis_time
        #   att_fd = FFT(ir_att, n=pow2_len)
        #
        # Due to FFT linearity:
        #   att_fd = C @ FFT(basis_time, n=pow2_len)
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
        # Frequency-domain product.
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        fd *= frac_fd
        fd *= att_fd

        stats.add(
            "frequency_domain_multiply",
            time.perf_counter() - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Final IFFT. Same convolution length/trim as multi_convolve.
        # ------------------------------------------------------------
        t0 = time.perf_counter()

        irs = np.fft.irfft(
            fd,
            axis=-1,
            n=cache.pow2_len,
        )

        irs = irs[
            :,
            :cache.max_len,
        ]

        stats.add(
            "final_ifft",
            time.perf_counter() - t0,
            n_images,
        )

        # ------------------------------------------------------------
        # Native overlap-add.
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

        # Same trimming as PRA for linear-phase octave filters.
        s = (
            constants.get(
                "octave_bands_n_fft"
            )
            // 2
        )

        rir = rir[
            s:
        ]

        stats.add(
            "fast_compute_ism_total",
            0.0,
            n_images,
        )

        return rir

    return fast_compute_ism_rir


def run_native(
    directivity,
):
    room = build_room(
        directivity
    )

    t0 = time.perf_counter()
    room.compute_rir()
    elapsed = (
        time.perf_counter()
        - t0
    )

    rirs = flatten_rirs(
        room
    )

    return {
        "compute_rir_s":
            float(elapsed),
        "energy_mean":
            energy_mean(rirs),
        "rirs":
            rirs,
        "stats":
            None,
    }


def run_cached_synth(
    directivity,
    basis_time,
):
    room = build_room(
        directivity
    )

    original, stats = (
        install_cached_synthesis(
            basis_time
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
        restore_synthesis(
            original
        )

    rirs = flatten_rirs(
        room
    )

    return {
        "compute_rir_s":
            float(elapsed),
        "energy_mean":
            energy_mean(rirs),
        "rirs":
            rirs,
        "stats":
            stats,
    }


def run_fd_cache(
    directivity,
    cache,
):
    room = build_room(
        directivity
    )

    room_module = importlib.import_module(
        "pyroomacoustics.room"
    )

    original_compute = (
        room_module.compute_ism_rir
    )

    stats = FastStats()

    room_module.compute_ism_rir = (
        make_fast_compute_ism_rir(
            original_compute,
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
            original_compute
        )

    rirs = flatten_rirs(
        room
    )

    return {
        "compute_rir_s":
            float(elapsed),
        "energy_mean":
            energy_mean(rirs),
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

    (
        measured_file,
        directivity,
        load_s,
    ) = load_native_directivity(
        args.general_fir
    )

    # ----------------------------------------------------------------
    # Build exact linear synthesis basis once outside benchmark timing.
    # ----------------------------------------------------------------
    temp_room = build_room(
        directivity
    )

    original_synthesis = (
        AntoniOctaveFilterBank.synthesis
    )

    t0 = time.perf_counter()

    basis_time = prepare_linear_basis(
        temp_room.octave_bands,
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

    cache = FrequencyDomainCache(
        directivity=directivity,
        basis_time=basis_time,
        fdl=fdl,
    )

    print(
        "=" * 112
    )
    print(
        "EXPERIMENT 6G-C - FULL-PHASE FREQUENCY-DOMAIN CACHE"
    )
    print(
        "=" * 112
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
        "Load/resample time          : {:.6f} s".format(
            load_s
        )
    )
    print(
        "Octave basis shape          :",
        basis_time.shape,
    )
    print(
        "Fractional delay taps       :",
        fdl,
    )
    print(
        "Convolution max_len         :",
        cache.max_len,
    )
    print(
        "Convolution FFT length      :",
        cache.pow2_len,
    )
    print(
        "Directivity FD cache shape  :",
        cache.directivity_fd.shape,
    )
    print(
        "Basis build excluded        : {:.6f} s".format(
            basis_build_s
        )
    )
    print(
        "Directivity FFT cache build : {:.6f} s".format(
            cache.directivity_fft_build_s
        )
    )
    print(
        "Basis FFT cache build       : {:.6f} s".format(
            cache.basis_fft_build_s
        )
    )

    # ----------------------------------------------------------------
    # Algebraic closure for attenuation FFT.
    # ----------------------------------------------------------------
    rng = np.random.default_rng(
        12345
    )

    coeffs = np.abs(
        rng.normal(
            size=(
                256,
                basis_time.shape[0],
            )
        )
    )

    ir_att = (
        coeffs
        @ basis_time
    )

    ref_att_fd = np.fft.rfft(
        ir_att,
        axis=-1,
        n=cache.pow2_len,
    )

    fast_att_fd = (
        coeffs
        @ cache.basis_fd
    )

    attenuation_fd_max_abs = float(
        np.max(
            np.abs(
                ref_att_fd
                - fast_att_fd
            )
        )
    )

    attenuation_fd_rmse = float(
        np.sqrt(
            np.mean(
                np.abs(
                    ref_att_fd
                    - fast_att_fd
                ) ** 2
            )
        )
    )

    print()
    print(
        "Attenuation FD closure"
    )
    print(
        "  max abs error             : {:.3e}".format(
            attenuation_fd_max_abs
        )
    )
    print(
        "  RMSE                      : {:.3e}".format(
            attenuation_fd_rmse
        )
    )

    if attenuation_fd_max_abs > 1e-10:
        raise RuntimeError(
            "Frequency-domain attenuation closure failed."
        )

    # ----------------------------------------------------------------
    # Warm-up with complete cyclic balance.
    # ----------------------------------------------------------------
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
                " -> ".join(order),
            )
        )

        for name in order:
            if name == "SOFA_NATIVE":
                result = run_native(
                    directivity
                )
            elif name == "SOFA_CACHED_SYNTH":
                result = run_cached_synth(
                    directivity,
                    basis_time,
                )
            else:
                result = run_fd_cache(
                    directivity,
                    cache,
                )

            print(
                "  {:18s} {:.6f}s".format(
                    name,
                    result[
                        "compute_rir_s"
                    ],
                )
            )

    # ----------------------------------------------------------------
    # Measured balanced rounds.
    # ----------------------------------------------------------------
    rows = []
    closure_rows = []
    component_rows = []

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
                " -> ".join(order),
            )
        )

        round_results = {}

        for position, name in enumerate(
            order,
            start=1,
        ):
            if name == "SOFA_NATIVE":
                result = run_native(
                    directivity
                )
            elif name == "SOFA_CACHED_SYNTH":
                result = run_cached_synth(
                    directivity,
                    basis_time,
                )
            else:
                result = run_fd_cache(
                    directivity,
                    cache,
                )

            round_results[
                name
            ] = result

            rows.append({
                "round":
                    rep + 1,
                "position_in_round":
                    position,
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

            if name == "SOFA_FD_CACHE":
                stats = result[
                    "stats"
                ]

                for key in sorted(
                    stats.time
                ):
                    component_rows.append({
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

        native_rirs = round_results[
            "SOFA_NATIVE"
        ][
            "rirs"
        ]

        for candidate in (
            "SOFA_CACHED_SYNTH",
            "SOFA_FD_CACHE",
        ):
            closure = compare_rirs(
                native_rirs,
                round_results[
                    candidate
                ][
                    "rirs"
                ],
            )

            closure_rows.append({
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

    # ----------------------------------------------------------------
    # Aggregate timing.
    # ----------------------------------------------------------------
    summaries = []
    medians = {}

    for name in CASES:
        case_rows = [
            x
            for x in rows
            if x[
                "case"
            ] == name
        ]

        times = [
            x[
                "compute_rir_s"
            ]
            for x in case_rows
        ]

        median_t = float(
            statistics.median(
                times
            )
        )

        medians[
            name
        ] = median_t

        summaries.append({
            "case":
                name,
            "median_s":
                median_t,
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
                    min(times)
                ),
            "max_s":
                float(
                    max(times)
                ),
        })

    native_t = medians[
        "SOFA_NATIVE"
    ]

    synth_t = medians[
        "SOFA_CACHED_SYNTH"
    ]

    fd_t = medians[
        "SOFA_FD_CACHE"
    ]

    max_fd_closure = max(
        x[
            "rir_max_abs_error"
        ]
        for x in closure_rows
        if x[
            "candidate"
        ] == "SOFA_FD_CACHE"
    )

    max_fd_energy_rmse = max(
        x[
            "energy_delta_db_rmse"
        ]
        for x in closure_rows
        if x[
            "candidate"
        ] == "SOFA_FD_CACHE"
    )

    overall = (
        max_fd_closure
        <= 1e-6
        and max_fd_energy_rmse
        <= 1e-6
    )

    # Aggregate 6G-C internal components.
    component_summary = []

    component_names = sorted(
        set(
            x[
                "component"
            ]
            for x in component_rows
        )
    )

    for key in component_names:
        vals = [
            x[
                "time_s"
            ]
            for x in component_rows
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
            "share_of_fd_case_pct":
                float(
                    100.0
                    * statistics.median(
                        vals
                    )
                    / fd_t
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
        rows,
    )

    write_csv(
        args.output_dir
        / "closure.csv",
        closure_rows,
    )

    write_csv(
        args.output_dir
        / "fd_components.csv",
        component_rows,
    )

    write_csv(
        args.output_dir
        / "summary.csv",
        summaries,
    )

    report = {
        "experiment":
            "6G-C full-phase frequency-domain cache",
        "status":
            (
                "PASS"
                if overall
                else "FAIL"
            ),
        "general_fir":
            str(
                args.general_fir
            ),
        "configuration": {
            "room_dim_m":
                ROOM_DIM.tolist(),
            "source_pos_m":
                SOURCE_POS.tolist(),
            "n_mics":
                int(
                    MIC_MATRIX.shape[
                        1
                    ]
                ),
            "fs_hz":
                FS,
            "max_order":
                MAX_ORDER,
            "absorption":
                ABSORPTION,
            "min_phase":
                False,
            "ir_shape":
                list(
                    np.asarray(
                        measured_file.impulse_responses
                    ).shape
                ),
            "fractional_delay_taps":
                fdl,
            "convolution_max_len":
                cache.max_len,
            "convolution_fft_len":
                cache.pow2_len,
        },
        "precompute_excluded": {
            "basis_build_s":
                basis_build_s,
            "directivity_fft_cache_build_s":
                cache.directivity_fft_build_s,
            "basis_fft_cache_build_s":
                cache.basis_fft_build_s,
            "directivity_fd_cache_shape":
                list(
                    cache.directivity_fd.shape
                ),
        },
        "attenuation_frequency_domain_closure": {
            "max_abs_error":
                attenuation_fd_max_abs,
            "rmse":
                attenuation_fd_rmse,
        },
        "timing": {
            "native_median_s":
                native_t,
            "cached_synth_median_s":
                synth_t,
            "fd_cache_median_s":
                fd_t,
            "cached_synth_speedup_vs_native_x":
                native_t
                / synth_t,
            "fd_cache_speedup_vs_native_x":
                native_t
                / fd_t,
            "fd_cache_speedup_vs_cached_synth_x":
                synth_t
                / fd_t,
            "fd_cache_time_reduction_vs_native_pct":
                (
                    1.0
                    - fd_t
                    / native_t
                )
                * 100.0,
            "fd_cache_incremental_reduction_vs_cached_synth_pct":
                (
                    1.0
                    - fd_t
                    / synth_t
                )
                * 100.0,
        },
        "closure": {
            "fd_cache_max_rir_abs_error":
                float(
                    max_fd_closure
                ),
            "fd_cache_max_energy_rmse_db":
                float(
                    max_fd_energy_rmse
                ),
            "rir_tolerance":
                1e-6,
            "energy_tolerance_db":
                1e-6,
        },
        "fd_component_medians":
            component_summary,
        "summary":
            summaries,
        "method_note":
            (
                "6G-C changes only computation. It caches the measured "
                "directivity FFT and evaluates linear octave attenuation "
                "directly in the convolution frequency domain. The original "
                "SOFA FIRs, phase, magnitude and room model are preserved."
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
        "FINAL 6G-C SUMMARY"
    )
    print(
        "=" * 112
    )

    for row in summaries:
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
        "Cached synth speedup/native : {:.3f}x".format(
            native_t
            / synth_t
        )
    )

    print(
        "FD cache speedup/native     : {:.3f}x".format(
            native_t
            / fd_t
        )
    )

    print(
        "FD cache speedup/6G-B       : {:.3f}x".format(
            synth_t
            / fd_t
        )
    )

    print(
        "FD reduction vs native      : {:.2f}%".format(
            (
                1.0
                - fd_t
                / native_t
            )
            * 100.0
        )
    )

    print(
        "Incremental reduction vs B  : {:.2f}%".format(
            (
                1.0
                - fd_t
                / synth_t
            )
            * 100.0
        )
    )

    print(
        "RIR closure max abs         : {:.3e}".format(
            max_fd_closure
        )
    )

    print(
        "Energy closure RMSE         : {:.3e} dB".format(
            max_fd_energy_rmse
        )
    )

    print()
    print(
        "6G-C component medians"
    )

    for x in component_summary:
        print(
            "  {:28s} {:8.4f}s {:6.2f}%".format(
                x[
                    "component"
                ],
                x[
                    "median_s"
                ],
                x[
                    "share_of_fd_case_pct"
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
