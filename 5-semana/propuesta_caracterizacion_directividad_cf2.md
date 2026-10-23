# Propuesta técnica — Caracterización espectral y espacial de directividad a partir de CF2

**Fecha:** 14 de octubre de 2026  
**Estado:** propuesta metodológica; pendiente de ejecución sobre archivos `.CF2` binarios.  
**Componente de entrada:** `cf2_parser_v5.py` (versión adjunta y revisada).  
**Objetivo:** obtener un perfil objetivo de directividad por banda, visualizaciones reproducibles y comparaciones entre altavoces, preservando los datos CF2 validados.

## 1. Fundamento disponible

El parser actual ya permite recuperar las matrices de magnitud `D(f, azimuth, arc)` para los slots de frecuencia declarados. Cada matriz presenta `72 × 37` muestras (azimut de `0°` a `355°`; arco de `0°` a `180°`; paso angular `5°`). La tabla global comprende **30 frecuencias de tercio de octava**, entre 25 Hz y 20 kHz; cada CF2 puede declarar únicamente un subconjunto. El método `directivity_third_octave(f)` restringe el acceso al intervalo declarado, `directivity_grid(f)` aporta los ejes y `directivity_octave(f, require_declared_triplet=True)` combina tres tercios por promedio energético.

La extracción binaria parte de `DIRECTIVITY_BASE = 14232` y **no debe alterarse para acomodarla a una visualización**. El gráfico compatible con CATT aplica un suelo visual de −50 dB, que **no** debe aplicarse antes del cálculo de índices físicos. Los valores atípicos legítimos (incluyendo positivos y casos con −200 dB) deben preservarse y acompañarse de indicadores de calidad.

El archivo `LA1_UW24(1).EMF` aporta un gráfico polar de referencia con curvas de cortes izquierda/derecha (rojo) y arriba/abajo (azul); **no trae un identificador de frecuencia verificable en la propia imagen**. Por ello no sirve por sí solo para cuantificar una curva `DI(f)` ni para atribuirle una banda específica al gráfico.

## 2. Preguntas científicas

1. ¿Cuál es la evolución de la directividad de cada altavoz en función de la frecuencia?
2. ¿Qué ancho de haz horizontal y vertical se observa en cada banda?
3. ¿Cuán distinta es la energía relativa emitida hacia el hemisferio posterior?
4. ¿Cuánta asimetría presentan las respuestas derecha/izquierda y arriba/abajo?
5. ¿Cuándo los patrones presentan máximos fuera del eje, lóbulos secundarios o cambios abruptos entre bandas?
6. ¿Qué patrones ofrecen mejor cobertura para distintos objetivos de simulación y optimización acústica?

## 3. Caracterización cuantitativa propuesta

Definimos `D_f(φ, θ)` como la matriz **original** de directividad en dB; `θ` es el *arc* y `φ` el azimut del formato. Primero se valida que `arc` equivale geométricamente al ángulo polar respecto al eje frontal para aplicar integración sobre la esfera.

| Indicador por frecuencia | Cálculo o definición | Utilidad |
|---|---|---|
| Nivel en eje `D_axis` | Mediana o valor común de `D_f[:, arc=0°]`, guardando su dispersión | Referencia de normalización |
| Nivel máximo y dirección del máximo | Máximo sobre la esfera y posición angular | Identificar haz inclinado y lóbulos |
| Directividad normalizada `D_rel` | `D_f − D_axis` (solo para métricas relativas) | Comparar patrones sin cambiar el raw |
| Factor de directividad `Q` | Potencia relativa sobre eje / promedio direccional de potencia sobre la esfera | Índice de concentración energética |
| Índice de directividad `DI` | `10 log10(Q)` dB | Métrica principal de clasificación |
| Ancho de haz horizontal a −6 dB | Suma de las semiamplitudes del lóbulo principal alrededor del eje | Cuantificar cobertura lateral |
| Ancho de haz vertical a −6 dB | Igual, para el plano vertical | Cuantificar cobertura vertical |
| Relación frontal/posterior `F/B` | Diferencia de niveles en eje frontal y posterior | Rechazo axial hacia atrás |
| Fracción energética posterior | Energía integrada sobre hemisferio posterior / energía integrada total | Medir radiación global hacia atrás |
| Asimetría H/V | Diferencias entre mitades izquierda/derecha y arriba/abajo | Identificar patrones no simétricos |
| Complejidad de lóbulos | Cantidad de máximos o máximos secundarios relevantes sobre cortes | Distinguir patrones limpios de irregulares |
| Indicadores de calidad | Datos finitos, coincidencia de polos, valores extremos, bandas disponibles | Evitar clasificaciones engañosas |

### 3.1. Índice de directividad sobre toda la esfera

