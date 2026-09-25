
import traceback
import numpy as np
import pyroomacoustics as pra

from pyroomacoustics.directivities import Directivity
from pyroomacoustics.soundsource import SoundSource


OCTAVE_CENTERS = np.array(
    [125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0],
    dtype=float,
)

SOURCE = np.array([2.0, 2.5, 1.5], dtype=float)
MIC = np.array([3.0, 2.5, 1.5], dtype=float)

BAND_GAINS = np.array(
    [0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85],
    dtype=float,
)


class ProbeDirectivity(Directivity):
    def __init__(self, mode):
        if mode not in {"flat", "multiband"}:
            raise ValueError(mode)
        self.mode = mode

    @property
    def is_impulse_response(self):
        return False

    @property
    def filter_len_ir(self):
        return 1

    def set_orientation(self, orientation):
        self.orientation = orientation
        return self

    def get_response(
        self,
        azimuth,
        colatitude=None,
        magnitude=False,
        degrees=True,
    ):
        az = np.asarray(azimuth, dtype=float)

        if self.mode == "flat":
            return np.full(az.shape, 0.5, dtype=float)

        if az.ndim == 0:
            return BAND_GAINS.copy()

        return np.broadcast_to(
            BAND_GAINS,
            az.shape + (len(BAND_GAINS),),
        ).copy()

    def sample_rays(self, n_rays):
        n = int(n_rays)
        if n <= 0:
            raise ValueError("n_rays must be > 0")

        i = np.arange(n, dtype=float)
        z = 1.0 - 2.0 * (i + 0.5) / n
        golden_angle = np.pi * (3.0 - np.sqrt(5.0))
        phi = golden_angle * i
        radius = np.sqrt(np.maximum(0.0, 1.0 - z * z))

        directions = np.column_stack(
            (
                radius * np.cos(phi),
                radius * np.sin(phi),
                z,
            )
        )

        energies = np.ones((n, 1), dtype=float)
        return directions, energies


def make_scalar_material():
    return pra.Material(0.35)


def make_multiband_material():
    return pra.Material(
        energy_absorption={
            "coeffs": [0.35] * len(OCTAVE_CENTERS),
            "center_freqs": OCTAVE_CENTERS.tolist(),
        }
    )


def make_room(material):
    return pra.ShoeBox(
        [6.0, 5.0, 3.0],
        fs=16000,
        materials=material,
        max_order=2,
    )


def insert_custom_source(room, directivity):
    source = SoundSource(
        SOURCE,
        directivity=directivity,
    )

    room.add(source)

    if len(room.sources) != 1:
        raise RuntimeError(
            f"Expected exactly one source after room.add(); got {len(room.sources)}"
        )

    if room.sources[0].directivity is not directivity:
        raise RuntimeError(
            "Custom directivity object was not preserved on the SoundSource."
        )


def summarize_rir(room):
    rir = np.asarray(room.rir[0][0], dtype=float)
    return {
        "len": int(rir.size),
        "energy": float(np.sum(rir * rir)),
        "peak": float(np.max(np.abs(rir))) if rir.size else 0.0,
        "finite": bool(np.all(np.isfinite(rir))),
    }


def probe_directivity_object(mode):
    d = ProbeDirectivity(mode)

    print("directivity instantiated:", type(d).__name__)
    print("Directivity.__abstractmethods__:", getattr(Directivity, "__abstractmethods__", None))
    print("ProbeDirectivity.__abstractmethods__:", getattr(ProbeDirectivity, "__abstractmethods__", None))
    print("is_impulse_response:", d.is_impulse_response)

    az = np.array([0.0, 0.5, 1.0])
    col = np.array([np.pi / 2.0] * 3)

    response = np.asarray(
        d.get_response(
            azimuth=az,
            colatitude=col,
            degrees=False,
        )
    )

    print("direct get_response shape:", response.shape)
    print("direct get_response:")
    print(response)

    return d


