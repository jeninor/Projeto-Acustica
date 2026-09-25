"""
Pyroomacoustics 0.10.1 custom Directivity / multiband contract probe v3.

This version deliberately DOES NOT call octave_bands.get_centers(),
because AntoniOctaveFilterBank in PRA 0.10.1 does not expose that method.

Goals
-----
1. Inspect the actual octave-band object without assumptions.
2. Verify how Room.add_source treats a generic Directivity subclass.
3. Verify the safe SoundSource path for a generic custom Directivity.
4. Test the ISM contract for:
     - scalar room + flat custom directivity
     - scalar room + 7-band custom directivity
     - explicit multiband room + flat custom directivity
     - explicit multiband room + 7-band custom directivity
5. Record the shapes PRA actually passes to get_response().

This is still only an API/contract probe. It does not yet use CF2 data.
"""

import inspect
import traceback

import numpy as np
import pyroomacoustics as pra

from pyroomacoustics.directivities import Directivity


ROOM_DIM = [6.0, 5.0, 3.0]
SOURCE_POS = [2.0, 2.5, 1.5]
MIC_POS = [3.0, 2.5, 1.2]
FS = 16000
MAX_ORDER = 2
ABSORPTION = 0.35

EXPLICIT_CENTER_FREQS_HZ = [
    125,
    250,
    500,
    1000,
    2000,
    4000,
    8000,
]


def banner(title):
    print()
    print("=" * 92)
    print(title)
    print("=" * 92)


class ProbeDirectivity(Directivity):
    def __init__(self, n_bands=1, label="probe"):
        super().__init__()
        self.n_bands = int(n_bands)
        self.label = str(label)
        self.calls = []

        if self.n_bands < 1:
            raise ValueError("n_bands must be >= 1")

        if self.n_bands == 1:
            self.band_gains = np.array([1.0], dtype=float)
        else:
            self.band_gains = np.linspace(
                1.0,
                0.4,
                self.n_bands,
                dtype=float,
            )

    @property
    def is_impulse_response(self):
        return False

    @property
    def filter_len_ir(self):
        return 1

    def sample_rays(self, n_rays):
        n_rays = int(n_rays)

        directions = np.zeros(
            (n_rays, 3),
            dtype=float,
        )
        directions[:, 0] = 1.0

        energies = np.ones(
            (n_rays, 1),
            dtype=float,
        )

        return directions, energies

    def get_response(
        self,
        azimuth,
        colatitude=None,
        magnitude=False,
        degrees=True,
    ):
        az = np.asarray(
            azimuth,
            dtype=float,
        )

        if colatitude is None:
            col = np.zeros_like(az)
        else:
            col = np.asarray(
                colatitude,
                dtype=float,
            )

        az, col = np.broadcast_arrays(
            az,
            col,
        )

        base_shape = az.shape

        if self.n_bands == 1:
            response = np.ones(
                base_shape,
                dtype=float,
            )
        else:
            response = np.broadcast_to(
                self.band_gains,
                base_shape + (self.n_bands,),
            ).copy()

        self.calls.append(
            {
                "azimuth_shape": az.shape,
                "colatitude_shape": col.shape,
                "response_shape": response.shape,
                "degrees": bool(degrees),
                "magnitude": bool(magnitude),
            }
        )

        return response


def inspect_octave_bands(room):
    ob = room.octave_bands

    print("octave_bands type:", type(ob))
    print("octave_bands repr:", repr(ob))

    names = [
        n for n in dir(ob)
        if (
            "band" in n.lower()
            or "freq" in n.lower()
            or "center" in n.lower()
            or "bw" in n.lower()
        )
    ]

    print("interesting attributes:")
    for name in names:
        try:
            value = getattr(ob, name)
        except Exception as e:
            print(f"  {name}: <error: {e}>")
            continue

        if callable(value):
            try:
                sig = inspect.signature(value)
            except Exception:
                sig = "<signature unavailable>"
            print(f"  {name}{sig}")
        else:
            try:
                arr = np.asarray(value)
                print(
                    f"  {name}: type={type(value).__name__} "
                    f"shape={getattr(arr, 'shape', None)} "
                    f"value={value}"
                )
            except Exception:
                print(
                    f"  {name}: type={type(value).__name__} "
                    f"value={value}"
                )

    print("__dict__:")
    try:
        for k, v in ob.__dict__.items():
            try:
                arr = np.asarray(v)
                shape = getattr(arr, "shape", None)
            except Exception:
                shape = None

            print(
                f"  {k}: type={type(v).__name__} "
                f"shape={shape} value={v}"
            )
    except Exception as e:
        print("  <unavailable>", e)

    if hasattr(ob, "get_bw"):
        try:
            bw = np.asarray(
                ob.get_bw(),
                dtype=float,
            )
            print(
                "get_bw():",
                bw,
                "shape=",
                bw.shape,
            )
        except Exception as e:
            print("get_bw() failed:", repr(e))


