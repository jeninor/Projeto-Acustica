"""
CF2 -> Pyroomacoustics 0.10.1 directivity adapter (ISM smoke-test version).

Pyroomacoustics 0.10.1 calls get_response(azimuth, colatitude) without an
explicit frequency, but its ISM accepts a gain matrix of shape
(n_image_sources, n_octave_bands). Room.add_source() only accepts
CardioidFamily or MeasuredDirectivity instances, so this adapter subclasses
MeasuredDirectivity while reporting is_impulse_response=False.

This version is intentionally ISM-only; ray tracing is not implemented yet.
Requires cf2_interpolator.py in the same mounted directory.
"""

import argparse
import math
from pathlib import Path

import numpy as np
import pyroomacoustics as pra
from pyroomacoustics.directivities import MeasuredDirectivity

from cf2_interpolator import CF2MagnitudeInterpolator

ROOM_DIM = np.array([6.0, 5.0, 3.0], dtype=float)
SOURCE_POS = np.array([3.0, 2.5, 1.5], dtype=float)
ABSORPTION = 0.35
FS = 16000


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    if n == 0.0:
        raise ValueError("Zero-length vector.")
    return v / n


def direction_unit_vector(azimuth_deg, colatitude_deg):
    az = math.radians(float(azimuth_deg))
    col = math.radians(float(colatitude_deg))
    return np.array([
        math.sin(col) * math.cos(az),
        math.sin(col) * math.sin(az),
        math.cos(col),
    ], dtype=float)


def loudspeaker_basis(azimuth_deg=0.0, colatitude_deg=90.0, roll_deg=0.0):
    """Local +X forward, +Y left, +Z top, expressed in world coordinates."""
    az = math.radians(float(azimuth_deg))
    col = math.radians(float(colatitude_deg))
    forward = direction_unit_vector(azimuth_deg, colatitude_deg)
    left0 = _unit(np.array([-math.sin(az), math.cos(az), 0.0], dtype=float))
    top0 = _unit(np.array([
        -math.cos(col) * math.cos(az),
        -math.cos(col) * math.sin(az),
        math.sin(col),
    ], dtype=float))

    roll = math.radians(float(roll_deg))
    left = math.cos(roll) * left0 + math.sin(roll) * top0
    forward = _unit(forward)
    left = _unit(left - np.dot(left, forward) * forward)
    top = _unit(np.cross(forward, left))
    return forward, left, top


def spherical_to_cartesian(azimuth, colatitude, degrees=True):
    az = np.asarray(azimuth, dtype=float)
    col = np.asarray(colatitude, dtype=float)
    az, col = np.broadcast_arrays(az, col)
    if degrees:
        az = np.radians(az)
        col = np.radians(col)
    x = np.sin(col) * np.cos(az)
    y = np.sin(col) * np.sin(az)
    z = np.cos(col)
    return np.stack([x, y, z], axis=-1)


def get_room_band_centers(room):
    """Extract octave-band centers robustly from the installed PRA object."""
    ob = room.octave_bands
    for name in ("get_centers", "get_center_frequencies", "get_frequencies"):
        fn = getattr(ob, name, None)
        if callable(fn):
            try:
                values = np.asarray(fn(), dtype=float).reshape(-1)
                if len(values):
                    return values
            except Exception:
                pass
    for name in ("centers", "center_frequencies", "frequencies", "freqs"):
        value = getattr(ob, name, None)
        if value is not None:
            try:
                values = np.asarray(value, dtype=float).reshape(-1)
                if len(values):
                    return values
            except Exception:
                pass
    public = [n for n in dir(ob) if not n.startswith("_")]
    raise RuntimeError(
        "Could not determine room octave-band centers. "
        f"Available octave_bands attributes: {public}"
    )


def make_constant_multiband_material(center_frequencies_hz, absorption=ABSORPTION):
    centers = np.asarray(center_frequencies_hz, dtype=float).reshape(-1)
    spec = {
        "coeffs": [float(absorption)] * len(centers),
        "center_freqs": centers.tolist(),
    }
    return pra.Material(energy_absorption=spec)


