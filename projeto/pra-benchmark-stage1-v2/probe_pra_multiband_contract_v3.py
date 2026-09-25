#!/usr/bin/env python3
"""
Pyroomacoustics 0.10.1 custom multiband Directivity contract probe v3.

What v2 established
-------------------
- SoundSource accepts a custom Directivity subclass.
- Room.add(SoundSource) accepts it.
- SINGLE-BAND room produces oct_band_amplitude shape (1, n_images).
- EXPLICIT 7-BAND room produces oct_band_amplitude shape (7, n_images).
- Returning (n_images, 7) therefore fails broadcasting.

This v3 tests the effective runtime contract:
    get_response(...) -> shape (n_bands, n_images)

Tests
-----
1) 7-band custom directivity with unity gains:
       gain = [1,1,1,1,1,1,1]
   Compare its RIR against an OMNI source in the SAME 7-band room.
   Expected: numerically equal/nearly equal.

2) 7-band custom directivity with distinct gains:
       [1.0,0.9,0.8,0.7,0.6,0.5,0.4]
   Expected: compute_rir succeeds and energy differs from unity/OMNI.

This is an ISM-only probe. Ray tracing is intentionally not validated here.
"""

import math
import traceback
import numpy as np
import pyroomacoustics as pra

from pyroomacoustics.directivities import Directivity
from pyroomacoustics.soundsource import SoundSource


ROOM_DIM = [6.0, 5.0, 3.0]
SOURCE_POS = [2.0, 2.5, 1.5]
MIC_POS = [3.0, 2.5, 1.2]
FS = 16000
MAX_ORDER = 2
ABSORPTION = 0.35

PRA_CENTERS_HZ = np.array(
    [125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0],
    dtype=float,
)


class SevenBandDirectivity(Directivity):
    def __init__(self, band_gains):
        self.band_gains = np.asarray(band_gains, dtype=float)

        if self.band_gains.shape != (7,):
            raise ValueError(
                f"Expected exactly 7 band gains, got {self.band_gains.shape}"
            )

    @property
    def is_impulse_response(self):
        return False

    @property
    def filter_len_ir(self):
        return 1

    def get_response(
        self,
        azimuth,
        colatitude=None,
        magnitude=False,
        degrees=True,
    ):
        az = np.atleast_1d(np.asarray(azimuth, dtype=float))
        n_images = az.size

        # IMPORTANT:
        # Pyroomacoustics 0.10.1 compute_ism_rir() internally uses
        # oct_band_amplitude with shape (n_bands, n_images).
        out = np.broadcast_to(
            self.band_gains[:, None],
            (7, n_images),
        ).copy()

        print(
            "SevenBandDirectivity.get_response:",
            "azimuth_shape=", np.shape(azimuth),
            "return_shape=", out.shape,
        )

        return out

    def sample_rays(self, n_rays, rng=None):
        raise NotImplementedError(
            "Ray tracing intentionally not tested in this ISM probe."
        )


def make_seven_band_room():
    material = pra.Material(
        energy_absorption={
            "description": "flat 0.35 at PRA octave-band centers",
            "coeffs": [ABSORPTION] * len(PRA_CENTERS_HZ),
            "center_freqs": PRA_CENTERS_HZ.tolist(),
        }
    )

    return pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=material,
        max_order=MAX_ORDER,
    )


def run_omni():
    room = make_seven_band_room()
    room.add_microphone(MIC_POS)
    room.add_source(SOURCE_POS)

    print("\n--- OMNI CONTROL ---")
    print("room.is_multi_band:", room.is_multi_band)

    room.compute_rir()

    rir = np.asarray(room.rir[0][0], dtype=float)

    print("compute_rir: SUCCESS")
    print("rir_len:", len(rir))
    print("rir_peak:", float(np.max(np.abs(rir))))
    print("rir_energy:", float(np.sum(rir * rir)))

    return rir