def scalar_material():
    return pra.Material(
        ABSORPTION
    )


def multiband_material():
    return pra.Material(
        energy_absorption={
            "coeffs": [
                ABSORPTION
            ] * len(
                EXPLICIT_CENTER_FREQS_HZ
            ),
            "center_freqs": (
                EXPLICIT_CENTER_FREQS_HZ
            ),
        }
    )


def make_room(material):
    room = pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=material,
        max_order=MAX_ORDER,
    )

    room.add_microphone(
        MIC_POS
    )

    return room


def add_custom_source_via_public_gate(room, directivity):
    before = len(
        room.sources
    )

    result = room.add_source(
        SOURCE_POS,
        directivity=directivity,
    )

    after = len(
        room.sources
    )

    return {
        "before": before,
        "after": after,
        "return_value": result,
    }


def add_custom_source_as_soundsource(room, directivity):
    src = pra.SoundSource(
        SOURCE_POS,
        directivity=directivity,
    )

    before = len(
        room.sources
    )

    result = room.add_source(
        src
    )

    after = len(
        room.sources
    )

    return {
        "source": src,
        "before": before,
        "after": after,
        "return_value": result,
    }


def summarize_rir(room):
    if len(room.rir) == 0:
        return {
            "n_mics_rir": 0,
            "n_sources_rir": 0,
        }

    rir = np.asarray(
        room.rir[0][0],
        dtype=float,
    )

    return {
        "n_mics_rir": len(room.rir),
        "n_sources_rir": len(room.rir[0]),
        "rir_len": len(rir),
        "rir_energy": float(
            np.sum(
                rir * rir
            )
        ),
        "rir_peak": float(
            np.max(
                np.abs(rir)
            )
        ),
    }


def run_gate_test():
    banner(
        "TYPE-GATE TEST — Room.add_source(position, directivity=generic Directivity)"
    )

    room = make_room(
        scalar_material()
    )

    d = ProbeDirectivity(
        n_bands=1,
        label="gate-test",
    )

    try:
        result = add_custom_source_via_public_gate(
            room,
            d,
        )

        print(
            "sources before:",
            result["before"],
        )
        print(
            "sources after:",
            result["after"],
        )
        print(
            "return type:",
            type(
                result["return_value"]
            ).__name__
            if result["return_value"] is not None
            else None,
        )

        if result["after"] == result["before"]:
            print(
                "RESULT: generic Directivity was NOT added by this call."
            )
        else:
            print(
                "RESULT: generic Directivity WAS added by this call."
            )

    except Exception as e:
        print(
            "CALL RAISED:",
            type(e).__name__,
            str(e),
        )
        traceback.print_exc()


