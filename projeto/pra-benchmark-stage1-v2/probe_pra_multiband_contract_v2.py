#!/usr/bin/env python3
"""
Probe v2: custom 7-band Directivity contract in Pyroomacoustics 0.10.1.

This corrects the first probe: DummySevenBandDirectivity now inherits from
pyroomacoustics.directivities.Directivity, so SoundSource.set_directivity()
can actually accept it.

Tests
-----
A) room.add_source(..., directivity=custom)
   Expected: custom Directivity is still NOT accepted by Room.add_source()
   because that method explicitly gates CardioidFamily/MeasuredDirectivity.

B1) SoundSource(custom) + Room.add() in the current SINGLE-BAND room
    (pra.Material(0.35)).
    This tests whether an (n_images, 7) source gain can coexist with
    room.is_multi_band == False.

B2) SoundSource(custom) + Room.add() in a 7-BAND room using the same
    absorption coefficient 0.35 at every PRA octave-band center:
    [125, 250, 500, 1000, 2000, 4000, 8000] Hz.

No project files are modified.
"""

import pprint
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


class DummySevenBandDirectivity(Directivity):
    """
    Minimal valid Pyroomacoustics Directivity subclass.

    Frequency model:
      - is_impulse_response == False
      - get_response() returns shape (n_directions, 7)

    The gains are intentionally constant versus direction; this probe is about
    the integration contract, not directional acoustics.
    """

    def __init__(self, n_bands=7):
        self.n_bands = int(n_bands)
        self.band_gains = np.linspace(1.0, 0.4, self.n_bands, dtype=float)

    @property
    def is_impulse_response(self):
        return False

    @property
    def filter_len_ir(self):
        # Contract: non-IR directivities should report 1.
        return 1

    def get_response(
        self,
        azimuth,
        colatitude=None,
        magnitude=False,
        degrees=True,
    ):
        az = np.atleast_1d(np.asarray(azimuth, dtype=float))
        n = az.size

        out = np.broadcast_to(
            self.band_gains[None, :],
            (n, self.n_bands),
        ).copy()

        print(
            "Dummy.get_response:",
            "azimuth_shape=", np.shape(azimuth),
            "colatitude_shape=", np.shape(colatitude),
            "return_shape=", out.shape,
        )

        return out

    def sample_rays(self, n_rays, rng=None):
        # Not needed by the ISM tests below. Defining the method satisfies
        # the Directivity abstract contract without pretending RT is validated.
        raise NotImplementedError(
            "sample_rays intentionally not implemented: this is an ISM-only probe"
        )


def make_single_band_room():
    return pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=pra.Material(ABSORPTION),
        max_order=MAX_ORDER,
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


def print_octave_info(room, title):
    print()
    print("=" * 100)
    print(title)
    print("=" * 100)
    print("room_type:", type(room))
    print("is_multi_band:", room.is_multi_band)
    print("octave_bands_type:", type(room.octave_bands))

    try:
        print("get_bw():", room.octave_bands.get_bw())
    except Exception as e:
        print("get_bw() ERROR:", repr(e))

    print("octave_bands.__dict__:")
    pprint.pp(getattr(room.octave_bands, "__dict__", {}))

    # Wall absorption shape is useful for diagnosing the actual band model.
    print("wall_absorption_shapes:")
    try:
        for i, wall in enumerate(room.walls[: min(3, len(room.walls))]):
            print(
                f"  wall[{i}] absorption.shape=",
                np.shape(wall.absorption),
                "value=",
                wall.absorption,
            )
    except Exception as e:
        print("  <unavailable>", repr(e))


def test_add_source_gate():
    print()
    print("=" * 100)
    print("TEST A: Room.add_source custom Directivity subclass")
    print("=" * 100)

    room = make_single_band_room()
    dummy = DummySevenBandDirectivity(7)

    print("isinstance(dummy, Directivity):", isinstance(dummy, Directivity))
    print("dummy.is_impulse_response:", dummy.is_impulse_response)
    print("dummy.filter_len_ir:", dummy.filter_len_ir)

    print("sources_before:", len(room.sources))

    try:
        ret = room.add_source(
            SOURCE_POS,
            directivity=dummy,
        )
        print("add_source_return:", ret)
    except Exception as e:
        print("add_source_exception:", repr(e))
        traceback.print_exc()

    print("sources_after:", len(room.sources))

    if room.sources:
        print("stored_directivity_type:", type(room.sources[-1].directivity))


