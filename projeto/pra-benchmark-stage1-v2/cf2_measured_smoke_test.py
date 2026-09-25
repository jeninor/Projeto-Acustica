"""
CF2 -> Pyroomacoustics MeasuredDirectivity smoke test.

Goal
----
Validate the frequency-dependent integration path in pyroomacoustics 0.10.1
WITHOUT rebuilding Docker and WITHOUT converting to SOFA yet.

The script:
1. Reads the validated CF2 magnitude block.
2. Keeps only non-zero frequency bands.
3. Deduplicates the two spherical poles -> 2522 physical directions.
4. Builds a short minimum-phase FIR for every direction.
5. Creates pyroomacoustics.directivities.MeasuredDirectivity directly.
6. Runs an AnechoicRoom / max-order-0 style test with six canonical directions.
7. Compares the 1 kHz RIR level against the original CF2 values.

Current validated CF2 magnitude layout
--------------------------------------
base       = 14232 bytes
shape      = [30 frequencies, 72 rotations, 37 arcs]
rotation   = 0..355 deg, step 5
arc        = 0..180 deg, step 5
dtype      = little-endian float32
order      = [frequency][rotation][arc]

Local speaker frame used
------------------------
+X = front
+Y = left
+Z = top

For this symmetric loudspeaker, the unresolved rotation handedness does not
change magnitude values.
"""

import argparse
import math
import time
from pathlib import Path

import numpy as np
import pyroomacoustics as pra
from pyroomacoustics.directivities import MeasuredDirectivity, Rotation3D
from pyroomacoustics.doa import GridSphere


FREQUENCIES_HZ = np.array([
    25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200,
    250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000,
    2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000,
], dtype=float)

BASE_OFFSET = 14232
N_FREQ = 30
N_ROT = 72
N_ARC = 37
STEP_DEG = 5.0
DTYPE = np.dtype("<f4")
FLOATS_PER_BAND = N_ROT * N_ARC
BYTES_PER_BAND = FLOATS_PER_BAND * DTYPE.itemsize


def load_balloon(path, base_offset):
    raw = Path(path).read_bytes()
    needed = base_offset + N_FREQ * BYTES_PER_BAND

    if needed > len(raw):
        raise RuntimeError(
            f"CF2 too short: size={len(raw)}, needed={needed}"
        )

    x = np.frombuffer(
        raw,
        dtype=DTYPE,
        count=N_FREQ * FLOATS_PER_BAND,
        offset=base_offset,
    ).copy()

    return x.reshape(N_FREQ, N_ROT, N_ARC)


def detect_valid_bands(balloon):
    valid_mask = ~np.all(balloon == 0.0, axis=(1, 2))
    valid_idx = np.flatnonzero(valid_mask)

    if len(valid_idx) == 0:
        raise RuntimeError("No non-zero CF2 directivity bands.")

    expected = np.arange(valid_idx[0], valid_idx[-1] + 1)
    if not np.array_equal(valid_idx, expected):
        raise RuntimeError(
            f"Valid bands are not contiguous: {valid_idx.tolist()}"
        )

    return valid_idx


def cf2_local_vector(rotation_deg, arc_deg):
    """
    CF2 spherical coordinates -> local Cartesian unit vector.

    rotation=0 reference arc passes through +Z.
    positive rotation used here: top -> +Y (left).
    """
    r = math.radians(float(rotation_deg))
    a = math.radians(float(arc_deg))

    return np.array([
        math.cos(a),
        math.sin(a) * math.sin(r),
        math.sin(a) * math.cos(r),
    ], dtype=float)


def build_unique_direction_grid(balloon, valid_idx):
    """
    Remove duplicate front/back pole entries.

    Physical direction count:
        1 front + 72*35 interior + 1 back = 2522
    """
    directions = []
    rotation_indices = []
    arc_indices = []

    # Front pole once.
    directions.append(cf2_local_vector(0.0, 0.0))
    rotation_indices.append(0)
    arc_indices.append(0)

    # Interior arcs: 5..175 deg for all rotations.
    for ri in range(N_ROT):
        rdeg = ri * STEP_DEG
        for ai in range(1, N_ARC - 1):
            adeg = ai * STEP_DEG
            directions.append(cf2_local_vector(rdeg, adeg))
            rotation_indices.append(ri)
            arc_indices.append(ai)

    # Back pole once.
    directions.append(cf2_local_vector(0.0, 180.0))
    rotation_indices.append(0)
    arc_indices.append(N_ARC - 1)

    directions = np.asarray(directions, dtype=float)
    ri = np.asarray(rotation_indices, dtype=int)
    ai = np.asarray(arc_indices, dtype=int)

    # Shape: (n_valid_bands, n_directions)
    values_db = balloon[valid_idx][:, ri, ai]

    return directions, ri, ai, values_db


