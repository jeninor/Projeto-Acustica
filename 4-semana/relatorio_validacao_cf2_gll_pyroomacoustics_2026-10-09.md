# Relatório técnico — Validação CF2 × GLL e preparação para benchmark em Pyroomacoustics

**Data:** 09/10/2026  
**Projeto:** Integração e validação de diretividade de alto-falantes em Pyroomacoustics  
**Escopo:** atividades realizadas nesta etapa, desde a descoberta dos pares CF2/GLL até a validação cruzada multi-modelo e a definição do próximo benchmark externo.

---

## 1. Objetivo geral

O objetivo desta etapa foi validar, de forma independente do Pyroomacoustics, se os dados de diretividade extraídos de arquivos **CF2** e **GLL** correspondentes ao mesmo modelo físico de alto-falante apresentam comportamento espacial coerente.

A estratégia adotada foi:

1. localizar pares de arquivos CF2 e GLL do mesmo modelo;
2. validar metadados e frequências;
3. comparar diretamente as malhas de diretividade;
4. evitar qualquer influência de sala, reflexões ou RIR;
5. comparar os dois formatos em uma malha esférica comum;
6. repetir a validação em vários modelos;
7. consolidar os resultados antes de avançar para o benchmark em Pyroomacoustics.

Foram separadas duas perguntas principais:

- **Parser/dados:** CF2 e GLL descrevem padrões espaciais semelhantes para o mesmo alto-falante?
- **Simulação:** uma implementação GLL integrada ao Pyroomacoustics produz resultados e desempenho coerentes?

Nesta etapa, a primeira pergunta foi tratada de forma extensa e concluída com três modelos.

---

## 2. Inventário dos arquivos disponíveis

Foram contados os arquivos disponíveis nos dois conjuntos locais:

```text
CF2 files: 428
GLL files: 144
```

Diretórios principais:

```text
CF2:
/home/alunos/Documentos/Juan/mba/pyroomacoustic/windows/storage/speaker_cf2

GLL:
/home/alunos/Documentos/Juan/mba/gll-pyroom/speakers
```

O primeiro algoritmo de correspondência por nome foi rígido demais e inicialmente não encontrou pares. Em seguida foi criado um matcher aproximado que removeu partes como fabricante, versão, hífens e separadores.

Esse matcher revelou várias coincidências potenciais, mas também mostrou que scores altos podem ser falsos positivos quando nomes curtos coincidem, por exemplo `sub`, `ps`, `66` ou `64`. Por isso, os pares finais foram escolhidos por fabricante, modelo explícito e coerência dos metadados.

---

## 3. Pares selecionados

Os pares considerados mais confiáveis foram da **d&b audiotechnik**:

```text
d&b audiotechnik-M4.CF2
<-> d&b/Monitors/M4.gll

d&b audiotechnik-M6.CF2
<-> d&b/Monitors/M6.gll

d&b audiotechnik-MAX2.CF2
<-> d&b/Monitors/MAX2.gll
```

Esses três modelos foram usados na validação principal.

---

# 4. Validação do modelo M4

## 4.1. Preflight

Arquivos:

```text
CF2:
/home/alunos/Documentos/Juan/mba/pyroomacoustic/windows/storage/speaker_cf2/d&b audiotechnik-M4.CF2

GLL:
/home/alunos/Documentos/Juan/mba/gll-pyroom/speakers/d&b/Monitors/M4.gll
```

Metadados do CF2:

```text
model        : M4
manufacturer : d&b audiotechnik
distance     : 1.0 m
structure    : PASS
```

Frequências declaradas no CF2:

```text
100, 125, 160, 200, 250, 315, 400, 500, 630, 800,
1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000,
6300, 8000, 10000 Hz
```

Metadados do GLL:

