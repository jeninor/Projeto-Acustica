"""
Inspect only the pieces needed to choose the clean CF2 integration path
for Pyroomacoustics 0.10.1.

Read-only. No benchmark or package files are modified.

Checks:
1) CardioidFamily constructor / inheritance / response behavior.
2) Directivity base contract and is_impulse_response.
3) SoundSource constructor and whether it type-checks directivity.
4) Room.add generic path, to see whether a custom Directivity can bypass
   Room.add_source's CardioidFamily/MeasuredDirectivity restriction.
5) Exact octave-band configuration of the current Stage-1 room.
6) Shapes used by the ISM for a constant-absorption room.
"""

import inspect
from pathlib import Path

import numpy as np
import pyroomacoustics as pra
from pyroomacoustics import room as room_module
from pyroomacoustics import soundsource as soundsource_module
from pyroomacoustics.directivities import (
    CardioidFamily,
    Directivity,
    Cardioid,
    DirectionVector,
)


ROOM_DIM = [6.0, 5.0, 3.0]
FS = 16000
MAX_ORDER = 10
ABSORPTION = 0.35


def sig(obj):
    try:
        return str(inspect.signature(obj))
    except Exception as e:
        return f"<unavailable: {e}>"


def src(obj, limit=16000):
    try:
        return inspect.getsource(obj)[:limit]
    except Exception as e:
        return f"<unavailable: {e}>"


def print_obj(title, obj):
    print()
    print("=" * 100)
    print(title)
    print("=" * 100)
    print("object:", obj)
    print("signature:", sig(obj))
    try:
        print("MRO:", " -> ".join(c.__name__ for c in obj.__mro__))
    except Exception:
        pass
    print("source:")
    print(src(obj))


def safe_call(obj, name):
    if hasattr(obj, name):
        attr = getattr(obj, name)
        try:
            value = attr() if callable(attr) else attr
            print(f"{name}: {value}")
        except Exception as e:
            print(f"{name}: <error: {e}>")
    else:
        print(f"{name}: <not present>")


def inspect_room():
    print()
    print("=" * 100)
    print("CURRENT STAGE-1 ROOM BAND MODEL")
    print("=" * 100)

    room = pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=pra.Material(ABSORPTION),
        max_order=MAX_ORDER,
    )

    print("room type:", type(room))
    print("fs:", room.fs)
    print("is_multi_band:", getattr(room, "is_multi_band", "<missing>"))
    print("octave_bands type:", type(getattr(room, "octave_bands", None)))

    ob = getattr(room, "octave_bands", None)

    if ob is not None:
        for name in [
            "get_center_freqs",
            "get_bw",
            "n_bands",
            "centers",
            "center_freqs",
        ]:
            safe_call(ob, name)

        print("octave_bands __dict__:")
        try:
            for k, v in vars(ob).items():
                if isinstance(v, np.ndarray):
                    print(k, "shape=", v.shape, "value=", v)
                else:
                    print(k, "=", v)
        except Exception as e:
            print("<vars unavailable:", e, ">")

    print()
    print("room __dict__ selected:")
    for name in [
        "is_multi_band",
        "max_order",
        "air_absorption",
        "absorption",
        "materials",
    ]:
        if hasattr(room, name):
            value = getattr(room, name)
            if isinstance(value, np.ndarray):
                print(name, "shape=", value.shape, "value=", value)
            else:
                print(name, "=", value)

    # Compare with a cardioid room because that is our Stage-1 reference.
    cardioid = Cardioid(
        orientation=DirectionVector(
            azimuth=0.0,
            colatitude=90.0,
            degrees=True,
        ),
        gain=1.0,
    )

    room.add_source(
        [2.0, 2.5, 1.5],
        directivity=cardioid,
    )

    print()
    print("source directivity:")
    print("type:", type(room.sources[0].directivity))
    print(
        "is_impulse_response:",
        room.sources[0].directivity.is_impulse_response,
    )

    az = np.radians(
        np.array([0.0, 45.0, 90.0])
    )
    col = np.radians(
        np.array([90.0, 90.0, 90.0])
    )

    response = room.sources[0].directivity.get_response(
        azimuth=az,
        colatitude=col,
        degrees=False,
    )

    print(
        "Cardioid get_response test shape:",
        np.asarray(response).shape,
    )
    print(
        "Cardioid get_response test values:",
        response,
    )


def main():
    print("pyroomacoustics:", getattr(pra, "__version__", "unknown"))
    print("package:", Path(pra.__file__).resolve())

    print_obj(
        "Directivity class",
        Directivity,
    )

    print_obj(
        "Directivity.is_impulse_response",
        Directivity.is_impulse_response,
    )

    print_obj(
        "CardioidFamily class",
        CardioidFamily,
    )

    print_obj(
        "CardioidFamily.__init__",
        CardioidFamily.__init__,
    )

    print_obj(
        "CardioidFamily.get_response",
        CardioidFamily.get_response,
    )

    print_obj(
        "SoundSource class",
        soundsource_module.SoundSource,
    )

    print_obj(
        "SoundSource.__init__",
        soundsource_module.SoundSource.__init__,
    )

    print_obj(
        "Room.add",
        room_module.Room.add,
    )

    print_obj(
        "Room.add_source",
        room_module.Room.add_source,
    )

    inspect_room()


if __name__ == "__main__":
    main()
