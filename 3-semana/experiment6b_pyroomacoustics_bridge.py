#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
experiment6b_pyroomacoustics_bridge.py

EXPERIMENTO 6B
==============
Puente controlado desde SOFA FreeFieldDirectivityTF hacia la interfaz
MeasuredDirectivityFile de Pyroomacoustics.

Motivación
----------
Pyroomacoustics no lee FreeFieldDirectivityTF de forma nativa. Su
MeasuredDirectivityFile espera respuestas impulsionales mediante un callback
con esta firma:

    callback(path, fs) ->
        ir, fs, source_locs, mic_locs, source_labels, mic_labels

donde:
    ir          : [n_sources, n_mics, n_taps]
    source_locs : [3, n_sources] spherical
    mic_locs    : [3, n_mics] cartesian

Nuestro SOFA contiene:
    Data.Real/Data.Imag : [1, 2522, F]
    N                   : F frecuencias de tercio de octava
    ReceiverPosition    : [2522,3] spherical

Por tanto, 6B convierte el TF de tercio de octava a un FIR REAL por dirección.

Método TF -> FIR
----------------
Por defecto se usa "third_octave_hold":

1. Cada frecuencia FFT positiva recibe el coeficiente complejo H de la banda
   de tercio de octava correspondiente.
2. Los límites entre bandas son las medias geométricas entre centros
   consecutivos.
3. Fuera del rango medido, por defecto se mantiene la banda extrema
   (--outside-mode edge-hold).
4. DC se fuerza a fase cero y Nyquist a valor real, requisito de un FIR real.
5. Se aplica el MISMO retardo circular a todas las direcciones
   (por defecto NFFT/2) para representar un FIR causal con un retardo global
   conocido.

Este paso NO inventa resolución espectral fina: simplemente extiende cada
valor de tercio de octava sobre su propia banda.

Validaciones
------------
- Convención SOFA y shapes.
- Direcciones cardinales.
- FFT(FIR), compensando el retardo global, reproduce exactamente el espectro
  piecewise usado para sintetizar el FIR.
- En las 24 frecuencias centrales, el TF recuperado debe coincidir con el
  Data.Real/Data.Imag original salvo precisión numérica.
- Opcionalmente, --pra-check instancia MeasuredDirectivityFile mediante el
  callback y comprueba Front/Left/Back/Right.

Uso (preflight independiente de Pyroomacoustics)
------------------------------------------------
python experiment6b_pyroomacoustics_bridge.py ^
  --sofa "clfviewer_validation\experiment6a_sofa_AXYS4549\AXYS4549_FreeFieldDirectivityTF.sofa" ^
  --output-dir clfviewer_validation\experiment6b_pra_bridge ^
  --fs 48000 ^
  --n-fft 2048

Uso con integración Pyroomacoustics
-----------------------------------
python experiment6b_pyroomacoustics_bridge.py ^
  --sofa "clfviewer_validation\experiment6a_sofa_AXYS4549\AXYS4549_FreeFieldDirectivityTF.sofa" ^
  --output-dir clfviewer_validation\experiment6b_pra_bridge ^
  --fs 48000 ^
  --n-fft 2048 ^
  --pra-check

Nota de compatibilidad
----------------------
Pyroomacoustics 0.10.x requiere Python >=3.9. El bridge/preflight funciona
sin Pyroomacoustics; para --pra-check use un entorno compatible.

Salidas
-------
summary.json
band_edges.csv
cardinal_checks.csv

Opcional:
fir_example_front.npy
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import h5py
import numpy as np


EXPECTED_CONVENTION = "FreeFieldDirectivityTF"
EXPECTED_VERSION = "1.1"


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.bytes_):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def write_csv(path: Path, rows: Iterable[dict]) -> None:
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
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def spherical_deg_to_unit_xyz(position: np.ndarray) -> np.ndarray:
    p = np.asarray(position, dtype=float)

    if p.ndim != 2 or p.shape[1] != 3:
        raise ValueError(
            "ReceiverPosition must have shape [R,3], got {}".format(p.shape)
        )

    az = np.deg2rad(p[:, 0])
    el = np.deg2rad(p[:, 1])

    cos_el = np.cos(el)
    x = cos_el * np.cos(az)
    y = cos_el * np.sin(az)
    z = np.sin(el)

    xyz = np.column_stack([x, y, z])

    norms = np.linalg.norm(xyz, axis=1)
    if np.max(np.abs(norms - 1.0)) > 1e-10:
        raise ValueError("Receiver directions are not unit vectors.")

    return xyz


