"""
CF2 magnitude interpolator for the currently validated Bosch/CLF2 layout.

Validated binary layout used here
---------------------------------
- magnitude base offset: 14232 bytes
- 30 frequency slots
- 72 rotations: 0..355 deg, step 5 deg
- 37 arcs:      0..180 deg, step 5 deg
- float32 little-endian
- storage order: [frequency][rotation][arc]

Important behavior
------------------
- All-zero bands are treated as MISSING directivity data, not as 0 dB omni.
- For the current file, valid measured/directivity bands are detected
  automatically as 100 Hz .. 10 kHz.
- Rotation interpolation is periodic across 355 -> 0 deg.
- Arc interpolation is bounded to 0..180 deg.
- Frequency interpolation is logarithmic in frequency.
- Directivity values are interpolated in dB by default.

This module does not yet subclass a Pyroomacoustics Directivity object.
It is the validated data/interpolation layer that will feed that adapter.
"""

import argparse
import math
from pathlib import Path

import numpy as np


FREQUENCIES_HZ = np.array([
    25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200,
    250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000,
    2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000,
], dtype=float)

DEFAULT_BASE_OFFSET = 14232
N_FREQ = 30
N_ROT = 72
N_ARC = 37
ROT_STEP_DEG = 5.0
ARC_STEP_DEG = 5.0
DTYPE = np.dtype("<f4")
FLOATS_PER_BAND = N_ROT * N_ARC
BYTES_PER_BAND = FLOATS_PER_BAND * DTYPE.itemsize


