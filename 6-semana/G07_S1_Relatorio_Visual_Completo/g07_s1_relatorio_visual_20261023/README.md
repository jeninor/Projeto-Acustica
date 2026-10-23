# Pacote do relatório G07-S1

**Relatório em português com 12 figuras e rastreabilidade dos dados da campanha 2026-10-23.**

Abra primeiro **`RELATORIO_G07_S1_Reconstrucao_e_Acustica.md`** em VS Code (Markdown Preview) ou no GitHub. Os caminhos de todas as imagens são relativos, por isso **não separe** o arquivo `.md` da pasta `figuras/`.

## Conteúdo

- `RELATORIO_G07_S1_Reconstrucao_e_Acustica.md`: texto técnico-científico com figuras e tabelas.
- `figuras/*.png`: doze figuras (gráficos e esquemas, **não** capturas do Blender).
- `dados/metricas_g07_s1.json`: valores numéricos transcritos das execuções.
- `gerar_figuras.py`: script de geração das imagens usando Matplotlib e NumPy.
- `evidencias/*.txt`: transcrições de terminal e saídas fornecidas pelo pesquisador.
- `MANIFESTO_SHA256.txt`: hashes para checagem dos arquivos entregues.

### Pré-visualização rápida no Ubuntu

```bash
unzip G07_S1_Relatorio_Visual_Completo.zip -d relatorio_g07_s1
cd relatorio_g07_s1/g07_s1_relatorio_visual_20261023
code RELATORIO_G07_S1_Reconstrucao_e_Acustica.md
```

### Regerar gráficos (opcional)

```bash
python3 -m pip install --user matplotlib numpy  # apenas se necessário
python3 gerar_figuras.py
```

Sem necessidade de Blender, COLMAP ou Pyroomacoustics para **ler** o relatório ou regerar os gráficos. As métricas não foram reexecutadas para este pacote.

## Advertências científicas

GT e Sim3 são empregados somente na avaliação, não na reconstrução métrica autônoma; a campanha possui uma única sala sintética, com restrições Manhattan/altura constante. G07-H1 é planejamento: **não existem resultados de Geral52** neste pacote. A singularidade acústica de G07-G5/G6 é documentada, sem ser eliminada dos resultados globais.
