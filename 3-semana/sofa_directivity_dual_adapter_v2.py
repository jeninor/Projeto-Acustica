#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
sofa_directivity_dual_adapter.py

Unified SOFA directivity loader for Pyroomacoustics 0.10.1.

Accepted inputs
---------------
1) SOFAConventions = FreeFieldDirectivityTF
   DataType         = TF

2) SOFAConventions = GeneralFIR

The two formats remain independent first-class inputs.

FreeFieldDirectivityTF path
---------------------------
The measured complex transfer functions stored in SOFA are used directly as
the measured anchors. They are rendered to a finite runtime FIR only because
Pyroomacoustics 0.10.1's standard ISM path consumes impulse responses.

Optional CF2 validation:
    If cf2_path + parser_path + canonical_path are supplied, the original CF2
    binary is reconstructed and required to close against the measured SOFA TF.
    In that mode, the binary H values are used as the authoritative measured
    anchors after successful validation.

GeneralFIR path
---------------
Loaded directly with Pyroomacoustics MeasuredDirectivityFile. It is NOT
regenerated from the TF file and is NOT required to match the TF simulation.

Runtime modes
-------------
standard:
    Standard Pyroomacoustics MeasuredDirectivity.

ism-lite:
    Skips octave-band energy / Voronoi / ray-sampling initialization.
    This is valid only for deterministic ISM. Ray tracing is intentionally
    unsupported.

This module performs input/loading only. Simulation is left to the caller.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np
import pyroomacoustics as pra

from scipy.spatial import cKDTree

from pyroomacoustics.directivities import Rotation3D
from pyroomacoustics.directivities.measured import (
    MeasuredDirectivity,
    MeasuredDirectivityFile,
)
from pyroomacoustics.doa.grid import GridSphere
from pyroomacoustics.utilities import resample


SUPPORTED = {
    ("FreeFieldDirectivityTF", "TF"),
}


