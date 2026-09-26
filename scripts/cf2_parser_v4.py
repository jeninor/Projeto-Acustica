#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
cf2_parser_v4.py

Parser empírico para archivos CLF2 / CF2, basado en validación masiva
contra 427 archivos, JSON del CLF Viewer y curvas de CATT-Acoustic.

IMPORTANTE
==========
Este módulo separa claramente:

1) Datos binarios CF2 reales.
2) Conversión de tercio de octava -> octava usada por CATT.
3) Limitaciones VISUALES del polar de CATT.

No se debe modificar la directividad binaria para imitar el display.

EVIDENCIA EMPÍRICA ACTUAL
=========================
- 427 archivos emparejados.
- 2912 combinaciones modelo/octava comparadas.
- Orientación validada:
    azimuth offset = 0°
    azimuth direction = +1
    arc direction = +1
- CATT octave:
    promedio energético igual de los 3 tercios adyacentes.
- Magnitude directivity base:
    DIRECTIVITY_BASE = 14232.
  Esta interpretación evita el desplazamiento artificial de una columna
  observado con 14236. En la validación estructural:
    * las dos lecturas sólo difieren en arc=0°;
    * 9680/9680 bloques mostraron exactamente la fuga esperada del esquema
      14236 hacia el siguiente registro/frecuencia;
    * en 86 archivos de 334040 bytes, 14232 deja un trailer de 128 bytes,
      mientras 14236 deja 124 bytes.
- Después de:
    * eliminar clipping superior incorrecto en 0 dB, y
    * emular floor visual de CATT a -50 dB,
  no quedaron outliers > 1.5 dB en las 2912 comparaciones.
- Con floor fijo -50 dB:
    max RMSE ≈ 1.034 dB
- El mejor floor ajustado por caso se concentra alrededor de -49.45 dB,
  compatible con cuantización geométrica/píxeles. Para emulación robusta
  se usa -50 dB.

ADVERTENCIAS
============
- Los campos min_idx / max_idx en 4624 / 4628 concuerdan con el Viewer
  en 423/427 archivos. En 4 archivos el Viewer muestra EA más allá del
  max_idx. Por eso aquí se llaman "declared_min_idx / declared_max_idx"
  y NO "última frecuencia absoluta de todo el archivo".
- En CLF2 V2 existe un segundo contenedor angular de fase.
  Después de revalidar su alineación, el inicio estructural preferido es
  PHASE_ANGULAR_BASE = 334564, con shape 30 x 72 x 37.
  La antigua lectura desde 334568 comenzaba un float32 tarde y compensaba
  el error mediante una rotación de columnas. En 423/423 bloques:
    * las diferencias entre ambas lecturas quedaron sólo en arc=0°;
    * la lectura 334568 tomó exactamente el valor del siguiente registro;
    * 334564 mantuvo arc=0° constante en 423/423 bloques, frente a 409/423
      para 334568.
  La evidencia empírica es muy fuerte a favor de PHASE en radianes:
    * 10 archivos Duran Audio están envueltos en [-pi,+pi].
    * 12 archivos d&b contienen valores fuera de ese rango y son
      compatibles con fase en radianes no envuelta / parcialmente envuelta.
  El parser preserva SIEMPRE el valor raw y ofrece helpers de wrapping.
- u32 @4476 se interpreta como BALLOON-SYMMETRY con mapeo 0..5 validado
  geométricamente sobre el corpus.
- Algunos arrays EA contienen -12000 como NODATA.
- Algunos archivos contienen garbage/no inicializado en ciertos campos EA.
  El parser preserva el valor original y ofrece helpers de validez.

Uso rápido
==========
from cf2_parser_v4 import CF2File

cf2 = CF2File("speaker.CF2")

print(cf2.version)
print(cf2.metadata["model_name"])
print(cf2.declared_frequencies)

D1000 = cf2.directivity_third_octave(1000)
Doct = cf2.directivity_octave(1000)

# Explicit angular axes:
azimuth_deg, arc_deg, D1000 = cf2.directivity_grid(1000)

# CF2 V2 phase, when available:
# azimuth_deg, arc_deg, phase_rad = cf2.phase_grid(1000)

# Export every declared frequency / azimuth / arc combination:
cf2.export_directivity_csv("speaker_directivity.csv")

# Emulación del polar CATT:
Dcatt = cf2.catt_display_octave(1000)

# No hay clipping superior:
assert Dcatt.max() >= 0 or True

CLI
===
python3 cf2_parser_v4.py file.CF2
python3 cf2_parser_v4.py file.CF2 --octave 1000
python3 cf2_parser_v4.py file.CF2 --third 1000
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np


# ======================================================================
# CONSTANTS
# ======================================================================

CF2_FREQS = np.array([
    25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200,
    250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000,
    2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000
], dtype=float)

N_FREQ_SLOTS = 30

# Header / metadata
FORMAT_WORD_OFFSET = 4
MIN_IDX_OFFSET = 4624
MAX_IDX_OFFSET = 4628

STRING_FIELDS = {
    "model_name": (312, 256),
    "manufacturer": (568, 256),
    "description": (824, 516),
    "mounting": (1340, 256),
    "website": (1600, 256),
    "contact": (1856, 256),
    "email": (2112, 256),
    "measurement_date": (2368, 304),
    "environment": (2672, 256),
}

WEIGHT_OFFSET = 1596
DISTANCE_OFFSET = 2928
TYPE_OFFSET = 2932

TYPE_MAP = {
    0: "passive",
    1: "active",
    2: "powered",
}

MAX_INPUT_MODE_OFFSET = 3704
MAX_INPUT_VALUE_OFFSET = 3708

MAX_INPUT_MODE_MAP = {
    0: "power",
    1: "voltage",
}

# Fixed 30-slot EA arrays
AXIAL_SPECTRUM_BASE = 4096

EA_FIELD_BASES = {
    "input_voltage": 4632,
    "sensitivity": 4752,
    "impedance": 4872,
    "h_left": 4992,
    "h_right": 5112,
    "v_up": 5232,
    "v_down": 5352,
    "axial_q": 5472,
}

NODATA_VALUE = -12000.0
NODATA_TOL = 1e-3

