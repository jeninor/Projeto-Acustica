"""
CF2 / CLF coordinate transform helper.

Purpose
-------
Convert a world-space source -> receiver direction into the CLF/CF2
angular coordinates used by loudspeaker directivity balloons.

Local loudspeaker frame used here
---------------------------------
+X : loudspeaker forward / on-axis
+Y : loudspeaker left
+Z : loudspeaker top

CF2 angular coordinates
-----------------------
arc:
    0 deg   = front (+X)
    90 deg  = side/top/bottom plane
    180 deg = back (-X)

rotation:
    0 deg reference arc passes through the loudspeaker top (+Z).

The sign/handedness of increasing rotation is kept explicit because
that is the one convention we still want to validate against CLF Viewer.

Two candidates are therefore returned:
    rotation_top_to_left_deg
    rotation_top_to_right_deg

For a 5-degree CF2 grid, nearest indices are also returned.
"""

import argparse
import csv
import math
from pathlib import Path

import numpy as np


CF2_STEP_DEG = 5.0
CF2_N_ROTATION = 72   # 0..355
CF2_N_ARC = 37        # 0..180


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    if n == 0.0:
        raise ValueError("Zero-length vector cannot be normalized.")
    return v / n


def direction_from_azimuth_colatitude(
    azimuth_deg,
    colatitude_deg=90.0,
):
    """
    World-space direction vector.

    Convention:
      azimuth 0 deg, colatitude 90 deg -> +X
      azimuth 90 deg, colatitude 90 deg -> +Y
      colatitude 0 deg -> +Z
    """
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


def loudspeaker_basis(
    azimuth_deg=0.0,
    colatitude_deg=90.0,
    roll_deg=0.0,
):
    """
    Build an orthonormal local loudspeaker basis expressed in world axes.

    Returns
    -------
    forward, left, top : ndarray shape (3,)

    roll_deg:
        Positive roll rotates the cabinet's local left/top basis around
        the forward axis using a right-handed rotation.
    """
    az = math.radians(float(azimuth_deg))
    col = math.radians(float(colatitude_deg))

    forward = direction_from_azimuth_colatitude(
        azimuth_deg,
        colatitude_deg,
    )

    # Spherical azimuth tangent. For az=0 this is +Y, i.e. local left.
    left0 = np.array(
        [
            -math.sin(az),
            math.cos(az),
            0.0,
        ],
        dtype=float,
    )

    # For non-polar orientations this gives the cabinet top direction.
    # At colatitude=90, azimuth=0 -> +Z.
    top0 = np.array(
        [
            -math.cos(col) * math.cos(az),
            -math.cos(col) * math.sin(az),
            math.sin(col),
        ],
        dtype=float,
    )

    # At the poles azimuth is geometrically degenerate, but the formulas
    # still produce a usable orthonormal orientation tied to azimuth.
    left0 = _unit(left0)
    top0 = _unit(top0)

    # Apply cabinet roll around local +X/forward.
    roll = math.radians(float(roll_deg))

    left = (
        math.cos(roll) * left0
        + math.sin(roll) * top0
    )

    top = (
        -math.sin(roll) * left0
        + math.cos(roll) * top0
    )

    # Clean up accumulated floating-point error.
    forward = _unit(forward)
    left = _unit(left - np.dot(left, forward) * forward)
    top = _unit(np.cross(forward, left))

    return forward, left, top


def world_vector_to_local(
    world_vector,
    speaker_azimuth_deg=0.0,
    speaker_colatitude_deg=90.0,
    speaker_roll_deg=0.0,
):
    """
    Express a world-space vector in the loudspeaker's local XYZ frame.
    """
    d = _unit(world_vector)

    forward, left, top = loudspeaker_basis(
        speaker_azimuth_deg,
        speaker_colatitude_deg,
        speaker_roll_deg,
    )

    return np.array(
        [
            np.dot(d, forward),
            np.dot(d, left),
            np.dot(d, top),
        ],
        dtype=float,
    )


