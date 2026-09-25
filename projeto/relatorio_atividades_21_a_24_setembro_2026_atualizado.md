# Relatório de atividades — 21 a 24 de setembro de 2026

## Visão geral

Entre **segunda-feira, 21 de setembro de 2026, e quinta-feira, 24 de setembro de 2026**, concentramos o trabalho na validação e integração de dados de diretividade acústica, principalmente envolvendo arquivos **CF2/CLF**, automação do **CLF Viewer**, investigação de arquivos **GLL** e preparação de um benchmark com **Pyroomacoustics**.

O objetivo geral desse período foi avançar de uma etapa de inspeção e validação dos dados de diretividade para uma estrutura reproduzível que permita comparar modelos **omnidirecionais, cardioides e diretividades reais extraídas de arquivos CF2**, inicialmente em ambiente local com Docker e posteriormente em outros ambientes de execução.

---

# Segunda-feira — 21/09/2026

## 1. Investigação da relação entre CF2 e a visualização por oitavas no CATT/CLF Viewer

O trabalho começou com uma dúvida sobre a forma como o CATT/CLF Viewer apresenta as frequências disponíveis. Foi observado que a interface poderia mostrar botões correspondentes a bandas de oitava mesmo quando determinadas bandas de terço de oitava não estivessem explicitamente presentes no arquivo CF2.

A partir disso, analisamos se esse comportamento poderia ser uma característica normal da interface, uma agregação automática de bandas de terço de oitava, um fallback aplicado pelo software ou algum tipo de inconsistência de visualização.

## 2. Validação da agregação por oitavas

Foi realizada uma análise comparando os valores apresentados pelo CATT com diferentes formas de combinar três bandas de terço de oitava.

A hipótese que apresentou melhor correspondência foi a média energética:

```text
10 log10(
    (
        10^(D_low/10)
        + 10^(D_center/10)
        + 10^(D_high/10)
    ) / 3
)
```

Nos casos em que as três bandas estavam disponíveis, essa regra apresentou forte correspondência com o valor mostrado na visualização principal.

Foram analisados **2.912 tripletes completos**, com aproximadamente:

```text
RMSE mediano ≈ 0,234 dB
84,8% dos casos favorecendo a média energética
```

Isso forneceu evidência forte de que a visualização em oitavas é formada, em grande parte dos casos completos, a partir da combinação energética das bandas de terço de oitava.

## 3. Casos com bandas incompletas

Também foram examinados **504 casos parciais**, nos quais uma ou mais bandas do triplete não estavam disponíveis.

Nesses casos não foi encontrada uma regra única e consistente de fallback. Também não apareceu evidência suficiente de que o programa simplesmente copie ou replique uma banda vizinha.

### Resultado do dia

Ao final da segunda-feira, ficou estabelecido que:

- a média energética explica de forma muito boa os casos com tripletes completos;
- os casos com bandas ausentes precisam ser tratados separadamente;
- não deveríamos inferir automaticamente valores inexistentes;
- a comparação direta com o CATT/CLF Viewer continuaria sendo importante como mecanismo de validação.

---

# Terça-feira — 22/09/2026

## 1. Automação de captura no CLF Viewer

O foco passou para a automatização da coleta de imagens e valores no **CLF Viewer**.

Foi desenvolvido e ajustado um fluxo para abrir arquivos `.CF2`, percorrer as frequências realmente disponíveis e capturar as visualizações:

```text
S
F
T
Polar 2D
```

O objetivo era evitar uma coleta manual extremamente demorada e gerar material suficiente para validar a orientação e os valores extraídos diretamente do binário CF2.

## 2. Correção da abertura dos arquivos CF2

Durante os testes foi identificado que o comando correto no CLF Viewer para abrir um arquivo binário de distribuição não era `Ctrl+O`.

O fluxo foi corrigido para usar:

```text
Ctrl+B
```

correspondente à abertura de um **CLF binary-file / Distribution Binary**.

Também foram adicionados métodos alternativos para localizar e preencher o campo de nome do arquivo, combinando automação Win32/UI, atalhos de teclado e fallback por posição da janela.

## 3. Descoberta das frequências realmente existentes

Foi definido como requisito que o script **não deveria assumir frequências inexistentes**.