def read_freefield_directivity_tf(path: Path) -> Dict[str, np.ndarray]:
    with h5py.File(str(path), "r") as f:
        convention = _decode(f.attrs.get("SOFAConventions", ""))
        version = _decode(f.attrs.get("SOFAConventionsVersion", ""))

        if convention != EXPECTED_CONVENTION:
            raise ValueError(
                "Expected {}, found {}".format(EXPECTED_CONVENTION, convention)
            )

        if version != EXPECTED_VERSION:
            raise ValueError(
                "Expected convention version {}, found {}".format(
                    EXPECTED_VERSION, version
                )
            )

        required = [
            "Data.Real",
            "Data.Imag",
            "N",
            "ReceiverPosition",
        ]
        missing = [name for name in required if name not in f]
        if missing:
            raise ValueError("Missing SOFA datasets: {}".format(missing))

        real = np.asarray(f["Data.Real"], dtype=float)
        imag = np.asarray(f["Data.Imag"], dtype=float)
        freq = np.asarray(f["N"], dtype=float).reshape(-1)
        receiver = np.asarray(f["ReceiverPosition"], dtype=float)

        rp_type = _decode(f["ReceiverPosition"].attrs.get("Type", ""))
        rp_units = _decode(f["ReceiverPosition"].attrs.get("Units", ""))

        global_attrs = {
            key: _decode(value)
            for key, value in f.attrs.items()
        }

    if real.shape != imag.shape:
        raise ValueError(
            "Data.Real shape {} != Data.Imag shape {}".format(
                real.shape, imag.shape
            )
        )

    if real.ndim != 3 or real.shape[0] != 1:
        raise ValueError(
            "Expected Data.Real/Data.Imag [M=1,R,N], got {}".format(real.shape)
        )

    if real.shape[2] != freq.size:
        raise ValueError(
            "Frequency count {} != Data N dimension {}".format(
                freq.size, real.shape[2]
            )
        )

    if receiver.shape != (real.shape[1], 3):
        raise ValueError(
            "ReceiverPosition shape {} incompatible with R={}".format(
                receiver.shape, real.shape[1]
            )
        )

    if rp_type.lower() != "spherical":
        raise ValueError(
            "6B currently expects spherical ReceiverPosition, got {!r}".format(
                rp_type
            )
        )

    if "degree" not in rp_units.lower() or "metre" not in rp_units.lower():
        raise ValueError(
            "Unexpected ReceiverPosition units: {!r}".format(rp_units)
        )

    if not np.all(np.diff(freq) > 0):
        raise ValueError("SOFA frequency vector must be strictly increasing.")

    H = real[0] + 1j * imag[0]  # [R,F]
    xyz = spherical_deg_to_unit_xyz(receiver)

    return {
        "frequencies_hz": freq,
        "H": H,
        "receiver_position": receiver,
        "direction_xyz": xyz,
        "global_attrs": global_attrs,
    }


def third_octave_edges(center_hz: np.ndarray) -> np.ndarray:
    f = np.asarray(center_hz, dtype=float)

    if f.ndim != 1 or f.size < 2 or np.any(f <= 0):
        raise ValueError("Need at least two positive center frequencies.")

    if not np.all(np.diff(f) > 0):
        raise ValueError("Center frequencies must be strictly increasing.")

    internal = np.sqrt(f[:-1] * f[1:])

    first_ratio = math.sqrt(f[1] / f[0])
    last_ratio = math.sqrt(f[-1] / f[-2])

    lower = f[0] / first_ratio
    upper = f[-1] * last_ratio

    return np.concatenate(
        [
            np.asarray([lower]),
            internal,
            np.asarray([upper]),
        ]
    )


