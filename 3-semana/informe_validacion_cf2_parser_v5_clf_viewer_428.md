# Informe de validación empírica de `cf2_parser_v5.py` contra CLF Viewer

**Fecha:** 1 de octubre de 2026  
**Objeto de validación:** magnitud de directividad de archivos CLF2/CF2  
**Parser evaluado:** `cf2_parser_v5.py`  
**Referencia externa:** capturas *Balloon spectra* de CLF Viewer reconstruidas numéricamente  
**Corpus final:** 428 modelos CF2

---

## 1. Resumen ejecutivo

Se realizó una validación masiva de la interpretación de la magnitud de directividad implementada en `cf2_parser_v5.py` utilizando, como referencia independiente, datos reconstruidos desde las gráficas *Balloon spectra* mostradas por CLF Viewer.

Los 428 modelos disponibles fueron emparejados y comparados correctamente, sin errores de ejecución. De ellos, **424 (99.07%)** fueron clasificados como `EXCELLENT` y **4 (0.93%)** como `GOOD`; no se obtuvieron casos `REVIEW`, `POOR` ni fallos de comparación.

La validación produjo **21,873,459 puntos numéricos comparados** entre las curvas reconstruidas de CLF Viewer y las matrices de directividad obtenidas directamente del binario CF2.

Los resultados agregados fueron:

| Métrica | Resultado |
|---|---:|
| Modelos comparados | 428 / 428 |
| Errores de ejecución | 0 |
| Puntos comparados | 21,873,459 |
| MAE global ponderado | 0.1190 dB |
| RMSE global ponderado | 0.3923 dB |
| Bias global ponderado, imagen − parser | +0.0344 dB |
| Puntos dentro de ±0.25 dB | 93.58% |
| Puntos dentro de ±0.50 dB | 97.68% |
| Puntos dentro de ±1.00 dB | 98.99% |
| Mediana del MAE por modelo | 0.1028 dB |
| Mediana de la mediana de error absoluto por modelo | 0.0642 dB |
| P90 del MAE entre modelos | 0.1720 dB |
| Rango del MAE entre modelos | 0.0506–0.2822 dB |

La correspondencia estructural encontrada fue completamente consistente:

- **desplazamiento de frecuencia:** `0` en **428/428** modelos;
- **mapeo del ARC de la interfaz de CLF Viewer:** `reverse` en **428/428** modelos;
- entre los **56 modelos** cuya geometría permitió identificar de forma no ambigua el azimut, **56/56** seleccionaron `direction = +1` y `offset = 0°`.

Estos resultados respaldan fuertemente la interpretación de la magnitud de directividad usada por `cf2_parser_v5.py`.

---

## 2. Alcance de la validación

Este informe valida específicamente la **magnitud de directividad angular** del parser.

El parser adopta actualmente la siguiente interpretación estructural:

```text
DIRECTIVITY_BASE = 14232

shape por frecuencia = (72, 37)

filas    = azimuth [0°, 5°, ..., 355°]
columnas = arc     [0°, 5°, ..., 180°]

2664 float32 por slot de frecuencia
10656 bytes por slot
```

La validación presentada aquí **no debe interpretarse automáticamente como una validación completa de todos los campos del formato CF2**. En particular, quedan fuera del alcance de este experimento:

- semántica completa de todos los campos EA/header;
- interpretación final de fase en CF2 V2;
- datos no visibles en las gráficas *Balloon spectra*;
- cualquier campo binario que no intervenga en la magnitud angular comparada.

El corpus contiene:

- V1: 406, V2: 22;
- distribuciones de `balloon_symmetry`: none: 207, horizontal: 101, rotational: 61, full: 50, vertical: 6, polar: 3.

Esto aporta diversidad de versiones y geometrías al conjunto de validación.

---

## 3. Fuentes de datos

### 3.1. Fuente binaria

Para cada modelo se utilizó el archivo `.CF2` original y se obtuvo la matriz de directividad mediante `cf2_parser_v5.py`.