class CF2DirectivityISM(MeasuredDirectivity):
    """Frequency-dependent CF2 source directivity for PRA's image-source model."""

    def __init__(
        self,
        cf2_path,
        band_frequencies_hz,
        speaker_azimuth_deg=0.0,
        speaker_colatitude_deg=90.0,
        speaker_roll_deg=0.0,
        handedness="top_to_left",
        base_offset=14232,
        interpolation_domain="db",
        out_of_range="error",
    ):
        if handedness not in {"top_to_left", "top_to_right"}:
            raise ValueError("handedness must be 'top_to_left' or 'top_to_right'")

        self.cf2 = CF2MagnitudeInterpolator(
            cf2_path=cf2_path,
            base_offset=base_offset,
            out_of_range=out_of_range,
            interpolation_domain=interpolation_domain,
        )
        self.band_frequencies_hz = np.asarray(
            band_frequencies_hz, dtype=float
        ).reshape(-1)
        if len(self.band_frequencies_hz) == 0:
            raise ValueError("band_frequencies_hz is empty")

        self.handedness = handedness
        self.set_cf2_orientation(
            speaker_azimuth_deg,
            speaker_colatitude_deg,
            speaker_roll_deg,
        )

        # Fail early if PRA requires a band outside the CF2 valid range.
        for f in self.band_frequencies_hz:
            self.cf2._prepare_frequency(float(f))

    @property
    def is_impulse_response(self):
        return False

    def set_cf2_orientation(self, azimuth_deg=0.0, colatitude_deg=90.0, roll_deg=0.0):
        self.speaker_azimuth_deg = float(azimuth_deg)
        self.speaker_colatitude_deg = float(colatitude_deg)
        self.speaker_roll_deg = float(roll_deg)
        forward, left, top = loudspeaker_basis(
            self.speaker_azimuth_deg,
            self.speaker_colatitude_deg,
            self.speaker_roll_deg,
        )
        # Columns are the local axes in world coordinates.
        self._basis = np.column_stack([forward, left, top])

    def _world_cartesian_to_cf2_angles(self, directions):
        d = np.asarray(directions, dtype=float)
        scalar = False
        if d.ndim == 1:
            if d.shape[0] != 3:
                raise ValueError("1-D direction must have length 3")
            d = d[None, :]
            scalar = True
        if d.ndim != 2 or d.shape[1] != 3:
            raise ValueError("directions must have shape (n, 3)")

        norms = np.linalg.norm(d, axis=1)
        if np.any(norms == 0.0):
            raise ValueError("Zero-length direction supplied")
        d = d / norms[:, None]

        local = d @ self._basis
        x = np.clip(local[:, 0], -1.0, 1.0)
        y = local[:, 1]
        z = local[:, 2]
        arc_deg = np.degrees(np.arccos(x))

        if self.handedness == "top_to_left":
            rotation_deg = np.degrees(np.arctan2(y, z)) % 360.0
        else:
            rotation_deg = np.degrees(np.arctan2(-y, z)) % 360.0

        rotation_deg = np.where(np.hypot(y, z) < 1e-12, 0.0, rotation_deg)
        if scalar:
            return float(rotation_deg[0]), float(arc_deg[0])
        return rotation_deg, arc_deg

    def _evaluate_angles(self, rotation_deg, arc_deg):
        r, a = np.broadcast_arrays(
            np.asarray(rotation_deg, dtype=float),
            np.asarray(arc_deg, dtype=float),
        )
        flat_r = r.reshape(-1)
        flat_a = a.reshape(-1)
        gains = np.empty((len(flat_r), len(self.band_frequencies_hz)), dtype=float)

        # Correctness-first. Vectorization comes after this integration is validated.
        for i in range(len(flat_r)):
            for j, f in enumerate(self.band_frequencies_hz):
                gains[i, j] = self.cf2.gain_amplitude(
                    float(f), float(flat_r[i]), float(flat_a[i])
                )
        return gains

    def get_response(self, azimuth, colatitude=None, magnitude=False, degrees=True):
        if colatitude is None:
            colatitude = np.full_like(
                np.asarray(azimuth, dtype=float),
                90.0 if degrees else np.pi / 2.0,
                dtype=float,
            )

        az_arr, col_arr = np.broadcast_arrays(
            np.asarray(azimuth, dtype=float),
            np.asarray(colatitude, dtype=float),
        )
        world = spherical_to_cartesian(az_arr, col_arr, degrees=degrees)
        scalar_input = world.ndim == 1
        world_eval = world[None, :] if scalar_input else world.reshape(-1, 3)

        rotation_deg, arc_deg = self._world_cartesian_to_cf2_angles(world_eval)
        gains = self._evaluate_angles(rotation_deg, arc_deg)

        if scalar_input:
            return gains[0]
        return gains.reshape(az_arr.shape + (len(self.band_frequencies_hz),))

    def get_response_cartesian(self, directions, magnitude=False):
        d = np.asarray(directions, dtype=float)
        if d.ndim == 1:
            d2 = d[None, :]
            scalar = True
        elif d.ndim == 2 and d.shape[1] == 3:
            d2 = d
            scalar = False
        elif d.ndim == 2 and d.shape[0] == 3:
            d2 = d.T
            scalar = False
        else:
            raise ValueError("directions must have shape (3,), (n,3), or (3,n)")

        rotation_deg, arc_deg = self._world_cartesian_to_cf2_angles(d2)
        gains = self._evaluate_angles(rotation_deg, arc_deg)
        return gains[0] if scalar else gains

    def get_direction_vectors(self):
        raise NotImplementedError("CF2DirectivityISM currently supports ISM only")

    def sample_rays(self, n_rays):
        raise NotImplementedError(
            "CF2DirectivityISM currently supports ISM only; ray tracing is not implemented"
        )


