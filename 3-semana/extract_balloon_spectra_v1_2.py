#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
extract_balloon_spectra_v1_2.py

Reconstructs numerical Balloon-spectra data from CLF Viewer PNG captures.

V1.2 strategy
-----------
1. Detect the 29 third-octave vertical grid lines automatically.
2. Detect the +10 / 0 / -10 / ... / -40 dB horizontal calibration.
3. Inside the graph ROI, isolate the thin dark curve.
4. Remove the fixed 0 dB baseline.
5. Use connected components to keep horizontally extended curve fragments.
6. Sample the curve at the nominal third-octave grid positions.
7. Convert y-pixel to dB by piecewise interpolation through the detected dB grid.
8. Export WIDE and/or LONG CSV plus optional overlay PNGs.

Supported capture geometries are detected automatically; no hardcoded x/y offsets
are required. This is useful for the observed 348x326 and 390x339 captures.

Important
---------
This is a reconstruction from rendered graphics, not a replacement for parsing
the original CF2 binary. Always preserve the source PNG/ROW-ZIP data and use
quality-control overlays before large-scale conversion.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import re
import statistics
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

try:
    import cv2
except Exception:
    cv2 = None

try:
    from scipy import ndimage as scipy_ndimage
except Exception:
    scipy_ndimage = None


# CLF Viewer visible vertical grid (29 lines).
#
# The rendered labels establish:
#   grid[2]  = 63 Hz
#   grid[5]  = 125 Hz
#   grid[8]  = 250 Hz
#   grid[11] = 500 Hz
#   grid[14] = 1 kHz
#   ...
# Therefore the 29 visible grid positions are 40 Hz ... 25 kHz.
# The final 25 kHz display-grid position has no corresponding slot in the
# 30-slot CF2 table (which ends at 20 kHz); it is preserved as Viewer data.
FREQS = [
    40, 50, 63, 80, 100, 125, 160, 200, 250, 315,
    400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500,
    3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000,
    20000, 25000,
]

STATE_RE = re.compile(
    r"(?P<model>.+)__x(?P<x>\d{3})__y(?P<y>\d{3})__balloon_spectra\.png$",
    re.IGNORECASE,
)


def freq_col_name(freq):
    if float(freq).is_integer():
        return f"db_{int(freq)}"
    return "db_" + str(freq).replace(".", "_")


def parse_state_name(name):
    m = STATE_RE.search(Path(name).name)
    if not m:
        return None
    return {
        "model": m.group("model"),
        "x_index": int(m.group("x")),
        "y_index": int(m.group("y")),
    }


def _connected_components(mask):
    """Return labels, component count; 8-connectivity."""
    u8 = mask.astype(np.uint8)

    if cv2 is not None:
        n, labels, stats, centroids = cv2.connectedComponentsWithStats(
            u8,
            connectivity=8,
        )
        return labels, n - 1, stats

    if scipy_ndimage is None:
        raise RuntimeError(
            "Need either opencv-python (cv2) or scipy for connected components."
        )

    labels, n = scipy_ndimage.label(
        u8,
        structure=np.ones((3, 3), dtype=np.uint8),
    )
    return labels, n, None


