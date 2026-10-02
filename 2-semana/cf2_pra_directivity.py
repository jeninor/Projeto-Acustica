#!/usr/bin/env python3
"""
Reusable CF2 -> Pyroomacoustics 7-band Directivity adapter for ISM.

Validated runtime assumptions for Pyroomacoustics 0.10.1
--------------------------------------------------------
- Custom directivities must inherit from Directivity.
- Insert through SoundSource(..., directivity=...) + Room.add().
- For an explicit 7-band room at fs=16 kHz, get_response() must return:
      (n_bands, n_directions)
- The values are linear pressure/amplitude gains.
- This adapter is for ISM. Ray tracing is NOT implemented yet.

CF2 coordinate frame
--------------------
+X = front
+Y = left
+Z = top

CF2 rotation 0 passes through +Z (top).
"""

import math

import numpy as np
from pyroomacoustics.directivities import Directivity

from cf2_interpolator import (
    CF2MagnitudeInterpolator,
    DEFAULT_BASE_OFFSET,
)

from cf2_coordinates import (
    world_vector_to_local,
    local_vector_to_cf2_angles,
)


PRA_BAND_CENTERS_HZ = np.array(
    [125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0],
    dtype=float,
)


class CF2SevenBandDirectivity(Directivity):
    """
    CF2 magnitude adapter for Pyroomacoustics 0.10.1 ISM.

    Parameters
    ----------
    cf2_path
        Path to validated CF2/CLF2 file.
    base_offset
        Magnitude block base offset.
    speaker_azimuth_deg
        Loudspeaker forward-axis azimuth in world coordinates.
    speaker_colatitude_deg
        Loudspeaker forward-axis colatitude in world coordinates.
    speaker_roll_deg
        Cabinet roll around its forward axis.
    rotation_handedness
        'top_to_left' or 'top_to_right'.
    interpolation_domain
        Passed to CF2MagnitudeInterpolator ('db' or 'linear_amplitude').
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

        for f in PRA_BAND_CENTERS_HZ:
            if not (
                self.interpolator.min_frequency_hz
                <= f
                <= self.interpolator.max_frequency_hz
            ):
                raise ValueError(
                    f"PRA band center {f:g} Hz is outside CF2 valid range "
                    f"[{self.interpolator.min_frequency_hz:g}, "
                    f"{self.interpolator.max_frequency_hz:g}] Hz"
                )

    @property
    def is_impulse_response(self):
        return False

    @property
    def filter_len_ir(self):
        return 1

    def _world_direction_to_cf2(self, world_vector):
        local = world_vector_to_local(
            world_vector,
            speaker_azimuth_deg=self.speaker_azimuth_deg,
            speaker_colatitude_deg=self.speaker_colatitude_deg,
            speaker_roll_deg=self.speaker_roll_deg,
        )

        angles = local_vector_to_cf2_angles(local)

        if self.rotation_handedness == "top_to_left":
            rotation_deg = angles["rotation_top_to_left_deg"]
        else:
            rotation_deg = angles["rotation_top_to_right_deg"]

        return float(rotation_deg), float(angles["arc_deg"])

    def get_response(
        self,
        azimuth,
        colatitude=None,
        magnitude=False,
        degrees=True,
    ):
        az = np.atleast_1d(np.asarray(azimuth, dtype=float))

        if colatitude is None:
            col = np.full_like(
                az,
                90.0 if degrees else np.pi / 2.0,
            )
        else:
            col = np.atleast_1d(np.asarray(colatitude, dtype=float))

        az, col = np.broadcast_arrays(az, col)

        if degrees:
            az_rad = np.radians(az)
            col_rad = np.radians(col)
        else:
            az_rad = az
            col_rad = col

        n_dirs = az_rad.size

        # Effective PRA 0.10.1 ISM contract established experimentally:
        # rows = bands, columns = image-source directions.
        out = np.empty(
            (len(PRA_BAND_CENTERS_HZ), n_dirs),
            dtype=float,
        )

        for j, (a, c) in enumerate(zip(az_rad.flat, col_rad.flat)):
            world_vector = np.array(
                [
                    math.sin(c) * math.cos(a),
                    math.sin(c) * math.sin(a),
                    math.cos(c),
                ],
                dtype=float,
            )

            rotation_deg, arc_deg = self._world_direction_to_cf2(
                world_vector
            )

            for bi, frequency_hz in enumerate(PRA_BAND_CENTERS_HZ):
                out[bi, j] = self.interpolator.gain_amplitude(
                    frequency_hz=float(frequency_hz),
                    rotation_deg=rotation_deg,
                    arc_deg=arc_deg,
                )

        return out

    def sample_rays(self, n_rays, rng=None):
        raise NotImplementedError(
            "CF2SevenBandDirectivity currently validates ISM only; "
            "ray tracing support has not been implemented."
        )
