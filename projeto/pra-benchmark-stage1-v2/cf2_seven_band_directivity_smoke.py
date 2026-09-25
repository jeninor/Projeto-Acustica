#!/usr/bin/env python3
"""
CF2 -> custom 7-band Pyroomacoustics Directivity smoke test.

Goal
----
Validate the real CF2 adapter BEFORE using it in a room.

This test checks:
1. CF2SevenBandDirectivity is a valid PRA Directivity subclass.
2. get_response() returns the runtime shape required by PRA 0.10.1:
       (n_bands, n_directions)
3. The 7 PRA octave-band centers:
       125, 250, 500, 1000, 2000, 4000, 8000 Hz
   are evaluated as linear AMPLITUDE gains.
4. Six canonical directions are compared directly against the raw CF2
   stored nodes, not merely against the interpolator:
       FRONT, TOP, LEFT, RIGHT, BOTTOM, BACK
5. A 90-deg speaker azimuth test verifies that orientation is applied
   before mapping world directions to CF2 rotation/arc.

No Room / compute_rir() is used here.
Ray tracing is intentionally outside the scope of this smoke test.
"""

import argparse
import math

import numpy as np
import pyroomacoustics as pra
from pyroomacoustics.directivities import Directivity

from cf2_interpolator import (
    CF2MagnitudeInterpolator,
    FREQUENCIES_HZ,
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
    Pyroomacoustics 0.10.1 ISM adapter for CF2 magnitude directivity.

    Effective runtime contract established experimentally:
        get_response(...) -> shape (n_bands, n_directions)

    The returned values are LINEAR PRESSURE/AMPLITUDE ratios.

    Local loudspeaker frame:
        +X = front
        +Y = left
        +Z = top

    CF2 rotation convention used:
        rotation 0 passes through TOP (+Z)
        positive rotation = TOP -> LEFT

    The handedness remains explicit because future asymmetric CF2 files
    may require the alternative convention.
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

        # All seven PRA centers must be inside the detected valid CF2 range.
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

        return (
            float(rotation_deg),
            float(angles["arc_deg"]),
        )

    def get_response(
        self,
        azimuth,
        colatitude=None,
        magnitude=False,
        degrees=True,
    ):
        az = np.atleast_1d(np.asarray(azimuth, dtype=float))

        if colatitude is None:
            col = np.full_like(az, np.pi / 2.0 if not degrees else 90.0)
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

        # PRA 0.10.1 effective ISM contract:
        # rows=bands, columns=image-source directions.
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
            "Ray tracing is intentionally not implemented in this ISM adapter smoke test."
        )


def vector_to_pra_angles_deg(v):
    v = np.asarray(v, dtype=float)
    v = v / np.linalg.norm(v)

    azimuth = math.degrees(math.atan2(v[1], v[0])) % 360.0
    colatitude = math.degrees(
        math.acos(float(np.clip(v[2], -1.0, 1.0)))
    )

    return azimuth, colatitude


def raw_cf2_db(interpolator, frequency_hz, rotation_index, arc_index):
    idx = np.where(
        np.isclose(
            FREQUENCIES_HZ,
            float(frequency_hz),
            rtol=0.0,
            atol=1e-12,
        )
    )[0]

    if len(idx) != 1:
        raise RuntimeError(
            f"Frequency {frequency_hz:g} Hz is not an exact CF2 slot."
        )

    fi = int(idx[0])

    return float(
        interpolator.balloon_db[
            fi,
            int(rotation_index),
            int(arc_index),
        ]
    )


def amp_to_db(a):
    return 20.0 * math.log10(max(float(a), 1e-15))


def canonical_cases():
    # Raw CF2 indices for 5-degree grid:
    # arc index 18 = 90 degrees
    # rotation indices: 0=TOP, 18=LEFT, 36=BOTTOM, 54=RIGHT
    return [
        {
            "name": "FRONT",
            "vector": np.array([+1.0, 0.0, 0.0]),
            "ri": 0,
            "ai": 0,
        },
        {
            "name": "TOP",
            "vector": np.array([0.0, 0.0, +1.0]),
            "ri": 0,
            "ai": 18,
        },
        {
            "name": "LEFT",
            "vector": np.array([0.0, +1.0, 0.0]),
            "ri": 18,
            "ai": 18,
        },
        {
            "name": "RIGHT",
            "vector": np.array([0.0, -1.0, 0.0]),
            "ri": 54,
            "ai": 18,
        },
        {
            "name": "BOTTOM",
            "vector": np.array([0.0, 0.0, -1.0]),
            "ri": 36,
            "ai": 18,
        },
        {
            "name": "BACK",
            "vector": np.array([-1.0, 0.0, 0.0]),
            "ri": 0,
            "ai": 36,
        },
    ]