```text
manufacturer : d&b audiotechnik
product      : M4 GLL
source count : 1
source key   : Z0800
source label : M4
nominal band : 55 -> 17000 Hz
measured dist: 1 m
on-axis level: 94 dB
meridian step: 5°
parallel step: 5°
symmetry     : 3
responses    : 667
```

O GLL possui espectro com:

```text
bands/octave : 24
point count  : 241
```

Foram confirmadas como bins nativos exatos as sete frequências:

```text
125, 250, 500, 1000, 2000, 4000, 8000 Hz
```

Sem interpolação de frequência.

---

## 4.2. Malha espacial comum

Os dois formatos foram comparados em uma representação física comum:

```text
1 polo frontal
72 meridianos × 35 paralelos não polares
1 polo traseiro
```

Total:

```text
1 + 72×35 + 1 = 2522 direções físicas únicas
```

A malha retangular completa é `72 × 37 = 2664`, mas os polos aparecem repetidos e foram deduplicados.

Com sete frequências:

```text
7 × 2522 = 17 654 comparações por modelo
```

---

## 4.3. Busca de alinhamento angular

Foi feita busca automática sobre:

```text
sign = +1 / -1
offset = 0 ... 355° em passos de 5°
```

com:

```text
CF2_rotation_index = offset + sign × GLL_meridian_index
```

Para o M4 apareceram quatro soluções equivalentes:

```text
sign=+1 offset=0°
sign=-1 offset=0°
sign=+1 offset=180°
sign=-1 offset=180°
```

Todas com:

```text
RMSE = 1.431098 dB
```

Conclusão:

> O M4 não permite determinar handedness de forma única, porque seu padrão possui simetria suficiente para tornar vários alinhamentos equivalentes.

O mapeamento escolhido para as métricas seguintes foi apenas uma solução representativa, não uma convenção universal CF2↔GLL.

---

## 4.4. Resultados M4 por frequência

| Frequência | RMSE relativo | MAE | Correlação | Erro máx. |
|---:|---:|---:|---:|---:|
| 125 Hz | 0.163 dB | 0.143 dB | 0.99701 | 0.330 dB |
| 250 Hz | 0.277 dB | 0.236 dB | 0.99815 | 0.660 dB |
| 500 Hz | 0.464 dB | 0.298 dB | 0.99813 | 1.410 dB |
| 1000 Hz | 2.092 dB | 1.760 dB | 0.97950 | 7.560 dB |
| 2000 Hz | 1.824 dB | 1.227 dB | 0.98527 | 12.131 dB |
| 4000 Hz | 1.745 dB | 1.316 dB | 0.99047 | 9.280 dB |
| 8000 Hz | 1.808 dB | 1.432 dB | 0.99440 | 6.150 dB |

Métricas globais:

```text
samples       : 17654
RMSE          : 1.431098 dB
MAE           : 0.916073 dB
bias          : -0.242624 dB
P50 abs       : 0.420033 dB
P95 abs       : 3.079719 dB
P99 abs       : 4.769972 dB
max abs       : 12.130623 dB
```

---

## 4.5. Diagnóstico por profundidade — M4

Foi criada uma análise adicional para identificar se as grandes diferenças aparecem no lóbulo principal ou principalmente em nulos profundos.

Exemplo em 2 kHz:

```text
>=  -3 dB : RMSE = 0.329 dB
>=  -6 dB : RMSE = 0.401 dB
>= -10 dB : RMSE = 0.387 dB
>= -20 dB : RMSE = 1.089 dB
>= -30 dB : RMSE = 1.578 dB
>= -40 dB : RMSE = 1.736 dB
```

O pior ponto em 2 kHz foi:

```text
meridian = 235°
parallel = 130°
CF2 = -28.800 dB
GLL = -40.931 dB
Δ   = +12.131 dB
```

Isso mostrou que o maior erro absoluto ocorre em região de forte atenuação.

---

# 5. Validação do modelo M6

Arquivos:

```text
CF2: d&b audiotechnik-M6.CF2
GLL: d&b/Monitors/M6.gll
```

