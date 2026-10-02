#!/usr/bin/env python3
"""
Vectorized CF2 -> Pyroomacoustics 7-band Directivity adapter for ISM.

This module implements the same acoustic model as cf2_pra_directivity.py,
but removes the per-direction/per-band Python loops from get_response().

Validated PRA 0.10.1 ISM contract:
    get_response(...) -> (n_bands, n_directions)

The seven PRA centers are exact CF2 stored frequencies:
    125, 250, 500, 1000, 2000, 4000, 8000 Hz
so this adapter pre-extracts those CF2 balloon slices once.

Ray tracing is intentionally not implemented here.
"""

import numpy as np
from pyroomacoustics.directivities import Directivity

from cf2_interpolator import (
    CF2MagnitudeInterpolator,
    FREQUENCIES_HZ,
    DEFAULT_BASE_OFFSET,
    ROT_STEP_DEG,
    ARC_STEP_DEG,
    N_ROT,
    N_ARC,
)
from cf2_coordinates import loudspeaker_basis


PRA_BAND_CENTERS_HZ = np.array(
    [125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0],
    dtype=float,
)


class CF2SevenBandDirectivityFast(Directivity):
    """
    Vectorized equivalent of CF2SevenBandDirectivity for PRA 0.10.1 ISM.
    """

    def __init__(
        self,
        cf2_path,
        base_offset=DEFAULT_BASE_OFFSET,
        speaker_azimuth_deg=0.0,
        speaker_colatitude_deg=90.0,
        speaker_roll_deg=0.0,
        rotation_handedness="top_to_left",
        interpolation_domain="db",
    ):
        if rotation_handedness not in {"top_to_left", "top_to_right"}:
            raise ValueError(
                "rotation_handedness must be 'top_to_left' or 'top_to_right'"
            )

        self.interpolator = CF2MagnitudeInterpolator(
            cf2_path=cf2_path,
            base_offset=base_offset,
            out_of_range="error",
            interpolation_domain=interpolation_domain,
        )

        self.speaker_azimuth_deg = float(speaker_azimuth_deg)
        self.speaker_colatitude_deg = float(speaker_colatitude_deg)
        self.speaker_roll_deg = float(speaker_roll_deg)
        self.rotation_handedness = rotation_handedness
        self.interpolation_domain = interpolation_domain

        # Map each PRA center to its exact stored CF2 frequency slot.
        freq_indices = []
        for f in PRA_BAND_CENTERS_HZ:
            idx = np.where(
                np.isclose(
                    FREQUENCIES_HZ,
                    f,
                    rtol=0.0,
                    atol=1e-12,
                )
            )[0]

            if len(idx) != 1:
                raise RuntimeError(
                    f"PRA center {f:g} Hz is not an exact CF2 frequency slot."
                )

            global_idx = int(idx[0])

            if global_idx not in set(
                self.interpolator.valid_indices.tolist()
            ):
                raise RuntimeError(
                    f"PRA center {f:g} Hz is not valid in this CF2 file."
                )

            freq_indices.append(global_idx)

        self._pra_freq_indices = np.asarray(
            freq_indices,
            dtype=int,
        )

        # Shape: (7, 72, 37), kept as float64 for stable vector arithmetic.
        self._balloon_db = np.asarray(
            self.interpolator.balloon_db[
                self._pra_freq_indices
            ],
            dtype=np.float64,
        )

        # Local loudspeaker axes expressed in world coordinates.
        forward, left, top = loudspeaker_basis(
            azimuth_deg=self.speaker_azimuth_deg,
            colatitude_deg=self.speaker_colatitude_deg,
            roll_deg=self.speaker_roll_deg,
        )

        # Columns are the local basis vectors. For row-vector world directions:
        # local = world @ basis_matrix
        self._basis_matrix = np.column_stack(
            [forward, left, top]
        ).astype(np.float64)

    @property
    def is_impulse_response(self):
        return False

    @property
    def filter_len_ir(self):
        return 1

    def _angles_to_world_vectors(
        self,
        azimuth,
        colatitude,
        degrees,
    ):
        az = np.atleast_1d(
            np.asarray(
                azimuth,
                dtype=np.float64,
            )
        )

        if colatitude is None:
            col = np.full_like(
                az,
                90.0 if degrees else np.pi / 2.0,
            )
        else:
            col = np.atleast_1d(
                np.asarray(
                    colatitude,
                    dtype=np.float64,
                )
            )

        az, col = np.broadcast_arrays(
            az,
            col,
        )

        az = az.ravel()
        col = col.ravel()

        if degrees:
            az = np.deg2rad(az)
            col = np.deg2rad(col)

        sin_col = np.sin(col)

        world = np.column_stack(
            [
                sin_col * np.cos(az),
                sin_col * np.sin(az),
                np.cos(col),
            ]
        )

        return world

    def _world_to_cf2_angles_vectorized(
        self,
        world_vectors,
    ):
        # World directions produced from spherical angles are unit vectors.
        # Multiplication by an orthonormal basis preserves norm.
        local = (
            np.asarray(
                world_vectors,
                dtype=np.float64,
            )
            @ self._basis_matrix
        )

        x = np.clip(
            local[:, 0],
            -1.0,
            1.0,
        )
        y = local[:, 1]
        z = local[:, 2]

        arc_deg = np.rad2deg(
            np.arccos(x)
        )

        transverse = np.hypot(
            y,
            z,
        )

        if self.rotation_handedness == "top_to_left":
            rotation_deg = (
                np.rad2deg(
                    np.arctan2(y, z)
                )
                % 360.0
            )
        else:
            rotation_deg = (
                np.rad2deg(
                    np.arctan2(-y, z)
                )
                % 360.0
            )

        # At the front/back poles rotation is geometrically undefined.
        rotation_deg = np.where(
            transverse < 1e-12,
            0.0,
            rotation_deg,
        )

        return rotation_deg, arc_deg

    def _interpolate_all_bands(
        self,
        rotation_deg,
        arc_deg,
    ):
        r = (
            np.asarray(
                rotation_deg,
                dtype=np.float64,
            )
            % 360.0
        )

        a = np.asarray(
            arc_deg,
            dtype=np.float64,
        )

        if np.any(
            (a < -1e-10)
            | (a > 180.0 + 1e-10)
        ):
            raise ValueError(
                "arc_deg outside [0,180]"
            )

        a = np.clip(
            a,
            0.0,
            180.0,
        )

        rpos = r / ROT_STEP_DEG
        rfloor = np.floor(rpos)
        r0 = (
            rfloor.astype(int)
            % N_ROT
        )
        r1 = (
            (r0 + 1)
            % N_ROT
        )
        wr = rpos - rfloor

        apos = a / ARC_STEP_DEG
        afloor = np.floor(apos)

        a0 = afloor.astype(int)
        a0 = np.clip(
            a0,
            0,
            N_ARC - 1,
        )

        a1 = np.minimum(
            a0 + 1,
            N_ARC - 1,
        )

        wa = apos - afloor

        pole_end = (
            a0 >= N_ARC - 1
        )

        if np.any(pole_end):
            wa = wa.copy()
            wa[pole_end] = 0.0
            a1 = a1.copy()
            a1[pole_end] = a0[pole_end]

        # Advanced indexing:
        # each expression has shape (7, n_directions).
        q00 = self._balloon_db[:, r0, a0]
        q10 = self._balloon_db[:, r1, a0]
        q01 = self._balloon_db[:, r0, a1]
        q11 = self._balloon_db[:, r1, a1]

        wr2 = wr[None, :]
        wa2 = wa[None, :]

        if self.interpolation_domain == "db":
            v0 = (
                (1.0 - wr2) * q00
                + wr2 * q10
            )

            v1 = (
                (1.0 - wr2) * q01
                + wr2 * q11
            )

            db = (
                (1.0 - wa2) * v0
                + wa2 * v1
            )

            return np.power(
                10.0,
                db / 20.0,
            )

        # Match the existing interpolator's linear-amplitude angular domain.
        p00 = np.power(10.0, q00 / 20.0)
        p10 = np.power(10.0, q10 / 20.0)
        p01 = np.power(10.0, q01 / 20.0)
        p11 = np.power(10.0, q11 / 20.0)

        v0 = (
            (1.0 - wr2) * p00
            + wr2 * p10
        )

        v1 = (
            (1.0 - wr2) * p01
            + wr2 * p11
        )

        return (
            (1.0 - wa2) * v0
            + wa2 * v1
        )

    def get_response(
        self,
        azimuth,
        colatitude=None,
        magnitude=False,
        degrees=True,
    ):
        world = self._angles_to_world_vectors(
            azimuth=azimuth,
            colatitude=colatitude,
            degrees=degrees,
        )

        rotation_deg, arc_deg = (
            self._world_to_cf2_angles_vectorized(
                world
            )
        )

        return self._interpolate_all_bands(
            rotation_deg=rotation_deg,
            arc_deg=arc_deg,
        )

    def sample_rays(self, n_rays, rng=None):
        raise NotImplementedError(
            "CF2SevenBandDirectivityFast currently validates ISM only; "
            "ray tracing support has not been implemented."
        )