Para un patrón en dB **normalizado respecto al eje frontal**, `D_rel`, una aproximación discreta es:

\[
Q(f)=\frac{1}{\langle 10^{D_{rel}(f,\varphi,\theta)/10}\rangle_{\Omega}},\qquad
DI(f)=10\log_{10}Q(f).
\]

La media angular `⟨·⟩_Ω` se obtiene con ponderación de ángulo sólido, no como promedio simple de celdas de la matriz. Si la correspondencia geométrica es la de coordenadas esféricas polares, se usa `w(θ)∝sin(θ)` (o una integración por celdas equivalente). Así, un patrón uniforme de `0 dB` relativo a eje produce `Q≈1`, `DI≈0 dB`.

**Cuidado:** la integral se define con el patrón relativo y la referencia **frontal real**, no con una normalización automática por el máximo, pues eso escondería altavoces cuyo máximo se encuentra fuera de eje. Es útil registrar separadamente `peak_off_axis_db`.

### 3.2. Anchos de haz y asimetría

Construir un corte horizontal completo y otro vertical combinando los dos semiplanos que parten del eje frontal hasta el posterior. Como hipótesis operativa basada en las convenciones angulares documentadas por el parser: para el plano horizontal usar las rotaciones `90°` y `270°`; para el vertical `0°` y `180°`. **Confirmar ambos cortes contra los modelos asimétricos y CLF Viewer antes de usar las etiquetas izquierda/derecha/arriba/abajo en informes.**

Calcular cada cruce a `−6 dB` por interpolación angular. El ancho de haz es la **suma de las extensiones izquierda y derecha**, o arriba y abajo, del lóbulo principal frontal. Si hay múltiples cruces, reportar que la métrica es ambigua, en vez de sumar lóbulos secundarios. Si el patrón nunca cae 6 dB dentro del semiplano, reportar un límite/cobertura amplia, no inventar un ancho preciso.

Los campos existentes `h_left`, `h_right`, `v_up`, `v_down` y `axial_q` constituyen **referencias EA** con las que comparar la estimación; no deben sustituir al cálculo sobre el balloon. La propiedad `horizontal_width_deg` del parser es `2×min(h_left,h_right)` y, por tanto, **no equivale a la suma exacta de semiamplitudes en casos asimétricos**; ocurre lo mismo en vertical. Tampoco debe suponerse la equivalencia automática entre `axial_q` y el `Q` integrado sin comprobar convenciones y unidades.

### 3.3. Umbrales iniciales de clasificación (hipótesis a calibrar)

| DI por banda | Etiqueta tentativa | Interpretación |
|---|---|---|
| < 3 dB | Poco directivo / casi omnidireccional | Amplia dispersión espacial |
| 3 a < 6 dB | Moderadamente directivo | Concentración limitada |
| 6 a < 10 dB | Directivo | Energía concentrada en menor zona |
| ≥ 10 dB | Muy directivo | Concentración elevada |

**Estos cortes no son un estándar universal de clasificación de altavoces.** Deben ajustarse al corpus y acompañarse de indicadores H/V, F/B y lóbulos secundarios. Una fuente puede tener alto `DI` y aun así ser poco adecuada para una cobertura uniforme (p. ej. con lóbulos muy irregulares). Si el eje es erróneo, la matriz es inválida o existen restricciones de cobertura, la categoría será `indeterminada`.

## 4. Visualizaciones recomendadas

**Figura A — Polar doble por frecuencia.** Reproducir el estilo de la imagen EMF: cortes horizontal y vertical en la misma escala y sobre la misma banda. Exportar en PNG y SVG; indicar nombre del modelo, unidad, frecuencia, frecuencia declarada y referencia `0 dB`.

**Figura B — Mosaico multibanda.** Para cada modelo, desplegar paneles con 125, 250, 500, 1000, 2000, 4000 y 8000 Hz (solo cuando la banda esté disponible). Preferir la matriz original por tercio de octava; cuando se use octava, explicitar el triplete y exigir cobertura declarada completa.

**Figura C — Mapa de calor frecuencia × ángulo.** Un mapa para horizontal y otro para vertical, con `f` en escala logarítmica. Usar límites cromáticos fijos y comunes entre modelos; conservar el dato raw en cálculo y permitir un suelo de color únicamente visual.

**Figura D — Curvas de métricas vs frecuencia.** `DI(f)`, `Q(f)`, `BW_H(f)`, `BW_V(f)`, `F/B(f)` y porcentaje energético posterior. Marcar frecuencias inválidas/ausentes y eventos off-axis.

**Figura E — Globo 3D de directividad.** Representar magnitud normalizada por banda, indicando con claridad el mapeo angular y la escala de radio. Es complementario; no sustituye las curvas cuantitativas.

