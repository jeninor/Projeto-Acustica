#!/usr/bin/env python3
"""
Stage 2B — validate CF2 custom multiband Directivity with multiple image sources.

Reuses the already-tested implementation from:
    cf2_pra_directivity_smoke.py

No Docker rebuild is required.
"""

import argparse
import time

import numpy as np
import pyroomacoustics as pra

from cf2_pra_directivity_smoke import (
    BASE_OFFSET,
    CF2OctaveDirectivity,
    explicit_multiband_material,
)


ROOM_DIM = [6.0, 5.0, 3.0]
SOURCE_POS = np.array([2.0, 2.5, 1.5], dtype=float)
MIC_POS = np.array([4.0, 2.5, 1.2], dtype=float)
FS = 16000
ORDERS = [1, 3, 10]


def build_room(cf2_path, base_offset, max_order):
    room = pra.ShoeBox(
        ROOM_DIM,
        fs=FS,
        materials=explicit_multiband_material(),
        max_order=max_order,
    )

    directivity = CF2OctaveDirectivity(
        cf2_path,
        azimuth_deg=0.0,
        colatitude_deg=90.0,
        roll_deg=0.0,
        base_offset=base_offset,
    )

    src = pra.SoundSource(
        SOURCE_POS,
        directivity=directivity,
    )

    room.add_source(src)
    room.add_microphone(MIC_POS)

    return room, directivity


def validate_call(call, call_index):
    az_shape = tuple(call["azimuth_shape"])
    response_shape = tuple(call["response_shape"])

    if len(az_shape) != 1:
        raise RuntimeError(
            f"call {call_index}: expected 1-D azimuth input, got {az_shape}"
        )

    n_dirs = int(az_shape[0])
    expected_response = (7, n_dirs)

    if response_shape != expected_response:
        raise RuntimeError(
            f"call {call_index}: response shape {response_shape}, "
            f"expected {expected_response}"
        )

    return n_dirs


def run_case(cf2_path, base_offset, max_order):
    room, directivity = build_room(
        cf2_path,
        base_offset,
        max_order,
    )

    t0 = time.perf_counter()
    room.compute_rir()
    elapsed = time.perf_counter() - t0

    if not directivity.calls:
        raise RuntimeError(
            f"max_order={max_order}: get_response() was never called"
        )

    direction_counts = [
        validate_call(call, i)
        for i, call in enumerate(directivity.calls, 1)
    ]

    rir = np.asarray(room.rir[0][0], dtype=float)

    source = room.sources[0]
    images = getattr(source, "images", None)
    n_images = None

    if images is not None:
        images = np.asarray(images)
        if images.ndim == 2:
            n_images = int(images.shape[1])

    return {
        "elapsed_s": elapsed,
        "direction_counts": direction_counts,
        "n_images": n_images,
        "rir_len": int(len(rir)),
        "rir_energy": float(np.sum(rir * rir)),
        "rir_peak": float(np.max(np.abs(rir))),
        "calls": directivity.calls,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cf2", required=True)
    ap.add_argument("--base", type=int, default=BASE_OFFSET)
    args = ap.parse_args()

    print("=== Stage 2B: CF2 multi-image ISM contract ===")
    print("pyroomacoustics:", getattr(pra, "__version__", "unknown"))
    print("CF2:", args.cf2)
    print("source:", SOURCE_POS.tolist())
    print("mic:", MIC_POS.tolist())
    print("orders:", ORDERS)
    print()

    for order in ORDERS:
        result = run_case(
            args.cf2,
            args.base,
            order,
        )

        print(f"--- max_order={order} ---")
        print("compute_rir: SUCCESS")
        print("elapsed_s:", f"{result['elapsed_s']:.9f}")
        print("direction_counts:", result["direction_counts"])
        print("n_images_source_object:", result["n_images"])
        print("rir_len:", result["rir_len"])
        print("rir_energy:", f"{result['rir_energy']:.9e}")
        print("rir_peak:", f"{result['rir_peak']:.9e}")

        for i, call in enumerate(result["calls"], 1):
            print(
                f"call {i}: "
                f"az={call['azimuth_shape']} "
                f"response={call['response_shape']} "
                f"rot=[{call['rotation_min_deg']:.3f},"
                f"{call['rotation_max_deg']:.3f}] "
                f"arc=[{call['arc_min_deg']:.3f},"
                f"{call['arc_max_deg']:.3f}]"
            )

        print()

    print(
        "PASS: CF2 multiband response shape is valid "
        "for multiple image-source directions."
    )


if __name__ == "__main__":
    main()