def detect_calibration(arr):
    """
    Detect:
      - 29 vertical third-octave grid lines
      - six horizontal dB calibration lines:
          +10, 0, -10, -20, -30, -40

    Works on the observed 348x326 and 390x339 CLF Viewer captures.
    """
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise ValueError("Expected RGB image")

    rgb = arr[:, :, :3]

    neutral = (
        (rgb[:, :, 0] == rgb[:, :, 1])
        & (rgb[:, :, 1] == rgb[:, :, 2])
    )
    v = rgb[:, :, 0]

    grid_gray = neutral & (v >= 180) & (v <= 220)

    # Vertical grid columns are ~266 px of neutral gray.
    x_counts = grid_gray.sum(axis=0)
    x_candidates = np.where(x_counts >= 180)[0]

    if len(x_candidates) != 29:
        # More tolerant fallback: select strong columns that form the near-regular
        # 29-point grid. The first pass is exact for our validated captures.
        strong = np.where(x_counts >= 150)[0]
        if len(strong) < 29:
            raise ValueError(
                f"Could not detect 29 vertical grid lines; found {len(strong)} strong columns"
            )

        # Keep the 29 strongest and sort geometrically.
        strongest = sorted(
            strong,
            key=lambda x: int(x_counts[x]),
            reverse=True,
        )[:29]
        x_candidates = np.array(sorted(strongest), dtype=int)

    x_grid = np.array(sorted(map(int, x_candidates)), dtype=int)

    x0, x1 = int(x_grid[0]), int(x_grid[-1])

    # ------------------------------------------------------------------
    # Horizontal dB calibration lines.
    #
    # Earlier V1 assumed exactly:
    #   5 gray rows + 1 black row (0 dB).
    #
    # That is too strict. Some CLF Viewer captures render -40 dB as black
    # as well, e.g.:
    #   gray = [27, 134, 187, 240]
    #   dark = [81, 293]
    #
    # Geometrically those are still the same six calibration lines:
    #   +10, 0, -10, -20, -30, -40 dB.
    #
    # Therefore detect FULL-WIDTH neutral horizontal lines independent of
    # whether Viewer rendered them gray or black, then choose the six-line
    # near-equally-spaced sequence.
    # ------------------------------------------------------------------
    graph_width = x1 - x0 + 1

    neutral_dark_or_gray = (
        neutral
        & (v <= 220)
    )

    neutral_row_counts = (
        neutral_dark_or_gray[:, x0:x1 + 1]
        .sum(axis=1)
    )

    # A calibration line spans almost the complete plot width. 70% keeps
    # this robust to curve/text intersections while excluding ordinary text.
    min_full_span = max(
        40,
        int(round(graph_width * 0.70)),
    )

    candidate_rows = list(
        map(
            int,
            np.where(
                neutral_row_counts >= min_full_span
            )[0],
        )
    )

    # Collapse adjacent pixels of a thick/anti-aliased horizontal line.
    clusters = []
    for y in candidate_rows:
        if not clusters or y > clusters[-1][-1] + 1:
            clusters.append([y])
        else:
            clusters[-1].append(y)

    line_rows = [
        int(
            max(
                cluster,
                key=lambda yy: int(
                    neutral_row_counts[yy]
                ),
            )
        )
        for cluster in clusters
    ]

    def six_line_score(rows6):
        rows6 = np.asarray(rows6, dtype=float)
        gaps = np.diff(rows6)

        if len(gaps) != 5 or np.any(gaps <= 0):
            return float("inf")

        mean_gap = float(np.mean(gaps))

        # The observed Viewer geometry is about 53 px / 10 dB.
        # Reject obviously unrelated six-row combinations.
        if not (20.0 <= mean_gap <= 90.0):
            return float("inf")

        spacing_error = float(
            np.mean(
                np.abs(
                    gaps - mean_gap
                )
            )
        )

        # Prefer sequences whose total span is very close to 5 equal gaps.
        span_error = abs(
            float(
                rows6[-1] - rows6[0]
            )
            -
            5.0 * mean_gap
        )

        return spacing_error + 0.05 * span_error

    if len(line_rows) < 6:
        raise ValueError(
            "Could not detect six horizontal calibration lines: "
            f"candidates={line_rows}"
        )

    best_rows = None
    best_score = float("inf")

    # Usually there are exactly six rows. The small exhaustive search also
    # handles an occasional extra full-width line without assuming its color.
    import itertools

    for combo in itertools.combinations(line_rows, 6):
        score = six_line_score(combo)

        if score < best_score:
            best_score = score
            best_rows = combo

    if best_rows is None or not np.isfinite(best_score):
        raise ValueError(
            "Could not identify regular six-line dB calibration from "
            f"candidates={line_rows}"
        )

    y_grid = np.array(
        best_rows,
        dtype=float,
    )
    db_grid = np.array(
        [10.0, 0.0, -10.0, -20.0, -30.0, -40.0],
        dtype=float,
    )

    if len(y_grid) != 6 or not np.all(np.diff(y_grid) > 0):
        raise ValueError(f"Unexpected dB grid geometry: {y_grid.tolist()}")

    return {
        "x_grid": x_grid,
        "y_grid": y_grid,
        "db_grid": db_grid,
        "x_left": x0,
        "x_right": x1,
        "y_top": int(y_grid[0]),
        "y_zero": int(y_grid[1]),
        "y_bottom": int(y_grid[-1]),
    }


