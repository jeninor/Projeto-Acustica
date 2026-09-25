"""
Stage 2A — CF2 -> Pyroomacoustics 0.10.1 custom multiband Directivity smoke test.

Single-file integration test. No Docker rebuild required.

Validated assumptions:
- CF2 magnitude block at byte offset 14232
- shape [30 frequencies, 72 rotations, 37 arcs]
- float32 little-endian
- valid CF2 magnitude bands: 100 Hz .. 10 kHz
- PRA 0.10.1 multiband ISM expects get_response() -> (n_bands, n_directions)
- generic Directivity subclasses must be inserted via SoundSource
"""

import argparse
import math
from pathlib import Path

import numpy as np
import pyroomacoustics as pra
from pyroomacoustics.directivities import Directivity


CF2_FREQUENCIES_HZ = np.array([
    25, 31.5, 40, 50, 63, 80,
    100, 125, 160,
    200, 250, 315,
    400, 500, 630,
    800, 1000, 1250,
    1600, 2000, 2500,
    3150, 4000, 5000,
    6300, 8000, 10000,
    12500, 16000, 20000,
], dtype=float)

BASE_OFFSET = 14232
N_FREQ = 30
N_ROT = 72
N_ARC = 37
ROT_STEP_DEG = 5.0
ARC_STEP_DEG = 5.0
DTYPE = np.dtype("<f4")
FLOATS_PER_BAND = N_ROT * N_ARC
BYTES_PER_BAND = FLOATS_PER_BAND * DTYPE.itemsize

PRA_OCTAVE_CENTERS_HZ = np.array(
    [125, 250, 500, 1000, 2000, 4000, 8000],
    dtype=float,
)

CF2_OCTAVE_TRIPLETS_HZ = [
    (100, 125, 160),
    (200, 250, 315),
    (400, 500, 630),
    (800, 1000, 1250),
    (1600, 2000, 2500),
    (3150, 4000, 5000),
    (6300, 8000, 10000),
]


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    if n == 0.0:
        raise ValueError("Cannot normalize zero-length vector.")
    return v / n


def direction_from_azimuth_colatitude(azimuth_deg, colatitude_deg=90.0):
    az = math.radians(float(azimuth_deg))
    col = math.radians(float(colatitude_deg))
    return np.array(
        [
            math.sin(col) * math.cos(az),
            math.sin(col) * math.sin(az),
            math.cos(col),
        ],
        dtype=float,
    )


def loudspeaker_basis(azimuth_deg=0.0, colatitude_deg=90.0, roll_deg=0.0):
    """Return world-space unit vectors for local forward(+X), left(+Y), top(+Z)."""
    az = math.radians(float(azimuth_deg))
    col = math.radians(float(colatitude_deg))

    forward = direction_from_azimuth_colatitude(azimuth_deg, colatitude_deg)

    left0 = np.array(
        [-math.sin(az), math.cos(az), 0.0],
        dtype=float,
    )
    top0 = np.array(
        [
            -math.cos(col) * math.cos(az),
            -math.cos(col) * math.sin(az),
            math.sin(col),
        ],
        dtype=float,
    )

    left0 = _unit(left0)
    top0 = _unit(top0)

    roll = math.radians(float(roll_deg))
    left = math.cos(roll) * left0 + math.sin(roll) * top0
    top = -math.sin(roll) * left0 + math.cos(roll) * top0

    forward = _unit(forward)
    left = _unit(left - np.dot(left, forward) * forward)
    top = _unit(np.cross(forward, left))

    return forward, left, top