def synthesize_piecewise_spectrum(
    H_band: np.ndarray,
    center_hz: np.ndarray,
    fs: int,
    n_fft: int,
    outside_mode: str = "edge-hold",
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Parameters
    ----------
    H_band : [R,F] complex
    center_hz : [F]
    fs : sampling rate
    n_fft : FFT/FIR length
    outside_mode : edge-hold | unity | zero

    Returns
    -------
    spectrum : [R,K] complex rFFT spectrum before causal delay
    fft_freq : [K]
    band_index : [K], -1 outside measured fractional-octave range
    edges : [F+1]
    """
    H_band = np.asarray(H_band, dtype=np.complex128)
    center_hz = np.asarray(center_hz, dtype=float)

    if H_band.ndim != 2 or H_band.shape[1] != center_hz.size:
        raise ValueError(
            "H_band shape {} incompatible with F={}".format(
                H_band.shape, center_hz.size
            )
        )

    if int(fs) <= 0:
        raise ValueError("fs must be positive.")

    if int(n_fft) < 32:
        raise ValueError("n_fft is too small.")

    nyquist = float(fs) / 2.0

    if center_hz[-1] >= nyquist:
        raise ValueError(
            "Highest SOFA frequency {:g} Hz must be below Nyquist {:g} Hz. "
            "Increase --fs.".format(center_hz[-1], nyquist)
        )

    edges = third_octave_edges(center_hz)
    fft_freq = np.fft.rfftfreq(int(n_fft), d=1.0 / float(fs))

    # Internal geometric boundaries choose one measured band.
    internal = edges[1:-1]
    idx = np.searchsorted(
        internal,
        fft_freq,
        side="right",
    )
    idx = np.clip(
        idx,
        0,
        center_hz.size - 1,
    )

    spectrum = H_band[:, idx].copy()

    below = fft_freq < edges[0]
    above = fft_freq >= edges[-1]
    outside = below | above

    band_index = idx.astype(int)
    band_index[outside] = -1

    if outside_mode == "edge-hold":
        # Already implemented by clamped idx.
        pass
    elif outside_mode == "unity":
        spectrum[:, outside] = 1.0 + 0.0j
    elif outside_mode == "zero":
        spectrum[:, outside] = 0.0 + 0.0j
    else:
        raise ValueError(
            "outside_mode must be edge-hold, unity, or zero"
        )

    # Necessary constraints for a real-valued irfft.
    # Phase at DC is undefined; retain low-frequency magnitude with phase 0.
    spectrum[:, 0] = np.abs(spectrum[:, 0]) + 0.0j

    # For even NFFT the Nyquist bin must be real.
    if int(n_fft) % 2 == 0:
        ny = spectrum[:, -1]
        sign = np.where(np.real(ny) < 0.0, -1.0, 1.0)
        spectrum[:, -1] = np.abs(ny) * sign + 0.0j

    return spectrum, fft_freq, band_index, edges


def spectrum_to_causal_fir(
    spectrum: np.ndarray,
    n_fft: int,
    delay_samples: Optional[int] = None,
) -> Tuple[np.ndarray, int]:
    spectrum = np.asarray(spectrum, dtype=np.complex128)

    if delay_samples is None:
        delay_samples = int(n_fft) // 2

    delay_samples = int(delay_samples)

    if not (0 <= delay_samples < int(n_fft)):
        raise ValueError(
            "delay_samples must satisfy 0 <= delay < n_fft"
        )

    ir_zero_time = np.fft.irfft(
        spectrum,
        n=int(n_fft),
        axis=-1,
    )

    ir_causal = np.roll(
        ir_zero_time,
        delay_samples,
        axis=-1,
    )

    return ir_causal, delay_samples


def recover_undelayed_spectrum(
    ir_causal: np.ndarray,
    fs: int,
    n_fft: int,
    delay_samples: int,
) -> np.ndarray:
    spec_delayed = np.fft.rfft(
        np.asarray(ir_causal, dtype=float),
        n=int(n_fft),
        axis=-1,
    )

    fft_freq = np.fft.rfftfreq(
        int(n_fft),
        d=1.0 / float(fs),
    )

    compensation = np.exp(
        1j
        * 2.0
        * np.pi
        * fft_freq
        * float(delay_samples)
        / float(fs)
    )

    return spec_delayed * compensation[None, :]


def center_bin_errors(
    recovered_spectrum: np.ndarray,
    fft_freq: np.ndarray,
    original_band_H: np.ndarray,
    center_hz: np.ndarray,
) -> dict:
    recovered_spectrum = np.asarray(
        recovered_spectrum,
        dtype=np.complex128,
    )
    original_band_H = np.asarray(
        original_band_H,
        dtype=np.complex128,
    )

    indices = np.asarray(
        [
            int(np.argmin(np.abs(fft_freq - f)))
            for f in center_hz
        ],
        dtype=int,
    )

    recovered = recovered_spectrum[:, indices]

    complex_err = np.abs(
        recovered
        - original_band_H
    )

    amp_ref = np.abs(
        original_band_H
    )
    amp_rec = np.abs(
        recovered
    )

    with np.errstate(
        divide="ignore",
        invalid="ignore",
    ):
        db_ref = 20.0 * np.log10(amp_ref)
        db_rec = 20.0 * np.log10(amp_rec)

    finite_db = np.isfinite(db_ref) & np.isfinite(db_rec)

    phase_err = np.angle(
        recovered
        * np.conj(
            original_band_H
        )
    )

    return {
        "center_fft_indices": indices,
        "center_fft_hz": fft_freq[indices],
        "max_complex_abs_error":
            float(np.max(complex_err)),
        "max_magnitude_error_db":
            float(
                np.max(
                    np.abs(
                        db_rec[finite_db]
                        - db_ref[finite_db]
                    )
                )
            )
            if np.any(finite_db)
            else None,
        "max_wrapped_phase_error_rad":
            float(np.max(np.abs(phase_err))),
    }


def cardinal_indices(xyz: np.ndarray) -> Dict[str, int]:
    targets = {
        "Front": np.asarray([1.0, 0.0, 0.0]),
        "Left": np.asarray([0.0, 1.0, 0.0]),
        "Back": np.asarray([-1.0, 0.0, 0.0]),
        "Right": np.asarray([0.0, -1.0, 0.0]),
        "Up": np.asarray([0.0, 0.0, 1.0]),
        "Down": np.asarray([0.0, 0.0, -1.0]),
    }

    out = {}
    for name, target in targets.items():
        out[name] = int(
            np.argmin(
                np.linalg.norm(
                    xyz - target,
                    axis=1,
                )
            )
        )
    return out


class FreeFieldTFBridge:
    """
    Reusable SOFA -> FIR bridge.

    This class intentionally does not import pyroomacoustics.
    Use .callback as MeasuredDirectivityFile(file_reader_callback=...).
    """

    def __init__(
        self,
        sofa_path: Path,
        fs: int = 48000,
        n_fft: int = 2048,
        delay_samples: Optional[int] = None,
        outside_mode: str = "edge-hold",
    ):
        self.sofa_path = Path(sofa_path)
        self.fs = int(fs)
        self.n_fft = int(n_fft)
        self.delay_samples_requested = delay_samples
        self.outside_mode = str(outside_mode)

        self.data = read_freefield_directivity_tf(
            self.sofa_path
        )

        (
            self.spectrum,
            self.fft_freq,
            self.band_index,
            self.band_edges,
        ) = synthesize_piecewise_spectrum(
            self.data["H"],
            self.data["frequencies_hz"],
            self.fs,
            self.n_fft,
            outside_mode=self.outside_mode,
        )

        (
            self.ir,
            self.delay_samples,
        ) = spectrum_to_causal_fir(
            self.spectrum,
            self.n_fft,
            delay_samples=self.delay_samples_requested,
        )

    def callback(self, path, fs=None):
        """
        Callback compatible with pyroomacoustics.MeasuredDirectivityFile.

        If Pyroomacoustics requests another fs, rebuild the bridge at that fs.
        """
        target_fs = self.fs if fs is None else int(fs)

        if target_fs != self.fs:
            tmp = FreeFieldTFBridge(
                self.sofa_path,
                fs=target_fs,
                n_fft=self.n_fft,
                delay_samples=self.delay_samples_requested,
                outside_mode=self.outside_mode,
            )
            return tmp.callback(path=path, fs=target_fs)

        # One source at origin. Pyroomacoustics expects spherical source_locs.
        source_locs = np.zeros(
            (3, 1),
            dtype=float,
        )

        # Measurement receivers become angular sampling points around source.
        # Radius is irrelevant for GridSphere; use unit vectors.
        mic_locs = self.data[
            "direction_xyz"
        ].T.copy()

        ir = self.ir[
            None,
            :,
            :
        ]  # [1,R,taps]

        source_labels = ["AXYS4549"]
        mic_labels = [
            "dir_{:04d}".format(i)
            for i in range(
                mic_locs.shape[1]
            )
        ]

        return (
            ir,
            target_fs,
            source_locs,
            mic_locs,
            source_labels,
            mic_labels,
        )


def run_pra_check(
    bridge: FreeFieldTFBridge,
) -> dict:
    try:
        import pyroomacoustics as pra
        from pyroomacoustics.directivities import (
            MeasuredDirectivityFile,
            Rotation3D,
        )
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "error": "{}: {}".format(
                type(exc).__name__,
                exc,
            ),
        }

    try:
        measured_file = MeasuredDirectivityFile(
            path=bridge.sofa_path,
            fs=bridge.fs,
            interp_order=None,
            file_reader_callback=bridge.callback,
        )

        identity = Rotation3D(
            [0.0, 0.0],
            "yz",
            degrees=True,
        )

        directivity = measured_file.get_source_directivity(
            0,
            orientation=identity,
        )

        card = cardinal_indices(
            bridge.data[
                "direction_xyz"
            ]
        )

        query_rows = {}

        targets = {
            "Front": np.asarray([1.0, 0.0, 0.0]),
            "Left": np.asarray([0.0, 1.0, 0.0]),
            "Back": np.asarray([-1.0, 0.0, 0.0]),
            "Right": np.asarray([0.0, -1.0, 0.0]),
        }

        max_ir_error = 0.0

        for name, vec in targets.items():
            response = np.asarray(
                directivity.get_response_cartesian(
                    vec.reshape(1, 3)
                )
            )

            response = np.squeeze(response)

            expected = bridge.ir[
                card[name]
            ]

            err = float(
                np.max(
                    np.abs(
                        response
                        - expected
                    )
                )
            )

            max_ir_error = max(
                max_ir_error,
                err,
            )

            query_rows[name] = {
                "direction_index":
                    int(card[name]),
                "max_ir_abs_error":
                    err,
            }

        return {
            "status": "PASS"
            if max_ir_error <= 1e-12
            else "FAIL",
            "pyroomacoustics_version":
                str(
                    getattr(
                        pra,
                        "__version__",
                        "",
                    )
                ),
            "ir_shape":
                list(
                    bridge.ir.shape
                ),
            "filter_len_ir":
                int(
                    directivity.filter_len_ir
                ),
            "max_cardinal_ir_abs_error":
                max_ir_error,
            "cardinal_queries":
                query_rows,
        }

    except Exception as exc:
        return {
            "status": "FAIL",
            "error": "{}: {}".format(
                type(exc).__name__,
                exc,
            ),
        }


def main() -> None:
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--sofa",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--fs",
        type=int,
        default=48000,
    )

    ap.add_argument(
        "--n-fft",
        type=int,
        default=2048,
    )

    ap.add_argument(
        "--delay-samples",
        type=int,
        default=None,
    )

    ap.add_argument(
        "--outside-mode",
        choices=[
            "edge-hold",
            "unity",
            "zero",
        ],
        default="edge-hold",
    )

    ap.add_argument(
        "--pra-check",
        action="store_true",
    )

    ap.add_argument(
        "--save-front-fir",
        action="store_true",
    )

    args = ap.parse_args()

    args.output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    bridge = FreeFieldTFBridge(
        args.sofa,
        fs=args.fs,
        n_fft=args.n_fft,
        delay_samples=args.delay_samples,
        outside_mode=args.outside_mode,
    )

    recovered = recover_undelayed_spectrum(
        bridge.ir,
        bridge.fs,
        bridge.n_fft,
        bridge.delay_samples,
    )

    full_spectrum_error = float(
        np.max(
            np.abs(
                recovered
                - bridge.spectrum
            )
        )
    )

    # Verify that FFT resolution is sufficient for every measured center:
    # the nearest FFT bin to center i must still belong to third-octave band i.
    center_indices = np.asarray(
        [
            int(
                np.argmin(
                    np.abs(
                        bridge.fft_freq - f
                    )
                )
            )
            for f in bridge.data[
                "frequencies_hz"
            ]
        ],
        dtype=int,
    )

    wrong_center_bands = []
    for i, fft_index in enumerate(center_indices):
        actual_band = int(
            bridge.band_index[
                fft_index
            ]
        )

        if actual_band != i:
            wrong_center_bands.append({
                "measured_band_index": i,
                "center_hz": float(
                    bridge.data[
                        "frequencies_hz"
                    ][i]
                ),
                "nearest_fft_hz": float(
                    bridge.fft_freq[
                        fft_index
                    ]
                ),
                "assigned_band_index": actual_band,
            })

    if wrong_center_bands:
        raise RuntimeError(
            "NFFT resolution is too low to represent all measured "
            "third-octave centers independently. "
            "Increase --n-fft. First mismatches: {}".format(
                wrong_center_bands[:5]
            )
        )

    center = center_bin_errors(
        recovered,
        bridge.fft_freq,
        bridge.data["H"],
        bridge.data["frequencies_hz"],
    )

    card = cardinal_indices(
        bridge.data[
            "direction_xyz"
        ]
    )

    cardinal_rows = []

    for name, index in card.items():
        pos = bridge.data[
            "receiver_position"
        ][index]
        xyz = bridge.data[
            "direction_xyz"
        ][index]

        cardinal_rows.append({
            "name":
                name,
            "direction_index":
                index,
            "azimuth_deg":
                float(pos[0]),
            "elevation_deg":
                float(pos[1]),
            "radius_m":
                float(pos[2]),
            "x":
                float(xyz[0]),
            "y":
                float(xyz[1]),
            "z":
                float(xyz[2]),
        })

    write_csv(
        args.output_dir
        / "cardinal_checks.csv",
        cardinal_rows,
    )

    edges = bridge.band_edges

    band_rows = []

    for i, f in enumerate(
        bridge.data[
            "frequencies_hz"
        ]
    ):
        band_rows.append({
            "band_index":
                i,
            "center_hz":
                float(f),
            "lower_edge_hz":
                float(edges[i]),
            "upper_edge_hz":
                float(edges[i + 1]),
        })

    write_csv(
        args.output_dir
        / "band_edges.csv",
        band_rows,
    )

    pra_result = None

    if args.pra_check:
        pra_result = run_pra_check(
            bridge
        )

    if args.save_front_fir:
        np.save(
            args.output_dir
            / "fir_example_front.npy",
            bridge.ir[
                card[
                    "Front"
                ]
            ],
        )

    attrs = bridge.data[
        "global_attrs"
    ]

    status = (
        "PASS"
        if (
            full_spectrum_error <= 5e-12
            and center[
                "max_complex_abs_error"
            ] <= 5e-12
            and center[
                "max_magnitude_error_db"
            ] <= 1e-10
            and center[
                "max_wrapped_phase_error_rad"
            ] <= 1e-10
            and (
                pra_result is None
                or pra_result[
                    "status"
                ] == "PASS"
            )
        )
        else "FAIL"
    )

    summary = {
        "experiment":
            "6B FreeFieldDirectivityTF to Pyroomacoustics bridge",
        "status":
            status,
        "sofa": {
            "file":
                str(args.sofa),
            "convention":
                attrs.get(
                    "SOFAConventions",
                    "",
                ),
            "convention_version":
                attrs.get(
                    "SOFAConventionsVersion",
                    "",
                ),
            "n_directions":
                int(
                    bridge.data[
                        "H"
                    ].shape[0]
                ),
            "n_measured_frequencies":
                int(
                    bridge.data[
                        "H"
                    ].shape[1]
                ),
            "frequency_min_hz":
                float(
                    bridge.data[
                        "frequencies_hz"
                    ][0]
                ),
            "frequency_max_hz":
                float(
                    bridge.data[
                        "frequencies_hz"
                    ][-1]
                ),
        },
        "fir_bridge": {
            "method":
                "third_octave_hold",
            "outside_mode":
                bridge.outside_mode,
            "fs_hz":
                bridge.fs,
            "n_fft":
                bridge.n_fft,
            "n_taps":
                int(
                    bridge.ir.shape[-1]
                ),
            "global_delay_samples":
                bridge.delay_samples,
            "global_delay_seconds":
                float(
                    bridge.delay_samples
                    / bridge.fs
                ),
            "ir_shape":
                [
                    1,
                    int(
                        bridge.ir.shape[0]
                    ),
                    int(
                        bridge.ir.shape[1]
                    ),
                ],
        },
        "roundtrip": {
            "piecewise_spectrum_max_abs_error":
                full_spectrum_error,
            "center_frequency_max_complex_abs_error":
                center[
                    "max_complex_abs_error"
                ],
            "center_frequency_max_magnitude_error_db":
                center[
                    "max_magnitude_error_db"
                ],
            "center_frequency_max_wrapped_phase_error_rad":
                center[
                    "max_wrapped_phase_error_rad"
                ],
            "center_fft_hz":
                center[
                    "center_fft_hz"
                ].tolist(),
        },
        "pyroomacoustics_check":
            pra_result,
        "important_semantics": {
            "tf_to_fir_assumption":
                (
                    "Each measured third-octave complex coefficient is "
                    "held constant over its geometric fractional-octave band."
                ),
            "global_delay":
                (
                    "A common causalization delay is applied to every "
                    "direction. Remove/account for it when interpreting "
                    "absolute RIR timing."
                ),
            "directivity_only":
                (
                    "The SOFA contains dimensionless directional gain, "
                    "not absolute loudspeaker sensitivity."
                ),
        },
    }

    (
        args.output_dir
        / "summary.json"
    ).write_text(
        json.dumps(
            summary,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("=" * 112)
    print("EXPERIMENT 6B - PYROOMACOUSTICS BRIDGE")
    print("=" * 112)
    print(
        "SOFA convention              :",
        summary[
            "sofa"
        ][
            "convention"
        ],
        summary[
            "sofa"
        ][
            "convention_version"
        ],
    )
    print(
        "Directions / measured bands  :",
        "{} / {}".format(
            summary[
                "sofa"
            ][
                "n_directions"
            ],
            summary[
                "sofa"
            ][
                "n_measured_frequencies"
            ],
        ),
    )
    print(
        "FIR fs / taps                :",
        "{} Hz / {}".format(
            bridge.fs,
            bridge.n_fft,
        ),
    )
    print(
        "Global FIR delay             :",
        "{} samples = {:.6f} s".format(
            bridge.delay_samples,
            bridge.delay_samples
            / bridge.fs,
        ),
    )
    print(
        "Piecewise spectrum error     :",
        "{:.3e}".format(
            full_spectrum_error
        ),
    )
    print(
        "Center complex error         :",
        "{:.3e}".format(
            center[
                "max_complex_abs_error"
            ]
        ),
    )
    print(
        "Center magnitude error       :",
        "{:.3e} dB".format(
            center[
                "max_magnitude_error_db"
            ]
        ),
    )
    print(
        "Center phase error           :",
        "{:.3e} rad".format(
            center[
                "max_wrapped_phase_error_rad"
            ]
        ),
    )

    if pra_result is not None:
        print()
        print(
            "Pyroomacoustics check        :",
            pra_result[
                "status"
            ],
        )
        if (
            pra_result[
                "status"
            ]
            == "PASS"
        ):
            print(
                "Pyroomacoustics version      :",
                pra_result[
                    "pyroomacoustics_version"
                ],
            )
            print(
                "Cardinal IR max error        :",
                "{:.3e}".format(
                    pra_result[
                        "max_cardinal_ir_abs_error"
                    ]
                ),
            )
        elif "error" in pra_result:
            print(
                "Pyroomacoustics error        :",
                pra_result[
                    "error"
                ],
            )

    print()
    print(
        "OVERALL                      :",
        status,
    )
    print(
        "Outputs                      :",
        args.output_dir,
    )


if __name__ == "__main__":
    main()