def interpolate_frequency_db(
    valid_freqs,
    values_db,
    target_freqs,
):
    """
    Log-frequency interpolation of relative directivity level.

    Out-of-range policy for this smoke test:
      clamp to the nearest valid CF2 pattern.

    At fs=16 kHz the upper FFT limit is 8 kHz, so only the <100 Hz
    extrapolation is actually used.
    """
    valid_freqs = np.asarray(valid_freqs, dtype=float)
    target_freqs = np.asarray(target_freqs, dtype=float)
    values_db = np.asarray(values_db, dtype=float)

    n_dirs = values_db.shape[1]
    result = np.empty(
        (n_dirs, len(target_freqs)),
        dtype=np.float64,
    )

    log_valid = np.log(valid_freqs)

    for k, f in enumerate(target_freqs):
        f_eff = float(np.clip(
            f,
            valid_freqs[0],
            valid_freqs[-1],
        ))

        # Exact or clamped endpoint.
        if f_eff <= valid_freqs[0]:
            result[:, k] = values_db[0]
            continue

        if f_eff >= valid_freqs[-1]:
            result[:, k] = values_db[-1]
            continue

        hi = int(np.searchsorted(valid_freqs, f_eff, side="right"))
        lo = hi - 1

        if np.isclose(f_eff, valid_freqs[lo], rtol=0.0, atol=1e-12):
            result[:, k] = values_db[lo]
            continue

        lf = math.log(f_eff)
        w = (
            (lf - log_valid[lo])
            /
            (log_valid[hi] - log_valid[lo])
        )

        result[:, k] = (
            (1.0 - w) * values_db[lo]
            + w * values_db[hi]
        )

    return result


