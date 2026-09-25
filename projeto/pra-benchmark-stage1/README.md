# Pyroomacoustics Benchmark — Stage 1

Baseline local Docker benchmark for omnidirectional vs cardioid sources.

## Build

```bash
docker compose build
```

## Run

```bash
docker compose run --rm pra-benchmark
```

Output:

```text
results/local_baseline.csv
```

The benchmark uses a fixed 3D shoebox room, 25 microphones, one warm-up, five measured runs, and single-thread settings. Only `room.compute_rir()` is timed.