class CF2MagnitudeInterpolator:
    def __init__(
        self,
        cf2_path,
        base_offset=DEFAULT_BASE_OFFSET,
        out_of_range="error",
        interpolation_domain="db",
    ):
        """
        Parameters
        ----------
        cf2_path : str or Path
            CF2 file path.
        base_offset : int
            Byte offset of the 30-band magnitude block.
        out_of_range : {"error", "clamp"}
            What to do outside the detected valid-frequency range.
        interpolation_domain : {"db", "linear_amplitude"}
            Domain used for angular and frequency interpolation.
            "db" is the default because CF2 directivity values are stored
            as relative level values in dB.
        """
        self.cf2_path = Path(cf2_path)
        self.base_offset = int(base_offset)

        if out_of_range not in {"error", "clamp"}:
            raise ValueError(
                "out_of_range must be 'error' or 'clamp'"
            )

        if interpolation_domain not in {
            "db",
            "linear_amplitude",
        }:
            raise ValueError(
                "interpolation_domain must be "
                "'db' or 'linear_amplitude'"
            )

        self.out_of_range = out_of_range
        self.interpolation_domain = interpolation_domain

        self.balloon_db = self._load_balloon()
        self.valid_mask = ~np.all(
            self.balloon_db == 0.0,
            axis=(1, 2),
        )

        self.valid_indices = np.flatnonzero(
            self.valid_mask
        )

        if len(self.valid_indices) == 0:
            raise RuntimeError(
                "No non-zero directivity bands were detected."
            )

        expected = np.arange(
            self.valid_indices[0],
            self.valid_indices[-1] + 1,
        )

        if not np.array_equal(
            self.valid_indices,
            expected,
        ):
            raise RuntimeError(
                "Non-zero CF2 frequency slots are not contiguous: "
                f"{self.valid_indices.tolist()}"
            )

        self.valid_frequencies_hz = (
            FREQUENCIES_HZ[
                self.valid_indices
            ].copy()
        )

        self.min_frequency_hz = float(
            self.valid_frequencies_hz[0]
        )
        self.max_frequency_hz = float(
            self.valid_frequencies_hz[-1]
        )

    def _load_balloon(self):
        raw = self.cf2_path.read_bytes()

        needed = (
            self.base_offset
            + N_FREQ * BYTES_PER_BAND
        )

        if needed > len(raw):
            raise RuntimeError(
                f"CF2 file too short: "
                f"size={len(raw)}, needed={needed}"
            )

        x = np.frombuffer(
            raw,
            dtype=DTYPE,
            count=N_FREQ * FLOATS_PER_BAND,
            offset=self.base_offset,
        ).copy()

        return x.reshape(
            N_FREQ,
            N_ROT,
            N_ARC,
        )

    @staticmethod
    def db_to_amplitude(db):
        return np.power(
            10.0,
            np.asarray(db, dtype=float) / 20.0,
        )

    @staticmethod
    def amplitude_to_db(amplitude):
        a = np.asarray(
            amplitude,
            dtype=float,
        )

        if np.any(a <= 0.0):
            raise ValueError(
                "Amplitude must be > 0 for dB conversion."
            )

        return 20.0 * np.log10(a)

    def _prepare_frequency(self, frequency_hz):
        f = float(frequency_hz)

        if self.min_frequency_hz <= f <= self.max_frequency_hz:
            return f

        if self.out_of_range == "clamp":
            return min(
                max(
                    f,
                    self.min_frequency_hz,
                ),
                self.max_frequency_hz,
            )

        raise ValueError(
            f"Frequency {f:g} Hz is outside the detected valid "
            f"CF2 range [{self.min_frequency_hz:g}, "
            f"{self.max_frequency_hz:g}] Hz. "
            "All-zero slots are treated as missing data."
        )

    def _frequency_bracket(self, frequency_hz):
        f = self._prepare_frequency(
            frequency_hz
        )

        freqs = self.valid_frequencies_hz

        # Exact endpoint / exact band
        exact = np.where(
            np.isclose(
                freqs,
                f,
                rtol=0.0,
                atol=1e-12,
            )
        )[0]

        if len(exact):
            local_idx = int(exact[0])
            global_idx = int(
                self.valid_indices[
                    local_idx
                ]
            )
            return (
                global_idx,
                global_idx,
                0.0,
                f,
            )

        hi_local = int(
            np.searchsorted(
                freqs,
                f,
                side="right",
            )
        )

        lo_local = hi_local - 1

        f0 = float(
            freqs[
                lo_local
            ]
        )
        f1 = float(
            freqs[
                hi_local
            ]
        )

        # Log-frequency interpolation.
        wf = (
            math.log(f / f0)
            /
            math.log(f1 / f0)
        )

        i0 = int(
            self.valid_indices[
                lo_local
            ]
        )
        i1 = int(
            self.valid_indices[
                hi_local
            ]
        )

        return i0, i1, float(wf), f

    @staticmethod
    def _angular_bracket(
        rotation_deg,
        arc_deg,
    ):
        r = float(rotation_deg) % 360.0
        a = float(arc_deg)

        if not (0.0 <= a <= 180.0):
            raise ValueError(
                f"arc_deg must be in [0, 180], got {a}"
            )

        rpos = r / ROT_STEP_DEG
        r0 = int(
            math.floor(rpos)
        ) % N_ROT
        wr = rpos - math.floor(rpos)
        r1 = (r0 + 1) % N_ROT

        apos = a / ARC_STEP_DEG
        a0 = int(
            math.floor(apos)
        )

        if a0 >= N_ARC - 1:
            a0 = N_ARC - 1
            a1 = a0
            wa = 0.0
        else:
            a1 = a0 + 1
            wa = apos - math.floor(apos)

        return (
            r0,
            r1,
            float(wr),
            a0,
            a1,
            float(wa),
        )

    def _bilinear_band_db(
        self,
        band_index,
        rotation_deg,
        arc_deg,
    ):
        (
            r0,
            r1,
            wr,
            a0,
            a1,
            wa,
        ) = self._angular_bracket(
            rotation_deg,
            arc_deg,
        )

        b = self.balloon_db[
            int(band_index)
        ]

        q00 = float(
            b[
                r0,
                a0,
            ]
        )
        q10 = float(
            b[
                r1,
                a0,
            ]
        )
        q01 = float(
            b[
                r0,
                a1,
            ]
        )
        q11 = float(
            b[
                r1,
                a1,
            ]
        )

        if self.interpolation_domain == "db":
            v0 = (
                (1.0 - wr) * q00
                + wr * q10
            )

            v1 = (
                (1.0 - wr) * q01
                + wr * q11
            )

            return (
                (1.0 - wa) * v0
                + wa * v1
            )

        # Interpolate amplitude instead of dB.
        p00 = float(
            self.db_to_amplitude(
                q00
            )
        )
        p10 = float(
            self.db_to_amplitude(
                q10
            )
        )
        p01 = float(
            self.db_to_amplitude(
                q01
            )
        )
        p11 = float(
            self.db_to_amplitude(
                q11
            )
        )

        v0 = (
            (1.0 - wr) * p00
            + wr * p10
        )

        v1 = (
            (1.0 - wr) * p01
            + wr * p11
        )

        amp = (
            (1.0 - wa) * v0
            + wa * v1
        )

        return float(
            self.amplitude_to_db(
                amp
            )
        )

    def gain_db(
        self,
        frequency_hz,
        rotation_deg,
        arc_deg,
    ):
        """
        Interpolated relative directivity level in dB.
        """
        (
            i0,
            i1,
            wf,
            _,
        ) = self._frequency_bracket(
            frequency_hz
        )

        g0_db = self._bilinear_band_db(
            i0,
            rotation_deg,
            arc_deg,
        )

        if i0 == i1:
            return float(
                g0_db
            )

        g1_db = self._bilinear_band_db(
            i1,
            rotation_deg,
            arc_deg,
        )

        if self.interpolation_domain == "db":
            return float(
                (1.0 - wf) * g0_db
                + wf * g1_db
            )

        a0 = float(
            self.db_to_amplitude(
                g0_db
            )
        )

        a1 = float(
            self.db_to_amplitude(
                g1_db
            )
        )

        amp = (
            (1.0 - wf) * a0
            + wf * a1
        )

        return float(
            self.amplitude_to_db(
                amp
            )
        )

    def gain_amplitude(
        self,
        frequency_hz,
        rotation_deg,
        arc_deg,
    ):
        """
        Linear pressure/amplitude ratio corresponding to gain_db().
        """
        db = self.gain_db(
            frequency_hz,
            rotation_deg,
            arc_deg,
        )

        return float(
            self.db_to_amplitude(
                db
            )
        )

    def gain_power(
        self,
        frequency_hz,
        rotation_deg,
        arc_deg,
    ):
        """
        Linear power ratio corresponding to gain_db().
        """
        db = self.gain_db(
            frequency_hz,
            rotation_deg,
            arc_deg,
        )

        return float(
            10.0 ** (
                db / 10.0
            )
        )

    def exact_grid_reconstruction_error(self):
        """
        Verify that interpolation reproduces every stored valid grid node.
        """
        max_abs = 0.0
        worst = None

        for fi in self.valid_indices:
            freq = float(
                FREQUENCIES_HZ[
                    fi
                ]
            )

            raw_band = self.balloon_db[
                fi
            ]

            for ri in range(
                N_ROT
            ):
                rotation = (
                    ri * ROT_STEP_DEG
                )

                for ai in range(
                    N_ARC
                ):
                    arc = (
                        ai * ARC_STEP_DEG
                    )

                    got = self.gain_db(
                        freq,
                        rotation,
                        arc,
                    )

                    expected = float(
                        raw_band[
                            ri,
                            ai,
                        ]
                    )

                    err = abs(
                        got - expected
                    )

                    if err > max_abs:
                        max_abs = err
                        worst = {
                            "frequency_hz": freq,
                            "rotation_deg": rotation,
                            "arc_deg": arc,
                            "expected_db": expected,
                            "got_db": got,
                            "abs_error_db": err,
                        }

        return max_abs, worst