def minimum_phase_firs_from_magnitude(magnitude_rfft, n_fft):
    """
    Vectorized homomorphic minimum-phase reconstruction.

    magnitude_rfft shape: (n_directions, n_fft//2 + 1)
    returns FIRs shape:   (n_directions, n_fft)
    """
    mag = np.asarray(magnitude_rfft, dtype=np.float64)
    mag = np.maximum(mag, 1e-12)

    log_mag_half = np.log(mag)

    # Build a real-even full log-magnitude spectrum.
    if n_fft % 2 == 0:
        mirror = log_mag_half[:, 1:-1][:, ::-1]
    else:
        mirror = log_mag_half[:, 1:][:, ::-1]

    log_mag_full = np.concatenate(
        [log_mag_half, mirror],
        axis=1,
    )

    cep = np.fft.ifft(
        log_mag_full,
        axis=1,
    ).real

    cep_min = np.zeros_like(cep)
    cep_min[:, 0] = cep[:, 0]

    if n_fft % 2 == 0:
        cep_min[:, 1:n_fft // 2] = (
            2.0 * cep[:, 1:n_fft // 2]
        )
        cep_min[:, n_fft // 2] = cep[:, n_fft // 2]
    else:
        cep_min[:, 1:(n_fft + 1) // 2] = (
            2.0 * cep[:, 1:(n_fft + 1) // 2]
        )

    h_min_spec = np.exp(
        np.fft.fft(
            cep_min,
            axis=1,
        )
    )

    firs = np.fft.ifft(
        h_min_spec,
        axis=1,
    ).real

    return firs.astype(np.float32)


def build_measured_directivity(
    cf2_path,
    fs,
    n_fft,
    base_offset,
):
    t0 = time.perf_counter()

    balloon = load_balloon(
        cf2_path,
        base_offset,
    )

    valid_idx = detect_valid_bands(
        balloon
    )

    valid_freqs = FREQUENCIES_HZ[
        valid_idx
    ]

    directions, ri, ai, values_db = (
        build_unique_direction_grid(
            balloon,
            valid_idx,
        )
    )

    fft_freqs = np.fft.rfftfreq(
        n_fft,
        d=1.0 / fs,
    )

    spectrum_db = interpolate_frequency_db(
        valid_freqs,
        values_db,
        fft_freqs,
    )

    magnitude = np.power(
        10.0,
        spectrum_db / 20.0,
    )

    firs = minimum_phase_firs_from_magnitude(
        magnitude,
        n_fft,
    )

    # Validate reconstructed FIR magnitude.
    reconstructed = np.abs(
        np.fft.rfft(
            firs.astype(np.float64),
            n=n_fft,
            axis=1,
        )
    )

    spectral_error_db = (
        20.0 * np.log10(
            np.maximum(
                reconstructed,
                1e-12,
            )
        )
        - spectrum_db
    )

    max_spectral_error_db = float(
        np.max(
            np.abs(
                spectral_error_db
            )
        )
    )

    # GridSphere uses cartesian points shaped (3, n_points).
    grid = GridSphere(
        cartesian_points=directions.T
    )

    # Identity: local CF2 frame is currently aligned with world XYZ.
    orientation = Rotation3D(
        [0.0, 0.0],
        "yz",
        degrees=True,
    )

    directivity = MeasuredDirectivity(
        orientation=orientation,
        grid=grid,
        impulse_responses=firs,
        fs=fs,
    )

    elapsed = time.perf_counter() - t0

    return {
        "directivity": directivity,
        "balloon": balloon,
        "valid_idx": valid_idx,
        "valid_freqs": valid_freqs,
        "directions": directions,
        "rotation_indices": ri,
        "arc_indices": ai,
        "values_db": values_db,
        "firs": firs,
        "fft_freqs": fft_freqs,
        "spectrum_db": spectrum_db,
        "max_spectral_error_db": max_spectral_error_db,
        "build_elapsed_s": elapsed,
    }


def canonical_cases(balloon, freq_index):
    """
    Canonical directions and their exact CF2 stored values.
    """
    cases = [
        ("FRONT",  np.array([+1.0,  0.0,  0.0]),  0,  0),
        ("TOP",    np.array([ 0.0,  0.0, +1.0]),  0, 18),
        ("LEFT",   np.array([ 0.0, +1.0,  0.0]), 18, 18),
        ("RIGHT",  np.array([ 0.0, -1.0,  0.0]), 54, 18),
        ("BOTTOM", np.array([ 0.0,  0.0, -1.0]), 36, 18),
        ("BACK",   np.array([-1.0,  0.0,  0.0]),  0, 36),
    ]

    out = []

    for name, vec, ri, ai in cases:
        out.append({
            "name": name,
            "vector": vec,
            "rotation_index": ri,
            "arc_index": ai,
            "expected_db": float(
                balloon[
                    freq_index,
                    ri,
                    ai,
                ]
            ),
        })

    return out


def response_db_at_frequency(ir, fs, n_fft_eval, frequency_hz):
    spec = np.fft.rfft(
        np.asarray(ir, dtype=float),
        n=n_fft_eval,
    )

    freqs = np.fft.rfftfreq(
        n_fft_eval,
        d=1.0 / fs,
    )

    k = int(
        np.argmin(
            np.abs(
                freqs - frequency_hz
            )
        )
    )

    return (
        20.0 * math.log10(
            max(
                abs(spec[k]),
                1e-15,
            )
        ),
        float(freqs[k]),
    )


def directivity_only_check(
    directivity,
    cases,
    fs,
    test_freq_hz,
):
    print()
    print("=== Directivity object check at 1 kHz ===")

    front_db = None
    rows = []

    for case in cases:
        ir = directivity.get_response_cartesian(
            case["vector"][None, :]
        )[0]

        db, actual_freq = response_db_at_frequency(
            ir,
            fs,
            16384,
            test_freq_hz,
        )

        if front_db is None:
            front_db = db

        rel_db = db - front_db

        rows.append(
            (
                case["name"],
                case["expected_db"],
                rel_db,
                rel_db - case["expected_db"],
                actual_freq,
            )
        )

    for name, expected, measured, error, actual_freq in rows:
        print(
            f"{name:7s} "
            f"expected={expected:9.4f} dB | "
            f"measured={measured:9.4f} dB | "
            f"error={error:+9.4f} dB | "
            f"fft_freq={actual_freq:.3f} Hz"
        )

    return rows


def anechoic_room_check(
    directivity,
    cases,
    fs,
    test_freq_hz,
):
    print()
    print("=== Pyroomacoustics AnechoicRoom check ===")

    room = pra.AnechoicRoom(
        fs=fs
    )

    source = np.array(
        [5.0, 5.0, 5.0],
        dtype=float,
    )

    radius = 2.0

    mic_positions = np.column_stack([
        source + radius * case["vector"]
        for case in cases
    ])

    room.add_microphone_array(
        mic_positions
    )

    room.add_source(
        source,
        directivity=directivity,
    )

    t0 = time.perf_counter()
    room.compute_rir()
    elapsed = time.perf_counter() - t0

    front_db = None
    rows = []

    for i, case in enumerate(cases):
        rir = np.asarray(
            room.rir[i][0],
            dtype=float,
        )

        db, actual_freq = response_db_at_frequency(
            rir,
            fs,
            16384,
            test_freq_hz,
        )

        if front_db is None:
            front_db = db

        rel_db = db - front_db

        rows.append(
            (
                case["name"],
                case["expected_db"],
                rel_db,
                rel_db - case["expected_db"],
                len(rir),
                actual_freq,
            )
        )

    print(
        "compute_rir_elapsed_s:",
        elapsed,
    )

    for name, expected, measured, error, rir_len, actual_freq in rows:
        print(
            f"{name:7s} "
            f"expected={expected:9.4f} dB | "
            f"RIR_rel={measured:9.4f} dB | "
            f"error={error:+9.4f} dB | "
            f"rir_len={rir_len:4d} | "
            f"fft_freq={actual_freq:.3f} Hz"
        )

    max_abs_error = max(
        abs(row[3])
        for row in rows
    )

    print(
        "max_abs_RIR_error_db:",
        max_abs_error,
    )

    return rows


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--cf2",
        required=True,
    )

    ap.add_argument(
        "--base",
        type=int,
        default=BASE_OFFSET,
    )

    ap.add_argument(
        "--fs",
        type=int,
        default=16000,
    )

    ap.add_argument(
        "--n-fft",
        type=int,
        default=512,
    )

    args = ap.parse_args()

    print(
        "=== CF2 -> MeasuredDirectivity smoke test ==="
    )

    print(
        "pyroomacoustics:",
        getattr(
            pra,
            "__version__",
            "unknown",
        )
    )

    print("fs:", args.fs)
    print("n_fft:", args.n_fft)
    print("CF2:", args.cf2)

    built = build_measured_directivity(
        cf2_path=args.cf2,
        fs=args.fs,
        n_fft=args.n_fft,
        base_offset=args.base,
    )

    print()
    print("=== CF2 build summary ===")
    print(
        "valid_frequencies_hz:",
        built["valid_freqs"].tolist(),
    )
    print(
        "n_unique_directions:",
        built["directions"].shape[0],
    )
    print(
        "expected_unique_directions:",
        2 + 72 * 35,
    )
    print(
        "firs_shape:",
        built["firs"].shape,
    )
    print(
        "max_FIR_spectral_reconstruction_error_db:",
        built["max_spectral_error_db"],
    )
    print(
        "build_elapsed_s:",
        built["build_elapsed_s"],
    )

    freq_index = int(
        np.where(
            FREQUENCIES_HZ == 1000.0
        )[0][0]
    )

    cases = canonical_cases(
        built["balloon"],
        freq_index,
    )

    directivity_only_check(
        directivity=built["directivity"],
        cases=cases,
        fs=args.fs,
        test_freq_hz=1000.0,
    )

    anechoic_room_check(
        directivity=built["directivity"],
        cases=cases,
        fs=args.fs,
        test_freq_hz=1000.0,
    )

    print()
    print("=== Interpretation ===")
    print(
        "PASS target: directivity-only errors near 0 dB and "
        "anechoic RIR errors small (ideally << 0.1 dB)."
    )
    print(
        "This test uses clamping below 100 Hz and a minimum-phase FIR. "
        "Those are explicit modeling choices, not CF2 parser facts."
    )


if __name__ == "__main__":
    main()
