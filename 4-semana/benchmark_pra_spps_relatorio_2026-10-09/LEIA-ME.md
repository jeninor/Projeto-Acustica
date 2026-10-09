# Entrega — Relatório PRA × I-Simpa/SPPS, 09/10/2026

Abra [`relatorio_benchmark_pra_spps_2026-10-09.md`](relatorio_benchmark_pra_spps_2026-10-09.md) no VS Code e use **Ctrl+Shift+V** para visualizar a prévia com as imagens.

O relatório inclui:

- 4 mapas de calor PNG em [`figuras/`](figuras/) (25 posições reais; R8 excluído dos indicadores);
- CSV original e resumo JSON em [`dados/`](dados/);
- script de benchmark preservado em [`codigo/`](codigo/);
- gerador dos mapas em [`gerar_mapas.py`](gerar_mapas.py), que pode ser executado com Python e Matplotlib/NumPy.

**Os arquivos estão ligados por caminhos relativos.** Mantenha a pasta completa ao mover o relatório para o projeto.

Para instalá-lo em seu projeto Ubuntu:

```bash
ROOT="$HOME/Documentos/Juan/mba/acoustic-simulator-benchmark"
mkdir -p "$ROOT/results/benchmarks/pra-vs-spps-multiformat"
unzip benchmark_pra_spps_relatorio_2026-10-09.zip -d "$ROOT/results/benchmarks/pra-vs-spps-multiformat/"
```

Se precisar reproduzir os quatro mapas:

```bash
cd benchmark_pra_spps_relatorio_2026-10-09
python3 gerar_mapas.py
```

**Nota:** os arquivos SOFA Bosch TF/FIR ainda são uma etapa futura do projeto. Os mapas e métricas atuais correspondem apenas a 1000 Hz com OMNI, CF2 V5 e CF2 FAST.