Em vez de gerar uma lista teórica e tentar selecionar bandas ausentes, o processo passou a descobrir as frequências efetivamente disponíveis na ComboBox do CLF Viewer.

Isso foi importante para evitar associação incorreta entre índice e frequência, capturas duplicadas, imagens com frequência errada e erros silenciosos na construção do dataset.

## 4. Benchmark das estratégias de navegação

Foram comparadas diferentes estratégias para percorrer as frequências e alternar entre as visualizações S/F/T.

A estratégia vencedora foi:

```text
snake_SFT_no_home

S: baixa → alta
F: alta → baixa
T: baixa → alta
```

A ideia foi evitar retornar ao início da ComboBox usando `HOME` entre cada visualização.

Para um cenário com:

```text
25 frequências
3 visualizações
75 capturas
```

a estratégia apresentou aproximadamente:

```text
mediana ≈ 55,27 s por modelo
≈ 1,357 imagens/s
```

Em comparação, uma estratégia baseada em tratar cada frequência individualmente (`per_frequency_SFT`) ficou em aproximadamente:

```text
219,21 s
```

Também foram medidos tempos aproximados de navegação:

```text
DOWN ≈ 0,177 s
UP   ≈ 0,172 s
HOME ≈ 2,606 s
```

Isso explicou por que evitar `HOME` produziu uma redução significativa no tempo total.

## 5. Consolidação do batch final

O fluxo final passou a utilizar a sequência:

```text
S ↑
F ↓
T ↑
```

com descoberta das frequências reais, verificação periódica da frequência atualmente selecionada, suporte a `--resume` e captura assíncrona das imagens.

Foram preparados scripts finais de batch, incluindo versões capazes de continuar uma execução interrompida e tratar automaticamente a janela:

```text
** File does not exist **
```

sem interromper todo o processamento.

### Resultado do dia

A terça-feira terminou com um processo de captura muito mais robusto e rápido, adequado para executar sobre vários arquivos CF2 e gerar um conjunto de referência para as próximas validações.

---

# Quarta-feira — 23/09/2026

## 1. Definição da estratégia de benchmark acústico

Foi estruturada a ideia de comparar, em condições controladas:

```text
OMNI
CARDIOIDE
CF2 real
```

mantendo constantes sala, posição da fonte, receptores, orientação, potência, bandas de frequência e parâmetros do simulador.

Também foi definida a estratégia de execução:

```text
Local/Docker
    ↓
Google Colab
    ↓
Cluster UFV / OpenPBS
```

O ambiente local seria usado para desenvolvimento e depuração, o Colab para experimentação/notebooks e o cluster para execuções maiores e repetidas.

## 2. Planejamento do cenário inicial no Pyroomacoustics

Foi proposto um cenário controlado utilizando aproximadamente:

```text
sala: 6 × 5 × 3 m
grade de receptores: 5 × 5
25 microfones
```

Também foram discutidas métricas como energia da RIR, uniformidade espacial, SPL, métricas temporais/acústicas, tempo de execução e uso de memória.

## 3. Exploração de arquivos GLL

Antes de avançar definitivamente com CF2, foi investigada a possibilidade de utilizar arquivos **GLL** como fonte de dados de diretividade.

Foi utilizado o projeto `gll-tools` e analisado o arquivo:

```text
/app/gll-test/Active_Audio/Active-Audio_B70_v5.gll
```

O parser conseguiu acessar informações estruturais do arquivo. Entretanto, ao tentar obter diretamente o balloon em 1 kHz com:

```python
source.get_balloon_at_frequency(1000)
```

foi retornado:

```text
ParseError: file does not exist
```

A inspeção mostrou que a estrutura Python apresentava:

```text
responses = []
frequency_response = None
```

apesar de o arquivo conter metadados indicando dados de resposta.

## 4. Inspeção do bloco bruto do GLL

A análise foi aprofundada examinando o `raw_block` armazenado no arquivo.

Foram identificados, entre outros dados:

```text
response_count = 1297
response_version = 1
on_axis_level = 94
on_axis spectrum = 241 pontos
```

O bloco bruto em Base64 foi decodificado, resultando em aproximadamente:

```text
958.859 bytes
```

e foi localizado um candidato para o valor `1297` em:

```text
offset 15784
```

