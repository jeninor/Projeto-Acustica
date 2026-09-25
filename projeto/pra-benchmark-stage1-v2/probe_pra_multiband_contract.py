import pprint
import traceback
import numpy as np
import pyroomacoustics as pra
from pyroomacoustics.soundsource import SoundSource

ROOM_DIM = [6.0, 5.0, 3.0]
SOURCE_POS = [2.0, 2.5, 1.5]
MIC_POS = [3.0, 2.5, 1.2]
FS = 16000
MAX_ORDER = 2
ABSORPTION = 0.35

class DummySevenBandDirectivity:
    is_impulse_response = False

    def __init__(self, n_bands=7):
        self.n_bands = int(n_bands)
        self.band_gains = np.linspace(1.0, 0.4, self.n_bands)

    def get_response(self, azimuth, colatitude=None, magnitude=False, degrees=True):
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

def make_room():
    return pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=pra.Material(ABSORPTION),
        max_order=MAX_ORDER,
    )

def print_octave_info(room):
    print("=== Octave-band object ===")
    print("type:", type(room.octave_bands))
    print("is_multi_band:", room.is_multi_band)
    try:
        print("get_bw():", room.octave_bands.get_bw())
    except Exception as e:
        print("get_bw() ERROR:", repr(e))

    attrs = getattr(room.octave_bands, "__dict__", {})
    print("__dict__:")
    pprint.pp(attrs)

    print()
    print("Candidate frequency-related attributes/methods:")
    names = sorted(
        n for n in dir(room.octave_bands)
        if any(key in n.lower() for key in ("freq", "center", "band", "base", "bw"))
    )
    for name in names:
        try:
            obj = getattr(room.octave_bands, name)
            if callable(obj):
                try:
                    value = obj()
                except TypeError:
                    value = "<call requires arguments>"
                except Exception as e:
                    value = f"<call error: {e!r}>"
            else:
                value = obj
            print(f"{name}: {value}")
        except Exception as e:
            print(f"{name}: <attribute error: {e!r}>")

def test_add_source_gate():
    print()
    print("=== Test A: Room.add_source custom directivity ===")
    room = make_room()
    dummy = DummySevenBandDirectivity(7)
    print("sources_before:", len(room.sources))
    try:
        ret = room.add_source(SOURCE_POS, directivity=dummy)
        print("add_source_return:", ret)
    except Exception as e:
        print("add_source_exception:", repr(e))
        traceback.print_exc()
    print("sources_after:", len(room.sources))
    if room.sources:
        print("stored_directivity_type:", type(room.sources[-1].directivity))

def test_room_add_soundsource():
    print()
    print("=== Test B: Room.add(SoundSource) custom directivity ===")
    room = make_room()
    room.add_microphone(MIC_POS)
    dummy = DummySevenBandDirectivity(7)

    try:
        src = SoundSource(SOURCE_POS, directivity=dummy)
        print("SoundSource_constructed: True")
        print("SoundSource.directivity:", type(src.directivity))
    except Exception as e:
        print("SoundSource_constructed: False")
        print("SoundSource_exception:", repr(e))
        traceback.print_exc()
        return

    try:
        ret = room.add(src)
        print("room.add_return_type:", type(ret))
        print("sources_after_room_add:", len(room.sources))
        print("stored_directivity_type:", type(room.sources[-1].directivity))
    except Exception as e:
        print("room.add_exception:", repr(e))
        traceback.print_exc()
        return

    print()
    print("Attempting room.compute_rir() ...")
    try:
        room.compute_rir()
        print("compute_rir: SUCCESS")
        print("rir_outer_len:", len(room.rir))
        print("rir_mic0_sources:", len(room.rir[0]))
        print("rir_len:", len(room.rir[0][0]))
        print("rir_peak:", float(np.max(np.abs(room.rir[0][0]))))
        print("rir_energy:", float(np.sum(np.asarray(room.rir[0][0]) ** 2)))
    except Exception as e:
        print("compute_rir: FAILED")
        print("compute_rir_exception:", repr(e))
        traceback.print_exc()

def main():
    print("=== PRA custom multiband directivity contract probe ===")
    print("pyroomacoustics:", pra.__version__)
    print("fs:", FS)
    room = make_room()
    print_octave_info(room)
    test_add_source_gate()
    test_room_add_soundsource()

if __name__ == "__main__":
    main()