def y_to_db(y, cal):
    return float(
        np.interp(
            float(y),
            cal["y_grid"],
            cal["db_grid"],
        )
    )


def extract_curve_mask(arr, cal, dark_threshold=80, min_component_span=3):
    """
    Identify curve fragments after removing the fixed 0-dB baseline.

    CLF Viewer uses a thin black curve, while the regular grid is light gray.
    The small orientation graphic is mostly gray/red/blue and normally does not
    survive the near-black threshold.
    """
    x0 = cal["x_left"]
    x1 = cal["x_right"]
    yt = cal["y_top"]
    yb = cal["y_bottom"]
    yz = cal["y_zero"]

    roi = arr[yt:yb + 1, x0:x1 + 1, :3]

    dark = np.max(roi, axis=2) <= int(dark_threshold)

    # Remove ALL six fixed horizontal calibration baselines (+10, 0, -10,
    # -20, -30, -40 dB) when they happen to be dark. Most are gray in the
    # Viewer and never enter `dark`, but some files render the bottom -40 dB
    # baseline black. If it is not removed, the extractor can misread the
    # entire -40 dB axis as an acoustic curve (especially for files whose
    # true rear directivity is below the visible plot range).
    #
    # If a real curve is exactly coincident with one of these fixed lines,
    # the raster image cannot independently distinguish the two. Treating
    # that point as missing is scientifically safer than inventing a value.
    for y_abs in cal["y_grid"]:
        y_rel = int(round(float(y_abs))) - yt
        lo = max(0, y_rel - 1)
        hi = min(dark.shape[0], y_rel + 2)
        dark[lo:hi, :] = False

    labels, n, stats = _connected_components(dark)

    keep = np.zeros_like(dark, dtype=bool)
    components = []

    for label_id in range(1, n + 1):
        if stats is not None:
            left = int(stats[label_id, cv2.CC_STAT_LEFT])
            top = int(stats[label_id, cv2.CC_STAT_TOP])
            width = int(stats[label_id, cv2.CC_STAT_WIDTH])
            height = int(stats[label_id, cv2.CC_STAT_HEIGHT])
            area = int(stats[label_id, cv2.CC_STAT_AREA])
            right = left + width - 1
            bottom = top + height - 1
            span = width
        else:
            yy, xx = np.where(labels == label_id)
            if len(xx) == 0:
                continue
            left, right = int(xx.min()), int(xx.max())
            top, bottom = int(yy.min()), int(yy.max())
            span = right - left + 1
            area = int(len(xx))

        if span >= int(min_component_span) and area >= 3:
            keep[labels == label_id] = True
            components.append({
                "label": label_id,
                "area": area,
                "x_min": left + x0,
                "x_max": right + x0,
                "span": span,
                "y_min": top + yt,
                "y_max": bottom + yt,
            })

    components.sort(
        key=lambda c: (c["span"], c["area"]),
        reverse=True,
    )

    return keep, components


def sample_band(keep_mask, x_abs, cal, max_dx=2):
    """
    Sample nearest retained curve pixels around one nominal band x position.

    Returns (y, confidence, qc_flag).
    """
    x0 = cal["x_left"]
    yt = cal["y_top"]

    for dx in range(0, int(max_dx) + 1):
        cols = [x_abs] if dx == 0 else [x_abs - dx, x_abs + dx]
        ys = []

        for xx_abs in cols:
            xx = xx_abs - x0
            if 0 <= xx < keep_mask.shape[1]:
                y_rel = np.where(keep_mask[:, xx])[0]
                ys.extend((y_rel + yt).astype(int).tolist())

        if ys:
            y = float(np.median(ys))
            confidence = {
                0: 1.00,
                1: 0.95,
                2: 0.90,
            }.get(dx, max(0.5, 1.0 - 0.05 * dx))
            return y, confidence, "ok"

    return None, 0.0, "no_curve_pixel"


