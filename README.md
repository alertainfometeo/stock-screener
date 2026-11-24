# Quality & Valuation Stock Screener

Puntúa empresas cotizadas de **0 a 100** combinando tres pilares:

| Pilar | Peso | Qué mide |
|---|---:|---|
| Calidad (15 puntos de Phil Fisher) | 30 | Crecimiento, márgenes, rentabilidad, solidez del balance |
| Salud financiera | 30 | ROIC y crecimiento del beneficio por acción |
| Valuación | 30 | PEG forward, P/E, P/FCF y FCF yield |
| Previsiones de analistas | 10 | Crecimiento del EPS previsto y revisiones de las previsiones |

Una empresa que cumple todos los criterios saca 100 y una que no cumple ninguno saca 0. Entre medias, cada criterio suma su parte, con puntuación parcial cuando una empresa se queda cerca del umbral.

**Web:**

- Ranking: https://alertainfometeo.github.io/stock-screener/
- Ficha de una empresa: https://alertainfometeo.github.io/stock-screener/NVDA (cambia `NVDA` por el ticker)

> Herramienta educativa. No es una recomendación de inversión.

---

## 1. De dónde salen los datos

### 1.1 Lista de empresas (universo)

| Fuente | Para qué |
|---|---|
| [SEC – company_tickers_exchange.json](https://www.sec.gov/files/company_tickers_exchange.json) | Lista oficial de todas las empresas que reportan a la SEC, con su ticker y su bolsa. Es el universo por defecto. |
| [Wikipedia – List of S&P 500 companies](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies) | Alternativa si la SEC no responde, o si se elige `sp500` al lanzar el workflow. |
| `DEFAULT_TICKERS` en `screener.py` | Selección manual que se añade siempre, sobre todo empresas europeas e internacionales (Amadeus, Inditex, Hermès, L'Oréal, LVMH, Constellation Software…). |

La lista de la SEC trae unas 10.000 filas. Antes de calcular nada se filtra así:

1. **Solo bolsas principales:** NYSE, Nasdaq, CBOE y TXSE. Se descartan las OTC y las que no tienen bolsa.
2. **Fuera los instrumentos que no son acciones comunes:** warrants (`-W`, `-WS`, `-WT`), units (`-U`, `-UN`), derechos (`-R`, `-RT`), preferentes (`-P…`) y, en Nasdaq, los tickers de 5 letras acabados en W, U o R.
3. **Una sola clase por empresa:** si una compañía tiene varias clases de acciones (GOOGL y GOOG), se queda la primera que aparece, identificándolas por su CIK.

Al calcular, se aplican dos filtros más:

4. **Solo acciones:** se descartan ETFs y fondos (`quoteType` distinto de `EQUITY` en Yahoo).
5. **Capitalización mínima de 1.000 M$:** la capitalización se convierte a dólares antes de compararla. El umbral se puede cambiar al lanzar el workflow.

### 1.2 Datos financieros

Todos los ratios salen de **Yahoo Finance** a través de la librería [`yfinance`](https://github.com/ranaroussi/yfinance). Por cada empresa se descargan tres cosas:

| Llamada | Qué contiene | Se usa para |
|---|---|---|
| `Ticker.info` | Ratios resumidos: capitalización, P/E, PEG, márgenes, ROE, FCF, deuda/patrimonio, % de insiders… | Casi todo |
| `Ticker.financials` | Cuenta de resultados anual (último ejercicio) | ROIC (EBIT e impuestos) |
| `Ticker.balance_sheet` | Balance anual (último ejercicio) | ROIC (deuda, patrimonio y caja) |

Los **tipos de cambio** también salen de Yahoo, con los pares `XXXYYY=X` (por ejemplo `TWDUSD=X`), usando el último cierre de los 5 últimos días.

Yahoo Finance no es una fuente oficial. Sus datos pueden tener errores, huecos o retrasos, y algunos campos usan definiciones propias (ver el apartado 5).

---

## 2. Cómo se calcula cada pilar

### 2.1 Puntuación parcial (`score_step`)

Casi todos los criterios se puntúan con la misma regla, que da **1** si se cumple, **0** si se falla claramente y una **fracción lineal** dentro de un margen del 25 % alrededor del umbral:

- **Cuando más es mejor** (ROIC, crecimiento, FCF yield), con un umbral U:
  - valor ≥ U → **1**
  - valor entre 0,75·U y U → **(valor − 0,75·U) / (0,25·U)**
  - valor ≤ 0,75·U → **0**
- **Cuando menos es mejor** (P/E, P/FCF), con un umbral U:
  - valor ≤ 0 o sin dato → **0** (un P/E negativo significa pérdidas)
  - valor ≤ U → **1**
  - valor entre U y 1,25·U → **(1,25·U − valor) / (0,25·U)**
  - valor ≥ 1,25·U → **0**

Por ejemplo, con un ROIC del 13,5 % frente a un umbral del 15 %, el margen empieza en 11,25 % y la nota es (13,5 − 11,25) / 3,75 = **0,6**.

### 2.2 Calidad: 15 puntos de Phil Fisher (30 puntos)

Fisher evaluaba sobre todo aspectos cualitativos: el equipo directivo, la I+D, la red comercial o las relaciones laborales. Aquí se aproximan con **15 indicadores cuantitativos** de Yahoo. Cada uno que se cumple suma 1 punto de 15.

| # | Criterio | Campo de Yahoo | Umbral |
|---:|---|---|---|
| 1 | Crecimiento de ventas | `revenueGrowth` | > 8 % |
| 2 | Pricing power | `grossMargins` | > 45 % |
| 3 | Margen operativo | `operatingMargins` | > 18 % |
| 4 | Margen neto | `profitMargins` | > 12 % |
| 5 | Rentabilidad sobre el patrimonio | `returnOnEquity` | > 18 % |
| 6 | Rentabilidad sobre activos | `returnOnAssets` | > 8 % |
| 7 | Crecimiento de beneficios | Crecimiento EPS medio de 3 años (ver 2.3) | > 10 % |
| 8 | Generación de caja | `freeCashflow` | > 0 |
| 9 | Calidad contable | `operatingCashflow` vs `netIncomeToCommon` | caja operativa > 90 % del beneficio neto |
| 10 | Solvencia | `debtToEquity` | < 100 % |
| 11 | Liquidez a corto plazo | `currentRatio` | > 1,2 |
| 12 | Alineación de la directiva | `heldPercentInsiders` | > 1 % |
| 13 | Respaldo institucional | `heldPercentInstitutions` | > 40 % |
| 14 | Eficiencia operativa | `ebitdaMargins` | > 22 % |
| 15 | Comportamiento en bolsa | `52WeekChange` | > −15 % en 12 meses |

**Puntos de calidad = (criterios cumplidos / 15) × 30.** Aquí no hay puntuación parcial: cada criterio se cumple o no. Si falta un dato, cuenta como no cumplido.

**Modo opcional con Claude:** si existe el secret `ANTHROPIC_API_KEY`, en lugar de los 15 indicadores se pide a Claude que puntúe la empresa de 0 a 15 según los criterios de Fisher, y el número que devuelve sustituye al recuento. Claude responde de memoria, sin recibir los datos de la empresa, así que su nota no es reproducible ni auditable. Si la llamada falla, se usan los indicadores. Con la lista de la SEC son unas 3.000 llamadas por ejecución.

### 2.3 Salud financiera (30 puntos)

| Criterio | Umbral | Cálculo |
|---|---|---|
| **ROIC** | > 15 % | Ver abajo |
| **Crecimiento del EPS** | > 10 % | Media de los crecimientos anuales del EPS diluido de los últimos 3 años (4 ejercicios de `Ticker.financials`). Los años con EPS de partida cero o negativo se saltan. Sin histórico anual, se usa `earningsGrowth` de Yahoo (trimestral) |

**ROIC = NOPAT / Capital invertido**, con datos del último ejercicio anual:

- **EBIT:** fila `EBIT` de la cuenta de resultados, o `Operating Income` si no existe.
- **Tasa impositiva:** `Tax Provision / Pretax Income`, limitada entre 0 % y 40 %. Si el beneficio antes de impuestos es 0, se usa un 21 %.
- **NOPAT** = EBIT × (1 − tasa impositiva).
- **Capital invertido** = Deuda total + Patrimonio neto − Caja y equivalentes.
- Si el capital invertido es 0 o negativo, el ROIC queda en **0**.
- Si Yahoo no tiene estados financieros, se usa el **ROE** (`returnOnEquity`) en su lugar.

Cada criterio se puntúa con `score_step`. **Puntos financieros = media de los dos × 30.**

### 2.4 Valuación (30 puntos)

| Criterio | Umbral | Cálculo | Puntuación |
|---|---|---|---|
| **PEG forward** | < 1,5 (tolerado < 2) | `pegRatio` de Yahoo; si falta o es ≤ 0, P/E forward ÷ (crecimiento del EPS × 100) | ≤ 1,5 → 1 · ≤ 2 → 0,65 · resto → 0 |
| **P/E** | < 25 | `trailingPE`; si falta, `forwardPE` | `score_step` |
| **P/FCF** | < 25 | Capitalización ÷ Free cash flow | `score_step` |
| **FCF yield** | > 4 % | Free cash flow ÷ Capitalización | `score_step` |

- El free cash flow es el campo `freeCashflow` de Yahoo, **convertido a la divisa de cotización** cuando la empresa reporta en otra. Así, en TSM el FCF en dólares taiwaneses se pasa a dólares antes de dividirlo por una capitalización en dólares. Las acciones de Londres, que cotizan en peniques, se ajustan aparte.
- Si el FCF es negativo o no hay dato, P/FCF y FCF yield puntúan **0**.

**Puntos de valuación = media de los cuatro × 30.**

### 2.5 Previsiones de analistas (10 puntos)

Datos de `Ticker.earnings_estimate` y `Ticker.eps_trend` de Yahoo. Dos criterios, cada uno de 0 a 1:

| Criterio | Dato | Nota |
|---|---|---|
| **Crecimiento EPS previsto** | Media del crecimiento esperado del EPS para el ejercicio actual y el siguiente | −5 % o menos → 0 · 5 % → 0,5 · 15 % o más → 1 (lineal entre medias) |
| **Revisión de previsiones** | Cambio del EPS previsto para el próximo ejercicio hoy frente a hace 90 días (la "pendiente" de las previsiones) | −10 % o menos → 0 · 0 % → 0,5 · +10 % o más → 1 (lineal) |

- Premia que se espere que el crecimiento continúe y que los analistas estén subiendo sus estimaciones, y penaliza lo contrario.
- **Neutro sin datos:** si hay menos de 3 analistas, si Yahoo no tiene previsiones (habitual en empresas pequeñas y no estadounidenses) o si falta uno de los dos datos, ese criterio vale 0,5. Así no se castiga a una empresa solo porque no la sigue nadie.
- Un crecimiento previsto superior al 100 % también cuenta como neutro: suele venir de una base casi nula.
- Si no hay previsión del próximo ejercicio, la revisión se mide con la del ejercicio actual.

**Puntos de previsiones = media de los dos × 10.**

### 2.6 Score total

**Score = Calidad + Salud financiera + Valuación + Previsiones**, redondeado a un decimal. Máximo 100.

---

## 3. Ejemplo

| Pilar | Dato | Nota | Puntos |
|---|---|---|---:|
| **Calidad** | 13 de 15 criterios | 13/15 × 30 | **26** |
| **Salud financiera** | ROIC 20 % (cumple) · EPS medio 3 años 8,5 % (entre 7,5 % y 10 %: (8,5 − 7,5)/2,5 = 0,4) | (1 + 0,4)/2 × 30 | **21** |
| **Valuación** | PEG 1,2 (cumple) · P/E 28 ((31,25 − 28)/6,25 = 0,52) · P/FCF 22 (cumple) · FCF yield 4,5 % (cumple) | (1 + 0,52 + 1 + 1)/4 × 30 | **26,4** |
| **Previsiones** | Crecimiento previsto 10 % (0,75) · revisión a 90 días −4 % (0,3) | (0,75 + 0,3)/2 × 10 | **5,3** |
| **Total** | | | **78,7** |

---

## 3b. Tachar empresas en el ranking

En la portada hay una casilla **"Tachar si el P/E es más de X veces el crecimiento del EPS"**. Al escribir un número:

- Se tachan las empresas cuyo **P/E > X × crecimiento del EPS** (media de 3 años, en %). Por ejemplo, con X = 1,5 y un crecimiento del 12 %, se tachan las de P/E por encima de 18.
- También se tachan las que tienen P/E positivo y crecimiento del EPS nulo o negativo.
- Las que no tienen P/E o crecimiento no se tachan.
- Con X = 1 equivale a la regla clásica de Peter Lynch: PEG (P/E ÷ crecimiento) mayor que 1.
- La casilla **Ocultar tachadas** las quita de la lista. El valor de X se recuerda en tu navegador.

Es solo un filtro visual: no cambia el score.

### ROIC frente a WACC

La casilla **"Tachar si el ROIC no supera el WACC"** tacha las empresas que no ganan más de lo que les cuesta su capital, es decir, que no crean valor. Se puede combinar con la del P/E: se tacha si se cumple cualquiera de las dos.

El WACC (coste medio ponderado del capital) se calcula así, con datos del último ejercicio:

- **WACC = E/(E+D) × Ke + D/(E+D) × Kd × (1 − t)**
- **E:** capitalización. **D:** deuda total del balance (convertida a la divisa de cotización).
- **Ke (coste del capital propio, CAPM) = libre de riesgo + beta × 5 %.** La tasa libre de riesgo es la rentabilidad del bono de EE. UU. a 10 años (`^TNX` en Yahoo; 4,3 % si no se puede leer). La beta sale de Yahoo, limitada entre 0,3 y 3; si no hay, se usa 1.
- **Kd (coste de la deuda) = gastos financieros / deuda total**, limitado entre 1 % y 15 %. Si no hay gastos financieros, libre de riesgo + 1,5 %.
- **t:** tasa impositiva efectiva, igual que en el ROIC (21 % si no se puede calcular).

La ficha muestra el WACC debajo del ROIC con el resultado de la comparación. El WACC no puntúa.

Limitaciones: se usa el bono de EE. UU. también para empresas de otros países, la beta de Yahoo es histórica y la prima de riesgo del 5 % es una convención. Es una estimación orientativa. Los parámetros están en `CONFIG["wacc"]`.

---

## 4. Cómo se ejecuta

### 4.1 Cuándo

- **Automático:** cada **sábado a las 07:17 UTC** (08:17 en Madrid en invierno), con los datos del cierre del viernes.
- **Manual:** en **Actions → Quality & Valuation Stock Screener → Run workflow**, eligiendo:
  - `sec`: todas las cotizadas en EE. UU. (por defecto)
  - `sp500`: S&P 500 y la selección global
  - `curated`: solo la selección global (prueba rápida)
  - la capitalización mínima en USD

### 4.2 Fases del workflow

1. **`universe`:** descarga la lista (SEC, S&P 500 o selección), la filtra y guarda `universe.json`.
2. **`scan`:** 10 máquinas en paralelo. Cada una calcula una décima parte de la lista con 3 consultas simultáneas a Yahoo y reintenta hasta 3 veces si Yahoo limita. Si una parte falla, las demás siguen.
3. **`publish`:** une los resultados, los ordena por score y publica la web en GitHub Pages. También deja el CSV completo en *Artifacts* del run y una tabla con las empresas de 60 puntos o más en el resumen.

### 4.3 Archivos que genera

| Archivo | Contenido |
|---|---|
| `index.json` | Ranking ligero: ticker, nombre, score, puntos por pilar y sector |
| `c/TICKER.json` | Ficha completa de cada empresa: ratios, nota de cada criterio y los 15 checks de Fisher |
| `screener_results.csv` | Ranking completo para Excel (en *Artifacts*) |

### 4.4 Configuración inicial (una vez)

- **Settings → Pages → Source:** GitHub Actions.
- **Settings → Secrets and variables → Actions → Variables:** `SEC_USER_AGENT` con un nombre y un email (por ejemplo `Juan Pérez juan@email.com`). La SEC exige identificarse para descargar su lista.
- **Opcional:** el secret `ANTHROPIC_API_KEY` para el modo Claude.

### 4.5 Cambiar umbrales y pesos

Todo está en el bloque `CONFIG`, al principio de `screener.py`: los pesos de cada pilar, los umbrales de ROIC, EPS, PEG, P/E, P/FCF y FCF yield, y el score mínimo de la tabla resumen. Los umbrales de los 15 criterios de Fisher están en la función `evaluate_fisher_15_points`.

---

## 5. Limitaciones conocidas

- **Las previsiones de analistas suelen pecar de optimistas** y se mueven en manada; las revisiones tienen cierto valor como señal de momentum, pero no son un dato fiable sobre el futuro.

- **P/FCF y FCF yield miden lo mismo:** uno es el inverso del otro, así que en la práctica el FCF pesa el doble dentro de la valuación.
- **El FCF de Yahoo es el "levered free cash flow"** (después de intereses), no la caja operativa menos capex. En empresas con mucha inversión, como MSFT o META con la IA, puede salir muy bajo y hundir su valuación.
- **El crecimiento del EPS es de un solo trimestre frente al mismo trimestre del año anterior**, así que es volátil y puede dar cifras extremas.
- **Sesgo hacia las cíclicas en su mejor momento:** mineras, petroleras o navieras con beneficios altos y múltiplos bajos cumplen todos los gates justo en el mejor momento del ciclo.
- **Bancos y aseguradoras:** el ROIC, la deuda/patrimonio y la liquidez corriente no tienen sentido para ellos, así que sus scores no son comparables.
- **Patrimonio neto negativo** (empresas que recompran mucho, como Booking): el capital invertido puede salir negativo, con lo que el ROIC queda en 0. Si no hay estados financieros, el ROE puede salir inflado.
- **Los proxies de Fisher son cuantitativos** y se solapan con la salud financiera, porque márgenes, ROE y crecimiento están muy relacionados con el ROIC y el EPS.
- **Score sin validar:** no se ha hecho un backtest. El score sirve para ordenar y filtrar, no para predecir rentabilidades.
- **Datos semanales:** la web muestra los datos de la última ejecución, no cotizaciones en tiempo real.