def local_vector_to_cf2_angles(local_vector):
    """
    Convert a local loudspeaker direction to CF2 arc + both rotation signs.

    Local axes:
      +X forward
      +Y left
      +Z top

    arc is unambiguous:
      arc = acos(x)

    Rotation zero is the reference arc through +Z.
    We intentionally report both possible handedness conventions:
      top -> left
      top -> right
    """
    x, y, z = _unit(local_vector)

    arc_deg = math.degrees(
        math.acos(
            float(
                np.clip(
                    x,
                    -1.0,
                    1.0,
                )
            )
        )
    )

    # At front/back all rotations represent the same physical point.
    transverse = math.hypot(y, z)

    if transverse < 1e-12:
        rot_left = 0.0
        rot_right = 0.0
    else:
        # Starting from top (+Z):
        # +90 deg points toward local +Y (left).
        rot_left = (
            math.degrees(
                math.atan2(y, z)
            )
            % 360.0
        )

        # Starting from top (+Z):
        # +90 deg points toward local -Y (right).
        rot_right = (
            math.degrees(
                math.atan2(-y, z)
            )
            % 360.0
        )

    return {
        "arc_deg": arc_deg,
        "rotation_top_to_left_deg": rot_left,
        "rotation_top_to_right_deg": rot_right,
    }


def nearest_cf2_grid(
    rotation_deg,
    arc_deg,
    step_deg=CF2_STEP_DEG,
):
    """
    Map continuous CF2 angles to the nearest native CF2 v2 5-degree grid.
    """
    rotation_grid_deg = (
        round(
            float(rotation_deg)
            / step_deg
        )
        * step_deg
    ) % 360.0

    arc_grid_deg = (
        round(
            float(arc_deg)
            / step_deg
        )
        * step_deg
    )

    arc_grid_deg = min(
        180.0,
        max(
            0.0,
            arc_grid_deg,
        ),
    )

    rotation_index = int(
        round(
            rotation_grid_deg
            / step_deg
        )
    ) % int(
        round(
            360.0
            / step_deg
        )
    )

    arc_index = int(
        round(
            arc_grid_deg
            / step_deg
        )
    )

    return {
        "rotation_grid_deg": rotation_grid_deg,
        "arc_grid_deg": arc_grid_deg,
        "rotation_index": rotation_index,
        "arc_index": arc_index,
    }


def world_point_to_cf2(
    source_position,
    receiver_position,
    speaker_azimuth_deg=0.0,
    speaker_colatitude_deg=90.0,
    speaker_roll_deg=0.0,
):
    """
    Full world point -> local loudspeaker -> CF2 transform.
    """
    source = np.asarray(
        source_position,
        dtype=float,
    )

    receiver = np.asarray(
        receiver_position,
        dtype=float,
    )

    ray = receiver - source
    distance = float(
        np.linalg.norm(ray)
    )

    if distance == 0.0:
        raise ValueError(
            "Receiver position equals source position."
        )

    local = world_vector_to_local(
        ray,
        speaker_azimuth_deg,
        speaker_colatitude_deg,
        speaker_roll_deg,
    )

    angles = local_vector_to_cf2_angles(
        local
    )

    left_grid = nearest_cf2_grid(
        angles[
            "rotation_top_to_left_deg"
        ],
        angles["arc_deg"],
    )

    right_grid = nearest_cf2_grid(
        angles[
            "rotation_top_to_right_deg"
        ],
        angles["arc_deg"],
    )

    return {
        "distance_m": distance,
        "local_x": float(local[0]),
        "local_y": float(local[1]),
        "local_z": float(local[2]),
        **angles,
        "left_rotation_grid_deg": (
            left_grid[
                "rotation_grid_deg"
            ]
        ),
        "left_arc_grid_deg": (
            left_grid[
                "arc_grid_deg"
            ]
        ),
        "left_rotation_index": (
            left_grid[
                "rotation_index"
            ]
        ),
        "left_arc_index": (
            left_grid[
                "arc_index"
            ]
        ),
        "right_rotation_grid_deg": (
            right_grid[
                "rotation_grid_deg"
            ]
        ),
        "right_arc_grid_deg": (
            right_grid[
                "arc_grid_deg"
            ]
        ),
        "right_rotation_index": (
            right_grid[
                "rotation_index"
            ]
        ),
        "right_arc_index": (
            right_grid[
                "arc_index"
            ]
        ),
    }