class CF2Magnitude:
    def __init__(self, cf2_path, base_offset=BASE_OFFSET):
        self.cf2_path = Path(cf2_path)
        self.base_offset = int(base_offset)
        self.balloon_db = self._load()

        self.valid_mask = ~np.all(
            self.balloon_db == 0.0,
            axis=(1, 2),
        )
        self.valid_indices = np.flatnonzero(self.valid_mask)
        self.valid_frequencies_hz = CF2_FREQUENCIES_HZ[self.valid_indices]

        expected = np.array([
            100, 125, 160,
            200, 250, 315,
            400, 500, 630,
            800, 1000, 1250,
            1600, 2000, 2500,
            3150, 4000, 5000,
            6300, 8000, 10000,
        ], dtype=float)

        if not np.array_equal(self.valid_frequencies_hz, expected):
            raise RuntimeError(
                "Unexpected valid CF2 bands: "
                f"{self.valid_frequencies_hz.tolist()}"
            )

        self.freq_to_index = {
            float(f): int(i)
            for i, f in enumerate(CF2_FREQUENCIES_HZ)
        }

    def _load(self):
        raw = self.cf2_path.read_bytes()
        needed = self.base_offset + N_FREQ * BYTES_PER_BAND

        if needed > len(raw):
            raise RuntimeError(
                f"CF2 file too short: size={len(raw)}, needed={needed}"
            )

        x = np.frombuffer(
            raw,
            dtype=DTYPE,
            count=N_FREQ * FLOATS_PER_BAND,
            offset=self.base_offset,
        ).copy()

        return x.reshape(N_FREQ, N_ROT, N_ARC)

    @staticmethod
    def _angular_indices(rotation_deg, arc_deg):
        r = np.mod(np.asarray(rotation_deg, dtype=float), 360.0)
        a = np.asarray(arc_deg, dtype=float)

        if np.any(a < 0.0) or np.any(a > 180.0):
            raise ValueError("arc_deg must be in [0, 180].")

        rpos = r / ROT_STEP_DEG
        rf = np.floor(rpos)
        r0 = rf.astype(int) % N_ROT
        r1 = (r0 + 1) % N_ROT
        wr = rpos - rf

        apos = a / ARC_STEP_DEG
        af = np.floor(apos)
        a0 = af.astype(int)

        at_back = a0 >= (N_ARC - 1)
        a0 = np.where(at_back, N_ARC - 1, a0)
        a1 = np.where(at_back, a0, a0 + 1)
        wa = np.where(at_back, 0.0, apos - af)

        return r0, r1, wr, a0, a1, wa

    def gain_db_at_stored_frequency(self, frequency_hz, rotation_deg, arc_deg):
        f = float(frequency_hz)

        if f not in self.freq_to_index:
            raise ValueError(f"{f:g} Hz is not a stored CF2 frequency.")

        fi = self.freq_to_index[f]

        if not self.valid_mask[fi]:
            raise ValueError(f"{f:g} Hz is an all-zero/missing CF2 band.")

        r0, r1, wr, a0, a1, wa = self._angular_indices(
            rotation_deg,
            arc_deg,
        )

        b = self.balloon_db[fi]

        q00 = b[r0, a0]
        q10 = b[r1, a0]
        q01 = b[r0, a1]
        q11 = b[r1, a1]

        v0 = (1.0 - wr) * q00 + wr * q10
        v1 = (1.0 - wr) * q01 + wr * q11

        return (1.0 - wa) * v0 + wa * v1

    def octave_gain_db(self, rotation_deg, arc_deg):
        octave_db = []

        for triplet in CF2_OCTAVE_TRIPLETS_HZ:
            third_db = np.stack(
                [
                    self.gain_db_at_stored_frequency(
                        f,
                        rotation_deg,
                        arc_deg,
                    )
                    for f in triplet
                ],
                axis=0,
            )

            power = np.power(10.0, third_db / 10.0)

            d_oct = 10.0 * np.log10(
                np.mean(power, axis=0)
            )

            octave_db.append(d_oct)

        return np.stack(octave_db, axis=0)