O número **1297** chamou atenção por ser compatível com uma representação esférica com simetria e amostragem angular de aproximadamente 5°, sugerindo que os dados de resposta estavam presentes em alguma estrutura binária interna mesmo não sendo expostos corretamente pela API Python.

## 5. Decisão de não bloquear o benchmark pelo GLL

Como a leitura direta do GLL ainda exigia engenharia reversa adicional, decidiu-se não deixar essa investigação bloquear o benchmark principal.

A prioridade voltou a ser:

```text
CF2 → Pyroomacoustics
```

mantendo o GLL como uma linha paralela de investigação.

### Resultado do dia

Na quarta-feira ficaram definidos o desenho experimental do benchmark, a ordem local → Colab → cluster, a necessidade de validar a diretividade em campo livre antes do cenário reverberante e a decisão de seguir primeiro com CF2.

---

# Quinta-feira — 24/09/2026

## 1. Stage 1 — baseline OMNI × CARDIOIDE

Foi criado um benchmark local em Docker utilizando:

```text
Python 3.11.16
Pyroomacoustics 0.10.1
NumPy 2.4.6
1 thread
```

com:

```text
sala            = 6 × 5 × 3 m
fonte           = [2, 2,5, 1,5]
microfones      = 25
fs              = 16 kHz
max_order       = 10
absorção        = 0,35
```

Foi inicialmente identificado e corrigido um erro na leitura das RIRs. O formato correto do Pyroomacoustics é:

```python
room.rir[microfone][fonte]
```

Após a correção, o benchmark confirmou:

```text
configured_mics    = 25
configured_sources = 1
```

### Resultados de tempo

Para 20 repetições, com 3 warm-ups:

```text
OMNI
mediana ≈ 0,02921 s

CARDIOIDE
mediana ≈ 0,03493 s
```

O cardioide apresentou aproximadamente **19,6% de aumento na mediana do tempo de cálculo da RIR** nesse cenário.

A energia média espacial também mudou:

```text
OMNI      ≈ 1,56149
CARDIOIDE ≈ 0,51610
```

## 2. Variação da orientação do cardioide

O benchmark foi expandido para:

```text
0°
45°
90°
180°
```

mantendo os demais parâmetros constantes.

Resultados de energia média:

```text
0°   = 0,51610
45°  = 0,50628
90°  = 0,48317
180° = 0,45193
```

Os tempos medianos ficaram praticamente iguais entre as orientações, aproximadamente entre **34,76 ms e 35,25 ms**.

## 3. Correção do cálculo angular para geometria 3D

O cálculo auxiliar anterior utilizava apenas a diferença de azimute no plano XY:

```text
horizontal_angle_from_axis_deg
```

Esse cálculo foi substituído pelo ângulo 3D real entre o eixo da fonte e o vetor fonte → receptor, utilizando produto escalar.

Passamos a registrar:

```text
receiver_azimuth_deg
receiver_colatitude_deg
angle_from_axis_3d_deg
cos_angle_3d
```

Um caso importante foi o microfone diretamente abaixo da fonte. Após a correção, ele passou a apresentar corretamente:

```text
angle_from_axis_3d ≈ 90°
ganho cardioide ≈ 0,5
≈ -6,02 dB
```

A simulação acústica permaneceu idêntica, confirmando que a alteração afetou somente o diagnóstico geométrico.

## 4. Transformação mundo → sistema local do alto-falante → CF2

Foi implementada a transformação necessária para consultar uma matriz CF2 utilizando a orientação física da caixa acústica.

Foi adotado o sistema local:

```text
+X = frente / on-axis
+Y = esquerda
+Z = topo
```

A partir do vetor fonte → receptor, calculamos coordenadas locais, `arc` e `rotation`.

O `arc` foi definido como:

```text
0°   = frente
90°  = plano lateral/vertical
180° = traseira
```

Também foram calculadas as duas possibilidades de sentido da rotação para não assumir prematuramente a handedness do formato.

## 5. Validação da matriz CF2 em 1 kHz

Foi analisada diretamente a banda de 1 kHz do arquivo CF2.

A estrutura usada foi:

```text
72 rotações × 37 arcos
rotation = 0, 5, ..., 355°
arc      = 0, 5, ..., 180°
```

Na banda de 1 kHz foram observados, por exemplo:

