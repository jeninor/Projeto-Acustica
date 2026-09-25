"""
Targeted inspection of Pyroomacoustics 0.10.1 measured-directivity path.

Prints exact source ranges around:
- MeasuredDirectivity implementation
- room.py special handling of MeasuredDirectivity
- RIR builder source/microphone directivity calls

Read-only; no simulation changes.
"""

from pathlib import Path
import pyroomacoustics as pra
import pyroomacoustics.directivities.measured as measured


def print_range(path, start, end, title):
    path = Path(path)
    lines = path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()

    print()
    print("=" * 100)
    print(title)
    print(f"{path}:{start}-{end}")
    print("=" * 100)

    for n in range(start, min(end, len(lines)) + 1):
        print(f"{n:5d}: {lines[n-1]}")


def find_and_context(path, needles, radius=15):
    path = Path(path)
    lines = path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()

    for needle in needles:
        print()
        print("#" * 100)
        print(f"SEARCH: {needle}")
        print("#" * 100)

        found = False
        for i, line in enumerate(lines, 1):
            if needle in line:
                found = True
                lo = max(1, i - radius)
                hi = min(len(lines), i + radius)
                print()
                print(f"--- {path}:{i} ---")
                for n in range(lo, hi + 1):
                    mark = ">>" if n == i else "  "
                    print(f"{mark}{n:5d}: {lines[n-1]}")

        if not found:
            print("NOT FOUND")


def main():
    package_root = Path(pra.__file__).resolve().parent
    measured_path = Path(measured.__file__).resolve()
    room_path = package_root / "room.py"

    print("pyroomacoustics:", getattr(pra, "__version__", "unknown"))
    print("package_root:", package_root)
    print("measured.py:", measured_path)
    print("room.py:", room_path)

    # Exact MeasuredDirectivity implementation.
    print_range(
        measured_path,
        136,
        280,
        "MeasuredDirectivity exact implementation",
    )

    # Exact loader/object creation path.
    print_range(
        measured_path,
        520,
        605,
        "MeasuredDirectivityFile object construction",
    )

    # Special room handling seen in previous scan.
    print_range(
        room_path,
        2050,
        2110,
        "room.py around MeasuredDirectivity handling",
    )

    print_range(
        room_path,
        2185,
        2240,
        "room.py around later MeasuredDirectivity handling",
    )

    # Find any directivity response call-sites across package.
    for py in package_root.rglob("*.py"):
        text = py.read_text(
            encoding="utf-8",
            errors="replace",
        )

        if (
            "src.directivity.get_response" in text
            or "mic_dir.get_response" in text
        ):
            find_and_context(
                py,
                [
                    "mic_dir.get_response",
                    "src.directivity.get_response",
                ],
                radius=18,
            )


if __name__ == "__main__":
    main()