La hipótesis bajo prueba fue deliberadamente mantenida fija durante la validación; es decir, el parser no fue reajustado modelo por modelo para aumentar artificialmente la concordancia.

### 3.2. Fuente visual independiente

En paralelo se utilizaron las capturas *Balloon spectra* obtenidas automáticamente desde CLF Viewer.

Cada modelo contiene una malla de:

```text
72 estados horizontales
×
37 estados verticales
=
2664 orientaciones
```

Las gráficas fueron transformadas a valores numéricos mediante el extractor visual `extract_balloon_spectra_v1_2.py`.

La extracción visual detecta la geometría de la gráfica, identifica las posiciones de frecuencia, calibra el eje vertical en dB y reconstruye la polilínea visible. Los valores no observables o confundibles con elementos fijos del gráfico se omiten en lugar de ser inventados.

Esta separación es metodológicamente importante:

```text
CF2 binario ──> cf2_parser_v5.py ─────┐
                                      ├── comparación
CLF Viewer ──> PNG ──> extractor ─────┘
```

De esta manera, la referencia visual no reutiliza los valores del parser para construir los datos que posteriormente se comparan con él.

---

## 4. Correcciones realizadas antes de la validación final

Durante el desarrollo del método se detectaron dos supuestos incorrectos en la primera versión del extractor visual.

### 4.1. Posiciones de frecuencia del gráfico

Inicialmente las 29 líneas verticales se interpretaron como `31.5 Hz ... 20 kHz`. La inspección de las etiquetas de CLF Viewer y la comparación multmodelo mostraron que las posiciones visibles corresponden a:

```text
40, 50, 63, 80, 100, 125, 160, 200, 250, 315,
400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500,
3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000,
20000, 25000 Hz
```

Después de corregir esta interpretación, el desplazamiento óptimo de frecuencia pasó a ser `0`.

### 4.2. Líneas horizontales fijas

En algunos modelos CLF Viewer renderiza la línea inferior de la gráfica en negro. La primera implementación podía confundir esta línea con una curva acústica real, especialmente cuando los valores binarios se encontraban por debajo del rango visible del gráfico.

La versión V1.2 elimina/ignora las seis líneas horizontales de referencia:

```text
+10, 0, -10, -20, -30 y -40 dB
```

cuando estas interfieren con la detección.

El caso `CES_Audio-SCP-2060` fue particularmente informativo: tras esta corrección dejó de presentar discrepancias extremas y pasó a concordar con el parser con un MAE cercano a 0.1 dB.

---

## 5. Búsqueda de la correspondencia Viewer ↔ parser

El comparador no impuso inicialmente una única transformación. Para cada modelo evaluó distintas hipótesis:

### Frecuencia

```text
shift ∈ {-2, -1, 0, +1, +2} slots
```

### ARC

```text
direct  : arc_index = y_index
reverse : arc_index = 36 - y_index
```

### Azimut

```text
direction ∈ {+1, -1}
offset    ∈ {0°, 5°, ..., 355°}
```

La búsqueda se realizó primero sobre una muestra determinista de hasta 3000 puntos por modelo. Las mejores hipótesis fueron después recalculadas sobre todos los puntos disponibles de cada modelo.

Las métricas principales fueron:

- MAE;
- RMSE;
- bias;
- mediana del error absoluto;
- P90 y P95 del error absoluto;
- proporción de muestras dentro de ±0.25, ±0.50 y ±1.00 dB.

El consenso estructural utilizó como criterios:

```text
MAE <= 1.0 dB
mediana(|error|) <= 0.25 dB
P90(|error|) <= 0.50 dB
```

Los **428 modelos** cumplieron simultáneamente esos criterios.

---

## 6. Resultados estructurales

### 6.1. Frecuencia

El resultado fue unánime:

```text
freq_shift = 0
428 / 428 modelos
```