def check_cardinals(directivity):
    cases = canonical_cases()

    az = []
    col = []

    for case in cases:
        a, c = vector_to_pra_angles_deg(case["vector"])
        az.append(a)
        col.append(c)

    response = directivity.get_response(
        azimuth=np.asarray(az),
        colatitude=np.asarray(col),
        degrees=True,
    )

    expected_shape = (
        len(PRA_BAND_CENTERS_HZ),
        len(cases),
    )

    print("\n=== Adapter contract ===")
    print("isinstance(Directivity):", isinstance(directivity, Directivity))
    print("is_impulse_response:", directivity.is_impulse_response)
    print("filter_len_ir:", directivity.filter_len_ir)
    print("response_shape:", response.shape)
    print("expected_shape:", expected_shape)

    if response.shape != expected_shape:
        raise RuntimeError(
            f"Unexpected get_response shape {response.shape}; "
            f"expected {expected_shape}"
        )

    print("\n=== Raw CF2 vs adapter: 6 canonical directions x 7 PRA bands ===")

    max_abs_error_db = 0.0
    rows = []

    for bi, f in enumerate(PRA_BAND_CENTERS_HZ):
        for j, case in enumerate(cases):
            expected_db = raw_cf2_db(
                directivity.interpolator,
                f,
                case["ri"],
                case["ai"],
            )

            measured_db = amp_to_db(
                response[bi, j]
            )

            error_db = measured_db - expected_db
            max_abs_error_db = max(
                max_abs_error_db,
                abs(error_db),
            )

            rows.append(
                (
                    f,
                    case["name"],
                    expected_db,
                    measured_db,
                    error_db,
                )
            )

    current_f = None

    for f, name, expected_db, measured_db, error_db in rows:
        if current_f != f:
            current_f = f
            print(f"\n{f:g} Hz")

        print(
            f"  {name:7s} "
            f"raw={expected_db:9.4f} dB | "
            f"adapter={measured_db:9.4f} dB | "
            f"error={error_db:+.3e} dB"
        )

    print("\nmax_abs_error_db:", max_abs_error_db)

    return max_abs_error_db


def check_rotated_front(directivity_cls, args):
    """
    Rotate loudspeaker main axis to world +Y (azimuth=90 deg).
    World +Y must then map to CF2 FRONT / arc=0.
    """
    rotated = directivity_cls(
        cf2_path=args.cf2,
        base_offset=args.base,
        speaker_azimuth_deg=90.0,
        speaker_colatitude_deg=90.0,
        speaker_roll_deg=0.0,
        rotation_handedness=args.handedness,
        interpolation_domain=args.interpolation_domain,
    )

    world_front_rotated = np.array([0.0, +1.0, 0.0])
    az, col = vector_to_pra_angles_deg(world_front_rotated)

    response = rotated.get_response(
        azimuth=np.array([az]),
        colatitude=np.array([col]),
        degrees=True,
    )

    print("\n=== Orientation smoke check: speaker azimuth=90 deg ===")
    print("world_direction:", world_front_rotated.tolist())
    print("PRA_angles_deg:", (az, col))

    max_err = 0.0

    for bi, f in enumerate(PRA_BAND_CENTERS_HZ):
        expected_db = raw_cf2_db(
            rotated.interpolator,
            f,
            0,
            0,
        )
        got_db = amp_to_db(response[bi, 0])
        err = got_db - expected_db
        max_err = max(max_err, abs(err))

        print(
            f"{f:7g} Hz "
            f"raw_front={expected_db:9.4f} dB | "
            f"rotated_adapter={got_db:9.4f} dB | "
            f"error={err:+.3e} dB"
        )

    print("rotated_front_max_abs_error_db:", max_err)

    return max_err


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument("--cf2", required=True)

    ap.add_argument(
        "--base",
        type=int,
        default=DEFAULT_BASE_OFFSET,
    )

    ap.add_argument(
        "--handedness",
        choices=["top_to_left", "top_to_right"],
        default="top_to_left",
    )

    ap.add_argument(
        "--interpolation-domain",
        choices=["db", "linear_amplitude"],
        default="db",
    )

    args = ap.parse_args()

    print("=== CF2 custom 7-band Directivity smoke test ===")
    print("pyroomacoustics:", pra.__version__)
    print("CF2:", args.cf2)
    print("base_offset:", args.base)
    print("PRA_band_centers_hz:", PRA_BAND_CENTERS_HZ.tolist())
    print("rotation_handedness:", args.handedness)
    print("interpolation_domain:", args.interpolation_domain)

    directivity = CF2SevenBandDirectivity(
        cf2_path=args.cf2,
        base_offset=args.base,
        speaker_azimuth_deg=0.0,
        speaker_colatitude_deg=90.0,
        speaker_roll_deg=0.0,
        rotation_handedness=args.handedness,
        interpolation_domain=args.interpolation_domain,
    )

    print(
        "valid_cf2_frequencies_hz:",
        directivity.interpolator.valid_frequencies_hz.tolist(),
    )

    cardinal_error = check_cardinals(
        directivity
    )

    rotated_error = check_rotated_front(
        CF2SevenBandDirectivity,
        args,
    )

    tolerance_db = 1e-10

    print("\n=== Interpretation ===")

    if cardinal_error <= tolerance_db and rotated_error <= tolerance_db:
        print("PASS")
        print(
            "The custom 7-band adapter reproduces the raw CF2 canonical nodes "
            "and applies loudspeaker azimuth correctly."
        )
        print(
            "Next step: insert this adapter into an explicit 7-band PRA room "
            "through SoundSource + Room.add(), then compare room-level RIRs."
        )
    else:
        print("FAIL")
        print(
            f"cardinal_error={cardinal_error} dB, "
            f"rotated_error={rotated_error} dB"
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