```text
front  = 0 dB
top    = -20 dB
back   ≈ -7,2518 dB
```

Foi demonstrado que `arc = 180°` é um ponto real e não padding, pois apresenta o mesmo valor para todas as 72 rotações.

## 6. Teste de simetria esquerda/direita

Foi executado um teste completo comparando:

```text
D[f, r, arc]
com
D[f, -r mod 360, arc]
```

para as 30 frequências e todos os pontos da matriz.

O resultado foi:

```text
max_abs = 0
MAE     = 0
RMSE    = 0
```

em todas as frequências.

Portanto, para esse alto-falante específico, a diretividade é exatamente simétrica esquerda/direita.

## 7. Identificação do intervalo real de frequências do CF2

Foi feito um perfil de todas as bandas.

As frequências de **25 a 80 Hz** estavam completamente zeradas. O mesmo ocorreu em **12,5 kHz, 16 kHz e 20 kHz**.

As bandas com dados reais formaram um intervalo contínuo de:

```text
100 Hz até 10 kHz
```

Assim, as bandas zeradas foram classificadas como **dados ausentes**, e não como padrão omnidirecional de 0 dB.

## 8. Implementação e validação do interpolador CF2

Foi criado um interpolador com:

```text
rotation   → interpolação periódica 0° ↔ 360°
arc        → limitado a 0° ... 180°
frequência → interpolação logarítmica
magnitude  → interpolação inicialmente em dB
```

O teste de reconstrução nos nós originais apresentou:

```text
max_abs_error_db = 0.0
```

Também foram validados:

```text
periodicidade de rotation = erro 0
invariância dos polos     = erro 0
```

Consultas fora do intervalo válido, como **80 Hz** e **12,5 kHz**, passaram a gerar erro explicitamente.

## 9. Inspeção da API real do Pyroomacoustics 0.10.1

Foi inspecionado diretamente o código da versão instalada no Docker.

Foi confirmado que:

```python
Directivity.get_response(
    azimuth,
    colatitude=None,
    magnitude=False,
    degrees=True
)
```

não recebe frequência explicitamente.

Também foi examinada a classe:

```text
MeasuredDirectivity
```

que trabalha com:

```text
orientation
grid
impulse_responses
fs
```

A inspeção do Image Source Method mostrou que o Pyroomacoustics aceita respostas de diretividade com três formatos:

```text
(n_images,)                → diretividade flat em frequência
(n_images, n_octave_bands) → diretividade multibanda
(n_images, n_taps)         → resposta impulsiva
```

Isso abriu duas rotas principais para a integração:

```text
CF2 → resposta multibanda
```

ou:

```text
CF2 → resposta impulsiva/FIR → MeasuredDirectivity
```

Nesse ponto do trabalho, a escolha definitiva da arquitetura de integração ainda estava em aberto. Essa decisão foi fechada posteriormente no mesmo dia, conforme descrito nas seções seguintes.


## 10. Fechamento da arquitetura de integração CF2 → Pyroomacoustics

Após a inspeção da API, foram testadas as duas rotas principais de integração.

A rota baseada em `MeasuredDirectivity` foi validada como **fallback funcional**, utilizando FIRs sintetizados a partir da magnitude CF2. O teste confirmou reconstrução muito próxima do padrão esperado e funcionamento dentro do Pyroomacoustics.

Entretanto, essa abordagem introduzia duas aproximações adicionais:

```text
magnitude CF2
    ↓
síntese de FIR / fase mínima
    ↓
MeasuredDirectivity
    ↓
consulta espacial por grade
```

Como o objetivo do trabalho exige variar continuamente posição e orientação da fonte, a estratégia preferida passou a ser uma diretividade customizada multibanda consultando diretamente o CF2.

Foi então confirmado experimentalmente o contrato efetivo usado pelo Image Source Method do Pyroomacoustics 0.10.1:

```text
get_response(...) → shape = (n_bands, n_images)
```

Para o cenário adotado:

```text
n_bands = 7
shape   = (7, N)
```

As bandas centrais utilizadas foram:

```text
125
250
500
1000
2000
4000
8000 Hz
```

Também foi confirmado que uma diretividade customizada deve ser associada a um objeto `SoundSource` e adicionada à sala com `room.add(source)`. A tentativa de passar diretamente uma classe customizada pelo argumento `directivity=` de `Room.add_source()` não era suficiente para essa integração.