class CF2OctaveDirectivity(Directivity):
    def __init__(
        self,
        cf2_path,
        azimuth_deg=0.0,
        colatitude_deg=90.0,
        roll_deg=0.0,
        base_offset=BASE_OFFSET,
    ):
        super().__init__()

        self.cf2 = CF2Magnitude(cf2_path, base_offset=base_offset)

        self.azimuth_deg = float(azimuth_deg)
        self.colatitude_deg = float(colatitude_deg)
        self.roll_deg = float(roll_deg)

        self.forward, self.left, self.top = loudspeaker_basis(
            self.azimuth_deg,
            self.colatitude_deg,
            self.roll_deg,
        )

        self.calls = []

    @property
    def is_impulse_response(self):
        return False

    @property
    def filter_len_ir(self):
        return 1

    def _angles_world_to_cf2(self, azimuth, colatitude, degrees=True):
        az = np.asarray(azimuth, dtype=float)

        if colatitude is None:
            col = np.full_like(
                az,
                90.0 if degrees else np.pi / 2.0,
            )
        else:
            col = np.asarray(colatitude, dtype=float)

        az, col = np.broadcast_arrays(az, col)

        if degrees:
            az_r = np.radians(az)
            col_r = np.radians(col)
        else:
            az_r = az
            col_r = col

        sin_col = np.sin(col_r)

        world_x = sin_col * np.cos(az_r)
        world_y = sin_col * np.sin(az_r)
        world_z = np.cos(col_r)

        local_x = (
            world_x * self.forward[0]
            + world_y * self.forward[1]
            + world_z * self.forward[2]
        )
        local_y = (
            world_x * self.left[0]
            + world_y * self.left[1]
            + world_z * self.left[2]
        )
        local_z = (
            world_x * self.top[0]
            + world_y * self.top[1]
            + world_z * self.top[2]
        )

        local_x = np.clip(local_x, -1.0, 1.0)

        arc_deg = np.degrees(np.arccos(local_x))
        transverse = np.hypot(local_y, local_z)

        # Positive CF2 rotation convention used for this baseline: top -> left.
        rotation_deg = np.mod(
            np.degrees(np.arctan2(local_y, local_z)),
            360.0,
        )
        rotation_deg = np.where(
            transverse < 1e-12,
            0.0,
            rotation_deg,
        )

        return rotation_deg, arc_deg

    def get_response(
        self,
        azimuth,
        colatitude=None,
        magnitude=False,
        degrees=True,
    ):
        rotation_deg, arc_deg = self._angles_world_to_cf2(
            azimuth,
            colatitude,
            degrees=degrees,
        )

        octave_db = self.cf2.octave_gain_db(
            rotation_deg,
            arc_deg,
        )

        # PRA's ISM multiplies amplitude by this response.
        response = np.power(10.0, octave_db / 20.0)

        self.calls.append(
            {
                "azimuth_shape": np.shape(azimuth),
                "colatitude_shape": (
                    None if colatitude is None else np.shape(colatitude)
                ),
                "response_shape": response.shape,
                "degrees": bool(degrees),
                "rotation_min_deg": float(np.min(rotation_deg)),
                "rotation_max_deg": float(np.max(rotation_deg)),
                "arc_min_deg": float(np.min(arc_deg)),
                "arc_max_deg": float(np.max(arc_deg)),
            }
        )

        return response

    def sample_rays(self, n_rays):
        """
        Minimal Fibonacci-sphere implementation to satisfy Directivity ABC.
        This is not yet our validated ray-tracing implementation.
        """
        n = int(n_rays)

        if n <= 0:
            raise ValueError("n_rays must be positive.")

        i = np.arange(n, dtype=float)
        golden_angle = np.pi * (3.0 - np.sqrt(5.0))

        z = 1.0 - 2.0 * (i + 0.5) / n
        radius = np.sqrt(np.maximum(0.0, 1.0 - z * z))
        phi = golden_angle * i

        directions = np.column_stack(
            [
                radius * np.cos(phi),
                radius * np.sin(phi),
                z,
            ]
        )

        az = np.arctan2(directions[:, 1], directions[:, 0])
        col = np.arccos(np.clip(directions[:, 2], -1.0, 1.0))

        amp = self.get_response(
            azimuth=az,
            colatitude=col,
            degrees=False,
        )

        energies = (amp.T * amp.T)

        return directions, energies


def octave_db_from_amplitude(amp):
    return 20.0 * np.log10(np.asarray(amp, dtype=float))