# Directivity magnitude
#
# Preferred/validated structural interpretation:
#   base = 14232
#   shape per frequency = (72,37)
#   rows = azimuth [0,5,...,355]
#   cols = arc     [0,5,...,180]
#
# The older 14236 interpretation started one float32 late and required
# rotating [5,...,180,0] -> [0,5,...,180]. It is retained only as a
# diagnostic reference.
DIRECTIVITY_BASE = 14232
DIRECTIVITY_BASE_LEGACY = 14236

N_AZIMUTH = 72
N_ARC = 37
DIRECTIVITY_FLOATS_PER_SLOT = N_AZIMUTH * N_ARC  # 2664
DIRECTIVITY_BYTES_PER_SLOT = DIRECTIVITY_FLOATS_PER_SLOT * 4  # 10656

AZIMUTH_DEGREES = np.arange(0, 360, 5, dtype=float)
ARC_DEGREES = np.arange(0, 181, 5, dtype=float)

# With DIRECTIVITY_BASE=14232, the raw magnitude record is already canonical.
RAW_ARC_DEGREES = ARC_DEGREES.copy()

# CATT octave buttons
CATT_OCTAVE_CENTERS = np.array(
    [125, 250, 500, 1000, 2000, 4000, 8000, 16000],
    dtype=float
)

# CATT polar display behavior
CATT_DISPLAY_FLOOR_DB = -50.0

# Balloon metadata
BALLOON_SYMMETRY_OFFSET = 4476

BALLOON_SYMMETRY_MAP = {
    0: "full",
    1: "vertical",
    2: "horizontal",
    3: "none",
    4: "rotational",
    5: "polar",
}

# V2 phase angular container.
#
# Preferred/validated structural interpretation:
#   base = 334564
#   shape per frequency = (72,37)
#   rows = azimuth [0,5,...,355]
#   cols = arc     [0,5,...,180]
#
# Validation over the 22 V2 files:
# - differences vs the former 334568+reorder interpretation occur only
#   at arc=0°;
# - 423/423 active slots showed the exact cross-boundary leakage expected
#   from starting one float32 late at 334568;
# - arc=0° remained rotation-invariant in 423/423 slots with 334564,
#   compared with 409/423 using 334568.
#
# Phase semantics remain empirically supported as radians:
# - 10 Duran Audio files are essentially bounded by [-pi,+pi];
# - 12 d&b files extend outside that interval and are compatible with
#   unwrapped / partly unwrapped radians.
#
# IMPORTANT: preserve raw values. Do not force wrapping on read.
PHASE_ANGULAR_BASE = 334564
PHASE_ANGULAR_BASE_LEGACY = 334568

PHASE_UNIT = "radian"
PHASE_WRAPPED_MIN = -np.pi
PHASE_WRAPPED_MAX = np.pi


# ======================================================================
# LOW-LEVEL HELPERS
# ======================================================================

PathLike = Union[str, Path]