def print_matrix_db(directivity, labels, directions):
    print("band_frequencies_hz:", directivity.band_frequencies_hz.tolist())
    for label, direction in zip(labels, directions):
        gain = directivity.get_response_cartesian(np.asarray(direction, dtype=float))
        gain_db = 20.0 * np.log10(np.maximum(gain, 1e-300))
        rot, arc = directivity._world_cartesian_to_cf2_angles(direction)
        print(
            f"{label:7s} CF2(rot={rot:8.3f}, arc={arc:8.3f}) dB="
            + np.array2string(gain_db, precision=3, separator=", ")
        )


def build_probe_room():
    probe = pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=pra.Material(ABSORPTION),
        max_order=0,
    )
    centers = get_room_band_centers(probe)
    material = make_constant_multiband_material(centers, ABSORPTION)
    room = pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=material,
        max_order=0,
    )
    return room, get_room_band_centers(room)


def run_smoke_test(cf2_path, handedness, interpolation_domain):
    print("=== CF2 -> Pyroomacoustics 0.10.1 ISM smoke test ===")
    print("pyroomacoustics:", getattr(pra, "__version__", "unknown"))

    room, centers = build_probe_room()
    print("room.is_multi_band:", getattr(room, "is_multi_band", "<missing>"))
    print("PRA octave centers:", centers.tolist())

    directivity = CF2DirectivityISM(
        cf2_path=cf2_path,
        band_frequencies_hz=centers,
        speaker_azimuth_deg=0.0,
        speaker_colatitude_deg=90.0,
        speaker_roll_deg=0.0,
        handedness=handedness,
        interpolation_domain=interpolation_domain,
        out_of_range="error",
    )

    print("isinstance(MeasuredDirectivity):", isinstance(directivity, MeasuredDirectivity))
    print("is_impulse_response:", directivity.is_impulse_response)

    labels = ["FRONT", "BACK", "TOP", "LEFT", "RIGHT", "BOTTOM"]
    directions = [
        [1.0, 0.0, 0.0],
        [-1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
        [0.0, 1.0, 0.0],
        [0.0, -1.0, 0.0],
        [0.0, 0.0, -1.0],
    ]

    print("\n=== Direct adapter response ===")
    print_matrix_db(directivity, labels, directions)

    room.add_source(SOURCE_POS, directivity=directivity)
    for direction in directions:
        room.add_microphone(SOURCE_POS + np.asarray(direction, dtype=float))

    print("\nconfigured_mics:", room.mic_array.R.shape[1])
    print("configured_sources:", len(room.sources))
    print("source_directivity_class:", type(room.sources[0].directivity).__name__)

    print("\nCalling room.compute_rir() with max_order=0 ...")
    room.compute_rir()
    print("compute_rir: SUCCESS")

    print("\n=== RIR summary by direction ===")
    for i, label in enumerate(labels):
        rir = np.asarray(room.rir[i][0], dtype=float)
        energy = float(np.sum(rir * rir))
        peak = float(np.max(np.abs(rir)))
        print(f"{label:7s} len={len(rir):4d} energy={energy:.9e} peak={peak:.9e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cf2", required=True)
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
    run_smoke_test(Path(args.cf2), args.handedness, args.interpolation_domain)


if __name__ == "__main__":
    main()