def run_case(title, material_factory, mode):
    print()
    print("=" * 88)
    print(title)
    print("=" * 88)

    try:
        material = material_factory()
        room = make_room(material)

        print("room.is_multi_band:", room.is_multi_band)
        print(
            "octave centers:",
            np.asarray(room.octave_bands.get_centers(), dtype=float),
        )
        print(
            "octave n_bands:",
            len(room.octave_bands.get_bw()),
        )

        d = probe_directivity_object(mode)

        insert_custom_source(room, d)
        room.add_microphone(MIC)

        print("source insertion: OK")
        print("stored directivity type:", type(room.sources[0].directivity))
        print("stored mode:", room.sources[0].directivity.mode)

        print("calling room.compute_rir() ...")
        room.compute_rir()

        summary = summarize_rir(room)

        print("compute_rir: SUCCESS")
        print("rir_len:", summary["len"])
        print("rir_energy:", summary["energy"])
        print("rir_peak:", summary["peak"])
        print("rir_finite:", summary["finite"])

        return {
            "success": True,
            "room_is_multi_band": bool(room.is_multi_band),
            "summary": summary,
        }

    except Exception as e:
        print("CASE FAILED")
        print("exception:", repr(e))
        traceback.print_exc()

        return {
            "success": False,
            "exception_type": type(e).__name__,
            "exception": str(e),
        }


def main():
    print("=== PRA custom Directivity subclass / multiband contract probe v2 ===")
    print("pyroomacoustics:", getattr(pra, "__version__", "unknown"))
    print("Directivity abstract methods:", getattr(Directivity, "__abstractmethods__", None))

    print()
    print("=" * 88)
    print("PRECHECK — sample_rays contract")
    print("=" * 88)

    try:
        d = ProbeDirectivity("flat")
        dirs, energies = d.sample_rays(8)
        print("instantiation: SUCCESS")
        print("sample_rays directions shape:", np.asarray(dirs).shape)
        print("sample_rays energies shape:", np.asarray(energies).shape)
    except Exception as e:
        print("PRECHECK FAILED:", repr(e))
        traceback.print_exc()

    results = {}

    results["test1"] = run_case(
        "TEST 1 — scalar material + flat custom Directivity",
        make_scalar_material,
        "flat",
    )

    results["test2"] = run_case(
        "TEST 2 — scalar material + 7-band custom Directivity",
        make_scalar_material,
        "multiband",
    )

    print()
    print("=" * 88)
    print("BUILD EXPLICIT MULTIBAND MATERIAL")
    print("=" * 88)

    try:
        m = make_multiband_material()
        print("multiband material constructed: True")
        print("material:", m)
    except Exception as e:
        print("multiband material constructed: False")
        print("exception:", repr(e))
        traceback.print_exc()

    results["test3"] = run_case(
        "TEST 3 — explicit 7-band material + 7-band custom Directivity",
        make_multiband_material,
        "multiband",
    )

    print()
    print("=" * 88)
    print("RESULT SUMMARY")
    print("=" * 88)

    for key in ("test1", "test2", "test3"):
        r = results[key]
        print(
            f"{key}: "
            + ("SUCCESS" if r["success"] else "FAILED")
            + (
                f" | room.is_multi_band={r['room_is_multi_band']}"
                if r["success"]
                else f" | {r['exception_type']}: {r['exception']}"
            )
        )

    print()
    print("=" * 88)
    print("INTERPRETATION")
    print("=" * 88)

    if results["test1"]["success"]:
        print(
            "CONFIRMED: a generic custom Directivity can reach the ISM when "
            "inserted via SoundSource + room.add()."
        )
    else:
        print(
            "NOT YET CONFIRMED: custom Directivity did not reach/complete ISM."
        )

    if (
        results["test1"]["success"]
        and not results["test2"]["success"]
        and results["test3"]["success"]
    ):
        print(
            "STRONG RESULT: octave-band directivity requires an explicitly "
            "multiband room in this PRA 0.10.1 path."
        )
    elif results["test2"]["success"]:
        print(
            "SURPRISING RESULT: a 7-band directivity response was accepted "
            "even in a room reporting is_multi_band=False."
        )
    elif not results["test3"]["success"]:
        print(
            "The 7-band contract is still unresolved; inspect TEST 3 traceback."
        )


if __name__ == "__main__":
    main()