Metadados principais:

```text
CF2:
model      : M6
distance   : 8 m
structure  : PASS

GLL:
product      : M6 GLL
source key   : Z0820 80x50
source label : M6 80x50
distance     : 1 m
```

A distância de medição difere entre CF2 e GLL. Isso não invalida a comparação relativa de diretividade, porque cada frequência é comparada após referência frontal, mas impede interpretar diretamente os resultados como equivalência absoluta de SPL.

Melhores alinhamentos:

```text
sign=+1 offset=90°
sign=-1 offset=90°
sign=+1 offset=270°
sign=-1 offset=270°
```

Métricas globais:

```text
RMSE      : 1.382243 dB
MAE       : 0.812485 dB
bias      : +0.503838 dB
P50       : 0.340215 dB
P95       : 3.219675 dB
P99       : 5.019708 dB
max       : 9.870101 dB
corr min  : 0.98595
```

| Frequência | RMSE relativo | MAE | Correlação |
|---:|---:|---:|---:|
| 125 Hz | 0.093 dB | 0.065 dB | 0.99354 |
| 250 Hz | 0.181 dB | 0.164 dB | 0.99977 |
| 500 Hz | 0.394 dB | 0.304 dB | 0.99726 |
| 1000 Hz | 2.204 dB | 1.755 dB | 0.99170 |
| 2000 Hz | 1.828 dB | 1.205 dB | 0.98595 |
| 4000 Hz | 1.164 dB | 0.770 dB | 0.99514 |
| 8000 Hz | 1.903 dB | 1.425 dB | 0.99414 |

O mesmo padrão observado no M4 reapareceu: excelente concordância nas baixas frequências e maiores diferenças em regiões profundas a partir de 1 kHz.

---

# 6. Validação do modelo MAX2

Arquivos:

```text
CF2: d&b audiotechnik-MAX2.CF2
GLL: d&b/Monitors/MAX2.gll
```

Metadados:

```text
CF2:
model      : MAX2
distance   : 8 m
structure  : PASS

GLL:
product      : MAX2
source key   : Z1120
distance     : 1 m
```

Melhores alinhamentos:

```text
sign=+1 offset=90°
sign=-1 offset=90°
```

Métricas globais:

```text
RMSE      : 1.648024 dB
MAE       : 0.898401 dB
bias      : +0.157613 dB
P50       : 0.379888 dB
P95       : 3.287023 dB
P99       : 6.539113 dB
max       : 17.610103 dB
corr min  : 0.97727
```

| Frequência | RMSE relativo | MAE | Correlação |
|---:|---:|---:|---:|
| 125 Hz | 0.053 dB | 0.043 dB | 0.99742 |
| 250 Hz | 0.210 dB | 0.175 dB | 0.99878 |
| 500 Hz | 0.416 dB | 0.308 dB | 0.99687 |
| 1000 Hz | 3.024 dB | 1.841 dB | 0.97727 |
| 2000 Hz | 1.538 dB | 1.130 dB | 0.98939 |
| 4000 Hz | 1.697 dB | 1.229 dB | 0.98876 |
| 8000 Hz | 2.098 dB | 1.563 dB | 0.98456 |

O maior erro foi:

```text
17.610 dB
```

em:

```text
1000 Hz
meridian = 5°
parallel = 120°
CF2 = -25.410 dB
GLL = -43.020 dB
```

Novamente, um nulo profundo.

---

# 7. Consolidação multi-modelo

Foi criado o script:

```text
scripts/summarize_cf2_gll_models.py
```

Entradas:

```text
results/m4_cf2_vs_gll_v2/summary.json
results/m6_cf2_vs_gll/summary.json
results/max2_cf2_vs_gll/summary.json
```

Saídas:

```text
results/cf2_gll_validation_summary/
├── model_summary.csv
├── per_frequency_summary.csv
├── error_vs_depth_all.csv
├── error_vs_depth_pooled_by_model.csv
├── worst_directions_all.csv
├── combined_summary.json
└── validation_summary.md
```