Por tanto, una vez corregida la interpretación de la rejilla visual, no se observa evidencia de un desplazamiento sistemático entre las frecuencias usadas por el parser y las presentadas por CLF Viewer.

### 6.2. ARC

También se obtuvo consenso total:

```text
arc_mode = reverse
428 / 428 modelos
```

Esto no implica invertir la matriz binaria interna.

La explicación consistente es que el índice vertical empleado durante la automatización de CLF Viewer recorre la interfaz en dirección opuesta al eje canónico del parser.

La transformación Viewer → coordenada canónica es:

```text
arc_deg = (36 - y_index) * 5°
```

Por ejemplo:

| `y_index` de Viewer | ARC canónico |
|---:|---:|
| 36 | 0° |
| 35 | 5° |
| 18 | 90° |
| 1 | 175° |
| 0 | 180° |

El parser puede, por tanto, conservar de forma natural:

```text
arc = [0°, 5°, ..., 180°]
```

### 6.3. Azimut

Muchos altavoces presentan simetrías que impiden distinguir matemáticamente varias transformaciones azimutales equivalentes.

Sin embargo, **56 modelos** tuvieron una solución no ambigua según el comparador y todos coincidieron:

```text
azimuth direction = +1
azimuth offset    = 0°
```

Es decir:

```text
azimuth_deg = x_index * 5°
```

con:

| `x_index` | Azimut |
|---:|---:|
| 0 | 0° |
| 18 | 90° |
| 36 | 180° |
| 54 | 270° |
| 71 | 355° |

La unanimidad de los 56 casos identificables constituye evidencia adicional a favor de la convención angular actual.

---

## 7. Resultados cuantitativos del corpus

### 7.1. Clasificación por modelo

```text
EXCELLENT : 424
GOOD      : 4
REVIEW    : 0
POOR      : 0
ERROR     : 0
```

Esto significa que el **99.07%** de los modelos quedó en la categoría `EXCELLENT`.

### 7.2. Métricas globales

Sobre **21,873,459 comparaciones**:

```text
MAE ponderado              = 0.1190 dB
RMSE ponderado             = 0.3923 dB
bias ponderado             = +0.0344 dB

|error| <= 0.25 dB         = 93.58%
|error| <= 0.50 dB         = 97.68%
|error| <= 1.00 dB         = 98.99%
```

El bias pequeño frente al MAE indica que no existe una desviación global de magnitud suficiente para explicar los errores por una traslación sistemática del parser.

El RMSE es mayor que el MAE porque un número relativamente pequeño de outliers visuales recibe un peso cuadrático elevado. Esta interpretación también es coherente con las medianas bajas obtenidas en prácticamente todo el corpus.

### 7.3. Distribución entre modelos

```text
mediana del MAE/modelo               = 0.1028 dB
P90 del MAE/modelo                   = 0.1720 dB
MAE mínimo observado                 = 0.0506 dB
MAE máximo observado                 = 0.2822 dB
mediana de medianas |error|/modelo   = 0.0642 dB
```

Estos resultados son compatibles con la cuantización espacial de una curva rasterizada y con errores ocasionales de seguimiento de píxeles.

---

## 8. Modelos clasificados como `GOOD`

Los únicos cuatro modelos fuera de la categoría `EXCELLENT` fueron:

| Modelo | N | MAE (dB) | RMSE (dB) | Mediana \|error\| (dB) | P90 (dB) | ≤ ±0.5 dB |
|---|---:|---:|---:|---:|---:|---:|
| `FZ_Audio-FZ_1203A` | 52,720 | 0.2822 | 1.1573 | 0.0792 | 0.2849 | 94.40% |
| `d_b_audiotechnik-24C-E_HF_0_` | 66,962 | 0.2516 | 0.9105 | 0.0943 | 0.3528 | 94.15% |
| `d_b_audiotechnik-24C-E_HF_1_` | 67,038 | 0.2518 | 0.9041 | 0.0925 | 0.3491 | 94.03% |
| `d_b_audiotechnik-24C-E_HF_2_` | 67,365 | 0.2561 | 0.9429 | 0.0962 | 0.3434 | 94.24% |