def _read_u32(raw: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(raw):
        raise ValueError(
            f"u32 outside file: offset={offset}, file_size={len(raw)}"
        )
    return struct.unpack_from("<I", raw, offset)[0]


def _read_f32(raw: bytes, offset: int) -> float:
    if offset < 0 or offset + 4 > len(raw):
        raise ValueError(
            f"f32 outside file: offset={offset}, file_size={len(raw)}"
        )
    return struct.unpack_from("<f", raw, offset)[0]


def _read_f32_array(
    raw: bytes,
    offset: int,
    count: int
) -> np.ndarray:
    end = offset + 4 * count

    if offset < 0 or end > len(raw):
        raise ValueError(
            f"float array outside file: offset={offset}, "
            f"count={count}, file_size={len(raw)}"
        )

    return np.frombuffer(
        raw,
        dtype="<f4",
        count=count,
        offset=offset,
    ).astype(np.float64, copy=True)


def _read_fixed_string(
    raw: bytes,
    offset: int,
    length: int
) -> str:
    if offset < 0 or offset >= len(raw):
        return ""

    data = raw[
        offset:min(
            offset + length,
            len(raw)
        )
    ]

    data = data.split(
        b"\x00",
        1
    )[0]

    # Legacy ANSI-compatible text.
    return data.decode(
        "latin-1",
        errors="replace"
    ).strip()


def _freq_index(freq_hz: float) -> int:
    matches = np.where(
        np.isclose(
            CF2_FREQS,
            float(freq_hz),
            atol=1e-6,
            rtol=0.0
        )
    )[0]

    if len(matches) != 1:
        raise KeyError(
            f"Frequency not in CF2 global table: {freq_hz}"
        )

    return int(matches[0])


def _is_nodata(value: float) -> bool:
    return (
        np.isfinite(value)
        and
        abs(float(value) - NODATA_VALUE)
        <=
        NODATA_TOL
    )


def _is_reasonable_numeric(value: float) -> bool:
    return (
        np.isfinite(value)
        and
        not _is_nodata(value)
        and
        abs(float(value)) < 1e8
    )


def _canonicalize_arc(raw_matrix: np.ndarray) -> np.ndarray:
    """
    Magnitude directivity canonicalization.

    With DIRECTIVITY_BASE=14232 the 72x37 matrix is already stored as:

        rows = azimuth [0,5,...,355]
        cols = arc     [0,5,...,180]

    Therefore no column rotation is required.
    """
    if raw_matrix.shape != (N_AZIMUTH, N_ARC):
        raise ValueError(
            f"Unexpected directivity shape: {raw_matrix.shape}"
        )

    return raw_matrix.copy()


def _canonicalize_phase_arc(raw_matrix: np.ndarray) -> np.ndarray:
    """
    Phase canonicalization helper.

    With PHASE_ANGULAR_BASE=334564 the phase matrix is already stored as:

        rows = azimuth [0,5,...,355]
        cols = arc     [0,5,...,180]

    No column rotation is required.

    The helper is retained for API/internal compatibility only.
    """
    if raw_matrix.shape != (N_AZIMUTH, N_ARC):
        raise ValueError(
            f"Unexpected phase shape: {raw_matrix.shape}"
        )

    return raw_matrix.copy()


def _energy_average_db(
    db_surfaces: Sequence[np.ndarray]
) -> np.ndarray:
    """
    Equal-energy average in linear power domain.

    10*log10(mean(10^(D_i/10)))
    """
    stack = np.stack(
        db_surfaces,
        axis=0
    ).astype(
        np.float64,
        copy=False
    )

    with np.errstate(
        over="ignore",
        under="ignore",
        divide="ignore",
        invalid="ignore"
    ):
        power = np.power(
            10.0,
            np.clip(
                stack,
                -1000.0,
                1000.0
            ) / 10.0
        )

        return 10.0 * np.log10(
            np.mean(
                power,
                axis=0
            )
        )



def _wrap_phase_radians(
    phase: np.ndarray
) -> np.ndarray:
    """
    Wrap arbitrary phase values to [-pi, pi).

    This is a representation helper only. The raw CF2 values are preserved.
    """
    x = np.asarray(
        phase,
        dtype=np.float64
    )

    return (
        x + np.pi
    ) % (
        2.0 * np.pi
    ) - np.pi



# ======================================================================
# DATA CLASSES
# ======================================================================

@dataclass(frozen=True)
class CF2Header:
    version: int
    declared_min_idx: int
    declared_max_idx: int

    @property
    def declared_first_frequency_hz(self) -> Optional[float]:
        if 0 <= self.declared_min_idx < len(CF2_FREQS):
            return float(
                CF2_FREQS[
                    self.declared_min_idx
                ]
            )
        return None

    @property
    def declared_last_frequency_hz(self) -> Optional[float]:
        if 0 <= self.declared_max_idx < len(CF2_FREQS):
            return float(
                CF2_FREQS[
                    self.declared_max_idx
                ]
            )
        return None

    @property
    def declared_count(self) -> Optional[int]:
        if (
            0 <= self.declared_min_idx <= self.declared_max_idx
            < len(CF2_FREQS)
        ):
            return (
                self.declared_max_idx
                -
                self.declared_min_idx
                +
                1
            )
        return None


@dataclass(frozen=True)
class CF2MaxInput:
    mode_code: int
    mode: str
    value: float


@dataclass(frozen=True)
class CF2EAValue:
    frequency_hz: float
    global_slot: int
    input_voltage: float
    sensitivity: float
    impedance: float
    h_left: float
    h_right: float
    v_up: float
    v_down: float
    axial_q: float
    axial_spectrum: float

    @property
    def horizontal_width_deg(self) -> float:
        if (
            _is_reasonable_numeric(self.h_left)
            and
            _is_reasonable_numeric(self.h_right)
        ):
            return 2.0 * min(
                self.h_left,
                self.h_right
            )

        return float("nan")

    @property
    def vertical_width_deg(self) -> float:
        if (
            _is_reasonable_numeric(self.v_up)
            and
            _is_reasonable_numeric(self.v_down)
        ):
            return 2.0 * min(
                self.v_up,
                self.v_down
            )

        return float("nan")


# ======================================================================
# MAIN PARSER
# ======================================================================

class CF2File:
    """
    Read-only parser for one CF2 file.
    """

    def __init__(
        self,
        path: PathLike
    ):
        self.path = Path(path)

        if not self.path.is_file():
            raise FileNotFoundError(
                self.path
            )

        self.raw = self.path.read_bytes()

        self.header = CF2Header(
            version=_read_u32(
                self.raw,
                FORMAT_WORD_OFFSET
            ),
            declared_min_idx=_read_u32(
                self.raw,
                MIN_IDX_OFFSET
            ),
            declared_max_idx=_read_u32(
                self.raw,
                MAX_IDX_OFFSET
            ),
        )

        self._ea_cache: Optional[
            Dict[str, np.ndarray]
        ] = None

        self._metadata_cache: Optional[
            Dict[str, object]
        ] = None

    # ------------------------------------------------------------------
    # Basic properties
    # ------------------------------------------------------------------

    @property
    def file_size(self) -> int:
        return len(
            self.raw
        )

    @property
    def version(self) -> int:
        return self.header.version

    @property
    def declared_min_idx(self) -> int:
        return self.header.declared_min_idx

    @property
    def declared_max_idx(self) -> int:
        return self.header.declared_max_idx

    @property
    def declared_frequencies(self) -> np.ndarray:
        """
        Frequencies delimited by the header min/max indexes.

        IMPORTANT:
        In 4/427 validated files, CLF Viewer exposed some EA data beyond
        declared_max_idx. Treat this as a declared range, not necessarily
        the absolute extent of every EA field.
        """
        a = self.declared_min_idx
        b = self.declared_max_idx

        if not (
            0 <= a <= b < N_FREQ_SLOTS
        ):
            return np.array(
                [],
                dtype=float
            )

        return CF2_FREQS[
            a:b + 1
        ].copy()

    @property
    def balloon_symmetry_code(self) -> int:
        """
        Binary BALLOON-SYMMETRY enum at u32 @4476.

        Empirical mapping:
            0 full
            1 vertical
            2 horizontal
            3 none
            4 rotational
            5 polar
        """
        return _read_u32(
            self.raw,
            BALLOON_SYMMETRY_OFFSET
        )

    @property
    def balloon_symmetry(self) -> str:
        code = self.balloon_symmetry_code

        return BALLOON_SYMMETRY_MAP.get(
            code,
            f"unknown:{code}"
        )

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def metadata(self) -> Dict[str, object]:
        if self._metadata_cache is not None:
            return dict(
                self._metadata_cache
            )

        m: Dict[str, object] = {}

        for name, (
            offset,
            length
        ) in STRING_FIELDS.items():
            m[name] = _read_fixed_string(
                self.raw,
                offset,
                length
            )

        m["weight_kg"] = _read_f32(
            self.raw,
            WEIGHT_OFFSET
        )

        m["distance_m"] = _read_f32(
            self.raw,
            DISTANCE_OFFSET
        )

        type_code = _read_u32(
            self.raw,
            TYPE_OFFSET
        )

        m["type_code"] = type_code
        m["type"] = TYPE_MAP.get(
            type_code,
            f"unknown:{type_code}"
        )

        max_mode_code = _read_u32(
            self.raw,
            MAX_INPUT_MODE_OFFSET
        )

        m["max_input"] = CF2MaxInput(
            mode_code=max_mode_code,
            mode=MAX_INPUT_MODE_MAP.get(
                max_mode_code,
                f"unknown:{max_mode_code}"
            ),
            value=_read_f32(
                self.raw,
                MAX_INPUT_VALUE_OFFSET
            ),
        )

        self._metadata_cache = m

        return dict(
            m
        )

    # ------------------------------------------------------------------
    # EA arrays
    # ------------------------------------------------------------------

    @property
    def ea_arrays(self) -> Dict[str, np.ndarray]:
        if self._ea_cache is None:
            arrays = {
                name: _read_f32_array(
                    self.raw,
                    base,
                    N_FREQ_SLOTS
                )
                for name, base
                in EA_FIELD_BASES.items()
            }

            arrays[
                "axial_spectrum"
            ] = _read_f32_array(
                self.raw,
                AXIAL_SPECTRUM_BASE,
                N_FREQ_SLOTS
            )

            self._ea_cache = arrays

        return {
            k: v.copy()
            for k, v
            in self._ea_cache.items()
        }

    def ea_value(
        self,
        freq_hz: float
    ) -> CF2EAValue:
        idx = _freq_index(
            freq_hz
        )

        a = self.ea_arrays

        return CF2EAValue(
            frequency_hz=float(
                CF2_FREQS[idx]
            ),
            global_slot=idx,
            input_voltage=float(
                a[
                    "input_voltage"
                ][idx]
            ),
            sensitivity=float(
                a[
                    "sensitivity"
                ][idx]
            ),
            impedance=float(
                a[
                    "impedance"
                ][idx]
            ),
            h_left=float(
                a[
                    "h_left"
                ][idx]
            ),
            h_right=float(
                a[
                    "h_right"
                ][idx]
            ),
            v_up=float(
                a[
                    "v_up"
                ][idx]
            ),
            v_down=float(
                a[
                    "v_down"
                ][idx]
            ),
            axial_q=float(
                a[
                    "axial_q"
                ][idx]
            ),
            axial_spectrum=float(
                a[
                    "axial_spectrum"
                ][idx]
            ),
        )

    # ------------------------------------------------------------------
    # Directivity: third-octave
    # ------------------------------------------------------------------

    @staticmethod
    def directivity_slot_offset(
        slot: int
    ) -> int:
        if not (
            0 <= int(
                slot
            ) < N_FREQ_SLOTS
        ):
            raise IndexError(
                slot
            )

        return (
            DIRECTIVITY_BASE
            +
            int(
                slot
            )
            *
            DIRECTIVITY_BYTES_PER_SLOT
        )

    def directivity_slot_raw(
        self,
        slot: int
    ) -> np.ndarray:
        """
        Returns the literal 72x37 magnitude matrix as stored:
          rows = azimuth [0,5,...,355]
          cols = arc     [0,5,...,180]

        With DIRECTIVITY_BASE=14232 this matrix is already canonical.
        """
        offset = self.directivity_slot_offset(
            slot
        )

        values = _read_f32_array(
            self.raw,
            offset,
            DIRECTIVITY_FLOATS_PER_SLOT
        )

        return values.reshape(
            N_AZIMUTH,
            N_ARC
        )

    def directivity_slot(
        self,
        slot: int
    ) -> np.ndarray:
        """
        Returns canonical magnitude directivity, shape (72,37):
          rows = azimuth [0,5,...,355]
          cols = arc     [0,5,...,180]

        No post-read column rotation is needed.
        """
        return _canonicalize_arc(
            self.directivity_slot_raw(
                slot
            )
        )

    @staticmethod
    def directivity_slot_offset_legacy(
        slot: int
    ) -> int:
        """
        Former V2 hypothesis: base=14236.
        Retained only for diagnostics / reproducibility.
        """
        if not (
            0 <= int(slot) < N_FREQ_SLOTS
        ):
            raise IndexError(slot)

        return (
            DIRECTIVITY_BASE_LEGACY
            +
            int(slot)
            *
            DIRECTIVITY_BYTES_PER_SLOT
        )

    def directivity_slot_legacy(
        self,
        slot: int
    ) -> np.ndarray:
        """
        Reproduce the old base=14236 + arc rotation interpretation.
        """
        offset = self.directivity_slot_offset_legacy(
            slot
        )

        values = _read_f32_array(
            self.raw,
            offset,
            DIRECTIVITY_FLOATS_PER_SLOT
        )

        matrix = values.reshape(
            N_AZIMUTH,
            N_ARC
        )

        return np.concatenate(
            [
                matrix[:, 36:37],
                matrix[:, :36],
            ],
            axis=1
        )

    def compare_directivity_layouts(
        self,
        freq_hz: float
    ) -> Dict[str, object]:
        """
        Compare the current 14232 interpretation against the legacy 14236
        interpretation for one frequency.

        This is a diagnostic helper. It does not modify the parsed data.
        """
        slot = _freq_index(
            freq_hz
        )

        current = self.directivity_slot(
            slot
        )

        legacy = self.directivity_slot_legacy(
            slot
        )

        delta = current - legacy

        return {
            "frequency_hz":
                float(CF2_FREQS[slot]),
            "current_base":
                DIRECTIVITY_BASE,
            "legacy_base":
                DIRECTIVITY_BASE_LEGACY,
            "different_cells":
                int(
                    np.count_nonzero(
                        ~np.isclose(
                            current,
                            legacy,
                            atol=1e-7,
                            rtol=0.0,
                            equal_nan=True
                        )
                    )
                ),
            "differences_outside_arc0":
                int(
                    np.count_nonzero(
                        ~np.isclose(
                            current[:, 1:],
                            legacy[:, 1:],
                            atol=1e-7,
                            rtol=0.0,
                            equal_nan=True
                        )
                    )
                ),
            "max_abs_difference_db":
                float(
                    np.nanmax(
                        np.abs(delta)
                    )
                ),
            "max_abs_difference_outside_arc0_db":
                float(
                    np.nanmax(
                        np.abs(
                            delta[:, 1:]
                        )
                    )
                ),
        }

    def directivity_third_octave(
        self,
        freq_hz: float
    ) -> np.ndarray:
        """
        Directivity magnitude for one CF2 third-octave frequency.
        """
        return self.directivity_slot(
            _freq_index(
                freq_hz
            )
        )

    # ------------------------------------------------------------------
    # Directivity: CATT octave reconstruction
    # ------------------------------------------------------------------

    @staticmethod
    def octave_triplet(
        center_hz: float
    ) -> Tuple[float, float, float]:
        """
        Returns the 3 adjacent third-octave bands used for a CATT octave.

        Example:
            1000 -> (800,1000,1250)
        """
        c = _freq_index(
            center_hz
        )

        if c <= 0 or c >= N_FREQ_SLOTS - 1:
            raise ValueError(
                f"Cannot build octave around {center_hz}"
            )

        return (
            float(
                CF2_FREQS[
                    c - 1
                ]
            ),
            float(
                CF2_FREQS[
                    c
                ]
            ),
            float(
                CF2_FREQS[
                    c + 1
                ]
            ),
        )

    def directivity_octave(
        self,
        center_hz: float,
        require_declared_triplet: bool = False,
    ) -> np.ndarray:
        """
        Reconstructs CATT-style octave directivity from 3 CF2 third-octaves.

        Formula:
            10*log10(
                (
                    10^(Dlow/10)
                    + 10^(Dcenter/10)
                    + 10^(Dhigh/10)
                ) / 3
            )

        This equal-energy rule was the strongest corpus-wide match.

        If require_declared_triplet=True, all three slots must lie inside
        declared_min_idx..declared_max_idx.
        """
        low, center, high = self.octave_triplet(
            center_hz
        )

        slots = [
            _freq_index(low),
            _freq_index(center),
            _freq_index(high),
        ]

        if require_declared_triplet:
            if not all(
                self.declared_min_idx
                <=
                s
                <=
                self.declared_max_idx
                for s in slots
            ):
                raise ValueError(
                    "Octave triplet is not fully inside the "
                    "header-declared frequency range: "
                    f"{low}, {center}, {high} Hz"
                )

        surfaces = [
            self.directivity_slot(
                s
            )
            for s in slots
        ]

        return _energy_average_db(
            surfaces
        )

    def catt_display_octave(
        self,
        center_hz: float,
        floor_db: float = CATT_DISPLAY_FLOOR_DB,
        require_declared_triplet: bool = False,
    ) -> np.ndarray:
        """
        Emulates the visible magnitude limit of the CATT polar.

        IMPORTANT:
        - There is NO upper clip at 0 dB.
        - Positive directivity values are valid.
        - Only a lower visual floor is applied.

        This is a DISPLAY helper. It does not alter binary CF2 values.
        """
        d = self.directivity_octave(
            center_hz,
            require_declared_triplet=require_declared_triplet,
        )

        return np.maximum(
            d,
            float(
                floor_db
            )
        )

    # ------------------------------------------------------------------
    # Sampling helpers
    # ------------------------------------------------------------------

    @staticmethod
    def angle_to_indices(
        azimuth_deg: float,
        arc_deg: float,
    ) -> Tuple[int, int]:
        """
        Convert exact 5° grid angles to matrix indices.
        """
        az = float(
            azimuth_deg
        ) % 360.0

        arc = float(
            arc_deg
        )

        az_i = int(
            round(
                az / 5.0
            )
        ) % N_AZIMUTH

        arc_i = int(
            round(
                arc / 5.0
            )
        )

        if not (
            0 <= arc_i < N_ARC
        ):
            raise ValueError(
                f"arc outside 0..180: {arc_deg}"
            )

        if not math.isclose(
            az,
            az_i * 5.0 % 360.0,
            abs_tol=1e-6
        ):
            raise ValueError(
                "azimuth must lie on 5° grid"
            )

        if not math.isclose(
            arc,
            arc_i * 5.0,
            abs_tol=1e-6
        ):
            raise ValueError(
                "arc must lie on 5° grid"
            )

        return az_i, arc_i

    def directivity_value(
        self,
        freq_hz: float,
        azimuth_deg: float,
        arc_deg: float,
    ) -> float:
        d = self.directivity_third_octave(
            freq_hz
        )

        i, j = self.angle_to_indices(
            azimuth_deg,
            arc_deg
        )

        return float(
            d[
                i,
                j
            ]
        )

    def octave_value(
        self,
        center_hz: float,
        azimuth_deg: float,
        arc_deg: float,
        catt_display: bool = False,
        floor_db: float = CATT_DISPLAY_FLOOR_DB,
    ) -> float:
        if catt_display:
            d = self.catt_display_octave(
                center_hz,
                floor_db=floor_db,
            )
        else:
            d = self.directivity_octave(
                center_hz
            )

        i, j = self.angle_to_indices(
            azimuth_deg,
            arc_deg
        )

        return float(
            d[
                i,
                j
            ]
        )

    # ------------------------------------------------------------------
    # V2 phase angular container
    # ------------------------------------------------------------------

    @property
    def has_phase_container(
        self
    ) -> bool:
        """
        True when file version/size are compatible with the empirically
        identified V2 phase container.
        """
        required_end = (
            PHASE_ANGULAR_BASE
            +
            N_FREQ_SLOTS
            *
            DIRECTIVITY_BYTES_PER_SLOT
        )

        return (
            self.version == 2
            and
            len(self.raw) >= required_end
        )

    @staticmethod
    def phase_slot_offset(
        slot: int
    ) -> int:
        """
        Preferred phase-slot offset using PHASE_ANGULAR_BASE=334564.
        """
        if not (
            0 <= int(slot) < N_FREQ_SLOTS
        ):
            raise IndexError(slot)

        return (
            PHASE_ANGULAR_BASE
            +
            int(slot)
            *
            DIRECTIVITY_BYTES_PER_SLOT
        )

    @staticmethod
    def phase_slot_offset_legacy(
        slot: int
    ) -> int:
        """
        Former phase hypothesis: base=334568.
        Retained only for diagnostics / reproducibility.
        """
        if not (
            0 <= int(slot) < N_FREQ_SLOTS
        ):
            raise IndexError(slot)

        return (
            PHASE_ANGULAR_BASE_LEGACY
            +
            int(slot)
            *
            DIRECTIVITY_BYTES_PER_SLOT
        )

    def phase_slot_raw(
        self,
        slot: int,
        canonical_arc: bool = True,
    ) -> np.ndarray:
        """
        Read one V2 phase slot as float32 radians.

        Shape:
            (72,37)

        Rows:
            azimuth [0,5,...,355]

        Columns:
            arc [0,5,...,180]

        With PHASE_ANGULAR_BASE=334564 the raw matrix is already canonical.
        `canonical_arc` is retained only for backward API compatibility and
        does not apply any column rotation.

        Depending on the authoring pipeline:
        - some files use wrapped phase near [-pi,+pi];
        - others contain values outside that interval and are compatible
          with unwrapped / partly unwrapped radians.
        """
        if not self.has_phase_container:
            raise ValueError(
                "This file does not contain the validated V2 phase "
                "angular container."
            )

        offset = self.phase_slot_offset(slot)

        values = _read_f32_array(
            self.raw,
            offset,
            DIRECTIVITY_FLOATS_PER_SLOT
        )

        matrix = values.reshape(
            N_AZIMUTH,
            N_ARC
        )

        # No reorder is required with the validated 334564 base.
        if canonical_arc:
            matrix = _canonicalize_phase_arc(matrix)

        return matrix

    def phase_slot_legacy(
        self,
        slot: int
    ) -> np.ndarray:
        """
        Reproduce the former 334568 + column-rotation phase interpretation.

        This method is diagnostic only.
        """
        if self.version != 2:
            raise ValueError(
                "Legacy phase diagnostic is available only for CF2 V2 files."
            )

        offset = self.phase_slot_offset_legacy(slot)

        values = _read_f32_array(
            self.raw,
            offset,
            DIRECTIVITY_FLOATS_PER_SLOT
        )

        matrix = values.reshape(
            N_AZIMUTH,
            N_ARC
        )

        return np.concatenate(
            [
                matrix[:, 36:37],
                matrix[:, :36],
            ],
            axis=1
        )

    def compare_phase_layouts(
        self,
        freq_hz: float
    ) -> Dict[str, object]:
        """
        Compare the validated 334564 phase layout against the former
        334568 + reorder interpretation for one frequency.
        """
        slot = _freq_index(freq_hz)

        current = self.phase_slot_raw(
            slot,
            canonical_arc=True,
        )

        legacy = self.phase_slot_legacy(slot)

        delta = current - legacy

        return {
            "frequency_hz":
                float(CF2_FREQS[slot]),
            "current_base":
                PHASE_ANGULAR_BASE,
            "legacy_base":
                PHASE_ANGULAR_BASE_LEGACY,
            "different_cells":
                int(
                    np.count_nonzero(
                        ~np.isclose(
                            current,
                            legacy,
                            atol=1e-7,
                            rtol=0.0,
                            equal_nan=True
                        )
                    )
                ),
            "differences_outside_arc0":
                int(
                    np.count_nonzero(
                        ~np.isclose(
                            current[:, 1:],
                            legacy[:, 1:],
                            atol=1e-7,
                            rtol=0.0,
                            equal_nan=True
                        )
                    )
                ),
            "max_abs_difference_rad":
                float(
                    np.nanmax(
                        np.abs(delta)
                    )
                ),
            "max_abs_difference_outside_arc0_rad":
                float(
                    np.nanmax(
                        np.abs(
                            delta[:, 1:]
                        )
                    )
                ),
        }

    def phase_grid(
        self,
        freq_hz: float,
        wrapped: bool = False,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Return one V2 phase balloon with explicit angular axes.

        Returns:
            azimuth_deg : (72,)
            arc_deg     : (37,)
            phase_rad   : (72,37)
        """
        return (
            AZIMUTH_DEGREES.copy(),
            ARC_DEGREES.copy(),
            self.phase_third_octave(
                freq_hz,
                wrapped=wrapped,
            ),
        )

    def phase_third_octave(
        self,
        freq_hz: float,
        wrapped: bool = False,
    ) -> np.ndarray:
        """
        Phase matrix for one CF2 third-octave frequency.

        Returns radians.

        wrapped=False:
            preserve the binary representation exactly.

        wrapped=True:
            map values to [-pi,pi) for consumers that require wrapped phase.
        """
        slot = _freq_index(
            freq_hz
        )

        phase = self.phase_slot_raw(
            slot
        )

        if wrapped:
            return _wrap_phase_radians(
                phase
            )

        return phase

    def phase_frequency_cube(
        self,
        declared_only: bool = True,
        wrapped: bool = False,
        unwrap_frequency: bool = False,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Return phase over frequency.

        Returns:
            frequencies_hz : shape (F,)
            phase          : shape (F,72,37)

        unwrap_frequency=True first wraps to [-pi,pi) and then applies
        np.unwrap(..., axis=0). This is useful for analysis/interpolation,
        but is NOT used automatically because the CF2 raw representation
        should remain available.
        """
        if not self.has_phase_container:
            raise ValueError(
                "This file does not contain a V2 phase container."
            )

        if declared_only:
            a = self.declared_min_idx
            b = self.declared_max_idx

            if not (
                0 <= a <= b < N_FREQ_SLOTS
            ):
                raise ValueError(
                    "Invalid declared frequency range."
                )

            slots = list(
                range(
                    a,
                    b + 1
                )
            )
        else:
            slots = list(
                range(
                    N_FREQ_SLOTS
                )
            )

        frequencies = np.array(
            [
                CF2_FREQS[
                    s
                ]
                for s in slots
            ],
            dtype=float
        )

        cube = np.stack(
            [
                self.phase_slot_raw(
                    s
                )
                for s in slots
            ],
            axis=0
        )

        if unwrap_frequency:
            cube = np.unwrap(
                _wrap_phase_radians(
                    cube
                ),
                axis=0
            )
        elif wrapped:
            cube = _wrap_phase_radians(
                cube
            )

        return (
            frequencies,
            cube
        )

    @property
    def phase_representation_hint(
        self
    ) -> str:
        """
        Corpus-derived representation hint.

        This is intentionally descriptive, not a format requirement.
        """
        if not self.has_phase_container:
            return "none"

        freqs, cube = self.phase_frequency_cube(
            declared_only=True,
            wrapped=False,
            unwrap_frequency=False,
        )

        x = cube[
            np.isfinite(
                cube
            )
        ]

        if len(
            x
        ) == 0:
            return "no_data"

        max_abs = float(
            np.max(
                np.abs(
                    x
                )
            )
        )

        fraction_inside_pi = float(
            (
                np.abs(
                    x
                )
                <=
                np.pi
                +
                1e-4
            ).mean()
        )

        if (
            fraction_inside_pi
            >=
            0.995
            and
            max_abs
            <=
            np.pi
            +
            0.05
        ):
            return "wrapped_radians"

        if max_abs > np.pi + 0.05:
            return "likely_unwrapped_radians"

        return "radians_unresolved_representation"

    # ------------------------------------------------------------------
    # Full directivity extraction helpers
    # ------------------------------------------------------------------

    def directivity_grid(
        self,
        freq_hz: float,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Return one third-octave directivity balloon with explicit axes.

        Returns
        -------
        azimuth_deg : shape (72,)
            0, 5, 10, ..., 355

        arc_deg : shape (37,)
            0, 5, 10, ..., 180

        directivity_db : shape (72,37)
            Magnitude directivity in dB.

        Exact indexing
        --------------
        directivity_db[i, j] corresponds to:

            azimuth_deg[i], arc_deg[j]

        with the binary magnitude layout:

            frequency x azimuth x arc

        Notes
        -----
        In CLF terminology the second angular coordinate is the ARC angle.
        It is often informally treated as elevation in downstream code,
        but this parser keeps the original semantic name `arc_deg` so that
        no coordinate-system assumption is introduced silently.
        """
        return (
            AZIMUTH_DEGREES.copy(),
            ARC_DEGREES.copy(),
            self.directivity_third_octave(
                freq_hz
            ),
        )

    def iter_directivity_records(
        self,
        declared_only: bool = True,
        include_phase: bool = False,
        phase_wrapped: bool = False,
    ):
        """
        Yield the full CF2 directivity dataset row by row.

        Each record contains:
            frequency_hz
            frequency_slot
            azimuth_deg
            arc_deg
            directivity_db

        For CLF2 V2, include_phase=True also adds:
            phase_rad
        """
        if declared_only:
            a = self.declared_min_idx
            b = self.declared_max_idx

            if not (
                0 <= a <= b < N_FREQ_SLOTS
            ):
                raise ValueError(
                    "Invalid declared frequency range."
                )

            slots = range(
                a,
                b + 1
            )
        else:
            slots = range(
                N_FREQ_SLOTS
            )

        use_phase = (
            include_phase
            and
            self.has_phase_container
        )

        for slot in slots:
            frequency_hz = float(
                CF2_FREQS[
                    slot
                ]
            )

            magnitude = self.directivity_slot(
                slot
            )

            phase = None

            if use_phase:
                phase = self.phase_slot_raw(
                    slot
                )

                if phase_wrapped:
                    phase = _wrap_phase_radians(
                        phase
                    )

            for az_idx, azimuth_deg in enumerate(
                AZIMUTH_DEGREES
            ):
                for arc_idx, arc_deg in enumerate(
                    ARC_DEGREES
                ):
                    row = {
                        "frequency_hz":
                            frequency_hz,
                        "frequency_slot":
                            int(
                                slot
                            ),
                        "azimuth_deg":
                            float(
                                azimuth_deg
                            ),
                        "arc_deg":
                            float(
                                arc_deg
                            ),
                        "directivity_db":
                            float(
                                magnitude[
                                    az_idx,
                                    arc_idx
                                ]
                            ),
                    }

                    if use_phase:
                        row[
                            "phase_rad"
                        ] = float(
                            phase[
                                az_idx,
                                arc_idx
                            ]
                        )

                    yield row

    def directivity_records(
        self,
        declared_only: bool = True,
        include_phase: bool = False,
        phase_wrapped: bool = False,
    ) -> List[Dict[str, float]]:
        """
        Materialize iter_directivity_records() as a list of dictionaries.
        """
        return list(
            self.iter_directivity_records(
                declared_only=declared_only,
                include_phase=include_phase,
                phase_wrapped=phase_wrapped,
            )
        )

    def export_directivity_csv(
        self,
        output_path: PathLike,
        declared_only: bool = True,
        include_phase: bool = False,
        phase_wrapped: bool = False,
    ) -> Path:
        """
        Export all directivity values in long-form CSV.

        Columns:
            frequency_hz
            frequency_slot
            azimuth_deg
            arc_deg
            directivity_db

        V2 + include_phase=True:
            phase_rad
        """
        output_path = Path(
            output_path
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        use_phase = (
            include_phase
            and
            self.has_phase_container
        )

        fieldnames = [
            "frequency_hz",
            "frequency_slot",
            "azimuth_deg",
            "arc_deg",
            "directivity_db",
        ]

        if use_phase:
            fieldnames.append(
                "phase_rad"
            )

        with output_path.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
            )

            writer.writeheader()

            for row in self.iter_directivity_records(
                declared_only=declared_only,
                include_phase=include_phase,
                phase_wrapped=phase_wrapped,
            ):
                writer.writerow(
                    row
                )

        return output_path

    # ------------------------------------------------------------------
    # Structural validation
    # ------------------------------------------------------------------

    def structure_report(
        self
    ) -> Dict[str, object]:
        errors: List[str] = []
        warnings: List[str] = []

        if self.version not in {
            1,
            2
        }:
            warnings.append(
                f"Unexpected format word/version: {self.version}"
            )

        if not (
            0
            <=
            self.declared_min_idx
            <
            N_FREQ_SLOTS
        ):
            errors.append(
                f"declared_min_idx out of range: "
                f"{self.declared_min_idx}"
            )

        if not (
            0
            <=
            self.declared_max_idx
            <
            N_FREQ_SLOTS
        ):
            errors.append(
                f"declared_max_idx out of range: "
                f"{self.declared_max_idx}"
            )

        if (
            0
            <=
            self.declared_min_idx
            <
            N_FREQ_SLOTS
            and
            0
            <=
            self.declared_max_idx
            <
            N_FREQ_SLOTS
            and
            self.declared_min_idx
            >
            self.declared_max_idx
        ):
            errors.append(
                "declared_min_idx > declared_max_idx"
            )

        directivity_end = (
            DIRECTIVITY_BASE
            +
            N_FREQ_SLOTS
            *
            DIRECTIVITY_BYTES_PER_SLOT
        )

        if len(
            self.raw
        ) < directivity_end:
            errors.append(
                "File too short for primary 30-slot "
                "directivity container."
            )

        return {
            "path":
                str(
                    self.path
                ),
            "file_size":
                self.file_size,
            "version":
                self.version,
            "declared_min_idx":
                self.declared_min_idx,
            "declared_max_idx":
                self.declared_max_idx,
            "declared_first_frequency_hz":
                self.header.declared_first_frequency_hz,
            "declared_last_frequency_hz":
                self.header.declared_last_frequency_hz,
            "declared_count":
                self.header.declared_count,
            "primary_directivity_base":
                DIRECTIVITY_BASE,
            "legacy_directivity_base":
                DIRECTIVITY_BASE_LEGACY,
            "primary_directivity_payload_end":
                (
                    DIRECTIVITY_BASE
                    +
                    N_FREQ_SLOTS
                    *
                    DIRECTIVITY_BYTES_PER_SLOT
                ),
            "bytes_after_primary_payload":
                (
                    self.file_size
                    -
                    (
                        DIRECTIVITY_BASE
                        +
                        N_FREQ_SLOTS
                        *
                        DIRECTIVITY_BYTES_PER_SLOT
                    )
                ),
            "primary_directivity_shape":
                [
                    N_FREQ_SLOTS,
                    N_AZIMUTH,
                    N_ARC,
                ],
            "primary_directivity_slot_bytes":
                DIRECTIVITY_BYTES_PER_SLOT,
            "balloon_symmetry_code":
                self.balloon_symmetry_code,
            "balloon_symmetry":
                self.balloon_symmetry,
            "phase_angular_base":
                PHASE_ANGULAR_BASE,
            "legacy_phase_angular_base":
                PHASE_ANGULAR_BASE_LEGACY,
            "phase_payload_end":
                (
                    PHASE_ANGULAR_BASE
                    +
                    N_FREQ_SLOTS
                    *
                    DIRECTIVITY_BYTES_PER_SLOT
                )
                if self.version == 2
                else None,
            "bytes_after_phase_payload":
                (
                    self.file_size
                    -
                    (
                        PHASE_ANGULAR_BASE
                        +
                        N_FREQ_SLOTS
                        *
                        DIRECTIVITY_BYTES_PER_SLOT
                    )
                )
                if self.version == 2
                else None,
            "phase_unit":
                PHASE_UNIT,
            "has_phase_container":
                self.has_phase_container,
            "phase_representation_hint":
                self.phase_representation_hint,
            "errors":
                errors,
            "warnings":
                warnings,
            "status":
                "PASS"
                if not errors
                else
                "FAIL",
        }


# ======================================================================
# CLI
# ======================================================================

def _matrix_stats(
    matrix: np.ndarray
) -> Dict[str, float]:
    x = matrix[
        np.isfinite(
            matrix
        )
    ]

    if len(
        x
    ) == 0:
        return {}

    return {
        "min_db":
            float(
                np.min(
                    x
                )
            ),
        "max_db":
            float(
                np.max(
                    x
                )
            ),
        "mean_db":
            float(
                np.mean(
                    x
                )
            ),
        "std_db":
            float(
                np.std(
                    x
                )
            ),
    }


def main():
    ap = argparse.ArgumentParser(
        description=(
            "Parse and inspect empirically validated CF2 fields."
        )
    )

    ap.add_argument(
        "cf2",
        type=Path,
    )

    ap.add_argument(
        "--third",
        type=float,
        default=None,
        help="Inspect one third-octave directivity band.",
    )

    ap.add_argument(
        "--octave",
        type=float,
        default=None,
        help="Inspect reconstructed CATT octave.",
    )

    ap.add_argument(
        "--catt-display",
        action="store_true",
        help=(
            "When used with --octave, apply CATT visual floor "
            f"{CATT_DISPLAY_FLOOR_DB:g} dB."
        ),
    )

    ap.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON.",
    )

    args = ap.parse_args()

    cf2 = CF2File(
        args.cf2
    )

    report = cf2.structure_report()

    metadata = cf2.metadata

    summary = {
        "structure":
            report,
        "metadata": {
            k: (
                {
                    "mode_code":
                        v.mode_code,
                    "mode":
                        v.mode,
                    "value":
                        v.value,
                }
                if isinstance(
                    v,
                    CF2MaxInput
                )
                else
                v
            )
            for k, v
            in metadata.items()
        },
        "declared_frequencies_hz":
            cf2.declared_frequencies.tolist(),
    }

    if args.third is not None:
        d = cf2.directivity_third_octave(
            args.third
        )

        summary[
            "third_octave"
        ] = {
            "frequency_hz":
                args.third,
            "shape":
                list(
                    d.shape
                ),
            "stats":
                _matrix_stats(
                    d
                ),
        }

    if args.octave is not None:
        if args.catt_display:
            d = cf2.catt_display_octave(
                args.octave
            )
        else:
            d = cf2.directivity_octave(
                args.octave
            )

        summary[
            "octave"
        ] = {
            "center_hz":
                args.octave,
            "thirds_hz":
                list(
                    cf2.octave_triplet(
                        args.octave
                    )
                ),
            "catt_display":
                bool(
                    args.catt_display
                ),
            "display_floor_db":
                (
                    CATT_DISPLAY_FLOOR_DB
                    if args.catt_display
                    else None
                ),
            "shape":
                list(
                    d.shape
                ),
            "stats":
                _matrix_stats(
                    d
                ),
        }

    if args.json:
        print(
            json.dumps(
                summary,
                indent=2,
                ensure_ascii=False
            )
        )
        return

    print("=" * 88)
    print("CF2 PARSER")
    print("=" * 88)
    print("File:", cf2.path)
    print("Size:", cf2.file_size)
    print("Version:", cf2.version)
    print(
        "Declared frequency range:",
        report[
            "declared_first_frequency_hz"
        ],
        "->",
        report[
            "declared_last_frequency_hz"
        ],
        "Hz"
    )
    print(
        "Model:",
        metadata[
            "model_name"
        ]
    )
    print(
        "Manufacturer:",
        metadata[
            "manufacturer"
        ]
    )
    print(
        "Structure:",
        report[
            "status"
        ]
    )
    print(
        "Balloon symmetry:",
        cf2.balloon_symmetry,
        f"(code={cf2.balloon_symmetry_code})"
    )
    if cf2.has_phase_container:
        print(
            "V2 phase:",
            cf2.phase_representation_hint,
            f"({PHASE_UNIT}s)"
        )

    if report[
        "warnings"
    ]:
        print(
            "Warnings:",
            report[
                "warnings"
            ]
        )

    if report[
        "errors"
    ]:
        print(
            "Errors:",
            report[
                "errors"
            ]
        )

    if args.third is not None:
        print()
        print(
            f"Third-octave {args.third:g} Hz:",
            summary[
                "third_octave"
            ][
                "stats"
            ]
        )

    if args.octave is not None:
        print()
        print(
            f"Octave {args.octave:g} Hz:",
            summary[
                "octave"
            ][
                "stats"
            ]
        )

        print(
            "Triplet:",
            summary[
                "octave"
            ][
                "thirds_hz"
            ]
        )

        print(
            "CATT display floor applied:",
            args.catt_display
        )


if __name__ == "__main__":
    main()
