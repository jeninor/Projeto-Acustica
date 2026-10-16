# Benchmark Bosch LA1_UW24.CF2 — ODEON, I-Simpa e Pyroomacoustics

## Escopo

Auditório **Example.par** de ODEON, inclinado, fonte em (3, 0, 4) m e 25 receptores. Arquivos ODEON usados nesta versão com ganho adicional 0 dB (informação confirmada pelo usuário). I-Simpa 1.3.4, SPPS Energetic, 500 mil partículas, semente 123, duração 8 s, banda de 1000 Hz reconstruída por 800/1000/1250 Hz, Lw global 107,2 dB. Pyroomacoustics representa execução direcional anterior de 50.000 raios.

## Média dos 25 receptores

| Indicador | Simulador | 0° | −8° | −16° |
|---|---|---:|---:|---:|
| EDT (s) | ODEON | 3.439 | 3.399 | 3.394 |
| EDT (s) | I-Simpa | 3.863 | 3.714 | 3.644 |
| EDT (s) | Pyroom | 3.935 | 3.859 | 3.825 |
| T30 (s) | ODEON | 3.312 | 3.324 | 3.329 |
| T30 (s) | I-Simpa | 3.856 | 3.810 | 3.760 |
| T30 (s) | Pyroom | 3.960 | 3.922 | 3.863 |
| C80 (dB) | ODEON | -1.024 | 0.144 | 0.592 |
| C80 (dB) | I-Simpa | -2.524 | -1.333 | -0.766 |
| C80 (dB) | Pyroom | -3.696 | -2.626 | -1.999 |

### Indicadores complementares de I-Simpa

| Indicador | 0° | −8° | −16° |
|---|---:|---:|---:|
| SPL (dB) | 70.413 | 70.828 | 70.787 |
| Desvio-padrão SPL (dB) | 0.765 | 0.545 | 0.426 |
| C50 (dB) | -3.711 | -2.446 | -1.981 |
| D50 (%) | 31.420 | 37.593 | 39.916 |
| Ts (ms) | 279.984 | 252.443 | 240.425 |
| G (dB) | 5.321 | 5.737 | 5.696 |

## I-Simpa menos ODEON — 25 pares válidos por orientação

| Ângulo | Indicador | Viés | RMSE | Correlação espacial |
|---|---|---:|---:|---:|
| 0° | EDT (s) | +0.424 | 0.451 | 0.404 |
| 0° | T30 (s) | +0.544 | 0.554 | -0.147 |
| 0° | C80 (dB) | -1.500 | 1.549 | 0.984 |
| 0° | Ts (ms) | +79.264 | 80.958 | 0.868 |
| -8° | EDT (s) | +0.315 | 0.356 | -0.054 |
| -8° | T30 (s) | +0.486 | 0.491 | -0.046 |
| -8° | C80 (dB) | -1.477 | 1.539 | 0.968 |
| -8° | Ts (ms) | +75.563 | 77.199 | 0.723 |
| -16° | EDT (s) | +0.250 | 0.291 | -0.078 |
| -16° | T30 (s) | +0.430 | 0.435 | 0.015 |
| -16° | C80 (dB) | -1.358 | 1.399 | 0.897 |
| -16° | Ts (ms) | +73.225 | 74.475 | 0.906 |

## Concordância na seleção do melhor ângulo pelo C80

O máximo de C80 é calculado **para cada receptor**, entre 0°, −8° e −16°:

- ODEON × I-Simpa: **22/25** receptores.
- I-Simpa × Pyroom: **20/25** receptores.
- ODEON × Pyroom: **21/25** receptores.
- Os três coincidem: **19/25** receptores.

Em média dos 25 receptores, os três programas favorecem **−16° para C80**. Já o melhor SPL médio em I-Simpa ocorre em **−8°**, com diferença pequena de −16°. Por receptor, I-Simpa favorece −8° em 12 posições e −16° em 13.

## Verificações dos arquivos

- As três simulações I-Simpa incluem os 25 arquivos `Acoustic parameters.gabe`.
- Foram utilizados os mesmos bytes de `mesh.cbin` e do arquivo direcional EASE nas três simulações.
- A fonte mantém as potências 91,1707 dB em 800, 1000 e 1250 Hz, total espectral 107,20 dB.
- Os vetores de orientação importados dos ZIPs são (1,0,0), (0,9903,0,−0,1392) e (0,9613,0,−0,2750).
- Scattering, equivalência angular e sinal da tabela EASE não foram validados independentemente apenas por esta comparação.

## Limitações e recomendações

1. Embora o Lw global seja igualado, o espectro em terços do I-Simpa é aproximado a partir do espectro por oitava de ODEON. A calibração espectral completa requer os dados do CF2 por terço.
2. O SPL do Pyroomacoustics ainda não deve ser comparado como valor absoluto em dB contra I-Simpa ou ODEON sem calibração.
3. A diferença sistemática de C80 e T30 depende das implementações de absorção, scattering, respostas impulsivas e cálculo dos indicadores.
4. Recomenda-se testar estabilidade de orientação −8/−16 com outras sementes, antes de declarar um melhor ângulo universal.
5. Como a exportação ODEON disponibiliza C80 em resolução de 0,1 dB, rankings envolvendo empates ou diferenças próximas a 0,1 dB devem ser lidos com cautela.