Estos casos no muestran evidencia de una transformación estructural diferente:

- mantienen `freq_shift = 0`;
- mantienen `arc_mode = reverse`;
- continúan dentro de los criterios robustos de consenso;
- sus medianas y P90 son mucho menores que sus RMSE máximos.

Por tanto, el patrón observado es más compatible con una pequeña fracción de outliers de reconstrucción visual que con un error sistemático de estructura binaria.

---

## 9. Evidencia respecto a `DIRECTIVITY_BASE = 14232`

El parser V5 interpreta el inicio de la magnitud de directividad como:

```python
DIRECTIVITY_BASE = 14232
```

y cada frecuencia como una matriz `72 × 37` ya almacenada en orden canónico.

La validación actual no realiza por sí sola una nueva búsqueda exhaustiva de todos los offsets binarios posibles. Sin embargo, prueba directamente la consecuencia numérica de utilizar la interpretación actual sobre **428 archivos** y **21,873,459 puntos comparados**.

La concordancia obtenida, junto con:

```text
freq_shift = 0 en 428/428
arc transform consistente en 428/428
azimuth +1 / 0° en 56/56 casos identificables
```

no aporta evidencia que justifique sustituir la interpretación `DIRECTIVITY_BASE = 14232`.

Por ello se recomienda mantener esta base en el parser y tratar `14236` únicamente como hipótesis histórica/diagnóstica, salvo que nueva evidencia binaria independiente contradiga el resultado actual.

---

## 10. Diferencia entre estructura binaria y convención de interfaz

Un resultado central de esta validación es la necesidad de separar:

1. **cómo están almacenados los datos en el CF2**, y
2. **cómo CLF Viewer recorre o representa visualmente esos datos**.

La relación observada es:

```text
Parser canónico:
    azimuth = 0 -> 355°, paso 5°
    arc     = 0 -> 180°, paso 5°

Captura automatizada de Viewer:
    horizontal x aumenta con azimuth
    vertical y disminuye cuando aumenta el ARC canónico
```

Por tanto:

```text
azimuth_deg = 5 * x_index
arc_deg     = 5 * (36 - y_index)
```

La inversión del índice vertical pertenece a la capa de adaptación Viewer → coordenadas canónicas. No debe introducirse como una rotación artificial en la lectura del binario.

---

## 11. Limitaciones de la validación visual

Aunque el resultado es fuerte, la referencia visual no es una medición exacta del contenido binario.

Las principales limitaciones son:

1. **Resolución raster.** La posición vertical está cuantizada en píxeles.
2. **Anti-aliasing.** Una línea puede distribuir su intensidad entre varios píxeles.
3. **Solapamiento con la rejilla.** Algunos valores coinciden con líneas horizontales fijas.
4. **Floor visual.** CLF Viewer no necesariamente representa valores muy inferiores al rango vertical mostrado.
5. **Curvas parcialmente ocultas.** Un valor binario puede existir aunque no sea recuperable de la imagen.
6. **Simetrías acústicas.** Pueden hacer indistinguibles varias transformaciones azimutales.
7. **Modo de comparación.** La ejecución final fue realizada con `Declared-only mode = False`, por lo que la búsqueda de correspondencias pudo utilizar slots globales raw del parser. Esto es apropiado para reverse engineering, pero no sustituye una validación específica de la semántica `declared_min_idx..declared_max_idx`.

Por estas razones, las imágenes deben utilizarse como **fuente de validación externa**, no como sustituto de los valores binarios finales.

---

## 12. Implicaciones para la extracción científica

Después de esta validación, el flujo recomendado para magnitud es:

```text
CF2 original
     |
     v
cf2_parser_v5.py
     |
     v
directivity_db binaria
     |
     +--> CSV / análisis
     |
     +--> conversión CF2 -> SOFA
     |
     +--> Pyroomacoustics
```

