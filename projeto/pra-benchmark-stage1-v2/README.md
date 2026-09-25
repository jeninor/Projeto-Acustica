# Stage 1 v2

Corrige el resumen de RIR: Pyroomacoustics usa `room.rir[microphone][source]`.
La v1 resumía sólo el micrófono 0. El tiempo de `compute_rir()` de la v1 sigue siendo útil,
pero sus métricas acústicas no representaban los 25 micrófonos.

Ejecutar:

```bash
docker compose build
docker compose run --rm pra-benchmark
```