## 7.1. Comparação global

| Modelo | RMSE | MAE | Bias | P50 | P95 | P99 | Máx. | Corr. mínima |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| M4 | 1.431 | 0.916 | -0.243 | 0.420 | 3.080 | 4.770 | 12.131 | 0.97950 |
| M6 | 1.382 | 0.812 | +0.504 | 0.340 | 3.220 | 5.020 | 9.870 | 0.98595 |
| MAX2 | 1.648 | 0.898 | +0.158 | 0.380 | 3.287 | 6.539 | 17.610 | 0.97727 |

Total combinado:

```text
models        : 3
samples       : 52962
pooled RMSE   : 1.491601 dB
pooled MAE    : 0.875653 dB
pooled bias   : +0.139609 dB
```

O RMSE combinado foi calculado por ponderação quadrática:

\[
RMSE_{pool}
=
\sqrt{
\frac{\sum_i n_i RMSE_i^2}{\sum_i n_i}
}
\]

---

# 8. Resultado por profundidade de diretividade

Foi calculado RMSE acumulado apenas em regiões em que **CF2 e GLL estão simultaneamente acima de determinado nível relativo**.

| Modelo | ≥ -3 dB | ≥ -6 dB | ≥ -10 dB | ≥ -20 dB | ≥ -30 dB | ≥ -40 dB |
|---|---:|---:|---:|---:|---:|---:|
| M4 | 0.309 | 0.466 | 0.634 | 0.946 | 1.246 | 1.357 |
| M6 | 0.201 | 0.304 | 0.377 | 0.754 | 1.103 | 1.330 |
| MAX2 | 0.433 | 0.559 | 0.554 | 0.734 | 1.318 | 1.590 |

Na região principal de radiação, `>= -10 dB`, o RMSE permanece:

```text
M4    : 0.634 dB
M6    : 0.377 dB
MAX2  : 0.554 dB
```

Ou seja, aproximadamente meio decibel. À medida que regiões mais profundas são incluídas, o erro aumenta.

---

# 9. Interpretação técnica

## 9.1. Forte coerência espacial entre CF2 e GLL

Os três modelos apresentam:

- correlação mínima global por banda acima de aproximadamente `0.977`;
- erro mediano abaixo de `0.5 dB`;
- excelente concordância em 125, 250 e 500 Hz;
- grande concordância na região principal do padrão.

Isso é consistente com a hipótese de que os dois parsers estão recuperando corretamente a geometria de diretividade.

## 9.2. Os datasets não são numericamente idênticos

A comparação não mostrou igualdade ponto-a-ponto perfeita. As diferenças podem decorrer de:

- campanhas de medição distintas;
- distância de medição diferente;
- revisão diferente dos dados do fabricante;
- smoothing;
- interpolação;
- quantização;
- tratamento diferente de nulos;
- representação distinta em CF2 e GLL.

A conclusão correta é:

> CF2 e GLL apresentam padrões de diretividade altamente concordantes para os mesmos modelos físicos, particularmente na região de maior contribuição energética.

## 9.3. Erros máximos não representam o comportamento global

Valores como `12 dB` ou `17 dB` aparecem principalmente em regiões em que um dos datasets está próximo de `-30 dB` ou `-40 dB`. Pequenas mudanças no posicionamento ou profundidade de um nulo podem produzir diferenças muito grandes em dB sem representar uma diferença equivalente no campo principal.

Por isso foram consideradas mais informativas:

- RMSE;
- MAE;
- P50;
- P95;
- correlação;
- RMSE condicionado à profundidade.

## 9.4. Normalização frontal

A comparação principal usa:

\[
D_{rel}(\theta,\phi,f)
=
D(\theta,\phi,f)
-
D_{front}(f)
\]