def run_soundsource_path(label, room):
    print()
    print("=" * 100)
    print(label)
    print("=" * 100)

    room.add_microphone(MIC_POS)
    dummy = DummySevenBandDirectivity(7)

    print("room.is_multi_band:", room.is_multi_band)
    print("isinstance(dummy, Directivity):", isinstance(dummy, Directivity))
    print("dummy.band_gains:", dummy.band_gains)

    try:
        src = SoundSource(
            SOURCE_POS,
            directivity=dummy,
        )
        print("SoundSource_constructed: True")
        print("SoundSource.directivity:", type(src.directivity))
    except Exception as e:
        print("SoundSource_constructed: False")
        print("SoundSource_exception:", repr(e))
        traceback.print_exc()
        return {
            "constructed": False,
            "added": False,
            "compute_success": False,
            "exception": repr(e),
        }

    try:
        ret = room.add(src)
        print("room.add_return_type:", type(ret))
        print("sources_after_room_add:", len(room.sources))
        print("stored_directivity_type:", type(room.sources[-1].directivity))
    except Exception as e:
        print("room.add_exception:", repr(e))
        traceback.print_exc()
        return {
            "constructed": True,
            "added": False,
            "compute_success": False,
            "exception": repr(e),
        }

    print()
    print("Attempting room.compute_rir() ...")

    try:
        room.compute_rir()

        rir = np.asarray(room.rir[0][0], dtype=float)

        print("compute_rir: SUCCESS")
        print("rir_outer_len:", len(room.rir))
        print("rir_mic0_sources:", len(room.rir[0]))
        print("rir_len:", len(rir))
        print("rir_all_finite:", bool(np.all(np.isfinite(rir))))
        print("rir_peak:", float(np.max(np.abs(rir))))
        print("rir_energy:", float(np.sum(rir * rir)))

        return {
            "constructed": True,
            "added": True,
            "compute_success": True,
            "exception": None,
        }

    except Exception as e:
        print("compute_rir: FAILED")
        print("compute_rir_exception:", repr(e))
        traceback.print_exc()

        return {
            "constructed": True,
            "added": True,
            "compute_success": False,
            "exception": repr(e),
        }


def main():
    print("=== PRA custom multiband directivity contract probe v2 ===")
    print("pyroomacoustics:", pra.__version__)
    print("fs:", FS)
    print("PRA target centers Hz:", PRA_CENTERS_HZ.tolist())

    single = make_single_band_room()
    multi = make_seven_band_room()

    print_octave_info(
        single,
        "CURRENT SINGLE-BAND ROOM: Material(0.35)",
    )

    print_octave_info(
        multi,
        "EXPLICIT 7-BAND ROOM: 0.35 repeated at PRA centers",
    )

    test_add_source_gate()

    b1 = run_soundsource_path(
        "TEST B1: SoundSource + Room.add() in SINGLE-BAND room",
        make_single_band_room(),
    )

    b2 = run_soundsource_path(
        "TEST B2: SoundSource + Room.add() in EXPLICIT 7-BAND room",
        make_seven_band_room(),
    )

    print()
    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print("B1_single_band:", b1)
    print("B2_seven_band:", b2)

    print()
    print("Interpretation rules:")
    print(
        "- If B1 succeeds: custom (n_images,7) gains work even with "
        "room.is_multi_band=False."
    )
    print(
        "- If B1 fails but B2 succeeds: custom multiband Directivity is viable, "
        "but the benchmark room must also be explicitly multiband."
    )
    print(
        "- If B2 fails: do NOT proceed to CF2Directivity yet; inspect the exact "
        "ISM array shapes/error first."
    )
    print(
        "- This probe does NOT validate ray tracing; sample_rays is intentionally "
        "unimplemented."
    )


if __name__ == "__main__":
    main()
