# Quality & Valuation Stock Screener

Puntúa empresas de 0 a 100 según tres pilares:

- **Calidad (35):** los 15 puntos de Phil Fisher
- **Salud financiera (30):** ROIC > 15 % y crecimiento del EPS > 10 %
- **Valuación (35):** PEG forward, P/E, P/FCF y FCF yield

## Cómo usarlo (sin instalar nada)

1. Pulsa **Fork** (arriba a la derecha) para tener tu propia copia.
2. En tu copia, entra en la pestaña **Actions** y pulsa **I understand my workflows, go ahead and enable them**.
3. Abre **Quality & Valuation Stock Screener** → **Run workflow**.
   - `false`: lista curada (~40 empresas, 1–2 min)
   - `true`: S&P 500 + globales (~5 min)
4. Al terminar, abre el run: el ranking sale en la página y puedes descargar `screener_results.csv` en *Artifacts*.

## Opcional: evaluación con la API de Claude

Si quieres que Claude puntúe los 15 puntos de Fisher en vez de usar indicadores cuantitativos:

1. Consigue una clave en https://console.anthropic.com
2. En tu copia del repositorio: **Settings → Secrets and variables → Actions → New repository secret**
3. Nombre: `ANTHROPIC_API_KEY`, valor: tu clave.

Cada usuario usa **su propia clave**; nunca se comparte. Ojo: con el S&P 500 completo son ~500 llamadas a la API por ejecución.

Sin clave, el screener funciona igual con los 15 indicadores fundamentales.

> Herramienta educativa, no es asesoramiento de inversión.