def run_custom(label, gains):
    room = make_seven_band_room()
    room.add_microphone(MIC_POS)

    directivity = SevenBandDirectivity(gains)

    src = SoundSource(
        SOURCE_POS,
        directivity=directivity,
    )

    room.add(src)

    print(f"\n--- {label} ---")
    print("room.is_multi_band:", room.is_multi_band)
    print("stored_directivity_type:", type(room.sources[0].directivity))
    print("band_gains:", directivity.band_gains)

    try:
        room.compute_rir()

        rir = np.asarray(room.rir[0][0], dtype=float)

        print("compute_rir: SUCCESS")
        print("rir_len:", len(rir))
        print("rir_all_finite:", bool(np.all(np.isfinite(rir))))
        print("rir_peak:", float(np.max(np.abs(rir))))
        print("rir_energy:", float(np.sum(rir * rir)))

        return rir

    except Exception as e:
        print("compute_rir: FAILED")
        print("exception:", repr(e))
        traceback.print_exc()
        return None


def compare_rirs(name_a, a, name_b, b):
    print(f"\n=== RIR COMPARISON: {name_a} vs {name_b} ===")

    if a is None or b is None:
        print("comparison: SKIPPED because at least one RIR is missing")
        return None

    if len(a) != len(b):
        print("length_equal: False")
        print("len_a:", len(a))
        print("len_b:", len(b))
        return None

    diff = a - b

    max_abs = float(np.max(np.abs(diff)))
    rmse = float(np.sqrt(np.mean(diff * diff)))

    denom = float(np.linalg.norm(a))
    rel_l2 = (
        float(np.linalg.norm(diff) / denom)
        if denom > 0.0
        else float("nan")
    )

    energy_a = float(np.sum(a * a))
    energy_b = float(np.sum(b * b))

    energy_ratio_db = (
        10.0 * math.log10(energy_b / energy_a)
        if energy_a > 0.0 and energy_b > 0.0
        else float("nan")
    )

    print("length_equal: True")
    print("max_abs_sample_error:", max_abs)
    print("rmse:", rmse)
    print("relative_l2_error:", rel_l2)
    print("energy_a:", energy_a)
    print("energy_b:", energy_b)
    print(f"energy_{name_b}_vs_{name_a}_db:", energy_ratio_db)

    return {
        "max_abs": max_abs,
        "rmse": rmse,
        "relative_l2": rel_l2,
        "energy_ratio_db": energy_ratio_db,
    }


def main():
    print("=== PRA custom multiband directivity contract probe v3 ===")
    print("pyroomacoustics:", pra.__version__)
    print("fs:", FS)
    print("PRA centers Hz:", PRA_CENTERS_HZ.tolist())

    room_probe = make_seven_band_room()
    print("room.is_multi_band:", room_probe.is_multi_band)
    print("wall0_absorption_shape:", np.shape(room_probe.walls[0].absorption))
    print("wall0_absorption:", room_probe.walls[0].absorption)

    omni = run_omni()

    unity = run_custom(
        "CUSTOM 7-BAND UNITY",
        np.ones(7, dtype=float),
    )

    shaped = run_custom(
        "CUSTOM 7-BAND DISTINCT GAINS",
        np.array([1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4], dtype=float),
    )

    cmp_unity = compare_rirs(
        "OMNI",
        omni,
        "UNITY",
        unity,
    )

    cmp_shaped = compare_rirs(
        "OMNI",
        omni,
        "SHAPED",
        shaped,
    )

    print("\n" + "=" * 100)
    print("INTERPRETATION")
    print("=" * 100)

    if unity is None:
        print("FAIL: (7, n_images) custom multiband response did not run.")
        raise SystemExit(1)

    if cmp_unity is None:
        print("FAIL: could not compare OMNI and unity custom RIRs.")
        raise SystemExit(1)

    # Keep tolerance conservative. In a deterministic same-room control,
    # the result should normally be essentially identical.
    if (
        cmp_unity["max_abs"] < 1e-10
        and cmp_unity["relative_l2"] < 1e-10
    ):
        print(
            "PASS: effective ISM contract is consistent with "
            "(n_bands, n_images), and unity multiband gains reproduce OMNI."
        )
    else:
        print(
            "PARTIAL PASS: compute_rir works with (n_bands, n_images), "
            "but unity does not reproduce OMNI within 1e-10."
        )
        print(
            "Inspect the reported RIR differences before building CF2Directivity."
        )

    if shaped is not None:
        print(
            "Distinct-gain case also ran successfully; this establishes the "
            "minimal runtime path needed for a 7-band CF2Directivity ISM test."
        )
    else:
        print(
            "Distinct-gain case failed; inspect before proceeding."
        )


if __name__ == "__main__":
    main()
