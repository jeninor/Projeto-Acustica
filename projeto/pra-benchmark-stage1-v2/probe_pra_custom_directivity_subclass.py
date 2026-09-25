"""
Probe the real Pyroomacoustics 0.10.1 contract for a custom Directivity subclass.

This script answers four questions:

1) Can a custom Directivity subclass be attached to SoundSource?
2) Can it be inserted with room.add(source), bypassing Room.add_source's
   CardioidFamily / MeasuredDirectivity type gate?
3) Does ISM accept a frequency-flat response shaped (n_images,)?
4) Does ISM accept a 7-octave-band response shaped (n_images, 7), and
   does that require room.is_multi_band == True?

No CF2 parsing is used here; the gains are synthetic and deterministic.
"""

import traceback
import numpy as np
import pyroomacoustics as pra

from pyroomacoustics.directivities import Directivity
from pyroomacoustics.soundsource import SoundSource


ROOM_DIM = [6.0, 5.0, 3.0]
SOURCE_POS = [2.0, 2.5, 1.5]
MIC_POS = [4.0, 2.5, 1.2]
FS = 16000
MAX_ORDER = 2

PRA_BAND_CENTERS = np.array(
    [125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0]
)


def n_points_from_angles(azimuth, colatitude=None):
    a = np.asarray(azimuth)
    if a.ndim == 0:
        return 1
    return int(a.size)


class ProbeDirectivity(Directivity):
    """
    Minimal Directivity subclass.

    mode="flat":
        returns shape (n_images,)

    mode="multiband":
        returns shape (n_images, 7)
    """

    def __init__(self, mode):
        self.mode = mode
        self.calls = []

    @property
    def is_impulse_response(self):
        return False

    @property
    def filter_len_ir(self):
        return 1

    def set_orientation(self, orientation):
        self.orientation = orientation

    def get_response(
        self,
        azimuth,
        colatitude=None,
        magnitude=False,
        degrees=True,
    ):
        n = n_points_from_angles(
            azimuth,
            colatitude,
        )

        if self.mode == "flat":
            response = np.full(
                n,
                0.8,
                dtype=float,
            )
        elif self.mode == "multiband":
            per_band = np.array(
                [1.00, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40],
                dtype=float,
            )
            response = np.broadcast_to(
                per_band,
                (n, per_band.size),
            ).copy()
        else:
            raise ValueError(self.mode)

        self.calls.append(
            {
                "method": "get_response",
                "n": n,
                "azimuth_shape": np.asarray(azimuth).shape,
                "colatitude_shape": (
                    None
                    if colatitude is None
                    else np.asarray(colatitude).shape
                ),
                "response_shape": response.shape,
            }
        )

        return response

    def get_response_cartesian(
        self,
        directions,
        magnitude=False,
        frequency=None,
    ):
        d = np.asarray(
            directions,
            dtype=float,
        )

        if d.ndim == 1:
            n = 1
        elif d.shape[-1] == 3:
            n = d.shape[0]
        elif d.shape[0] == 3:
            n = d.shape[1]
        else:
            raise ValueError(
                f"Unexpected directions shape: {d.shape}"
            )

        if self.mode == "flat":
            response = np.full(
                n,
                0.8,
                dtype=float,
            )
        else:
            per_band = np.array(
                [1.00, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40],
                dtype=float,
            )
            response = np.broadcast_to(
                per_band,
                (n, per_band.size),
            ).copy()

        self.calls.append(
            {
                "method": "get_response_cartesian",
                "directions_shape": d.shape,
                "frequency": frequency,
                "response_shape": response.shape,
            }
        )

        return response


def make_room(material):
    room = pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=material,
        max_order=MAX_ORDER,
    )
    room.add_microphone(MIC_POS)
    return room


def print_room_bands(room):
    print("room.is_multi_band:", room.is_multi_band)
    print("octave centers:", getattr(room.octave_bands, "centers", None))
    print("octave n_bands:", getattr(room.octave_bands, "n_bands", None))


def insert_via_sound_source(room, directivity):
    src = SoundSource(
        SOURCE_POS,
        directivity=directivity,
    )
    print("SoundSource constructed:", True)
    print(
        "SoundSource directivity type:",
        type(src.directivity),
    )
    room.add(src)
    print("room.add(src) completed")
    print("sources_after:", len(room.sources))
    print(
        "stored directivity same object:",
        room.sources[0].directivity is directivity,
    )


def compute_and_report(room, directivity):
    try:
        room.compute_rir()
        print("compute_rir: SUCCESS")
        print("rir shape/count:")
        print(
            "  n_mics:",
            len(room.rir),
        )
        print(
            "  n_sources_for_mic0:",
            len(room.rir[0]),
        )
        print(
            "  rir_len:",
            len(room.rir[0][0]),
        )
        rir = np.asarray(room.rir[0][0], dtype=float)
        print(
            "  rir_energy:",
            float(np.sum(rir * rir)),
        )
    except Exception as e:
        print("compute_rir: FAILED")
        print("exception:", repr(e))
        traceback.print_exc()

    print("directivity calls:")
    for c in directivity.calls:
        print(" ", c)


def make_multiband_material():
    return pra.Material(
        energy_absorption={
            "coeffs": [0.35] * 7,
            "center_freqs": PRA_BAND_CENTERS.tolist(),
        }
    )


def run_case(title, material, mode):
    print()
    print("=" * 88)
    print(title)
    print("=" * 88)

    try:
        room = make_room(material)
        print_room_bands(room)

        d = ProbeDirectivity(mode)

        print(
            "isinstance(custom, Directivity):",
            isinstance(d, Directivity),
        )

        insert_via_sound_source(
            room,
            d,
        )

        compute_and_report(
            room,
            d,
        )

    except Exception as e:
        print("CASE SETUP/INSERT FAILED")
        print("exception:", repr(e))
        traceback.print_exc()


def main():
    print(
        "=== PRA custom Directivity subclass / multiband contract probe ==="
    )
    print(
        "pyroomacoustics:",
        getattr(pra, "__version__", "unknown"),
    )

    run_case(
        "TEST 1 — scalar material + flat custom Directivity",
        pra.Material(0.35),
        "flat",
    )

    run_case(
        "TEST 2 — scalar material + 7-band custom Directivity",
        pra.Material(0.35),
        "multiband",
    )

    print()
    print("=" * 88)
    print("BUILD EXPLICIT MULTIBAND MATERIAL")
    print("=" * 88)

    try:
        mb_material = make_multiband_material()
        print("multiband material constructed: True")
        print("material:", mb_material)

        run_case(
            "TEST 3 — explicit 7-band material + 7-band custom Directivity",
            mb_material,
            "multiband",
        )
    except Exception as e:
        print("multiband material constructed: False")
        print("exception:", repr(e))
        traceback.print_exc()

    print()
    print("=" * 88)
    print("INTERPRETATION TARGET")
    print("=" * 88)
    print(
        "If TEST 1 succeeds: custom Directivity subclasses can enter ISM "
        "through SoundSource + room.add()."
    )
    print(
        "If TEST 2 fails but TEST 3 succeeds: frequency-dependent CF2 gains "
        "require an explicitly multiband room."
    )
    print(
        "If TEST 2 succeeds too: ISM accepts the 7-band directivity response "
        "even when the room reports is_multi_band=False."
    )


if __name__ == "__main__":
    main()