Assim, a arquitetura adotada ficou:

```text
CF2
 ↓
parser de magnitude
 ↓
interpolação angular / frequência
 ↓
transformação mundo → frame local do alto-falante
 ↓
resposta multibanda (7, N)
 ↓
SoundSource + Directivity customizada
 ↓
Pyroomacoustics / Image Source Method
```

A integração atual permanece restrita ao **Image Source Method (ISM)**. O caminho de ray tracing ainda não foi validado.

## 11. Validação do adaptador CF2 de sete bandas

Foi implementada a classe:

```text
CF2SevenBandDirectivity
```

integrando o interpolador CF2, o sistema de coordenadas 3D e o contrato multibanda do Pyroomacoustics.

O smoke test utilizou seis direções canônicas:

```text
FRONT
TOP
BOTTOM
LEFT
RIGHT
BACK
```

nas sete bandas do benchmark.

Foram realizadas **42 comparações exatas em nós do CF2**, com:

```text
max_abs_error ≈ 7,1 × 10^-15 dB
```

Também foi validada a orientação física da caixa acústica. Em um teste com azimute da fonte igual a `90°`, o eixo `+Y` global passou a corresponder corretamente à frente do alto-falante em todas as sete bandas.

Esse resultado confirmou a cadeia:

```text
parser CF2
→ coordenadas 3D
→ consulta de ganho
→ conversão para amplitude
→ resposta multibanda no Pyroomacoustics
```

## 12. Validação estrutural dentro de uma sala multibanda

Foi criado um teste controlado com:

```text
max_order = 0
6 receptores cardinais
mesma distância até a fonte
7 bandas explícitas
```

O caso OMNI apresentou RIRs estruturalmente equivalentes para receptores equidistantes.

A fonte CF2 também foi aceita corretamente pela sala e gerou respostas distintas de acordo com a direção.

Inicialmente, uma reanálise posterior com `room.octave_bands.energy()` apresentou discrepâncias de até aproximadamente:

```text
2,92 dB
```

em relação aos ganhos originais usados na entrada.

Esse resultado não indicou erro no CF2. Foi posteriormente demonstrado que a síntese e a reanálise por bandas do próprio Pyroomacoustics não formam uma identidade exata e, portanto, essa reanálise não deve ser usada como critério de validação exata do adaptador.

## 13. Fechamento da síntese multibanda

Para verificar diretamente se o Pyroomacoustics estava aplicando os sete ganhos CF2 de forma correta, foi realizado um teste por decomposição em bases de banda.

Foram geradas sete respostas independentes, cada uma ativando somente uma banda. A RIR prevista pela combinação linear dessas bases foi comparada com a RIR produzida diretamente pelo adaptador CF2.

O erro global ficou na ordem de precisão numérica:

```text
max_abs_RIR_sample_error ≈ 1,59 × 10^-7
max_relative_L2_error    ≈ 4,26 × 10^-7
diferença de energia     ≈ micro-dB
```

Embora uma tolerância inicial excessivamente rígida tenha produzido uma mensagem de `FAIL`, a análise numérica mostrou equivalência prática entre as duas formas de síntese.

Assim, ficou **confirmado** que os ganhos multibanda do CF2 são aplicados corretamente pelo ISM.

## 14. Benchmark formal local — implementação escalar

Foi criado um benchmark multibanda formal comparando:

```text
OMNI
CARDIOID
CF2
```

sob exatamente a mesma configuração de sala e sete bandas.

Metodologia:

```text
3 warm-ups
20 repetições por caso
ordem cíclica balanceada
1 thread
tempo medido somente em room.compute_rir()
```

Resultados da implementação CF2 escalar:

```text
OMNI
mediana = 0,270355 s

CARDIOID
mediana = 0,279901 s
overhead vs OMNI ≈ +3,5%

CF2 escalar
mediana = 12,463069 s
overhead vs OMNI ≈ +4509,9%
≈ 46,10 × OMNI
```

As energias acústicas permaneceram determinísticas nas 20 repetições.

Esse resultado mostrou que a implementação estava funcional, mas apresentava um custo computacional incompatível com os experimentos de otimização planejados.

## 15. Vetorização do adaptador CF2

Foi então criada a implementação:

```text
CF2SevenBandDirectivityFast
```

A nova versão:

- pré-carrega somente as sete bandas utilizadas pelo Pyroomacoustics;
- elimina loops escalares Python sobre direções e bandas;
- vetoriza a transformação mundo → frame local;
- vetoriza o cálculo de `rotation` e `arc`;
- realiza a interpolação bilinear angular com NumPy.

A equivalência foi comparada em:

```text
5000 direções aleatórias
+ 6 direções canônicas
= 5006 direções
```

Resultado:

```text
max_abs_amplitude_error ≈ 1,10 × 10^-12
max_abs_db_error        ≈ 9,64 × 10^-12

legacy get_response ≈ 1,536 s
fast get_response   ≈ 0,00251 s

speedup ≈ 610,9 ×
```

No teste completo com 25 microfones e `max_order = 10`:

```text
legacy compute_rir ≈ 12,540 s
fast compute_rir   ≈ 0,315 s

speedup ≈ 39,78 ×
```

Mais importante, a comparação das RIRs retornou:

```text
max_abs_RIR_sample_error = 0.0
max_relative_L2_error    = 0.0
```

Portanto, a implementação vetorizada foi considerada **acusticamente equivalente** à implementação escalar validada.

## 16. Benchmark local definitivo com CF2 vetorizado

O benchmark formal foi repetido com a versão vetorizada, mantendo a mesma metodologia de 3 warm-ups e 20 repetições.

Resultados:

```text
OMNI
mediana = 0,260612 s

CARDIOID
mediana = 0,267995 s
overhead vs OMNI ≈ +2,8%

CF2 vetorizado
mediana = 0,288081 s
overhead vs OMNI ≈ +10,5%
```

Comparando diretamente a mediana CF2 escalar com a vetorizada:

```text
12,463069 / 0,288081 ≈ 43,26 ×
```

correspondendo a aproximadamente **97,7% de redução no tempo de execução do caso CF2**.

Assim, o elevado custo observado no primeiro benchmark não era inerente ao uso de diretividade CF2, mas principalmente resultado do overhead da implementação escalar em Python.

## 17. Execução reproduzível em Google Colab

Foi preparado um notebook reproduzível para Google Colab contendo:

- ambiente virtual isolado;
- versões fixadas de NumPy e Pyroomacoustics;
- verificação SHA-256 dos scripts;
- verificação do arquivo CF2;
- controle de threads;
- mesma configuração acústica do benchmark local.

Após uma correção para instalar o suporte a `venv` no Python disponibilizado pelo Colab, o benchmark foi executado com sucesso.

Resultados medianos:

```text
OMNI      ≈ 0,316270 s
CARDIOID  ≈ 0,321715 s
CF2       ≈ 0,361743 s
```

Overhead CF2 em relação ao OMNI:

```text
≈ +14,4%
```

A energia permaneceu determinística nas 20 repetições.

## 18. Execução reproduzível no cluster da UFV

No cluster, o Python padrão era:

```text
Python 3.6.8
```

e não permitia instalar:

```text
NumPy 2.4.6
```

Foi então investigado o sistema de módulos e localizado:

```text
python/3.11.2
```

Com esse módulo carregado, o ambiente final ficou:

```text
Python 3.11.2
NumPy 2.4.6
Pyroomacoustics 0.10.1
CPU: Intel Xeon Gold 6212U @ 2.40 GHz
threads: 1
```

Os hashes dos quatro scripts congelados e do arquivo `LA1_UW24.CF2` foram verificados antes da execução.

Resultados medianos:

```text
OMNI      = 0,207784 s
CARDIOID  = 0,211468 s
CF2       = 0,226681 s
```

Overhead:

```text
CARDIOID vs OMNI ≈ +1,8%
CF2 vs OMNI      ≈ +9,1%
```

Desvio padrão de tempo do caso CF2:

```text
≈ 0,000814 s
```

A energia permaneceu exatamente determinística nas 20 repetições.

## 19. Comparação Local × Colab × Cluster

A baseline vetorizada ficou consolidada em três ambientes:

| Ambiente | OMNI | CARDIOID | CF2 | Overhead CF2 vs OMNI |
|---|---:|---:|---:|---:|
| Local / Docker — i7-6700 | 0,260612 s | 0,267995 s | 0,288081 s | +10,5% |
| Google Colab | 0,316270 s | 0,321715 s | 0,361743 s | +14,4% |
| Cluster UFV — Xeon Gold 6212U | 0,207784 s | 0,211468 s | 0,226681 s | +9,1% |

O resultado mais importante dessa comparação foi a estabilidade do custo relativo da diretividade real:

```text
overhead CF2 ≈ 9% a 14%
```

nos três ambientes.

As diferenças acústicas entre plataformas ficaram em escala numérica muito pequena, fornecendo evidência forte de reprodutibilidade da simulação.

## 20. Stage 3 — varredura de orientação horizontal do CF2

Com a infraestrutura validada, o foco passou do benchmark computacional para a sensibilidade acústica à orientação da fonte.

Foi executada uma varredura no cluster com:

```text
posição fixa = [2,0; 2,5; 1,5] m
colatitude   = 90°
roll         = 0°
azimute      = 0° ... 355°
passo        = 5°
72 orientações
```

Para cada orientação foram calculadas, entre outras:

```text
energia média
nível médio relativo
desvio padrão espacial em dB
nível mínimo
P10
P90
P90 - P10
```

O primeiro sweep encontrou:

```text
menor desvio padrão:
15° → std ≈ 1,7882 dB

maior nível mínimo:
0° → min ≈ -12,3469 dB

maior nível médio:
0° → mean ≈ -10,2112 dB

menor P90-P10:
180° → ≈ 3,3989 dB
```

O sweep também confirmou quase perfeitamente a simetria esperada para `θ` e `360° - θ`:

```text
max diferença de energia média ≈ 3,58 × 10^-9
max diferença de std           ≈ 1,28 × 10^-7 dB
```

Esse resultado forneceu mais uma validação independente do sistema de coordenadas e da rotação da diretividade.

## 21. Refinamento da orientação entre 0° e 20°

Como os melhores compromissos estavam concentrados próximos de `0°`, foi executado um segundo sweep mais fino:

```text
0° ... 20°
passo = 1°
21 orientações
```

Resultados principais:

```text
maior nível médio:
0° → -10,2112 dB

maior nível mínimo:
0° → -12,3469 dB

menor desvio padrão:
15° → 1,7882 dB

menor P90-P10:
13° → 3,8827 dB
```

Os resultados mostram que não existe uma única orientação que seja simultaneamente dominante em todos os critérios.

A região entre aproximadamente:

```text
0° ... 15°
```

apresenta o principal compromisso entre nível médio, pior receptor e uniformidade espacial.

Essa observação será usada posteriormente na definição da função objetivo para otimização.

## 22. Geração de mapas de calor

Antes de avançar para a otimização de posição e orientação, foram gerados mapas de calor para facilitar a interpretação espacial dos resultados.

### 22.1 Comparação OMNI × CARDIOID × CF2 nos três ambientes

Foram produzidos mapas para:

```text
OMNI
CARDIOID
CF2
```

em:

```text
Local
Google Colab
Cluster UFV
```

utilizando o plano horizontal dos 25 microfones em:

```text
z = 1,2 m
```

Para permitir comparação direta, cada caso acústico utiliza a mesma escala de cores nos três ambientes.

A grandeza exibida é:

```text
10 log10(sum(RIR²))
```

ou seja, **nível relativo de energia da RIR**.

Essa métrica não deve ser interpretada como SPL físico calibrado em dB re 20 µPa.

Também foram gerados mapas de diferença:

```text
Colab - Local
Cluster - Local
```

em escala de micro-dB, tornando visíveis diferenças numéricas que seriam praticamente imperceptíveis nos mapas acústicos principais.

https://jeninor.github.io/Projeto-Acustica/projeto/stage2_heatmaps_local_colab_cluster/heatmap_report.html

### 22.2 Mapas do sweep fino de orientação

Também foram produzidos mapas para todas as 21 orientações do sweep:

```text
0°
1°
2°
...
20°
```

com escala de cor comum.

Foram destacados especialmente:

```text
0°  → maior nível médio e maior nível mínimo
13° → menor P90-P10 no sweep fino
15° → menor desvio padrão
```

Esses mapas ajudam a visualizar como uma rotação relativamente pequena do alto-falante redistribui a energia sobre a área de receptores.