def extract_image(
    image,
    image_name,
    calibration_cache=None,
    dark_threshold=80,
    min_component_span=3,
):
    """
    Extract one capture to a structured result.
    """
    state = parse_state_name(image_name)
    if state is None:
        raise ValueError(f"Unrecognized capture filename: {image_name}")

    arr = np.asarray(image.convert("RGB"))

    size_key = tuple(image.size)

    if calibration_cache is not None and size_key in calibration_cache:
        cal = calibration_cache[size_key]
    else:
        cal = detect_calibration(arr)
        if calibration_cache is not None:
            calibration_cache[size_key] = cal

    keep, components = extract_curve_mask(
        arr,
        cal,
        dark_threshold=dark_threshold,
        min_component_span=min_component_span,
    )

    if components:
        min_curve_x = min(c["x_min"] for c in components)
        max_curve_x = max(c["x_max"] for c in components)
    else:
        min_curve_x = None
        max_curve_x = None

    values = []

    for viewer_grid_index, (freq, x_abs) in enumerate(zip(FREQS, cal["x_grid"])):
        # Do not mistake the fixed grid/baseline for curve outside the actual
        # horizontal span of retained curve components.
        if (
            min_curve_x is None
            or int(x_abs) < min_curve_x - 2
            or int(x_abs) > max_curve_x + 2
        ):
            values.append({
                "viewer_grid_index": viewer_grid_index,
                "viewer_grid_hz": freq,
                "frequency_hz": freq,
                "value_db": None,
                "y_pixel": None,
                "confidence": 0.0,
                "qc_flag": "outside_curve_span",
            })
            continue

        y, conf, flag = sample_band(
            keep,
            int(x_abs),
            cal,
            max_dx=2,
        )

        if y is None:
            values.append({
                "viewer_grid_index": viewer_grid_index,
                "viewer_grid_hz": freq,
                "frequency_hz": freq,
                "value_db": None,
                "y_pixel": None,
                "confidence": conf,
                "qc_flag": flag,
            })
        else:
            values.append({
                "viewer_grid_index": viewer_grid_index,
                "viewer_grid_hz": freq,
                "frequency_hz": freq,
                "value_db": y_to_db(y, cal),
                "y_pixel": y,
                "confidence": conf,
                "qc_flag": flag,
            })

    detected = [v for v in values if v["value_db"] is not None]

    qc_flags = []
    if not components:
        qc_flags.append("no_curve_component")
    if detected and len(detected) < 4:
        qc_flags.append("very_few_detected_bands")

    result = {
        **state,
        "azimuth_deg": state["x_index"] * 5,
        "colatitude_deg": state["y_index"] * 5,
        "width": image.size[0],
        "height": image.size[1],
        "grid_x_left": cal["x_left"],
        "grid_x_right": cal["x_right"],
        "grid_y_top": cal["y_top"],
        "grid_y_zero": cal["y_zero"],
        "grid_y_bottom": cal["y_bottom"],
        "component_count": len(components),
        "detected_bands": len(detected),
        "min_detected_hz": min((v["frequency_hz"] for v in detected), default=None),
        "max_detected_hz": max((v["frequency_hz"] for v in detected), default=None),
        "mean_confidence": (
            statistics.mean(v["confidence"] for v in detected)
            if detected else 0.0
        ),
        "qc_flags": ";".join(qc_flags),
        "values": values,
        "_calibration": cal,
        "_curve_mask": keep,
        "_components": components,
    }

    return result