Las capturas de CLF Viewer permanecen como mecanismo de auditoría:

```text
CLF Viewer -> PNG -> extractor visual -> comparación/QC
```

No se recomienda utilizar la reconstrucción rasterizada como fuente numérica principal cuando el dato binario está disponible.

---

## 13. Estado del parser después de la validación

Para la **magnitud de directividad**, no se identifica actualmente una razón empírica para crear una nueva versión del parser únicamente para modificar:

- `DIRECTIVITY_BASE`;
- orientación del azimut;
- orden canónico del ARC;
- asociación entre slots de frecuencia y frecuencias nominales.

La recomendación es mantener congelada esta parte de `cf2_parser_v5.py` y documentarla como validada por el corpus actual.

Una futura `v6` tendría sentido únicamente si incorpora nueva funcionalidad o correcciones demostradas en otras áreas, por ejemplo:

- semántica de campos EA/header;
- validación adicional de fase CF2 V2;
- tratamiento explícito de variantes de formato;
- exportación CF2 → SOFA;
- controles de integridad y diagnóstico adicionales.

---

## 14. Observación sobre el `RuntimeWarning`

Durante el procesamiento apareció al menos un aviso similar a:

```text
RuntimeWarning: invalid value encountered in cast
```

en el helper que convierte arrays `float32` a `float64`.

Los modelos alrededor de ese aviso continuaron clasificándose correctamente, por lo que el warning no produjo evidencia de un fallo en la magnitud de directividad.

Debe investigarse de forma separada para determinar si procede de:

- `NaN` especiales;
- valores no inicializados;
- datos EA;
- fase;
- otro campo leído por el parser.

No se recomienda modificar offsets o directividad únicamente para eliminar este warning.

---

## 15. Conclusión

La validación masiva proporciona evidencia empírica fuerte a favor de la interpretación actual de la magnitud de directividad de `cf2_parser_v5.py`.

Los resultados principales son:

```text
428/428 archivos emparejados
428/428 comparaciones completadas
0 errores

424 EXCELLENT
4 GOOD

21,873,459 puntos comparados

freq_shift = 0:
    428/428

Viewer ARC mapping = reverse:
    428/428

Azimut identificable:
    +x, offset 0°
    56/56

MAE global ponderado:
    0.1190 dB

97.68% aproximadamente dentro de ±0.5 dB
98.99% aproximadamente dentro de ±1.0 dB
```

La inversión vertical encontrada debe interpretarse como una diferencia de convención entre la interfaz de CLF Viewer y el eje ARC canónico, no como un error del orden binario.

En consecuencia, la **magnitud de directividad del parser V5 puede considerarse fuertemente validada frente al corpus visual disponible**, dentro del alcance y las limitaciones descritas en este informe.

---

## 16. Reproducibilidad

La ejecución final del comparador utilizó:

```bash
python compare_viewer_vs_parser_v5_corpus_v1_1.py \
  --parser cf2_parser_v5.py \
  --cf2-dir speaker_cf2 \
  --viewer-root extracted_balloon_spectra_v12_all \
  --output-dir viewer_vs_parser_v5_corpus \
  --sample-points 3000 \
  --top-k 8
```

Tiempo reportado:

```text
172.19 s
```

Archivos de salida principales:

```text
viewer_vs_parser_v5_corpus/
├── match_manifest.csv
├── corpus_summary.csv
├── corpus_hypotheses_top.csv
└── errors.csv
```

---

## 17. Archivos usados como evidencia

- `cf2_parser_v5.py`
- `corpus_summary.csv`
- `corpus_hypotheses_top.csv`
- `match_manifest.csv`
- `corpus_summary.txt`
- log completo de la ejecución de 428 modelos
- CSV reconstruidos desde `extract_balloon_spectra_v1_2.py`

Los valores agregados adicionales de este informe fueron calculados a partir de `corpus_summary.csv`, ponderando las métricas globales por `best_n`, el número de puntos comparados por modelo.
