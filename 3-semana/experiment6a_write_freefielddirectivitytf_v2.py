#!/usr/bin/env python3
# -*- coding: utf-8 -*-

r"""
experiment6a_write_freefielddirectivitytf.py

EXPERIMENTO 6A
==============
Escribe y valida el primer SOFA estándar FreeFieldDirectivityTF v1.1
a partir de la representación compleja canónica de 2522 direcciones
generada por el Experimento 5G.

Diseño SOFA
-----------
Convención:
    FreeFieldDirectivityTF v1.1
    SOFA 2.1 / AES69-2022

Dimensiones:
    M = 1          una condición/medición
    R = 2522       direcciones receptoras únicas
    N = F          frecuencias CF2 declaradas

Datos:
    Data.Real[M,R,N]
    Data.Imag[M,R,N]
    N[N]                       frecuencias Hz

Geometría:
    SourcePosition = [0,0,0]   fuente en el origen
    ListenerPosition = [0,0,0]
    ReceiverPosition[R,3]      spherical:
        azimuth [deg]
        elevation [deg]
        radius [m]

IMPORTANTE:
- El radio de ReceiverPosition NO se toma del unit sphere del NPZ.
- Se toma de metadata.distance_m del CF2 original.
- direction_xyz del NPZ se usa solamente para la DIRECCIÓN.
- +X=Front, +Y=Left, +Z=Up.
- Azimuth SOFA:
      Front=0, Left=90, Back=180, Right=270.
- No se aplica sensibilidad EA ni normalización adicional.
- Data.Real/Data.Imag representan la ganancia compleja direccional
  reconstruida en 5G:
      H = 10^(magnitude_db/20) * exp(j*phase_rad)

Requisitos:
    Python >= 3.8
    numpy
    sofar >= 1.2.3

Instalación:
    python -m pip install "sofar==1.2.3"

Uso:
python experiment6a_write_freefielddirectivitytf.py ^
  --parser cf2_parser_v5.py ^
  --cf2 "speaker_cf2\AXYS4549.CF2" ^
  --npz "clfviewer_validation\experiment5g_complex2522\canonical_complex_example.npz" ^
  --output "clfviewer_validation\experiment6a_sofa_AXYS4549\AXYS4549_FreeFieldDirectivityTF.sofa"

Sólo preflight, sin necesitar sofar:
python experiment6a_write_freefielddirectivitytf.py ^
  --parser cf2_parser_v5.py ^
  --cf2 "speaker_cf2\AXYS4549.CF2" ^
  --npz "clfviewer_validation\experiment5g_complex2522\canonical_complex_example.npz" ^
  --output "clfviewer_validation\experiment6a_sofa_AXYS4549\AXYS4549_FreeFieldDirectivityTF.sofa" ^
  --preflight-only

Salidas:
    <output>.sofa
    <output_stem>_roundtrip_summary.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

import numpy as np


N_CANONICAL = 2522


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(
        module_name,
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            "No se pudo cargar modulo: {}".format(path)
        )

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    return module


def scalar_string(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).replace(
        microsecond=0
    ).isoformat()


def xyz_to_sofa_spherical(
    xyz: np.ndarray,
    radius_m: float,
) -> np.ndarray:
    """
    SOFA spherical:
        azimuth   0..360 deg
        elevation -90..90 deg
        radius    >0 m

    Axes:
        +X Front
        +Y Left
        +Z Up
    """
    xyz = np.asarray(
        xyz,
        dtype=float,
    )

    if xyz.shape != (
        N_CANONICAL,
        3,
    ):
        raise ValueError(
            "direction_xyz shape={}, expected ({},3)".format(
                xyz.shape,
                N_CANONICAL,
            )
        )

    norms = np.linalg.norm(
        xyz,
        axis=1,
    )

    if not np.all(
        np.isfinite(norms)
    ):
        raise ValueError(
            "direction_xyz contains non-finite vectors."
        )

    if np.max(
        np.abs(
            norms - 1.0
        )
    ) > 1e-10:
        raise ValueError(
            "direction_xyz is not a unit-vector grid."
        )

    x = xyz[:, 0]
    y = xyz[:, 1]
    z = xyz[:, 2]

    azimuth = (
        np.degrees(
            np.arctan2(
                y,
                x,
            )
        )
        % 360.0
    )

    elevation = np.degrees(
        np.arctan2(
            z,
            np.sqrt(
                x * x
                + y * y
            ),
        )
    )

    radius = np.full(
        N_CANONICAL,
        float(radius_m),
        dtype=float,
    )

    return np.column_stack(
        [
            azimuth,
            elevation,
            radius,
        ]
    )


def sofa_spherical_to_xyz(
    position: np.ndarray,
) -> np.ndarray:
    p = np.asarray(
        position,
        dtype=float,
    )

    if p.ndim == 3:
        p = np.squeeze(p)

    if p.shape != (
        N_CANONICAL,
        3,
    ):
        raise ValueError(
            "ReceiverPosition shape={} after squeeze".format(
                p.shape
            )
        )

    az = np.deg2rad(
        p[:, 0]
    )
    el = np.deg2rad(
        p[:, 1]
    )

    cos_el = np.cos(
        el
    )

    x = cos_el * np.cos(
        az
    )
    y = cos_el * np.sin(
        az
    )
    z = np.sin(
        el
    )

    return np.column_stack(
        [x, y, z]
    )


def canonical_mrn(
    array: np.ndarray,
    n_receivers: int,
    n_freq: int,
) -> np.ndarray:
    """
    Normalize Data.Real/Data.Imag read by sofar to [M,R,N].
    """
    a = np.asarray(
        array,
        dtype=float,
    )

    if a.shape == (
        1,
        n_receivers,
        n_freq,
    ):
        return a

    if a.shape == (
        n_receivers,
        n_freq,
    ):
        return a[
            None,
            :,
            :
        ]

    raise ValueError(
        "Unexpected MRN shape {}. Expected ({},{}) or (1,{},{})".format(
            a.shape,
            n_receivers,
            n_freq,
            n_receivers,
            n_freq,
        )
    )


def optional_delete(
    sofa,
    name: str,
) -> None:
    """
    Delete optional convention entries when present.
    Useful for musical-note metadata not applicable to loudspeakers.
    """
    if hasattr(
        sofa,
        name,
    ):
        try:
            sofa.delete(
                name
            )
        except Exception:
            # If a future convention changes the field to mandatory,
            # keep it and rely on sofar.verify().
            pass


def preflight(
    parser,
    cf2_path: Path,
    npz_path: Path,
    receiver_distance_override: float = None,
) -> Tuple[Dict[str, object], Dict[str, np.ndarray]]:
    cf2 = parser.CF2File(
        cf2_path
    )

    z = np.load(
        npz_path,
        allow_pickle=False,
    )

    required = [
        "frequencies_hz",
        "direction_xyz",
        "magnitude_db",
        "phase_rad",
        "H_real",
        "H_imag",
        "back_phase_resultant_R",
        "back_phase_quality_code",
    ]

    missing = [
        key
        for key in required
        if key not in z.files
    ]

    if missing:
        raise ValueError(
            "NPZ missing keys: {}".format(
                missing
            )
        )

    freq = np.asarray(
        z[
            "frequencies_hz"
        ],
        dtype=float,
    )

    xyz = np.asarray(
        z[
            "direction_xyz"
        ],
        dtype=float,
    )

    magnitude_db = np.asarray(
        z[
            "magnitude_db"
        ],
        dtype=float,
    )

    phase_rad = np.asarray(
        z[
            "phase_rad"
        ],
        dtype=float,
    )

    H_real = np.asarray(
        z[
            "H_real"
        ],
        dtype=float,
    )

    H_imag = np.asarray(
        z[
            "H_imag"
        ],
        dtype=float,
    )

    back_R = np.asarray(
        z[
            "back_phase_resultant_R"
        ],
        dtype=float,
    )

    back_quality = np.asarray(
        z[
            "back_phase_quality_code"
        ]
    )

    a = int(
        cf2.declared_min_idx
    )
    b = int(
        cf2.declared_max_idx
    )

    expected_freq = np.asarray(
        parser.CF2_FREQS[
            a:b + 1
        ],
        dtype=float,
    )

    if not np.array_equal(
        freq,
        expected_freq,
    ):
        raise ValueError(
            "NPZ frequencies do not match CF2 declared frequencies.\n"
            "NPZ={}\nCF2={}".format(
                freq.tolist(),
                expected_freq.tolist(),
            )
        )

    n_freq = int(
        freq.size
    )

    expected_shape = (
        n_freq,
        N_CANONICAL,
    )

    for name, arr in (
        (
            "magnitude_db",
            magnitude_db,
        ),
        (
            "phase_rad",
            phase_rad,
        ),
        (
            "H_real",
            H_real,
        ),
        (
            "H_imag",
            H_imag,
        ),
    ):
        if arr.shape != expected_shape:
            raise ValueError(
                "{} shape={}, expected={}".format(
                    name,
                    arr.shape,
                    expected_shape,
                )
            )

    if back_R.shape != (
        n_freq,
    ):
        raise ValueError(
            "back_phase_resultant_R shape={}".format(
                back_R.shape
            )
        )

    if back_quality.shape != (
        n_freq,
    ):
        raise ValueError(
            "back_phase_quality_code shape={}".format(
                back_quality.shape
            )
        )

    if xyz.shape != (
        N_CANONICAL,
        3,
    ):
        raise ValueError(
            "direction_xyz shape={}".format(
                xyz.shape
            )
        )

    if not (
        np.isfinite(
            freq
        ).all()
        and np.isfinite(
            xyz
        ).all()
        and np.isfinite(
            magnitude_db
        ).all()
        and np.isfinite(
            phase_rad
        ).all()
        and np.isfinite(
            H_real
        ).all()
        and np.isfinite(
            H_imag
        ).all()
    ):
        raise ValueError(
            "NPZ contains non-finite canonical data."
        )

    H = (
        H_real
        + 1j * H_imag
    )

    H_expected = (
        np.power(
            10.0,
            magnitude_db
            / 20.0,
        )
        * np.exp(
            1j
            * phase_rad
        )
    )

    complex_source_max_error = float(
        np.max(
            np.abs(
                H
                - H_expected
            )
        )
    )

    if complex_source_max_error > 1e-12:
        raise ValueError(
            "NPZ H differs from magnitude/phase reconstruction: {}".format(
                complex_source_max_error
            )
        )

    with np.errstate(
        divide="ignore",
        invalid="ignore",
    ):
        recovered_db = (
            20.0
            * np.log10(
                np.abs(
                    H
                )
            )
        )

    magnitude_source_max_error_db = float(
        np.max(
            np.abs(
                recovered_db
                - magnitude_db
            )
        )
    )

    metadata = cf2.metadata

    if receiver_distance_override is not None:
        distance_m = float(
            receiver_distance_override
        )
        distance_source = "command_line_override"
    else:
        distance_m = float(
            metadata.get(
                "distance_m",
                float("nan"),
            )
        )
        distance_source = "cf2_metadata.distance_m"

    if not (
        math.isfinite(
            distance_m
        )
        and distance_m > 0.0
    ):
        raise ValueError(
            "Invalid measurement distance {!r}. "
            "Use --receiver-distance-m with a positive value.".format(
                distance_m
            )
        )

    receiver_position = xyz_to_sofa_spherical(
        xyz,
        distance_m,
    )

    # Cardinal sanity checks.
    cardinals = {
        "Front": np.asarray(
            [1.0, 0.0, 0.0]
        ),
        "Left": np.asarray(
            [0.0, 1.0, 0.0]
        ),
        "Back": np.asarray(
            [-1.0, 0.0, 0.0]
        ),
        "Right": np.asarray(
            [0.0, -1.0, 0.0]
        ),
        "Up": np.asarray(
            [0.0, 0.0, 1.0]
        ),
        "Down": np.asarray(
            [0.0, 0.0, -1.0]
        ),
    }

    cardinal_report = {}

    for name, target in cardinals.items():
        idx = int(
            np.argmin(
                np.linalg.norm(
                    xyz - target,
                    axis=1,
                )
            )
        )

        cardinal_report[
            name
        ] = {
            "direction_index":
                idx,
            "xyz":
                xyz[
                    idx
                ].tolist(),
            "receiver_position_spherical":
                receiver_position[
                    idx
                ].tolist(),
            "xyz_error":
                float(
                    np.linalg.norm(
                        xyz[
                            idx
                        ]
                        - target
                    )
                ),
        }

    info = {
        "cf2_file":
            str(
                cf2_path
            ),
        "npz_file":
            str(
                npz_path
            ),
        "model_name":
            scalar_string(
                metadata.get(
                    "model_name"
                )
            ),
        "manufacturer":
            scalar_string(
                metadata.get(
                    "manufacturer"
                )
            ),
        "description":
            scalar_string(
                metadata.get(
                    "description"
                )
            ),
        "measurement_date":
            scalar_string(
                metadata.get(
                    "measurement_date"
                )
            ),
        "cf2_version":
            int(
                cf2.version
            ),
        "declared_min_idx":
            a,
        "declared_max_idx":
            b,
        "n_frequencies":
            n_freq,
        "n_directions":
            N_CANONICAL,
        "frequency_min_hz":
            float(
                freq[
                    0
                ]
            ),
        "frequency_max_hz":
            float(
                freq[
                    -1
                ]
            ),
        "measurement_distance_m":
            distance_m,
        "measurement_distance_source":
            distance_source,
        "complex_source_max_error":
            complex_source_max_error,
        "magnitude_source_max_error_db":
            magnitude_source_max_error_db,
        "back_quality_counts": {
            str(
                int(code)
            ):
                int(
                    np.sum(
                        back_quality
                        == code
                    )
                )
            for code
            in np.unique(
                back_quality
            )
        },
        "back_resultant_R_min":
            float(
                np.min(
                    back_R
                )
            ),
        "back_resultant_R_median":
            float(
                np.median(
                    back_R
                )
            ),
        "cardinals":
            cardinal_report,
    }

    arrays = {
        "frequencies_hz":
            freq,
        "direction_xyz":
            xyz,
        "receiver_position":
            receiver_position,
        "magnitude_db":
            magnitude_db,
        "phase_rad":
            phase_rad,
        "H_real":
            H_real,
        "H_imag":
            H_imag,
        "back_R":
            back_R,
        "back_quality":
            back_quality,
    }

    return info, arrays


def main() -> None:
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--parser",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--cf2",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--npz",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    ap.add_argument(
        "--receiver-distance-m",
        type=float,
        default=None,
        help=(
            "Override CF2 metadata.distance_m. "
            "Normally this should not be needed."
        ),
    )

    ap.add_argument(
        "--organization",
        default="",
    )

    ap.add_argument(
        "--author-contact",
        default="",
    )

    ap.add_argument(
        "--preflight-only",
        action="store_true",
    )

    args = ap.parse_args()

    parser = load_module(
        args.parser,
        "cf2_parser_v5_exp6a",
    )

    info, arrays = preflight(
        parser,
        args.cf2,
        args.npz,
        args.receiver_distance_m,
    )

    output = args.output

    if output.suffix.lower() != ".sofa":
        output = output.with_suffix(
            ".sofa"
        )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary_path = (
        output.parent
        / (
            output.stem
            + "_roundtrip_summary.json"
        )
    )

    print("=" * 112)
    print("EXPERIMENT 6A - WRITE FREEFIELDDIRECTIVITYTF")
    print("=" * 112)
    print(
        "CF2                     :",
        info[
            "cf2_file"
        ],
    )
    print(
        "Model                   :",
        info[
            "model_name"
        ],
    )
    print(
        "Manufacturer            :",
        info[
            "manufacturer"
        ],
    )
    print(
        "Frequencies             :",
        info[
            "n_frequencies"
        ],
        "({:g}..{:g} Hz)".format(
            info[
                "frequency_min_hz"
            ],
            info[
                "frequency_max_hz"
            ],
        ),
    )
    print(
        "Directions              :",
        info[
            "n_directions"
        ],
    )
    print(
        "Measurement distance    :",
        "{:g} m ({})".format(
            info[
                "measurement_distance_m"
            ],
            info[
                "measurement_distance_source"
            ],
        ),
    )
    print(
        "NPZ complex consistency :",
        "{:.3e}".format(
            info[
                "complex_source_max_error"
            ]
        ),
    )
    print(
        "NPZ magnitude error     :",
        "{:.3e} dB".format(
            info[
                "magnitude_source_max_error_db"
            ]
        ),
    )
    print()

    if args.preflight_only:
        result = {
            "experiment":
                "6A FreeFieldDirectivityTF preflight",
            "preflight_status":
                "PASS",
            **info,
            "output_planned":
                str(
                    output
                ),
            "sofa_written":
                False,
        }

        summary_path.write_text(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        print(
            "PREFLIGHT               : PASS"
        )
        print(
            "SOFA written            : NO (--preflight-only)"
        )
        print(
            "Summary                 :",
            summary_path,
        )
        return

    try:
        import sofar as sf
    except ImportError:
        raise RuntimeError(
            "Package 'sofar' is not installed.\n"
            "For Python 3.8 run:\n"
            "  python -m pip install \"sofar==1.2.3\"\n"
            "Then rerun Experiment 6A."
        )

    # --------------------------------------------------------------
    # Create latest standardized FreeFieldDirectivityTF version 1.1.
    # --------------------------------------------------------------
    sofa = sf.Sofa(
        "FreeFieldDirectivityTF",
        mandatory=False,
        version="1.1",
    )

    # Delete musical-note metadata that is not applicable to a loudspeaker.
    for optional_name in (
        "MIDINote",
        "Descriptions",
        "SourceTuningFrequency",
        "EmitterDescriptions",
    ):
        optional_delete(
            sofa,
            optional_name,
        )

    created = now_iso()

    # Standard metadata.
    sofa.GLOBAL_Title = (
        "{} FreeFieldDirectivityTF converted from CLF2/CF2".format(
            info[
                "model_name"
            ]
            or args.cf2.stem
        )
    )

    sofa.GLOBAL_DatabaseName = (
        "Validated CF2 directivity conversion"
    )

    sofa.GLOBAL_SourceType = (
        "Loudspeaker"
    )

    sofa.GLOBAL_SourceManufacturer = (
        info[
            "manufacturer"
        ]
    )

    sofa.GLOBAL_DateCreated = (
        created
    )

    sofa.GLOBAL_DateModified = (
        created
    )

    sofa.GLOBAL_Organization = (
        args.organization
    )

    sofa.GLOBAL_AuthorContact = (
        args.author_contact
    )

    sofa.GLOBAL_License = (
        "No license provided; consult the original CF2 data owner/manufacturer."
    )

    # Optional narrative fields should exist in a non-mandatory Sofa object.
    if hasattr(
        sofa,
        "GLOBAL_Description",
    ):
        sofa.GLOBAL_Description = (
            "Third-octave loudspeaker directivity converted from CF2. "
            "Complex response is dimensionless directional gain; "
            "no EA sensitivity spectrum or extra normalization was applied."
        )

    if hasattr(
        sofa,
        "GLOBAL_Comment",
    ):
        sofa.GLOBAL_Comment = (
            "CF2 geometry validated as +X=Front, +Y=Left, +Z=Up. "
            "2522 unique spherical directions after exact pole collapse. "
            "For non-exact V2 Back phase, canonicalization uses fixed-magnitude "
            "circular-mean phase; quality is retained in the 5G sidecar data."
        )

    if hasattr(
        sofa,
        "GLOBAL_Origin",
    ):
        sofa.GLOBAL_Origin = (
            "Converted from {}".format(
                args.cf2.name
            )
        )

    # Custom provenance attributes.
    custom_attrs = {
        "GLOBAL_CF2ModelName":
            info[
                "model_name"
            ],
        "GLOBAL_CF2Version":
            str(
                info[
                    "cf2_version"
                ]
            ),
        "GLOBAL_CF2MeasurementDistance":
            "{:.9g} metre".format(
                info[
                    "measurement_distance_m"
                ]
            ),
        "GLOBAL_CF2ConversionPipeline":
            "cf2_parser_v5 -> canonical2522 -> complex2522 -> FreeFieldDirectivityTF",
        "GLOBAL_CF2MagnitudeInterpretation":
            "dimensionless directional gain; 10^(dB/20); no sensitivity spectrum applied",
        "GLOBAL_CF2BackPhasePolicy":
            "EXACT else fixed-magnitude circular mean; low-coherence threshold R=0.90",
    }

    for name, value in custom_attrs.items():
        try:
            sofa.add_attribute(
                name,
                str(
                    value
                ),
            )
        except ValueError:
            # If the field already exists in a future convention, assign it.
            setattr(
                sofa,
                name,
                str(
                    value
                ),
            )

    # Coordinate system.
    sofa.ListenerPosition = np.asarray(
        [0.0, 0.0, 0.0]
    )
    sofa.ListenerPosition_Type = (
        "cartesian"
    )
    sofa.ListenerPosition_Units = (
        "metre"
    )

    sofa.ListenerView = np.asarray(
        [1.0, 0.0, 0.0]
    )
    sofa.ListenerView_Type = (
        "cartesian"
    )
    sofa.ListenerView_Units = (
        "metre"
    )

    sofa.ListenerUp = np.asarray(
        [0.0, 0.0, 1.0]
    )

    sofa.ReceiverPosition = arrays[
        "receiver_position"
    ]
    sofa.ReceiverPosition_Type = (
        "spherical"
    )
    sofa.ReceiverPosition_Units = (
        "degree, degree, metre"
    )

    sofa.SourcePosition = np.asarray(
        [0.0, 0.0, 0.0]
    )
    sofa.SourcePosition_Type = (
        "cartesian"
    )
    sofa.SourcePosition_Units = (
        "metre"
    )
    sofa.SourcePosition_Reference = (
        "CF2 acoustic source reference point at coordinate origin."
    )

    sofa.SourceView = np.asarray(
        [1.0, 0.0, 0.0]
    )
    sofa.SourceView_Type = (
        "cartesian"
    )
    sofa.SourceView_Units = (
        "metre"
    )
    sofa.SourceView_Reference = (
        "CF2 Front direction; validated as +X."
    )

    sofa.SourceUp = np.asarray(
        [0.0, 0.0, 1.0]
    )
    sofa.SourceUp_Reference = (
        "CF2 Up direction; validated as +Z."
    )

    # IMPORTANT for FreeFieldDirectivityTF v1.1:
    # EmitterPosition has dimensions eC / eCM. The lower-case `e`
    # defines the SOFA E dimension from axis 0. Therefore this must be
    # explicitly shaped as (E=1, C=3), not as a 1-D vector (3,).
    # A 1-D vector makes sofar infer E=3 and subsequently fails verify().
    sofa.EmitterPosition = np.asarray(
        [[0.0, 0.0, 0.0]],
        dtype=float,
    )
    sofa.EmitterPosition_Type = (
        "cartesian"
    )
    sofa.EmitterPosition_Units = (
        "metre"
    )

    # Frequency domain data:
    # input NPZ is [F,D], SOFA is [M,R,N].
    n_freq = int(
        arrays[
            "frequencies_hz"
        ].size
    )

    sofa.N = arrays[
        "frequencies_hz"
    ]
    sofa.N_LongName = (
        "frequency"
    )
    sofa.N_Units = (
        "hertz"
    )

    expected_real_mrn = (
        arrays[
            "H_real"
        ].T[
            None,
            :,
            :
        ]
    )

    expected_imag_mrn = (
        arrays[
            "H_imag"
        ].T[
            None,
            :,
            :
        ]
    )

    sofa.Data_Real = (
        expected_real_mrn
    )
    sofa.Data_Imag = (
        expected_imag_mrn
    )

    # Shape diagnostics before verification. This is intentionally printed
    # because EmitterPosition is the variable that defines SOFA dimension E.
    print(
        "EmitterPosition shape   :",
        np.asarray(sofa.EmitterPosition).shape,
        "(expected E=1, C=3)",
    )
    print(
        "ReceiverPosition shape  :",
        np.asarray(sofa.ReceiverPosition).shape,
        "(expected R=2522, C=3)",
    )
    print(
        "Data.Real shape         :",
        np.asarray(sofa.Data_Real).shape,
        "(expected M=1, R=2522, N={})".format(n_freq),
    )
    print()

    # Standard verification before writing.
    verify_before = sofa.verify(
        issue_handling="return",
        mode="write",
    )

    if verify_before:
        raise RuntimeError(
            "SOFA verification failed before write:\n{}".format(
                verify_before
            )
        )

    sf.write_sofa(
        str(
            output
        ),
        sofa,
        compression=4,
    )

    # --------------------------------------------------------------
    # Read back and independently compare all critical arrays.
    # --------------------------------------------------------------
    sofa_read = sf.read_sofa(
        str(
            output
        ),
        verify=True,
        verbose=False,
    )

    verify_after = sofa_read.verify(
        issue_handling="return",
        mode="read",
    )

    if verify_after:
        raise RuntimeError(
            "SOFA verification failed after read:\n{}".format(
                verify_after
            )
        )

    read_freq = np.asarray(
        sofa_read.N,
        dtype=float,
    ).reshape(
        -1
    )

    read_receiver = np.asarray(
        sofa_read.ReceiverPosition,
        dtype=float,
    )

    read_real = canonical_mrn(
        sofa_read.Data_Real,
        N_CANONICAL,
        n_freq,
    )

    read_imag = canonical_mrn(
        sofa_read.Data_Imag,
        N_CANONICAL,
        n_freq,
    )

    freq_error = float(
        np.max(
            np.abs(
                read_freq
                - arrays[
                    "frequencies_hz"
                ]
            )
        )
    )

    expected_receiver = arrays[
        "receiver_position"
    ]

    receiver_shape = np.squeeze(
        read_receiver
    ).shape

    receiver_error = float(
        np.max(
            np.abs(
                np.squeeze(
                    read_receiver
                )
                - expected_receiver
            )
        )
    )

    real_error = float(
        np.max(
            np.abs(
                read_real
                - expected_real_mrn
            )
        )
    )

    imag_error = float(
        np.max(
            np.abs(
                read_imag
                - expected_imag_mrn
            )
        )
    )

    read_H = (
        read_real
        + 1j * read_imag
    )

    expected_H = (
        expected_real_mrn
        + 1j
        * expected_imag_mrn
    )

    complex_error = float(
        np.max(
            np.abs(
                read_H
                - expected_H
            )
        )
    )

    with np.errstate(
        divide="ignore",
        invalid="ignore",
    ):
        roundtrip_db = (
            20.0
            * np.log10(
                np.abs(
                    read_H[
                        0
                    ].T
                )
            )
        )

    magnitude_error_db = float(
        np.max(
            np.abs(
                roundtrip_db
                - arrays[
                    "magnitude_db"
                ]
            )
        )
    )

    # Confirm receiver directions roundtrip back to NPZ unit vectors.
    receiver_xyz = sofa_spherical_to_xyz(
        read_receiver
    )

    direction_xyz_error = float(
        np.max(
            np.abs(
                receiver_xyz
                - arrays[
                    "direction_xyz"
                ]
            )
        )
    )

    conventions = scalar_string(
        getattr(
            sofa_read,
            "GLOBAL_SOFAConventions",
            "",
        )
    )

    convention_version = scalar_string(
        getattr(
            sofa_read,
            "GLOBAL_SOFAConventionsVersion",
            "",
        )
    )

    overall_pass = (
        conventions
        == "FreeFieldDirectivityTF"
        and convention_version
        == "1.1"
        and freq_error == 0.0
        and receiver_error <= 1e-12
        and real_error == 0.0
        and imag_error == 0.0
        and complex_error == 0.0
        and magnitude_error_db <= 1e-12
        and direction_xyz_error <= 1e-12
    )

    result = {
        "experiment":
            "6A write and roundtrip FreeFieldDirectivityTF",
        "status":
            (
                "PASS"
                if overall_pass
                else "FAIL"
            ),
        "source":
            info,
        "sofar_version":
            scalar_string(
                getattr(
                    sf,
                    "__version__",
                    "",
                )
            ),
        "sofa": {
            "file":
                str(
                    output
                ),
            "file_size_bytes":
                int(
                    output.stat().st_size
                ),
            "convention":
                conventions,
            "convention_version":
                convention_version,
            "M":
                1,
            "R":
                N_CANONICAL,
            "N":
                n_freq,
            "receiver_position_shape_after_read":
                list(
                    receiver_shape
                ),
            "data_real_shape_normalized":
                list(
                    read_real.shape
                ),
            "data_imag_shape_normalized":
                list(
                    read_imag.shape
                ),
        },
        "roundtrip_errors": {
            "frequency_max_abs_hz":
                freq_error,
            "receiver_position_max_abs":
                receiver_error,
            "receiver_direction_xyz_max_abs":
                direction_xyz_error,
            "data_real_max_abs":
                real_error,
            "data_imag_max_abs":
                imag_error,
            "complex_max_abs":
                complex_error,
            "magnitude_max_abs_db":
                magnitude_error_db,
        },
        "verification": {
            "before_write":
                (
                    "PASS"
                    if not verify_before
                    else verify_before
                ),
            "after_read":
                (
                    "PASS"
                    if not verify_after
                    else verify_after
                ),
        },
        "important_semantics": {
            "receiver_radius_m":
                info[
                    "measurement_distance_m"
                ],
            "receiver_radius_source":
                info[
                    "measurement_distance_source"
                ],
            "magnitude":
                (
                    "dimensionless directional gain from CF2 magnitude dB; "
                    "no sensitivity spectrum applied"
                ),
            "phase":
                (
                    "canonical 5G phase; non-exact Back uses "
                    "fixed-magnitude circular mean"
                ),
        },
    }

    summary_path.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print("=" * 112)
    print("EXPERIMENT 6A SUMMARY")
    print("=" * 112)
    print(
        "Convention               :",
        conventions,
        convention_version,
    )
    print(
        "Dimensions               :",
        "M=1 R={} N={}".format(
            N_CANONICAL,
            n_freq,
        ),
    )
    print(
        "Receiver radius          :",
        "{:g} m".format(
            info[
                "measurement_distance_m"
            ]
        ),
    )
    print(
        "Frequency roundtrip      :",
        "{:.3e} Hz".format(
            freq_error
        ),
    )
    print(
        "Receiver roundtrip       :",
        "{:.3e}".format(
            receiver_error
        ),
    )
    print(
        "Direction XYZ roundtrip  :",
        "{:.3e}".format(
            direction_xyz_error
        ),
    )
    print(
        "Data.Real roundtrip      :",
        "{:.3e}".format(
            real_error
        ),
    )
    print(
        "Data.Imag roundtrip      :",
        "{:.3e}".format(
            imag_error
        ),
    )
    print(
        "Complex roundtrip        :",
        "{:.3e}".format(
            complex_error
        ),
    )
    print(
        "Magnitude roundtrip      :",
        "{:.3e} dB".format(
            magnitude_error_db
        ),
    )
    print(
        "SOFA verify              :",
        "PASS",
    )
    print(
        "OVERALL                  :",
        result[
            "status"
        ],
    )
    print(
        "SOFA file                :",
        output,
    )
    print(
        "Summary                  :",
        summary_path,
    )


if __name__ == "__main__":
    main()