def run_case(
    title,
    material,
    n_bands,
):
    banner(title)

    room = make_room(
        material
    )

    d = ProbeDirectivity(
        n_bands=n_bands,
        label=title,
    )

    print(
        "room.is_multi_band:",
        room.is_multi_band,
    )

    try:
        bw = np.asarray(
            room.octave_bands.get_bw(),
            dtype=float,
        )
        print(
            "room.octave_bands.get_bw shape:",
            bw.shape,
        )
        print(
            "room.octave_bands.get_bw:",
            bw,
        )
    except Exception as e:
        print(
            "get_bw unavailable:",
            repr(e),
        )

    try:
        add_result = add_custom_source_as_soundsource(
            room,
            d,
        )

        print(
            "SoundSource path sources before:",
            add_result["before"],
        )
        print(
            "SoundSource path sources after:",
            add_result["after"],
        )

        if add_result["after"] != 1:
            raise RuntimeError(
                "Custom SoundSource was not added to the room."
            )

        print(
            "stored directivity type:",
            type(
                room.sources[0].directivity
            ).__name__,
        )

        room.compute_rir()

        print(
            "compute_rir: SUCCESS"
        )

        info = summarize_rir(
            room
        )

        for k, v in info.items():
            print(
                f"{k}: {v}"
            )

        print(
            "get_response call count:",
            len(d.calls),
        )

        for i, call in enumerate(
            d.calls[:10],
            1,
        ):
            print(
                f"call {i}: "
                f"az={call['azimuth_shape']} "
                f"col={call['colatitude_shape']} "
                f"response={call['response_shape']} "
                f"degrees={call['degrees']}"
            )

        return {
            "success": True,
            "exception": None,
            "room_is_multi_band": room.is_multi_band,
            "calls": d.calls,
        }

    except Exception as e:
        print(
            "CASE FAILED"
        )
        print(
            "exception:",
            repr(e),
        )
        traceback.print_exc()

        print(
            "get_response call count before failure:",
            len(d.calls),
        )

        for i, call in enumerate(
            d.calls[:10],
            1,
        ):
            print(
                f"call {i}: "
                f"az={call['azimuth_shape']} "
                f"col={call['colatitude_shape']} "
                f"response={call['response_shape']} "
                f"degrees={call['degrees']}"
            )

        return {
            "success": False,
            "exception": (
                f"{type(e).__name__}: {e}"
            ),
            "room_is_multi_band": room.is_multi_band,
            "calls": d.calls,
        }


def main():
    print(
        "=== PRA custom Directivity subclass / multiband contract probe v3 ==="
    )
    print(
        "pyroomacoustics:",
        getattr(
            pra,
            "__version__",
            "unknown",
        ),
    )
    print(
        "Directivity abstract methods:",
        Directivity.__abstractmethods__,
    )
    print(
        "SoundSource signature:",
        inspect.signature(
            pra.SoundSource
        ),
    )

    banner(
        "PRECHECK — AntoniOctaveFilterBank actual API"
    )

    pre_room = make_room(
        scalar_material()
    )
    inspect_octave_bands(
        pre_room
    )

    banner(
        "PRECHECK — sample_rays contract"
    )

    pre_d = ProbeDirectivity(
        n_bands=1,
        label="precheck",
    )

    dirs, energies = pre_d.sample_rays(
        8
    )

    print(
        "instantiation: SUCCESS"
    )
    print(
        "sample_rays directions shape:",
        dirs.shape,
    )
    print(
        "sample_rays energies shape:",
        energies.shape,
    )

    run_gate_test()

    results = {}

    results["test1"] = run_case(
        "TEST 1 — scalar material + flat custom Directivity",
        scalar_material(),
        1,
    )

    results["test2"] = run_case(
        "TEST 2 — scalar material + 7-band custom Directivity",
        scalar_material(),
        7,
    )

    results["test3"] = run_case(
        "TEST 3 — explicit 7-band material + flat custom Directivity",
        multiband_material(),
        1,
    )

    results["test4"] = run_case(
        "TEST 4 — explicit 7-band material + 7-band custom Directivity",
        multiband_material(),
        7,
    )

    banner(
        "RESULT SUMMARY"
    )

    for name, result in results.items():
        status = (
            "SUCCESS"
            if result["success"]
            else "FAILED"
        )

        print(
            f"{name}: {status} | "
            f"is_multi_band={result['room_is_multi_band']} | "
            f"calls={len(result['calls'])} | "
            f"{result['exception'] or ''}"
        )

    banner(
        "INTERPRETATION TARGET"
    )

    print(
        "Most important result: TEST 4."
    )
    print(
        "If TEST 4 succeeds and get_response returns "
        "(n_images, 7), then the PRA 0.10.1 ISM multiband "
        "custom-directivity contract is experimentally confirmed."
    )
    print(
        "TEST 2 is expected to be incompatible or ambiguous because "
        "a scalar room has only one acoustic band while the custom "
        "directivity returns seven."
    )
    print(
        "The TYPE-GATE TEST tells us whether generic Directivity can be "
        "passed directly to Room.add_source(position, directivity=...), "
        "or whether we must wrap it in SoundSource."
    )


if __name__ == "__main__":
    main()