def print_cardinal_self_test():
    """
    Canonical local-frame checks independent of the room.
    """
    tests = {
        "FRONT +X": [1, 0, 0],
        "TOP   +Z": [0, 0, 1],
        "LEFT  +Y": [0, 1, 0],
        "RIGHT -Y": [0, -1, 0],
        "BOTTOM-Z": [0, 0, -1],
        "BACK  -X": [-1, 0, 0],
    }

    print(
        "=== Canonical CF2 local coordinate test ==="
    )

    print(
        "Local frame: +X front, +Y left, +Z top"
    )

    print(
        "Rotation 0 reference arc passes through +Z (top)"
    )

    for name, vec in tests.items():
        a = local_vector_to_cf2_angles(
            vec
        )

        print(
            f"{name:10s} -> "
            f"arc={a['arc_deg']:7.3f} | "
            f"rot(top->left)="
            f"{a['rotation_top_to_left_deg']:7.3f} | "
            f"rot(top->right)="
            f"{a['rotation_top_to_right_deg']:7.3f}"
        )


def current_mic_grid():
    xs = np.linspace(
        1.0,
        5.0,
        5,
    )

    ys = np.linspace(
        0.75,
        4.25,
        5,
    )

    z = 1.2

    result = []
    i = 0

    for row, y in enumerate(ys):
        for col, x in enumerate(xs):
            result.append(
                {
                    "mic_index": i,
                    "row": row,
                    "col": col,
                    "x": float(x),
                    "y": float(y),
                    "z": float(z),
                }
            )
            i += 1

    return result


def run_room_demo(
    speaker_azimuth_deg,
    speaker_colatitude_deg,
    speaker_roll_deg,
    output,
):
    source = np.array(
        [2.0, 2.5, 1.5],
        dtype=float,
    )

    rows = []

    for mic in current_mic_grid():
        mapped = world_point_to_cf2(
            source_position=source,
            receiver_position=[
                mic["x"],
                mic["y"],
                mic["z"],
            ],
            speaker_azimuth_deg=speaker_azimuth_deg,
            speaker_colatitude_deg=speaker_colatitude_deg,
            speaker_roll_deg=speaker_roll_deg,
        )

        rows.append(
            {
                "speaker_azimuth_deg": (
                    speaker_azimuth_deg
                ),
                "speaker_colatitude_deg": (
                    speaker_colatitude_deg
                ),
                "speaker_roll_deg": (
                    speaker_roll_deg
                ),
                **mic,
                **mapped,
            }
        )

    out = Path(output)
    out.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with out.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(
                rows[0].keys()
            ),
        )
        writer.writeheader()
        writer.writerows(
            rows
        )

    print(
        f"\nRoom mapping saved: {out}"
    )

    interesting = {
        10,
        11,
        12,
        16,
        21,
    }

    print(
        "\nSelected microphones:"
    )

    for r in rows:
        if r["mic_index"] in interesting:
            print(
                f"mic={r['mic_index']:2d} "
                f"xyz=({r['x']:.3f},"
                f"{r['y']:.3f},"
                f"{r['z']:.3f}) "
                f"local=({r['local_x']:+.4f},"
                f"{r['local_y']:+.4f},"
                f"{r['local_z']:+.4f}) "
                f"arc={r['arc_deg']:7.3f} "
                f"rotL={r['rotation_top_to_left_deg']:7.3f} "
                f"rotR={r['rotation_top_to_right_deg']:7.3f}"
            )


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--azimuth",
        type=float,
        default=0.0,
        help="Speaker world azimuth in degrees.",
    )

    ap.add_argument(
        "--colatitude",
        type=float,
        default=90.0,
        help="Speaker world colatitude in degrees.",
    )

    ap.add_argument(
        "--roll",
        type=float,
        default=0.0,
        help="Speaker cabinet roll in degrees.",
    )

    ap.add_argument(
        "--output",
        default=(
            "/app/results/"
            "cf2_coordinate_mapping.csv"
        ),
    )

    args = ap.parse_args()

    print_cardinal_self_test()

    print(
        "\n=== Current room / microphone mapping ==="
    )

    print(
        "speaker orientation:",
        f"az={args.azimuth} deg,",
        f"col={args.colatitude} deg,",
        f"roll={args.roll} deg",
    )

    run_room_demo(
        speaker_azimuth_deg=args.azimuth,
        speaker_colatitude_deg=args.colatitude,
        speaker_roll_deg=args.roll,
        output=args.output,
    )


if __name__ == "__main__":
    main()