def print_cardinal_response(d):
    print("=== CF2 custom Directivity cardinal response ===")
    print("PRA octave centers Hz:", PRA_OCTAVE_CENTERS_HZ.tolist())

    cases = [
        ("FRONT +X", 0.0, 90.0),
        ("LEFT  +Y", 90.0, 90.0),
        ("BACK  -X", 180.0, 90.0),
        ("RIGHT -Y", 270.0, 90.0),
        ("TOP   +Z", 0.0, 0.0),
        ("BOTTOM-Z", 0.0, 180.0),
    ]

    for label, az, col in cases:
        amp = d.get_response(
            np.array([az]),
            np.array([col]),
            degrees=True,
        )[:, 0]

        db = octave_db_from_amplitude(amp)

        print(
            f"{label:10s} "
            f"az={az:7.1f} col={col:7.1f} "
            f"dB="
            + np.array2string(db, precision=4)
        )


def explicit_multiband_material():
    return pra.Material(
        energy_absorption={
            "coeffs": [0.35] * 7,
            "center_freqs": PRA_OCTAVE_CENTERS_HZ.tolist(),
        }
    )


def direct_path_smoke(cf2_path, base_offset):
    print()
    print("=== PRA max_order=0 direct-path smoke test ===")

    room = pra.ShoeBox(
        [6.0, 6.0, 3.0],
        fs=16000,
        materials=explicit_multiband_material(),
        max_order=0,
    )

    source_pos = np.array([3.0, 3.0, 1.5], dtype=float)

    microphones = [
        ("FRONT +X", [4.0, 3.0, 1.5]),
        ("LEFT  +Y", [3.0, 4.0, 1.5]),
        ("BACK  -X", [2.0, 3.0, 1.5]),
        ("RIGHT -Y", [3.0, 2.0, 1.5]),
        ("TOP   +Z", [3.0, 3.0, 2.5]),
        ("BOTTOM-Z", [3.0, 3.0, 0.5]),
    ]

    d = CF2OctaveDirectivity(
        cf2_path,
        azimuth_deg=0.0,
        colatitude_deg=90.0,
        roll_deg=0.0,
        base_offset=base_offset,
    )

    # Generic Directivity subclasses must enter via SoundSource in PRA 0.10.1.
    src = pra.SoundSource(
        source_pos,
        directivity=d,
    )
    room.add_source(src)

    for _, pos in microphones:
        room.add_microphone(pos)

    print("room.is_multi_band:", room.is_multi_band)
    print("n_sources_before_compute:", len(room.sources))
    print(
        "stored_directivity_type:",
        type(room.sources[0].directivity).__name__,
    )

    room.compute_rir()

    print("compute_rir: SUCCESS")
    print("n_mics:", len(room.rir))
    print("get_response_calls:", len(d.calls))

    for i, call in enumerate(d.calls, 1):
        print(
            f"call {i}: "
            f"az={call['azimuth_shape']} "
            f"col={call['colatitude_shape']} "
            f"response={call['response_shape']} "
            f"degrees={call['degrees']} "
            f"rot=[{call['rotation_min_deg']:.3f},"
            f"{call['rotation_max_deg']:.3f}] "
            f"arc=[{call['arc_min_deg']:.3f},"
            f"{call['arc_max_deg']:.3f}]"
        )

    print()
    print("Direct-path RIR summaries (all receivers at 1 m):")

    for mic_idx, (label, _) in enumerate(microphones):
        rir = np.asarray(room.rir[mic_idx][0], dtype=float)

        energy = float(np.sum(rir * rir))
        peak = float(np.max(np.abs(rir)))

        print(
            f"{mic_idx:2d} "
            f"{label:10s} "
            f"len={len(rir):4d} "
            f"energy={energy:.9e} "
            f"peak={peak:.9e}"
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cf2", required=True)
    ap.add_argument("--base", type=int, default=BASE_OFFSET)
    args = ap.parse_args()

    print("=== Stage 2A: CF2 -> PRA custom multiband Directivity ===")
    print("pyroomacoustics:", getattr(pra, "__version__", "unknown"))
    print("CF2:", args.cf2)
    print("base_offset:", args.base)
    print()

    d = CF2OctaveDirectivity(
        args.cf2,
        azimuth_deg=0.0,
        colatitude_deg=90.0,
        roll_deg=0.0,
        base_offset=args.base,
    )

    print(
        "valid CF2 frequencies:",
        d.cf2.valid_frequencies_hz.tolist(),
    )
    print()

    print_cardinal_response(d)
    direct_path_smoke(args.cf2, args.base)


if __name__ == "__main__":
    main()