def _decode(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
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


def inspect_sofa(path):
    path = Path(path)

    with h5py.File(str(path), "r") as f:
        convention = _decode(
            f.attrs.get("SOFAConventions", "")
        )
        data_type = _decode(
            f.attrs.get("DataType", "")
        )

    return {
        "path": str(path),
        "SOFAConventions": convention,
        "DataType": data_type,
    }


def _sofa_receiver_directions(f):
    """
    Return [R,3] unit Cartesian direction vectors from ReceiverPosition.

    SOFA spherical coordinates are interpreted as:
        azimuth [deg], elevation [deg], radius [m].
    """
    p = np.asarray(
        f["ReceiverPosition"],
        dtype=float,
    )

    if p.ndim == 3:
        # Common SOFA storage may contain an M dimension.
        p = np.squeeze(p)

    if p.ndim != 2 or p.shape[-1] != 3:
        raise RuntimeError(
            "Unsupported ReceiverPosition shape: {}".format(p.shape)
        )

    position_type = _decode(
        f["ReceiverPosition"].attrs.get(
            "Type",
            f.attrs.get(
                "ReceiverPosition_Type",
                "",
            ),
        )
    ).strip().lower()

    if "spherical" in position_type:
        az = np.deg2rad(p[:, 0])
        el = np.deg2rad(p[:, 1])

        x = np.cos(el) * np.cos(az)
        y = np.cos(el) * np.sin(az)
        z = np.sin(el)

        xyz = np.column_stack((x, y, z))

    elif "cartesian" in position_type:
        xyz = p.copy()

    else:
        raise RuntimeError(
            "Unsupported/missing ReceiverPosition Type: {!r}".format(
                position_type
            )
        )

    norms = np.linalg.norm(
        xyz,
        axis=1,
        keepdims=True,
    )

    if np.any(norms <= 0.0):
        raise RuntimeError(
            "ReceiverPosition contains zero-length direction vectors."
        )

    return xyz / norms


class ISMOnlyMeasuredDirectivity(MeasuredDirectivity):
    """
    MeasuredDirectivity-compatible object for deterministic ISM only.

    It intentionally skips _compute_band_energies(), Voronoi areas, and
    ray-sampling distributions. Therefore ray tracing is not supported.
    """

    def __init__(
        self,
        orientation,
        grid,
        impulse_responses,
        fs,
    ):
        if not isinstance(orientation, Rotation3D):
            raise ValueError(
                "orientation must be a Rotation3D"
            )

        self._original_grid = grid
        self._irs = np.asarray(
            impulse_responses,
            dtype=float,
        )
        self.fs = int(fs)

        self.set_orientation(
            orientation
        )

    def set_orientation(
        self,
        orientation,
    ):
        if not isinstance(orientation, Rotation3D):
            raise ValueError(
                "orientation must be a Rotation3D"
            )

        self._orientation = orientation

        self._grid = GridSphere(
            cartesian_points=self._orientation.rotate(
                self._original_grid.cartesian
            )
        )

        self._kdtree = cKDTree(
            self._grid.cartesian.T
        )

    def sample_rays(
        self,
        n_rays,
        rng=None,
    ):
        raise RuntimeError(
            "ISMOnlyMeasuredDirectivity does not support ray tracing."
        )


@dataclass
class LoadedSOFADirectivity:
    path: Path
    convention: str
    data_type: str
    runtime_mode: str
    directivity: MeasuredDirectivity
    room_fs: int
    preparation_s: float
    metadata: dict
    owner: object = None

    @property
    def supports_ray_tracing(self):
        return self.runtime_mode == "standard"


class SOFADirectivityFactory:
    def __init__(
        self,
        *,
        room_fs=16000,
        runtime_mode="ism-lite",
        render_fs=48000,
        render_n_fft=2048,
        render_delay_samples=None,
        outside_mode="edge-hold",
        resample_backend="scipy",
        bridge_path=None,
        source_id=0,
    ):
        self.room_fs = int(room_fs)
        self.runtime_mode = str(runtime_mode)
        self.render_fs = int(render_fs)
        self.render_n_fft = int(render_n_fft)
        self.render_delay_samples = (
            self.render_n_fft // 2
            if render_delay_samples is None
            else int(render_delay_samples)
        )
        self.outside_mode = str(outside_mode)
        self.resample_backend = str(resample_backend)
        self.bridge_path = (
            None
            if bridge_path is None
            else Path(bridge_path)
        )
        self.source_id = int(source_id)

        if self.runtime_mode not in (
            "standard",
            "ism-lite",
        ):
            raise ValueError(
                "runtime_mode must be 'standard' or 'ism-lite'"
            )

    @staticmethod
    def identity_orientation():
        return Rotation3D(
            [0.0, 0.0],
            "yz",
            degrees=True,
        )

    def load(
        self,
        path,
        *,
        orientation=None,
        cf2_path=None,
        parser_path=None,
        canonical_path=None,
        coherence_threshold=0.90,
        binary_closure_tolerance=1e-12,
    ):
        path = Path(path)
        info = inspect_sofa(path)

        if orientation is None:
            orientation = self.identity_orientation()

        convention = info["SOFAConventions"]
        data_type = info["DataType"]

        if convention == "FreeFieldDirectivityTF":
            if data_type != "TF":
                raise RuntimeError(
                    "FreeFieldDirectivityTF requires DataType=TF; "
                    f"got {data_type!r}"
                )

            return self._load_tf(
                path=path,
                orientation=orientation,
                cf2_path=cf2_path,
                parser_path=parser_path,
                canonical_path=canonical_path,
                coherence_threshold=coherence_threshold,
                binary_closure_tolerance=binary_closure_tolerance,
            )

        if convention == "GeneralFIR":
            return self._load_generalfir(
                path=path,
                orientation=orientation,
            )

        raise RuntimeError(
            "Unsupported SOFA input: SOFAConventions={!r}, DataType={!r}. "
            "Accepted: FreeFieldDirectivityTF/TF and GeneralFIR."
            .format(
                convention,
                data_type,
            )
        )

    def _make_directivity(
        self,
        *,
        grid,
        ir,
        fs,
        orientation,
    ):
        cls = (
            MeasuredDirectivity
            if self.runtime_mode == "standard"
            else ISMOnlyMeasuredDirectivity
        )

        return cls(
            orientation=orientation,
            grid=grid,
            impulse_responses=np.asarray(
                ir,
                dtype=float,
            ),
            fs=int(fs),
        )

    def _load_generalfir(
        self,
        *,
        path,
        orientation,
    ):
        # Make GeneralFIR resampling deterministic and consistent with
        # the FreeFieldDirectivityTF path.
        pra.constants.set(
            "resample_backend",
            self.resample_backend,
        )

        t0 = time.perf_counter()

        owner = MeasuredDirectivityFile(
            path=path,
            fs=self.room_fs,
            interp_order=None,
        )

        if self.runtime_mode == "standard":
            directivity = owner.get_source_directivity(
                self.source_id,
                orientation=orientation,
            )

        else:
            source_id = int(self.source_id)

            if not (
                0
                <= source_id
                < owner.impulse_responses.shape[0]
            ):
                raise ValueError(
                    "source_id={} outside available source range 0..{}"
                    .format(
                        source_id,
                        owner.impulse_responses.shape[0] - 1,
                    )
                )

            ir = owner.impulse_responses[
                source_id,
                :,
                :
            ]

            grid = GridSphere(
                cartesian_points=owner.mic_locs
            )

            directivity = self._make_directivity(
                grid=grid,
                ir=ir,
                fs=owner.fs,
                orientation=orientation,
            )

        elapsed = time.perf_counter() - t0

        return LoadedSOFADirectivity(
            path=path,
            convention="GeneralFIR",
            data_type=inspect_sofa(path)["DataType"],
            runtime_mode=self.runtime_mode,
            directivity=directivity,
            room_fs=int(owner.fs),
            preparation_s=float(elapsed),
            owner=owner,
            metadata={
                "source_id": int(self.source_id),
                "runtime_ir_shape": list(
                    directivity._irs.shape
                ),
                "input_is_used_directly": True,
                "tf_render_conversion": False,
                "ray_tracing_supported": (
                    self.runtime_mode == "standard"
                ),
            },
        )

    def _read_tf_sofa(
        self,
        path,
    ):
        with h5py.File(str(path), "r") as f:
            convention = _decode(
                f.attrs.get("SOFAConventions", "")
            )
            data_type = _decode(
                f.attrs.get("DataType", "")
            )

            if convention != "FreeFieldDirectivityTF":
                raise RuntimeError(
                    "Expected FreeFieldDirectivityTF."
                )

            if data_type != "TF":
                raise RuntimeError(
                    "Expected DataType=TF."
                )

            freq = np.asarray(
                f["N"],
                dtype=float,
            ).reshape(-1)

            real = np.asarray(
                f["Data.Real"],
                dtype=float,
            )

            imag = np.asarray(
                f["Data.Imag"],
                dtype=float,
            )

            direction_xyz = _sofa_receiver_directions(
                f
            )

        if real.ndim != 3 or real.shape[0] != 1:
            raise RuntimeError(
                "Expected Data.Real shape [1,R,F], got {}".format(
                    real.shape
                )
            )

        H = (
            real[0]
            + 1j * imag[0]
        )

        if H.shape[0] != direction_xyz.shape[0]:
            raise RuntimeError(
                "Direction count mismatch: H has {}, ReceiverPosition has {}"
                .format(
                    H.shape[0],
                    direction_xyz.shape[0],
                )
            )

        return freq, H, direction_xyz

    def _validate_and_optionally_replace_from_cf2(
        self,
        *,
        sofa_frequency_hz,
        sofa_H,
        sofa_direction_xyz,
        cf2_path,
        parser_path,
        canonical_path,
        coherence_threshold,
        tolerance,
    ):
        if cf2_path is None:
            return (
                sofa_frequency_hz,
                sofa_H,
                {
                    "cf2_validation_enabled": False,
                    "measured_anchor_source": "SOFA",
                },
            )

        if parser_path is None or canonical_path is None:
            raise ValueError(
                "When cf2_path is supplied, parser_path and canonical_path "
                "are also required."
            )

        parser = _load_module(
            Path(parser_path),
            f"_dual_cf2_parser_{id(self)}",
        )

        canonical = _load_module(
            Path(canonical_path),
            f"_dual_cf2_canonical_{id(self)}",
        )

        cf2 = parser.CF2File(
            Path(cf2_path)
        )

        if not bool(cf2.has_phase_container):
            raise RuntimeError(
                "CF2 phase container is required for complex TF validation."
            )

        a = int(cf2.declared_min_idx)
        b = int(cf2.declared_max_idx)

        geometry = canonical.canonical_geometry(
            parser
        )

        binary_direction_xyz = np.asarray(
            geometry["xyz"],
            dtype=float,
        )

        freq = []
        H_cols = []

        for slot in range(a, b + 1):
            f = float(
                parser.CF2_FREQS[slot]
            )

            mag = canonical.canonicalize_magnitude(
                np.asarray(
                    cf2.directivity_slot(slot)
                )
            )

            phase_info = canonical.canonicalize_phase(
                np.asarray(
                    cf2.phase_slot_raw(slot),
                    dtype=float,
                ),
                float(coherence_threshold),
            )

            H = canonical.complex_from_db_phase(
                mag,
                phase_info["phase_rad"],
            )

            freq.append(f)
            H_cols.append(H)

        binary_frequency_hz = np.asarray(
            freq,
            dtype=float,
        )

        binary_H = np.stack(
            H_cols,
            axis=1,
        ).astype(
            np.complex128
        )

        frequency_axis_exact = np.array_equal(
            binary_frequency_hz,
            sofa_frequency_hz,
        )

        if binary_H.shape != sofa_H.shape:
            raise RuntimeError(
                "CF2/SOFA H shape mismatch: {} vs {}".format(
                    binary_H.shape,
                    sofa_H.shape,
                )
            )

        complex_error = np.abs(
            binary_H - sofa_H
        )

        max_abs = float(
            np.max(complex_error)
        )

        rmse = float(
            np.sqrt(
                np.mean(
                    complex_error ** 2
                )
            )
        )

        direction_error = np.abs(
            binary_direction_xyz
            - sofa_direction_xyz
        )

        direction_max_abs = float(
            np.max(direction_error)
        )

        if (
            not frequency_axis_exact
            or max_abs > float(tolerance)
            or direction_max_abs > 1e-10
        ):
            raise RuntimeError(
                "CF2 binary does not close against FreeFieldDirectivityTF: "
                "freq_exact={}, H_max={:.3e}, direction_max={:.3e}"
                .format(
                    frequency_axis_exact,
                    max_abs,
                    direction_max_abs,
                )
            )

        # Binary is authoritative after validation.
        return (
            binary_frequency_hz,
            binary_H,
            {
                "cf2_validation_enabled": True,
                "measured_anchor_source": "CF2_BINARY",
                "cf2_declared_slots": [a, b],
                "frequency_axis_exact": bool(
                    frequency_axis_exact
                ),
                "complex_max_abs_error": max_abs,
                "complex_rmse": rmse,
                "direction_max_abs_error": direction_max_abs,
            },
        )

    def _load_tf(
        self,
        *,
        path,
        orientation,
        cf2_path,
        parser_path,
        canonical_path,
        coherence_threshold,
        binary_closure_tolerance,
    ):
        if self.bridge_path is None:
            raise ValueError(
                "bridge_path is required for FreeFieldDirectivityTF."
            )

        bridge = _load_module(
            self.bridge_path,
            f"_dual_bridge_{id(self)}",
        )

        t_total = time.perf_counter()

        t0 = time.perf_counter()

        (
            sofa_frequency_hz,
            sofa_H,
            direction_xyz,
        ) = self._read_tf_sofa(
            path
        )

        sofa_read_s = (
            time.perf_counter() - t0
        )

        (
            measured_frequency_hz,
            measured_H,
            validation,
        ) = self._validate_and_optionally_replace_from_cf2(
            sofa_frequency_hz=sofa_frequency_hz,
            sofa_H=sofa_H,
            sofa_direction_xyz=direction_xyz,
            cf2_path=cf2_path,
            parser_path=parser_path,
            canonical_path=canonical_path,
            coherence_threshold=coherence_threshold,
            tolerance=binary_closure_tolerance,
        )

        highest_hz = float(
            np.max(measured_frequency_hz)
        )

        if self.render_fs / 2.0 <= highest_hz:
            raise RuntimeError(
                "render_fs={} gives Nyquist={} Hz but highest measured TF "
                "frequency is {} Hz. Use render_fs > {}."
                .format(
                    self.render_fs,
                    self.render_fs / 2.0,
                    highest_hz,
                    2.0 * highest_hz,
                )
            )

        t0 = time.perf_counter()

        (
            spectrum,
            render_frequency_hz,
            _,
            _,
        ) = bridge.synthesize_piecewise_spectrum(
            measured_H,
            measured_frequency_hz,
            fs=self.render_fs,
            n_fft=self.render_n_fft,
            outside_mode=self.outside_mode,
        )

        spectrum_render_s = (
            time.perf_counter() - t0
        )

        t0 = time.perf_counter()

        (
            render_ir,
            applied_delay,
        ) = bridge.spectrum_to_causal_fir(
            spectrum,
            self.render_n_fft,
            delay_samples=self.render_delay_samples,
        )

        irfft_delay_s = (
            time.perf_counter() - t0
        )

        t0 = time.perf_counter()

        room_ir = resample(
            render_ir,
            self.render_fs,
            self.room_fs,
            backend=self.resample_backend,
        )

        resample_s = (
            time.perf_counter() - t0
        )

        t0 = time.perf_counter()

        grid = GridSphere(
            cartesian_points=direction_xyz.T
        )

        directivity = self._make_directivity(
            grid=grid,
            ir=room_ir,
            fs=self.room_fs,
            orientation=orientation,
        )

        directivity_build_s = (
            time.perf_counter() - t0
        )

        total_s = (
            time.perf_counter() - t_total
        )

        metadata = {
            "source_id": int(self.source_id),
            "measured_frequency_count": int(
                measured_frequency_hz.size
            ),
            "measured_frequencies_hz": (
                measured_frequency_hz.tolist()
            ),
            "measured_tf_shape": list(
                measured_H.shape
            ),
            "direction_count": int(
                measured_H.shape[0]
            ),
            "render_fs_hz": int(
                self.render_fs
            ),
            "render_n_fft": int(
                self.render_n_fft
            ),
            "render_bin_spacing_hz": float(
                self.render_fs
                / self.render_n_fft
            ),
            "render_delay_samples": int(
                applied_delay
            ),
            "render_delay_seconds": float(
                applied_delay
                / self.render_fs
            ),
            "outside_mode": self.outside_mode,
            "resample_backend": self.resample_backend,
            "runtime_ir_shape": list(
                directivity._irs.shape
            ),
            "input_is_used_directly": True,
            "tf_render_conversion": True,
            "ray_tracing_supported": (
                self.runtime_mode == "standard"
            ),
            "timing_s": {
                "sofa_read": float(
                    sofa_read_s
                ),
                "spectrum_render": float(
                    spectrum_render_s
                ),
                "irfft_delay": float(
                    irfft_delay_s
                ),
                "resample": float(
                    resample_s
                ),
                "directivity_build": float(
                    directivity_build_s
                ),
                "total": float(
                    total_s
                ),
            },
            **validation,
        }

        return LoadedSOFADirectivity(
            path=path,
            convention="FreeFieldDirectivityTF",
            data_type="TF",
            runtime_mode=self.runtime_mode,
            directivity=directivity,
            room_fs=self.room_fs,
            preparation_s=float(total_s),
            metadata=metadata,
            owner=None,
        )


def main():
    import argparse
    import json

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "sofa",
        type=Path,
    )

    ap.add_argument(
        "--room-fs",
        type=int,
        default=16000,
    )

    ap.add_argument(
        "--runtime-mode",
        choices=[
            "standard",
            "ism-lite",
        ],
        default="ism-lite",
    )

    ap.add_argument(
        "--bridge-script",
        type=Path,
        default=None,
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
        "--cf2",
        type=Path,
        default=None,
    )

    ap.add_argument(
        "--parser",
        type=Path,
        default=None,
    )

    ap.add_argument(
        "--canonical-script",
        type=Path,
        default=None,
    )

    args = ap.parse_args()

    info = inspect_sofa(
        args.sofa
    )

    factory = SOFADirectivityFactory(
        room_fs=args.room_fs,
        runtime_mode=args.runtime_mode,
        render_fs=args.render_fs,
        render_n_fft=args.render_n_fft,
        bridge_path=args.bridge_script,
    )

    loaded = factory.load(
        args.sofa,
        cf2_path=args.cf2,
        parser_path=args.parser,
        canonical_path=args.canonical_script,
    )

    print(
        json.dumps(
            {
                "input": info,
                "runtime_mode": loaded.runtime_mode,
                "room_fs": loaded.room_fs,
                "preparation_s": loaded.preparation_s,
                "filter_len_ir": int(
                    loaded.directivity.filter_len_ir
                ),
                "supports_ray_tracing": (
                    loaded.supports_ray_tracing
                ),
                "metadata": loaded.metadata,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