def run_self_test(
    cf2_path,
    base_offset,
    interpolation_domain,
):
    obj = CF2MagnitudeInterpolator(
        cf2_path=cf2_path,
        base_offset=base_offset,
        out_of_range="error",
        interpolation_domain=interpolation_domain,
    )

    print(
        "=== CF2 Magnitude Interpolator ==="
    )

    print(
        "file:",
        obj.cf2_path,
    )

    print(
        "interpolation_domain:",
        obj.interpolation_domain,
    )

    print(
        "valid_frequencies_hz:",
        obj.valid_frequencies_hz.tolist(),
    )

    print(
        "valid_range_hz:",
        obj.min_frequency_hz,
        "..",
        obj.max_frequency_hz,
    )

    print()

    print(
        "=== Exact stored-node reconstruction ==="
    )

    max_abs, worst = (
        obj.exact_grid_reconstruction_error()
    )

    print(
        "max_abs_error_db:",
        max_abs,
    )

    print(
        "worst:",
        worst,
    )

    print()

    print(
        "=== Periodicity checks ==="
    )

    checks = [
        (1000.0, 0.0, 90.0),
        (1000.0, 17.3, 42.7),
        (4000.0, 355.0, 95.0),
    ]

    for f, r, a in checks:
        g0 = obj.gain_db(
            f,
            r,
            a,
        )

        g360 = obj.gain_db(
            f,
            r + 360.0,
            a,
        )

        print(
            f"f={f:g} r={r:g} a={a:g} "
            f"g={g0:.9f} "
            f"g(r+360)={g360:.9f} "
            f"diff={g360-g0:+.3e}"
        )

    print()

    print(
        "=== Pole invariance checks ==="
    )

    for f in [100.0, 1000.0, 10000.0]:
        for arc in [0.0, 180.0]:
            vals = [
                obj.gain_db(
                    f,
                    r,
                    arc,
                )
                for r in [
                    0.0,
                    37.0,
                    123.0,
                    271.0,
                ]
            ]

            print(
                f"f={f:7g} arc={arc:6.1f} "
                f"values="
                + ", ".join(
                    f"{v:.6f}"
                    for v in vals
                )
                + f" span={max(vals)-min(vals):.3e}"
            )

    print()

    print(
        "=== Continuous-query examples ==="
    )

    examples = [
        (1000.0, 108.925, 90.0),
        (1000.0, 180.0, 16.699244),
        (1118.03398875, 2.5, 2.5),
        (3550.0, 123.4, 77.7),
    ]

    for f, r, a in examples:
        db = obj.gain_db(
            f,
            r,
            a,
        )

        amp = obj.gain_amplitude(
            f,
            r,
            a,
        )

        print(
            f"f={f:10.4f} Hz "
            f"rot={r:8.3f} "
            f"arc={a:8.3f} "
            f"gain_db={db:10.5f} "
            f"amplitude={amp:.8f}"
        )

    print()

    print(
        "=== Missing-frequency behavior ==="
    )

    for f in [80.0, 12500.0]:
        try:
            obj.gain_db(
                f,
                0.0,
                0.0,
            )
        except ValueError as e:
            print(
                f"{f:g} Hz -> expected error: {e}"
            )


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--cf2",
        required=True,
    )

    ap.add_argument(
        "--base",
        type=int,
        default=DEFAULT_BASE_OFFSET,
    )

    ap.add_argument(
        "--domain",
        choices=[
            "db",
            "linear_amplitude",
        ],
        default="db",
    )

    args = ap.parse_args()

    run_self_test(
        cf2_path=args.cf2,
        base_offset=args.base,
        interpolation_domain=args.domain,
    )


if __name__ == "__main__":
    main()