Isso isola a forma espacial do padrão de diretividade. Nos três modelos, o valor frontal já era praticamente `0 dB`, portanto as métricas raw e front-normalized ficaram quase iguais.

---

# 10. Convenções angulares e simetria

A comparação mostrou que não se deve inferir uma transformação universal do tipo:

```text
GLL -> CF2 = offset fixo + handedness fixo
```

Resultados:

```text
M4
  offset 0° / 180°
  4 soluções equivalentes

M6
  offset 90° / 270°
  4 soluções equivalentes

MAX2
  offset 90°
  2 soluções equivalentes
```

Portanto:

> A busca de alinhamento é válida para comparar datasets, mas não deve substituir a convenção geométrica explícita implementada nos adapters.

Também foi identificado que os códigos numéricos de simetria não devem ser comparados diretamente entre CF2 e GLL, pois pertencem a enums diferentes.

---

# 11. Estado da integração GLL

Antes desta validação, já havia sido implementado e testado um adapter GLL para Pyroomacoustics:

```text
GLLSevenBandDirectivityFast
```

Fluxo:

```text
GLL binary
   ↓
gll-tools
   ↓
_libgll.so
   ↓
Python gll
   ↓
GLL acoustic API
   ↓
GLLSevenBandDirectivityFast
   ↓
Pyroomacoustics
```

Características principais:

- leitura das sete bandas 125–8000 Hz;
- grid GLL carregado uma única vez;
- interpolação espacial vetorizada;
- conversão dB → amplitude;
- suporte à orientação da fonte;
- saída compatível com o caminho multibanda do ISM;
- sem leitura nativa repetida durante cada image source.

A integração usa:

```python
src = SoundSource(
    SOURCE_POS,
    directivity=gll_directivity,
)
room.add(src)
```

---

# 12. Benchmark anterior do Pyroomacoustics usado como referência

Cenário-base:

```text
room        = 6.0 × 5.0 × 3.0 m
source      = [2.0, 2.5, 1.5] m
mics        = 25
grid        = 5 × 5
mic z       = 1.2 m
fs          = 16000 Hz
max_order   = 10
absorption  = 0.35
threads     = 1
```

Microfones:

```text
x = 1, 2, 3, 4, 5 m
y = 0.75, 1.625, 2.5, 3.375, 4.25 m
z = 1.2 m
```

Metodologia de timing:

- construção das directivities fora do cronômetro;
- construção da sala fora do cronômetro;
- somente `room.compute_rir()` cronometrado;
- warm-up balanceado;
- ordem dos casos rotacionada;
- 20 repetições medidas.

No benchmark 6H anterior foram usados:

```text
OMNI
CARDIOID
CF2_FAST_FULL
SOFA_NATIVE
SOFA_FAST_FULL_PHASE
```

Valores de referência:

```text
OMNI                   median ≈ 0.253 s
CARDIOID               median ≈ 0.258 s
CF2_FAST_FULL          median ≈ 0.274 s
SOFA_NATIVE            median ≈ 8.636 s
SOFA_FAST_FULL_PHASE   median ≈ 0.952 s
```

Esses resultados pertencem ao cenário anterior com AXYS4549 e servem como referência metodológica, não como comparação direta com M4.

---

# 13. Benchmark CF2 FAST × GLL FAST em Pyroomacoustics

Foi proposto um novo experimento com o **mesmo modelo M4 nos dois formatos**:

```text
OMNI
CARDIOID
CF2_FAST_M4
GLL_FAST_M4
```

mantendo o cenário do benchmark anterior.

Indicadores principais previstos:

```text
CF2_FAST_M4 / OMNI
GLL_FAST_M4 / OMNI
GLL_FAST_M4 / CF2_FAST_M4
```

Além da comparação espacial de energia entre M4 CF2 e M4 GLL por microfone.

Esse benchmark foi preparado conceitualmente, mas ainda não foi executado nesta etapa.

---

# 14. Próxima validação independente: simulador com GLL nativo