def make_overlay(image, result, output_path):
    """
    Save an inspection overlay:
      - small circles at reconstructed nominal-band samples
      - dB labels for detected points
    """
    im = image.convert("RGB").copy()
    draw = ImageDraw.Draw(im)

    cal = result["_calibration"]

    # Draw graph bounds.
    draw.rectangle(
        [
            (cal["x_left"], cal["y_top"]),
            (cal["x_right"], cal["y_bottom"]),
        ],
        outline=(0, 120, 255),
        width=1,
    )

    for freq, x_abs, value in zip(
        FREQS,
        cal["x_grid"],
        result["values"],
    ):
        if value["y_pixel"] is None:
            continue

        x = int(x_abs)
        y = int(round(value["y_pixel"]))

        r = 2
        draw.ellipse(
            (x - r, y - r, x + r, y + r),
            outline=(255, 0, 0),
            width=1,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(output_path, "PNG", compress_level=1)


def iter_image_dir(image_dir):
    for p in sorted(Path(image_dir).rglob("*__x???__y???__balloon_spectra.png")):
        yield {
            "name": p.name,
            "open": lambda p=p: Image.open(p),
            "source": str(p),
        }


def iter_model_storage(model_dir):
    """
    Read one model directory in either:
      captures/*.png
      capture_rows/*.zip

    ROW-ZIP wins over legacy for duplicate states.
    """
    model_dir = Path(model_dir)
    records = {}

    cap = model_dir / "captures"
    if cap.is_dir():
        for p in cap.glob("*.png"):
            st = parse_state_name(p.name)
            if st is not None:
                key = (st["x_index"], st["y_index"])
                records.setdefault(
                    key,
                    {
                        "kind": "file",
                        "path": p,
                        "name": p.name,
                    },
                )

    rowdir = model_dir / "capture_rows"
    if rowdir.is_dir():
        for zp in sorted(rowdir.glob("*.zip")):
            with zipfile.ZipFile(zp, "r") as zf:
                for zi in zf.infolist():
                    if zi.is_dir() or not zi.filename.lower().endswith(".png"):
                        continue
                    st = parse_state_name(zi.filename)
                    if st is None:
                        continue
                    key = (st["x_index"], st["y_index"])
                    records[key] = {
                        "kind": "zip",
                        "path": zp,
                        "member": zi.filename,
                        "name": Path(zi.filename).name,
                    }

    # Open ZIPs lazily but one at a time while walking sorted states.
    open_zip_path = None
    open_zip = None

    try:
        for key in sorted(records, key=lambda k: (k[1], k[0])):
            rec = records[key]

            if rec["kind"] == "file":
                image = Image.open(rec["path"])
                yield rec["name"], image, str(rec["path"])
                image.close()
            else:
                if open_zip_path != rec["path"]:
                    if open_zip is not None:
                        open_zip.close()
                    open_zip_path = rec["path"]
                    open_zip = zipfile.ZipFile(open_zip_path, "r")

                data = open_zip.read(rec["member"])
                image = Image.open(io.BytesIO(data))
                yield rec["name"], image, f"{rec['path']}!{rec['member']}"
                image.close()
    finally:
        if open_zip is not None:
            open_zip.close()


def write_model_csvs(model, results, output_dir, mode="wide"):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    wide_path = output_dir / f"{model}__spectra_wide.csv"
    long_path = output_dir / f"{model}__spectra_long.csv"

    base_fields = [
        "model",
        "x_index",
        "y_index",
        "azimuth_deg",
        "colatitude_deg",
        "width",
        "height",
        "detected_bands",
        "min_detected_hz",
        "max_detected_hz",
        "mean_confidence",
        "qc_flags",
    ]

    if mode in ("wide", "both"):
        fields = base_fields + [freq_col_name(f) for f in FREQS]

        with wide_path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()

            for result in results:
                row = {k: result.get(k) for k in base_fields}
                for value in result["values"]:
                    row[freq_col_name(value["frequency_hz"])] = (
                        ""
                        if value["value_db"] is None
                        else round(value["value_db"], 4)
                    )
                w.writerow(row)

    if mode in ("long", "both"):
        fields = base_fields + [
            "viewer_grid_index",
            "viewer_grid_hz",
            "frequency_hz",
            "value_db",
            "y_pixel",
            "confidence",
            "point_qc_flag",
        ]

        with long_path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()

            for result in results:
                base = {k: result.get(k) for k in base_fields}

                for value in result["values"]:
                    row = dict(base)
                    row.update({
                        "viewer_grid_index": value["viewer_grid_index"],
                        "viewer_grid_hz": value["viewer_grid_hz"],
                        "frequency_hz": value["frequency_hz"],
                        "value_db": (
                            ""
                            if value["value_db"] is None
                            else round(value["value_db"], 4)
                        ),
                        "y_pixel": (
                            ""
                            if value["y_pixel"] is None
                            else round(value["y_pixel"], 3)
                        ),
                        "confidence": round(value["confidence"], 3),
                        "point_qc_flag": value["qc_flag"],
                    })
                    w.writerow(row)

    return (
        wide_path if mode in ("wide", "both") else None,
        long_path if mode in ("long", "both") else None,
    )


def process_image_dir(args):
    cache = {}
    grouped = defaultdict(list)

    overlay_dir = Path(args.output_dir) / "overlays"

    for rec in iter_image_dir(args.image_dir):
        with rec["open"]() as im:
            result = extract_image(
                im,
                rec["name"],
                calibration_cache=cache,
                dark_threshold=args.dark_threshold,
                min_component_span=args.min_component_span,
            )

            grouped[result["model"]].append(result)

            if args.overlays:
                make_overlay(
                    im,
                    result,
                    overlay_dir / result["model"] / rec["name"],
                )

    output_paths = []

    for model, results in grouped.items():
        results.sort(key=lambda r: (r["y_index"], r["x_index"]))
        paths = write_model_csvs(
            model,
            results,
            Path(args.output_dir) / model,
            mode=args.format,
        )
        output_paths.extend(p for p in paths if p is not None)

    return output_paths


def process_dataset_root(args):
    root = Path(args.dataset_root)

    if args.models:
        requested = {m.strip() for m in args.models.split(",") if m.strip()}
        model_dirs = [
            root / m for m in sorted(requested)
            if (root / m).is_dir()
        ]
    else:
        model_dirs = sorted(
            p.parent for p in root.rglob("_COMPLETE.txt")
        )

    for i, model_dir in enumerate(model_dirs, 1):
        model = model_dir.name
        cache = {}
        results = []

        print(f"[{i}/{len(model_dirs)}] {model}")

        for name, image, source in iter_model_storage(model_dir):
            result = extract_image(
                image,
                name,
                calibration_cache=cache,
                dark_threshold=args.dark_threshold,
                min_component_span=args.min_component_span,
            )
            results.append(result)

        results.sort(key=lambda r: (r["y_index"], r["x_index"]))

        write_model_csvs(
            model,
            results,
            Path(args.output_dir) / model,
            mode=args.format,
        )


def main():
    ap = argparse.ArgumentParser()

    src = ap.add_mutually_exclusive_group(required=True)

    src.add_argument(
        "--image-dir",
        type=Path,
        help="Directory containing unpacked Balloon PNG captures.",
    )

    src.add_argument(
        "--dataset-root",
        type=Path,
        help="Root containing model dirs with captures/ or capture_rows/.",
    )

    ap.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--models",
        default="",
        help="Dataset-root mode: comma-separated model names; empty = all.",
    )

    ap.add_argument(
        "--format",
        choices=["wide", "long", "both"],
        default="wide",
    )

    ap.add_argument(
        "--overlays",
        action="store_true",
        help="Image-dir mode: save reconstruction overlays.",
    )

    ap.add_argument(
        "--dark-threshold",
        type=int,
        default=80,
    )

    ap.add_argument(
        "--min-component-span",
        type=int,
        default=3,
    )

    args = ap.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.image_dir is not None:
        paths = process_image_dir(args)
        print("Created:")
        for p in paths:
            print(f"  {p}")
    else:
        process_dataset_root(args)


if __name__ == "__main__":
    main()