**Figura F — Comparación del corpus.** Perfiles multibanda, ranking por tramos, distribución de anchos de haz y dispersión entre fabricantes/modelos, sin mezclar rangos de frecuencia distintos ni comparar slots no declarados.

## 5. Arquitectura propuesta

**Módulo 1: `cf2_parser_v5.py` (sin cambios).** Recupera información CF2 validada y exporta matrices por frecuencia.

**Módulo 2: `cf2_directivity_metrics.py`.** Resuelve cortes angularmente orientados, integración esférica, DI/Q, beamwidth, frontal/posterior, asimetría, lóbulos y controles de calidad. Su salida por banda es un diccionario tipado o estructura tabular.

**Módulo 3: `cf2_directivity_plots.py`.** Produce mosaicos polares, mapas de calor, métricas vs frecuencia, comparación de modelos y opcionalmente globos 3D.

**Módulo 4: `cf2_corpus_characterization.py`.** Ejecuta el procesamiento por lote sobre el corpus, genera indicadores reproducibles y exporta tablas agregadas sin sobreescribir el parser.

**Salidas esperadas:** `per_band_metrics.csv`, `speaker_summary.json`, `corpus_metrics.csv`, `figures/<modelo>/*.png`, `figures/<modelo>/*.svg`, `quality_report.csv` y un reporte metodológico `.md`. Los resultados por banda deben incluir frecuencia original, tipo de agregación (`third_octave` o `octave`), metadatos, flags de calidad y versión de la metodología.

## 6. Pruebas de calidad y validación

- **Esfera uniforme sintética:** `D_rel=0` en todos los ángulos ⇒ `Q=1`, `DI=0 dB`, sin haz estrecho.
- **Patrón cardioide ideal sintético:** contrastar el `Q` numérico con `Q=3`, `DI≈4.77 dB` para una ley de amplitud cardioide ideal `A=(1+cosθ)/2`; no confundirla con cardioides aproximados de marcas comerciales.
- **Simetría y polos:** cada polo (`arc=0°`, `arc=180°`) debe conservar constancia azimutal (dentro de tolerancias que el corpus justifique).
- **Rotaciones validadas:** contrastar H/V y el sentido izquierda/derecha con pares asimétricos `DLA6_L` y `DLA6_R` más capturas del Viewer.
- **Cross-check EA:** contrastar anchos medidos y `Q` integrado con `h_left`, `h_right`, `v_up`, `v_down`, `axial_q`; documentar diferencias y casos no equivalentes.
- **Bandas y floors:** excluir del análisis operativo slots fuera del rango declarado; preservar valores `−200 dB` con marca explícita y no usar `−50 dB` visual en integrales.
- **Comparación:** no mezclar métricas de tercio de octava con las de octava sin identificarlas.

## 7. Orden de trabajo sugerido

**Fase 1 — Un modelo de referencia.** Analizar el CF2 binario del Bosch `LA1_UW24`, idealmente de las mismas capturas o fuente que la imagen EMF: normalización, 7 bandas, cortes H/V, DI, beamwidth y F/B.

**Fase 2 — Visualización automatizada.** Crear un panel por altavoz que combine polares, heatmaps de frecuencia-ángulo y curvas métricas. Comparar orientaciones con CLF Viewer.

**Fase 3 — Corpus completo.** Procesar los ~400+ archivos ya recopilados, identificar casos raros y perfilar cada modelo con los mismos criterios.

**Fase 4 — Taxonomía validada.** Calibrar clases `poco / moderado / directivo / muy directivo`; distinguir perfiles estables/variables y cobertura H/V asimétrica; producir ranking por bandas elegibles.

**Fase 5 — Simulación acústica.** Cruzar perfiles CF2 con simulaciones de Pyroomacoustics / I-Simpa / ODEON sobre una sala controlada, distinguiendo características inherentes de la fuente frente a resultados dependientes de habitación, geometría, posición y orientación.

## 8. Alcance y limitaciones

Actualmente se adjuntaron **el parser Python y una imagen EMF**, no el archivo `.CF2` binario. Por ello esta propuesta **no informa DI, Q, anchos de haz ni clasificación medidos para el LA1_UW24**. Solo describe cómo obtenerlos de forma reproducible cuando se procese el archivo correspondiente.

Las clases de directividad por banda caracterizan únicamente la **distribución relativa angular**. No identifican por sí solas qué altavoz ofrece mejor inteligibilidad, uniformidad SPL ni nivel máximo para una sala: esos resultados requieren simular y definir una función objetivo.

**Producto científico propuesto:** base de datos con firmas de directividad dependientes de frecuencia para altavoces reales y validación de su capacidad para anticipar resultados de posicionamiento/orientación en recintos cerrados.
