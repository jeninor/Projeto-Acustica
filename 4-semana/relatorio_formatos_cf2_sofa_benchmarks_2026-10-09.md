# Relatório técnico — formatos CF2/SOFA, validações e benchmarks em Pyroomacoustics

**Data:** 09/10/2026  
**Ambiente principal:** Python 3.12.3, Pyroomacoustics 0.10.1, NumPy 2.4.6  
**Cenário de referência:** sala 6 × 5 × 3 m, fonte em `[2.0, 2.5, 1.5]` m, 25 microfones, absorção 0.35, `max_order = 10`, execução single-thread (`PRA_NUM_THREADS=1`, `OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `MKL_NUM_THREADS=1`).

---

## 1. Objetivo

Este relatório consolida as descobertas realizadas durante a implementação e validação de diferentes representações de diretividade de alto-falantes em Pyroomacoustics, com foco em três famílias principais:

1. **CF2 binário**, usando uma implementação rápida de diretividade em bandas;
2. **SOFA `FreeFieldDirectivityTF` / `DataType=TF`**, representando a diretividade como função de transferência complexa em frequências medidas;
3. **SOFA `GeneralFIR` / `DataType=FIR`**, representando a diretividade diretamente como respostas impulsivas.

Além da leitura dos formatos, foram avaliados:

- fidelidade numérica;
- custo de preparação;
- tempo de `compute_rir`;
- impacto da frequência de amostragem;
- custo do `MeasuredDirectivity` padrão de Pyroomacoustics, incluindo Voronoi;
- desempenho do motor nativo;
- desempenho do motor otimizado 6G-E;
- variação com orientação da fonte;
- métricas acústicas derivadas dos RIRs;
- condições necessárias para comparação posterior com outros simuladores, como I-Simpa e Odeon.

---

# 2. Arquivos principais utilizados

## 2.1 Dados de diretividade

### CF2 original

```text
speaker_cf2/AXYS4549.CF2
```

Arquivo binário utilizado como origem da diretividade AXYS 4549.

### SOFA canônico em domínio da frequência

```text
clfviewer_validation/experiment6a_sofa_AXYS4549/
└── AXYS4549_FreeFieldDirectivityTF.sofa
```

Características validadas:

```text
SOFAConventions = FreeFieldDirectivityTF
DataType         = TF
direções         = 2522
frequências      = 24
faixa            = 100 Hz ... 20000 Hz
```

### SOFA em domínio temporal

```text
clfviewer_validation/experiment6b1_general_fir/
└── AXYS4549_GeneralFIR.sofa
```

Características principais:

```text
SOFAConventions = GeneralFIR
DataType         = FIR
direções         = 2522
fs original      = 48000 Hz
taps originais   = 2048
delay embutido   = 1024 amostras
                 = 21.333333 ms
```

Quando utilizado em uma sala com `fs = 16000 Hz`, esse FIR é reamostrado para aproximadamente:

```text
2522 × 683 taps
```

---

# 3. Scripts desenvolvidos e função de cada arquivo

## 3.1 Reconstrução CF2 e representação complexa

```text
cf2_parser_v5.py
```

Parser binário principal do CF2.

```text
experiment5g_canonical_complex2522.py
```

Responsável pela representação canônica de 2522 direções e construção da resposta complexa:

\[
H(f,\Omega)=10^{M(f,\Omega)/20}e^{j\phi(f,\Omega)}
\]

Principais convenções espaciais usadas:

```text
Front = +X
Left  = +Y
Up    = +Z
```

A grade canônica contém:

```text
1 Front
2520 pontos interiores
1 Back
----------------
2522 direções
```

## 3.2 SOFA `FreeFieldDirectivityTF`

```text
experiment6a_write_freefielddirectivitytf_v2.py
```

Criação do arquivo canônico:

```text
AXYS4549_FreeFieldDirectivityTF.sofa
```

A representação preserva as frequências medidas do CF2 e os valores complexos `Data.Real` e `Data.Imag`.

## 3.3 Bridge TF → runtime FIR

```text
experiment6b_pyroomacoustics_bridge.py
```

Implementa a adaptação necessária porque o Pyroomacoustics 0.10.1 não usa diretamente `FreeFieldDirectivityTF` como `MeasuredDirectivity`.

A conversão utilizada é:

```text
TF medida
→ preenchimento dos bins FFT
→ IFFT
→ delay comum
→ FIR runtime
```

O preenchimento entre frequências medidas usa a política validada:

```text
third_octave_hold
```

com fronteiras definidas pelas médias geométricas entre frequências adjacentes.

Essa política é uma **decisão do adaptador**, não uma regra formal do formato CLF/SOFA.

## 3.4 `GeneralFIR`

```text
experiment6b1_write_general_fir_sofa.py
```

Produziu o arquivo `GeneralFIR` derivado do mesmo conjunto de dados.

Esse formato pode ser carregado nativamente pelo `MeasuredDirectivityFile` de Pyroomacoustics.

## 3.5 Adaptador SOFA dual

```text
sofa_directivity_dual_adapter_v2.py
```

Loader atual capaz de detectar automaticamente:

```text
FreeFieldDirectivityTF / TF
```

ou:

```text
GeneralFIR / FIR
```

A interface conceitual passa a ser:

```python
directivity = load_sofa("speaker.sofa")
```

sem obrigar o usuário a conhecer previamente a convenção interna do arquivo.

## 3.6 Otimizações do motor

```text
experiment6g_b_cached_octave_synthesis.py
experiment6g_c_frequency_domain_cache.py
experiment6g_d_fft_length_optimization.py
experiment6g_e_fractional_delay_fd_cache.py
```

Esses experimentos compõem o caminho otimizado denominado neste relatório de **6G-E**.

Principais otimizações:

- cache da síntese por bandas;
- cache FFT da diretividade;
- uso de `next_fast_len`;
- cache em frequência do fractional delay;
- preservação da resposta FIR completa, sem reduzir direções, taps ou fase.

## 3.7 ISM-Lite

```text
binary_first_freefield_directivity_adapter_ism_lite.py
```

e posteriormente a versão integrada no:

```text
sofa_directivity_dual_adapter_v2.py
```

O `ISMOnlyMeasuredDirectivity` evita, quando o objetivo é somente ISM:

- cálculo das energias octave-band para ray tracing;
- áreas de Voronoi;
- distribuições de energia para ray sampling.

Ele mantém:

- FIRs;
- grid de direções;
- orientação;
- KD-tree;
- compatibilidade com o caminho ISM.

**Não deve ser usado para ray tracing.**

## 3.8 Benchmarks integrados

```text
experiment6j_sofa_dual_format_simulation.py
```

Comparação independente entre TF e FIR.

```text
experiment6k_sofa_autodetect_single.py
```

Validação de entrada única com autodetecção da convenção SOFA.

```text
experiment6l_sofa_orientation_sweep.py
```

Validação da rotação horizontal da fonte.

```text
experiment6m_all_formats_quality_benchmark.py
```

Benchmark consolidado de desempenho, frequência de amostragem, Voronoi, motor nativo e métricas acústicas.

---

# 4. Descobertas sobre o CF2

## 4.1 Frequências declaradas

Para o AXYS4549 foi identificada a faixa declarada:

```text
índices 6 ... 29
```

correspondendo a:

```text
100
125
160
200
250
315
400
500
630
800
1000
1250
1600
2000
2500
3150
4000
5000
6300
8000
10000
12500
16000
20000 Hz
```

Total:

```text
24 frequências medidas
```

Foi encontrado um valor residual extremamente pequeno fora da faixa declarada, no slot de 63 Hz. Por isso foi abandonada a política de inferir a faixa apenas por valores “não zero”.

A política empírica adotada passou a ser:

> usar a faixa declarada no header como faixa válida e tratar valores fora dessa faixa apenas como diagnóstico.

## 4.2 Grade angular

O bloco de magnitude foi identificado como:

```text
72 rotações × 37 arcos
```

com passo de 5°.

A representação bruta contém:

```text
72 × 37 = 2664 direções
```

mas Front e Back possuem redundância angular.

Após canonicalização:

```text
2522 direções únicas
```

A correspondência utilizada é:

```text
arc = 0°   → Front
arc = 180° → Back

rotation = 0°   → Up
rotation = 90°  → Left
rotation = 180° → Down
rotation = 270° → Right
```

## 4.3 Fase

A fase pôde ser reconstruída para os arquivos que possuem container de fase.

Foi verificado:

- Front redundante de forma exata;
- pontos interiores preservados;
- Back nem sempre redundante exatamente entre rotações.

Para Back foi adotada uma política empírica:

```text
se redundante → valor exato
se não redundante → média circular da fase
```

Essa decisão foi congelada como política do parser, mas **não deve ser apresentada como especificação formal do CLF2**.

---

# 5. `FreeFieldDirectivityTF` como representação canônica

O arquivo:

```text
AXYS4549_FreeFieldDirectivityTF.sofa
```

passou a ser a representação canônica de intercâmbio em domínio da frequência.

Estrutura principal:

```text
M = 1
R = 2522
N = 24
```

com:

```text
Data.Real
Data.Imag
ReceiverPosition
N = eixo explícito de frequências
```

Uma observação importante:

> `FreeFieldDirectivityTF` não possui uma frequência de amostragem temporal intrínseca da mesma forma que um FIR.

Ele armazena valores complexos em frequências explícitas.

A frequência de amostragem aparece somente quando essa representação precisa ser convertida para uma grade FFT uniforme para uso temporal.

---

# 6. Validação CF2 binário ↔ SOFA TF

O experimento 6I-A confirmou que a resposta complexa reconstruída diretamente do binário CF2 fecha numericamente contra o arquivo `FreeFieldDirectivityTF`.

Resultados:

```text
frequency axis exact = True
max complex error    = 8.882e-16
RMSE complex         = 2.553e-17
```

Isso confirma que o SOFA TF criado para o AXYS4549 preserva os dados complexos reconstruídos do CF2 dentro da precisão numérica.

---

# 7. `GeneralFIR`

O `GeneralFIR` foi produzido inicialmente como uma representação temporal derivada do mesmo TF.

A cadeia usada foi:

```text
FreeFieldDirectivityTF
        ↓
grid FFT 48 kHz / 2048
        ↓
IFFT
        ↓
delay circular comum de 1024 amostras
        ↓
GeneralFIR 48 kHz / 2048 taps
```

O delay embutido é:

\[
\frac{1024}{48000}
=
0.021333333\text{ s}
\]

ou:

```text
21.333333 ms
```

Esse delay é um detalhe da representação temporal e **não deve ser interpretado como atraso físico da sala**.

---

# 8. Relação entre TF e FIR

É importante distinguir:

```text
FreeFieldDirectivityTF
```

de:

```text
GeneralFIR
```

Eles são duas convenções SOFA diferentes.

No sistema desenvolvido, ambos são aceitos como entradas independentes.

## Caminho TF

```text
FreeFieldDirectivityTF
        ↓
frequências complexas medidas
        ↓
render uniforme em memória
        ↓
IFFT + delay
        ↓
FIR runtime
        ↓
ISM
```

## Caminho FIR

```text
GeneralFIR
        ↓
Data.IR
        ↓
resample se necessário
        ↓
FIR runtime
        ↓
ISM
```

O caminho TF **não exige gerar um arquivo GeneralFIR persistente**.

O `GeneralFIR` permanece útil como:

- formato de entrada suportado;
- referência de compatibilidade;
- artefato de validação;
- representação temporal pronta.

---

# 9. Autodetecção SOFA

O experimento 6K demonstrou que um único loader pode receber:

```text
speaker.sofa
```

e decidir automaticamente entre:

```text
FreeFieldDirectivityTF / TF
```

e:

```text
GeneralFIR / FIR
```

## Resultado FreeFieldDirectivityTF

```text
Loader preparation = 0.213974 s
Runtime IR          = 2522 × 683
FFT rápida          = 1280
compute_rir median  = 1.153934 s
energy              = 5.320828780e-02
PASS
```

## Resultado GeneralFIR

```text
Loader preparation = 0.322511 s
Runtime IR          = 2522 × 683
FFT rápida          = 1280
compute_rir median  = 1.146740 s
energy              = 5.320828780e-02
PASS
```

Portanto, ambos os formatos podem ser utilizados autonomamente.

O CF2 original deixa de ser obrigatório para simular um `FreeFieldDirectivityTF`; ele continua útil para validação da origem dos dados.

---

# 10. Orientação da fonte — experimento 6L

Foi executada a varredura:

```text
0°
45°
90°
135°
180°
225°
270°
315°
360°
```

com:

```python
Rotation3D([azimuth], "z", degrees=True)
```

e Front canônico em `+X`.

## Tempo de atualização da orientação

```text
FreeFieldDirectivityTF ≈ 0.000867 s
GeneralFIR             ≈ 0.000841 s
```

Enquanto o `compute_rir` permaneceu próximo de:

```text
1.13 s
```

Assim, a atualização de orientação custa menos de aproximadamente 0.1% do custo de uma simulação completa.

## Periodicidade

Foi verificado:

```text
0° = 360°
```

para ambos os formatos:

```text
RIR max error = 0
Energy RMSE   = 0 dB
```

## Energia média espacial

A varredura mostrou:

```text
0°    5.320828780e-02
45°   5.442678950e-02
90°   5.313385648e-02
135°  5.232142737e-02
180°  5.126642960e-02
225°  5.232142785e-02
270°  5.313385675e-02
315°  5.442678959e-02
360°  5.320828780e-02
```

O span da energia média foi:

```text
0.260 dB
```

Importante: esse valor representa a média espacial sobre os 25 microfones. Diferenças locais por receptor podem ser maiores.

---

# 11. Nomenclatura atual recomendada

Para evitar confusão entre formato e motor, recomenda-se usar os seguintes nomes.

## `CF2_FAST`

```text
entrada          = CF2
modelo           = magnitude em bandas
fase             = não
fs benchmark     = 16 kHz
filter_len       = 1
motor            = CF2 vectorizado
```

## `SOFA_TF_FAST`

```text
entrada          = FreeFieldDirectivityTF / TF
modelo           = complexo, magnitude + fase
runtime          = FIR em memória
motor            = 6G-E
```

## `SOFA_FIR_FAST`

```text
entrada          = GeneralFIR / FIR
modelo           = complexo, magnitude + fase
runtime          = FIR
motor            = 6G-E
```

## `SOFA_FIR_VORONOI_FAST`

Mesmo `GeneralFIR`, mas carregado por `MeasuredDirectivity` padrão:

```text
energias octave-band
Voronoi
ray-sampling setup
```

Depois disso ainda usa o motor 6G-E.

## `SOFA_FIR_NATIVE`

```text
GeneralFIR
+
MeasuredDirectivity padrão
+
compute_rir nativo do Pyroomacoustics
```

Esse caso é usado como referência numérica e de desempenho.

---

# 12. Benchmark consolidado 6M

Script:

```text
experiment6m_all_formats_quality_benchmark.py
```

## 12.1 Matriz de casos

| Caso | fs | Representação | FIR | Voronoi | Motor |
|---|---:|---|---:|---|---|
| `CF2_FAST_16K` | 16 kHz | CF2 magnitude / 7 bandas | 1 | Não | CF2 fast |
| `SOFA_TF_FAST_16K` | 16 kHz | FreeFieldDirectivityTF | 683 | Não | 6G-E |
| `SOFA_FIR_FAST_16K` | 16 kHz | GeneralFIR | 683 | Não | 6G-E |
| `SOFA_FIR_VORONOI_FAST_16K` | 16 kHz | GeneralFIR | 683 | Sim | 6G-E |
| `SOFA_FIR_NATIVE_16K` | 16 kHz | GeneralFIR | 683 | Sim | PRA native |
| `SOFA_TF_FAST_48K` | 48 kHz | FreeFieldDirectivityTF | 2048 | Não | 6G-E |
| `SOFA_FIR_FAST_48K` | 48 kHz | GeneralFIR | 2048 | Não | 6G-E |
| `SOFA_FIR_VORONOI_FAST_48K` | 48 kHz | GeneralFIR | 2048 | Sim | 6G-E |
| `SOFA_FIR_NATIVE_48K` | 48 kHz | GeneralFIR | 2048 | Sim | PRA native |

---

# 13. Tempos de preparação

Resultados do 6M:

| Caso | Preparação |
|---|---:|
| `CF2_FAST_16K` | 0.0246 s |
| `SOFA_TF_FAST_16K` | 0.2251 s |
| `SOFA_FIR_FAST_16K` | 0.3897 s |
| `SOFA_FIR_VORONOI_FAST_16K` | 3.2012 s |
| `SOFA_FIR_NATIVE_16K` | 3.2012 s |
| `SOFA_TF_FAST_48K` | 0.0714 s |
| `SOFA_FIR_FAST_48K` | 0.1575 s |
| `SOFA_FIR_VORONOI_FAST_48K` | 2.9302 s |
| `SOFA_FIR_NATIVE_48K` | 2.9302 s |

O maior custo adicional do `MeasuredDirectivity` padrão aparece no setup de Voronoi/energias para ray tracing.

O ISM-Lite demonstrou anteriormente uma redução de preparação superior a 90%, sem alterar o RIR do ISM.

---

# 14. Benchmark de tempo de `compute_rir`

Resultados finais de três rondas balanceadas:

| Caso | fs | Mediana | Média | Desvio padrão |
|---|---:|---:|---:|---:|
| `CF2_FAST_16K` | 16 kHz | **0.2768 s** | 0.2722 s | 0.0108 s |
| `SOFA_TF_FAST_16K` | 16 kHz | **1.0774 s** | 1.0808 s | 0.0213 s |
| `SOFA_FIR_FAST_16K` | 16 kHz | **1.0573 s** | 1.0531 s | 0.0380 s |
| `SOFA_FIR_VORONOI_FAST_16K` | 16 kHz | **1.0594 s** | 1.0511 s | 0.0250 s |
| `SOFA_FIR_NATIVE_16K` | 16 kHz | **7.7959 s** | 7.7911 s | 0.1704 s |
| `SOFA_TF_FAST_48K` | 48 kHz | **1.8031 s** | 1.8511 s | 0.1305 s |
| `SOFA_FIR_FAST_48K` | 48 kHz | **1.8248 s** | 1.8830 s | 0.1175 s |
| `SOFA_FIR_VORONOI_FAST_48K` | 48 kHz | **1.9058 s** | 1.8855 s | 0.0896 s |
| `SOFA_FIR_NATIVE_48K` | 48 kHz | **10.9257 s** | 10.9580 s | 0.1286 s |

---

# 15. Speedups mais relevantes

## 15.1 6G-E versus Pyroomacoustics nativo

### 16 kHz

\[
\frac{7.7959}{1.0573}
\approx 7.37\times
\]

Portanto:

```text
SOFA_FIR_FAST_16K ≈ 7.37× mais rápido que SOFA_FIR_NATIVE_16K
```

### 48 kHz

\[
\frac{10.9257}{1.8248}
\approx 5.99\times
\]

Portanto:

```text
SOFA_FIR_FAST_48K ≈ 5.99× mais rápido que SOFA_FIR_NATIVE_48K
```

## 15.2 CF2 versus SOFA full-phase em 16 kHz

\[
\frac{1.0573}{0.2768}
\approx 3.82
\]

Ou seja:

```text
CF2_FAST_16K ≈ 3.82× mais rápido que SOFA_FIR_FAST_16K
```

Essa comparação deve ser interpretada com cuidado porque os dois modelos de fonte não contêm a mesma informação.

## 15.3 48 kHz versus 16 kHz

Para `SOFA_FIR_FAST`:

\[
\frac{1.8248}{1.0573}
\approx 1.73
\]

Portanto o caso 48 kHz custou cerca de:

```text
1.73× o tempo do caso 16 kHz
```

neste cenário.

---

# 16. Validação numérica dos motores

## 16.1 ISM-Lite versus Voronoi

### 16 kHz

```text
SOFA_FIR_FAST_16K
vs
SOFA_FIR_VORONOI_FAST_16K

RIR max abs error = 0
Energy RMSE       = 0 dB
```

### 48 kHz

```text
SOFA_FIR_FAST_48K
vs
SOFA_FIR_VORONOI_FAST_48K

RIR max abs error = 0
Energy RMSE       = 0 dB
```

Conclusão:

> As estruturas adicionais de Voronoi e ray sampling não alteram o resultado do ISM determinista; elas afetam principalmente o custo de preparação.

## 16.2 6G-E versus PRA nativo

### 16 kHz

```text
RIR max abs error = 2.979e-08
Energy RMSE       = 1.885e-07 dB
```

### 48 kHz

```text
RIR max abs error = 2.243e-08
Energy RMSE       = 6.241e-08 dB
```

Conclusão:

> O motor otimizado 6G-E reproduz o resultado do motor nativo de Pyroomacoustics dentro de erro numérico muito pequeno, com aceleração de aproximadamente 7.37× a 16 kHz e 5.99× a 48 kHz.

## 16.3 SOFA TF versus SOFA FIR

Para o par AXYS derivado da mesma origem:

### 16 kHz

```text
RIR max abs error = 0
Energy RMSE       = 0 dB
```

### 48 kHz

```text
RIR max abs error = 0
Energy RMSE       = 0 dB
```

Essa igualdade é válida para os arquivos usados neste estudo.

Ela **não deve ser generalizada** para qualquer par arbitrário `FreeFieldDirectivityTF` / `GeneralFIR`.

---

# 17. Energia observada no benchmark

Valores médios do RIR:

```text
CF2_FAST_16K       = 3.262063725e-01

SOFA_*_16K         = 5.320828780e-02
SOFA_NATIVE_16K    = 5.320828717e-02

SOFA_*_48K         = 2.228598139e-01
SOFA_NATIVE_48K    = 2.228598138e-01
```

Esses números não devem ser tratados diretamente como SPL absoluto.

Também não é correto comparar diretamente a soma discreta de energia entre 16 e 48 kHz sem considerar a frequência de amostragem e o conteúdo espectral disponível.

---

# 18. Métricas de qualidade acústica

O 6M calculou, para cada um dos 25 receptores:

```text
energia
nível relativo
peak
C50
C80
D50
Ts
EDT
T20
T30
```

O resumo médio espacial foi:

| Caso | Nível relativo médio [dB] | C50 [dB] | C80 [dB] | D50 [%] | Ts [s] | EDT [s] | T20 [s] | T30 [s] |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `CF2_FAST_16K` | -5.932752 | 10.122172 | 15.921070 | 90.586217 | 0.019639 | 0.305068 | 0.299121 | 0.258388 |
| `SOFA_TF_FAST_16K` | -14.304832 | 9.661786 | 14.478633 | 89.698346 | 0.020812 | 0.319902 | 0.317818 | 0.264286 |
| `SOFA_FIR_FAST_16K` | -14.304832 | 9.661786 | 14.478633 | 89.698346 | 0.020812 | 0.319902 | 0.317818 | 0.264286 |
| `SOFA_FIR_VORONOI_FAST_16K` | -14.304832 | 9.661786 | 14.478633 | 89.698346 | 0.020812 | 0.319902 | 0.317818 | 0.264286 |
| `SOFA_FIR_NATIVE_16K` | -14.304832 | 9.661786 | 14.478633 | 89.698346 | 0.020812 | 0.319902 | 0.317818 | 0.264286 |
| `SOFA_TF_FAST_48K` | -7.989620 | 9.586623 | 14.890765 | 89.260894 | 0.020025 | 0.348111 | 0.318030 | 0.269779 |
| `SOFA_FIR_FAST_48K` | -7.989620 | 9.586623 | 14.890765 | 89.260894 | 0.020025 | 0.348111 | 0.318030 | 0.269779 |
| `SOFA_FIR_VORONOI_FAST_48K` | -7.989620 | 9.586623 | 14.890765 | 89.260894 | 0.020025 | 0.348111 | 0.318030 | 0.269779 |
| `SOFA_FIR_NATIVE_48K` | -7.989620 | 9.586623 | 14.890765 | 89.260894 | 0.020025 | 0.348111 | 0.318030 | 0.269779 |

---

# 19. Conclusões de qualidade

## 19.1 6G-E preserva as métricas acústicas

As quatro variantes SOFA de uma mesma frequência de amostragem produziram praticamente as mesmas métricas:

```text
SOFA_TF_FAST
SOFA_FIR_FAST
SOFA_FIR_VORONOI_FAST
SOFA_FIR_NATIVE
```

Isso confirma que a aceleração do 6G-E não introduziu alteração relevante em:

- C50;
- C80;
- D50;
- Ts;
- EDT;
- T20;
- T30.

## 19.2 CF2_FAST é mais rápido, mas representa outro modelo de fonte

Comparando `CF2_FAST_16K` com `SOFA_FIR_FAST_16K`:

```text
ΔC50  ≈ +0.460 dB
ΔC80  ≈ +1.442 dB
ΔD50  ≈ +0.888 ponto percentual
ΔTs   ≈ -1.173 ms
ΔEDT  ≈ -14.83 ms
ΔT20  ≈ -18.70 ms
ΔT30  ≈ -5.90 ms
```

Portanto:

> CF2_FAST oferece vantagem clara de velocidade, mas a simplificação para magnitude em 7 bandas produz diferenças acústicas mensuráveis em relação à representação SOFA complexa.

Não é correto descrever CF2_FAST como “a mesma simulação quatro vezes mais rápida”.

Ele é um modelo de fonte mais reduzido.

---

# 20. 16 kHz versus 48 kHz

Em 16 kHz, o conjunto de bandas utilizado no benchmark foi:

```text
125
250
500
1000
2000
4000
8000 Hz
```

Em 48 kHz:

```text
125
250
500
1000
2000
4000
8000
16000 Hz
```

Portanto, a comparação entre 16 e 48 kHz mistura dois efeitos:

1. maior resolução temporal;
2. maior largura de banda física disponível.

Além disso, a energia discreta:

\[
\sum_n h[n]^2
\]

depende da frequência de amostragem.

Por isso o valor:

```text
relative_level_db
```

não deve ser comparado diretamente entre 16 e 48 kHz como se fosse SPL.

---

# 21. Faixa comum recomendada para comparação entre simuladores

Para comparação justa entre:

```text
Pyroomacoustics
I-Simpa
Odeon
```

é recomendável separar:

## Faixa comum

```text
125
250
500
1000
2000
4000 Hz
```

Essa faixa evita usar a banda de 8 kHz exatamente no Nyquist de uma simulação a 16 kHz.

## Faixa estendida

```text
8000 Hz
16000 Hz
```

pode ser analisada separadamente nos simuladores/configurações que possuam largura de banda suficiente.

---

# 22. SPL absoluto ainda não está calibrado

O benchmark atual produz:

```text
relative_level_db
```

derivado da energia do RIR.

Esse valor **não deve ser chamado de SPL absoluto**.

Para comparar nível absoluto com Odeon ou I-Simpa ainda é necessário definir uma calibração comum, por exemplo:

- potência da fonte;
- sensibilidade;
- nível on-axis;
- distância de referência;
- unidade física da resposta;
- normalização usada por cada simulador.

Até essa etapa, são válidas comparações de:

- forma do RIR;
- energia relativa;
- distribuição espacial;
- C50;
- C80;
- D50;
- Ts;
- EDT;
- T20;
- T30;
- uniformidade;
- diferenças relativas entre orientações.

---

# 23. Métricas de decaimento: cuidado com T30

Foi observado que, para vários casos:

```text
T30 < T20
```

Exemplo SOFA 16 kHz:

```text
T20 ≈ 0.3178 s
T30 ≈ 0.2643 s
```

Isso não prova automaticamente um erro.

Pode ocorrer devido a:

- curva de decaimento não linear;
- faixa dinâmica insuficiente;
- efeito de `max_order = 10`;
- diferente comportamento da cauda do RIR.

Antes de usar T30 como métrica final inter-simulador, recomenda-se registrar também:

```text
R² da regressão EDT
R² da regressão T20
R² da regressão T30
número de pontos utilizados
nível mínimo atingido pela EDC
```

---

# 24. Memória: limitação do benchmark atual

O 6M registrou `ru_maxrss`.

Esse valor representa o **máximo histórico de memória do processo** no Linux.

Logo, valores como:

```text
612 MiB
814 MiB
854 MiB
```

não devem ser interpretados diretamente como consumo isolado de cada caso.

Para comparar memória corretamente entre formatos, cada caso deve ser executado:

- em processo separado; ou
- com amostragem instantânea de RSS durante a execução.

---

# 25. Resultado do experimento Single-IFFT

Também foi testada uma reorganização do ISM para acumular todas as image sources em frequência e executar apenas uma IFFT final.

Arquivo:

```text
experiment6i_c1_single_ifft_ism.py
```

O fechamento acústico passou:

```text
RIR max abs error = 4.864e-08
Energy RMSE       = 3.069e-07 dB
```

Mas o desempenho piorou:

```text
6G-E reference = 1.213 s
Single-IFFT    = 5.216 s
```

Principal custo:

```text
integer_delay_phase ≈ 3.216 s
                    ≈ 61.66% do total
```

A IFFT final custou apenas:

```text
≈ 0.0022 s
```

Conclusão:

> O Single-IFFT foi validado numericamente, mas rejeitado como otimização de desempenho.

---

# 26. Conclusões gerais

## 26.1 Formatos suportados

Atualmente o sistema suporta de forma explícita:

```text
CF2
FreeFieldDirectivityTF / TF
GeneralFIR / FIR
```

## 26.2 SOFA TF é uma entrada autônoma

`FreeFieldDirectivityTF` pode ser utilizado sem o CF2 original.

O CF2 passa a ser necessário apenas quando se deseja validar a proveniência/reconstrução do SOFA.

## 26.3 GeneralFIR também é uma entrada autônoma

`GeneralFIR` pode ser utilizado diretamente pelo loader e pelo Pyroomacoustics.

## 26.4 TF e FIR devem continuar como formatos independentes

O fato de os arquivos AXYS atuais produzirem RIRs idênticos não significa que toda dupla TF/FIR seja universalmente equivalente.

## 26.5 ISM-Lite é adequado para o benchmark ISM

Ao remover estruturas destinadas a ray tracing:

- reduz significativamente o tempo de preparação;
- não altera o RIR ISM;
- mantém orientação e KD-tree;
- não deve ser usado quando ray tracing estiver ativo.

## 26.6 6G-E preserva a qualidade e acelera fortemente o cálculo

Resultados observados:

```text
~7.37× speedup a 16 kHz
~5.99× speedup a 48 kHz
```

frente ao motor nativo para o mesmo `GeneralFIR`.

## 26.7 CF2_FAST é a opção de maior desempenho

```text
~0.277 s por compute_rir
```

mas utiliza um modelo mais reduzido:

```text
magnitude
7 bandas
sem fase
```

## 26.8 SOFA_FAST mantém a representação complexa

Tanto `SOFA_TF_FAST` quanto `SOFA_FIR_FAST` mantêm:

```text
2522 direções
magnitude
fase
FIR completo
```

e oferecem desempenho muito superior ao `SOFA_NATIVE`.

---

# 27. Arquitetura atual recomendada

```text
                              INPUT
                                │
          ┌─────────────────────┼────────────────────┐
          │                     │                    │
          ▼                     ▼                    ▼
         CF2        FreeFieldDirectivityTF         GeneralFIR
          │                     │                    │
          ▼                     ▼                    ▼
     CF2_FAST             SOFA_TF_FAST         SOFA_FIR_FAST
   magnitude/7 bands       full complex          full complex
          │                     │                    │
          │                     └─────────┬──────────┘
          │                               │
          │                         SOFA runtime
          │                               │
          │                        caches 6G-E
          │                               │
          └───────────────────────┬───────┘
                                  ▼
                              ISM / RIR
```

Para referência:

```text
GeneralFIR
   ↓
MeasuredDirectivity padrão
   ↓
SOFA_FIR_NATIVE
```

é mantido como baseline numérico do Pyroomacoustics.

---

# 28. Próximas etapas recomendadas

Antes da comparação com outros simuladores, os próximos passos técnicos recomendados são:

1. pós-processar os RIRs já salvos por banda de oitava;
2. separar comparação comum 125–4000 Hz da faixa estendida;
3. calcular `R²` e validade de EDT/T20/T30;
4. obter métricas espaciais por receptor:
   - média;
   - desvio;
   - RMSE;
   - mínimo;
   - máximo;
   - uniformidade;
5. definir calibração de nível para permitir SPL absoluto;
6. executar cada caso isoladamente para benchmark de memória;
7. preparar exatamente a mesma geometria/fonte/receptores em I-Simpa e Odeon;
8. comparar:
   - tempo;
   - RIR;
   - SPL/calibração;
   - C50;
   - C80;
   - D50;
   - EDT;
   - T20;
   - T30;
   - uniformidade espacial;
   - sensibilidade à orientação da fonte.

---

# 29. Resumo final

Os experimentos mostraram que o sistema evoluiu de um adapter CF2 específico para uma infraestrutura que suporta múltiplas representações de diretividade com diferentes compromissos de custo e fidelidade.

A situação atual pode ser resumida assim:

```text
CF2_FAST
    maior velocidade
    magnitude em bandas
    sem fase

SOFA_TF_FAST
    FreeFieldDirectivityTF
    magnitude + fase
    formato frequencial
    FIR runtime apenas em memória

SOFA_FIR_FAST
    GeneralFIR
    magnitude + fase
    FIR armazenado
    mesmo motor rápido 6G-E

SOFA_FIR_VORONOI_FAST
    mesmo GeneralFIR
    preparação padrão MeasuredDirectivity
    resultado ISM idêntico
    preparação mais cara

SOFA_FIR_NATIVE
    baseline Pyroomacoustics
    resultado praticamente idêntico ao 6G-E
    6–7× mais lento nos benchmarks atuais
```

Com isso, já existe uma base experimental adequada para a próxima fase: comparar não apenas desempenho interno, mas também a **qualidade acústica e consistência física entre Pyroomacoustics, I-Simpa e Odeon**.