Antes do benchmark Pyroomacoustics CF2×GLL, foi proposta uma validação ainda mais independente:

```text
mesmo arquivo M4.gll
        │
        ├── simulador GLL nativo
        │
        └── gll-tools + Pyroomacoustics
```

A opção considerada prioritária foi **EASE Focus 3**.

A ideia inicial é comparar **campo direto**, eliminando reflexões:

```text
Pyroomacoustics:
max_order = 0
```

Geometria proposta:

```text
source = [2.0, 2.5, 1.5] m

25 pontos:
x = 1, 2, 3, 4, 5 m
y = 0.75, 1.625, 2.5, 3.375, 4.25 m
z = 1.2 m
```

Frequências:

```text
125, 250, 500, 1000, 2000, 4000, 8000 Hz
```

Primeiro em nível relativo:

\[
L_{rel}(p,f)
=
L(p,f)
-
L_{ref}(f)
\]

Métricas previstas:

```text
RMSE
MAE
bias
correlação
erro máximo
```

Esse experimento ainda está **planejado, mas não executado**.

---

# 15. Estado atual

## Concluído

- inventário de 428 CF2 e 144 GLL;
- busca automática por modelos equivalentes;
- seleção de M4, M6 e MAX2;
- preflight de metadados;
- confirmação das sete frequências comuns;
- comparação de 2522 direções × 7 bandas por modelo;
- busca de alinhamento angular;
- análise de simetria;
- comparação por frequência;
- análise de direções cardinais;
- localização dos piores erros;
- análise de erro por profundidade;
- consolidação multi-modelo;
- geração de CSVs, JSON e Markdown;
- validação cruzada de magnitude CF2 × GLL.

## Em preparação

- benchmark GLL dentro do Pyroomacoustics;
- benchmark contra simulador que use GLL nativamente;
- avaliação de campo direto;
- posterior comparação em sala completa.

---

# 16. Conclusão

A etapa atual fornece evidência consistente de que a cadeia:

```text
CF2 -> cf2_parser_v5
```

e a cadeia:

```text
GLL -> gll-tools -> Python
```

produzem descrições espaciais altamente coerentes quando aplicadas a modelos fisicamente equivalentes.

Foram avaliados:

```text
M4
M6
MAX2
```

em um total de:

```text
52 962 comparações
```

Resultado combinado:

```text
RMSE = 1.491601 dB
MAE  = 0.875653 dB
bias = +0.139609 dB
```

Na região principal do padrão, os erros foram consideravelmente menores. O comportamento observado em três modelos reduz a probabilidade de que a concordância seja um caso acidental de um único arquivo e fortalece a confiança na implementação dos parsers.

A próxima etapa metodologicamente mais forte é comparar o mesmo arquivo GLL contra um simulador que consuma GLL de forma nativa, preferencialmente em campo direto, antes de avançar para o benchmark completo dentro do Pyroomacoustics.

---

# 17. Arquivos principais produzidos

```text
scripts/compare_cf2_gll_same_model.py
scripts/summarize_cf2_gll_models.py

results/m4_cf2_vs_gll_v2/
results/m6_cf2_vs_gll/
results/max2_cf2_vs_gll/

results/cf2_gll_validation_summary/
    model_summary.csv
    per_frequency_summary.csv
    error_vs_depth_all.csv
    error_vs_depth_pooled_by_model.csv
    worst_directions_all.csv
    combined_summary.json
    validation_summary.md
```

---

# 18. Resumo executivo

> **A implementação de leitura GLL e o parser CF2 apresentam forte concordância espacial em três modelos d&b independentes, especialmente na região principal de radiação, estando as maiores diferenças concentradas em lóbulos secundários e nulos profundos.**

Isso fornece uma base técnica adequada para iniciar a próxima fase: **benchmark independente do GLL contra um simulador nativo e, em seguida, benchmark completo dentro do Pyroomacoustics**.