### Limitação atual dos mapas

Os mapas atuais são construídos a partir da grade de:

```text
5 × 5 = 25 receptores
```

e a superfície entre os pontos é interpolada para visualização.

Portanto, ainda não representam uma simulação densa ponto a ponto de toda a superfície da sala.

Uma próxima etapa proposta é gerar uma malha densa, por exemplo:

```text
61 × 51 = 3111 pontos
z = 1,2 m
```

calculando diretamente a RIR em cada posição para produzir um mapa acústico espacial de alta resolução sem depender da interpolação de apenas 25 receptores.

---

# Síntese do progresso no período

```text
Validação CATT/CLF
        ↓
Automação de capturas
        ↓
Benchmark da automação
        ↓
Investigação GLL
        ↓
Definição do benchmark acústico
        ↓
OMNI × CARDIOIDE
        ↓
Validação de orientação 3D
        ↓
Mundo → coordenadas locais CF2
        ↓
Validação da matriz CF2
        ↓
Identificação de bandas válidas
        ↓
Interpolação CF2
        ↓
Integração multibanda customizada com Pyroomacoustics
        ↓
Validação do contrato (7, N)
        ↓
Validação da síntese multibanda
        ↓
Benchmark CF2 escalar
        ↓
Vetorização do adaptador
        ↓
Equivalência acústica legacy × fast
        ↓
Benchmark local definitivo
        ↓
Reprodução em Google Colab
        ↓
Reprodução no cluster da UFV
        ↓
Comparação entre três plataformas
        ↓
Sweep de orientação 0°...355°
        ↓
Refinamento 0°...20°
        ↓
Mapas de calor espaciais
```

## Estado ao final de 24/09/2026

### Confirmado

- leitura coerente da matriz CF2 de magnitude;
- estrutura angular `72 × 37`;
- `rotation = 0...355°` em passos de 5°;
- `arc = 0...180°` em passos de 5°;
- polos frontal e traseiro válidos;
- simetria esquerda/direita do arquivo Bosch LA1-UW24 analisado;
- intervalo útil de diretividade entre **100 Hz e 10 kHz**;
- transformação geométrica 3D mundo → frame local do alto-falante;
- interpolação reproduzindo os pontos originais com erro `0 dB`;
- integração CF2 diretamente como diretividade multibanda customizada;
- contrato efetivo do ISM com resposta `(7, N)`;
- aplicação correta dos ganhos multibanda dentro do Pyroomacoustics;
- equivalência acústica entre o adaptador escalar validado e o adaptador vetorizado;
- redução aproximada de **43×** na mediana do caso CF2 entre a implementação escalar e a vetorizada;
- baseline multibanda local definitiva com OMNI, CARDIOID e CF2;
- reprodução do benchmark no Google Colab;
- reprodução do benchmark no cluster da UFV;
- overhead CF2 vetorizado em torno de **9% a 14%** sobre OMNI nos três ambientes;
- determinismo das métricas acústicas nas repetições;
- sweep horizontal completo de 72 orientações;
- refinamento de orientação entre `0°` e `20°` em passos de `1°`;
- confirmação adicional de simetria do resultado para orientações espelhadas;
- geração de mapas de calor comparativos Local × Colab × Cluster;
- geração de mapas de calor do sweep fino de orientação.

### Evidência forte, mas com ressalvas

- reprodutibilidade acústica entre Local, Colab e Cluster, com diferenças apenas em escala numérica muito pequena;
- interpretação do comportamento off-grid do interpolador CF2 em dB;
- handedness mantida configurável, pois o alto-falante atual é exatamente simétrico esquerda/direita e não permite resolver fisicamente o sinal da rotação apenas com esse arquivo.

### Em andamento / próximos passos

- gerar mapas densos da superfície da sala com milhares de receptores simulados diretamente;
- definir formalmente a função objetivo combinando nível médio, pior receptor e uniformidade;
- expandir o espaço de busca de orientação para posição + orientação;
- implementar e comparar GA, PSO e Tabu Search;
- validar posteriormente pontos intermediários contra o CLF Viewer;
- manter GLL como linha paralela de investigação;
- avaliar ray tracing separadamente, pois a integração atual está validada para ISM;
- posteriormente avançar para reconstrução 3D automática e simuladores acústicos baseados em IA.
