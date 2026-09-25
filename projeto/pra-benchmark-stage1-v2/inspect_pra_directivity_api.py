"""
Inspect the exact Pyroomacoustics 0.10.1 directivity API installed
inside the current Docker image.

No simulation is changed. This is a read-only introspection tool.

It prints:
- package/module paths
- available directivity classes
- constructor and method signatures
- source code for relevant Python methods when available
- package source locations where directivity.get_response(...) is called

Goal:
Determine whether a frequency-dependent CF2 directivity can be integrated
directly, via MeasuredDirectivity, or requires an adapter/conversion layer.
"""

import inspect
import re
from pathlib import Path

import pyroomacoustics as pra
import pyroomacoustics.directivities as dmod


TARGET_CLASS_NAMES = [
    "Directivity",
    "Cardioid",
    "FigureEight",
    "HyperCardioid",
    "SubCardioid",
    "Omnidirectional",
    "MeasuredDirectivity",
    "DirectionVector",
]

TARGET_METHODS = [
    "__init__",
    "get_response",
    "get_response_azimuth",
    "set_orientation",
    "sample_response",
    "plot_response",
]


def safe_signature(obj):
    try:
        return str(inspect.signature(obj))
    except Exception as e:
        return f"<signature unavailable: {e}>"


def safe_source(obj):
    try:
        return inspect.getsource(obj)
    except Exception as e:
        return f"<source unavailable: {e}>"


def print_class_info(name):
    cls = getattr(dmod, name, None)

    print()
    print("=" * 80)
    print(f"CLASS: {name}")
    print("=" * 80)

    if cls is None:
        print("NOT FOUND")
        return

    print("object:", cls)
    print("module:", getattr(cls, "__module__", None))
    print("signature:", safe_signature(cls))

    try:
        print("MRO:", " -> ".join(c.__name__ for c in cls.__mro__))
    except Exception:
        pass

    for method_name in TARGET_METHODS:
        if hasattr(cls, method_name):
            method = getattr(cls, method_name)

            print()
            print(f"--- {name}.{method_name} ---")
            print("signature:", safe_signature(method))

            doc = inspect.getdoc(method)
            if doc:
                print("doc:")
                print(doc[:3000])

            src = safe_source(method)
            print("source:")
            print(src[:12000])


def scan_package_for_patterns(package_root):
    patterns = [
        re.compile(r"\.get_response\s*\("),
        re.compile(r"directivit", re.IGNORECASE),
        re.compile(r"MeasuredDirectivity"),
    ]

    hits = []

    for path in sorted(package_root.rglob("*.py")):
        try:
            lines = path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()
        except Exception:
            continue

        for lineno, line in enumerate(lines, 1):
            if any(p.search(line) for p in patterns):
                hits.append((path, lineno, line.rstrip()))

    return hits


def print_context(path, lineno, radius=4):
    lines = path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()

    lo = max(1, lineno - radius)
    hi = min(len(lines), lineno + radius)

    print()
    print(f"--- {path}:{lineno} ---")

    for n in range(lo, hi + 1):
        prefix = ">>" if n == lineno else "  "
        print(f"{prefix} {n:5d}: {lines[n-1]}")


def main():
    print("=== Pyroomacoustics directivity API inspection ===")
    print("pyroomacoustics version:", getattr(pra, "__version__", "unknown"))
    print("pyroomacoustics package:", Path(pra.__file__).resolve())
    print("directivities module:", Path(dmod.__file__).resolve())

    print()
    print("=== Names exported by pyroomacoustics.directivities ===")
    names = [
        n for n in dir(dmod)
        if not n.startswith("_")
    ]
    print(", ".join(names))

    for name in TARGET_CLASS_NAMES:
        print_class_info(name)

    package_root = Path(pra.__file__).resolve().parent

    print()
    print("=" * 80)
    print("PACKAGE CALL-SITE SCAN")
    print("=" * 80)
    print("package_root:", package_root)

    hits = scan_package_for_patterns(package_root)

    print("number_of_hits:", len(hits))

    # First print a compact hit list.
    for path, lineno, line in hits[:200]:
        rel = path.relative_to(package_root)
        print(f"{rel}:{lineno}: {line}")

    # Then expand contexts only around actual get_response calls and
    # MeasuredDirectivity references, which are the most important.
    important = [
        h for h in hits
        if ".get_response(" in h[2]
        or "MeasuredDirectivity" in h[2]
    ]

    print()
    print("=" * 80)
    print("IMPORTANT CONTEXTS")
    print("=" * 80)

    for path, lineno, _ in important[:80]:
        print_context(path, lineno, radius=6)

    print()
    print("=" * 80)
    print("END")
    print("=" * 80)


if __name__ == "__main__":
    main()
