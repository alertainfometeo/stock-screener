# Quality & Valuation Stock Screener

Puntúa empresas de 0 a 100 según tres pilares:

- **Calidad (35):** los 15 puntos de Phil Fisher
- **Salud financiera (30):** ROIC > 15 % y crecimiento del EPS > 10 %
- **Valuación (35):** PEG forward, P/E, P/FCF y FCF yield

## La web

Cada sábado por la mañana, con el cierre del viernes, se recalculan todas las empresas cotizadas en EE. UU. según la [lista de la SEC](https://www.sec.gov/files/company_tickers_exchange.json) (NYSE, Nasdaq, CBOE y TXSE, sin OTC, warrants ni preferentes, y con más de 1.000 M$ de capitalización), más una selección global. Se publica en:

- Ranking: `https://alertainfometeo.github.io/stock-screener/`
- Ficha de una empresa: `https://alertainfometeo.github.io/stock-screener/NVDA`

Para añadir empresas de fuera de EE. UU., mete su ticker de Yahoo Finance en `DEFAULT_TICKERS` dentro de `screener.py`.

## Configuración inicial (una vez)

- **Settings → Pages → Source:** GitHub Actions.
- **Settings → Secrets and variables → Actions → Variables → New repository variable:** `SEC_USER_AGENT` con tu nombre y email (por ejemplo `Juan Pérez juan@email.com`). La SEC exige identificarse para descargar su lista.

## Actualizarla a mano

**Actions → Quality & Valuation Stock Screener → Run workflow.** Puedes elegir:

- `sec`: todas las cotizadas en EE. UU. (por defecto; entre 20 y 40 minutos repartido en 10 máquinas)
- `sp500`: S&P 500 y la selección global
- `curated`: solo la selección global (prueba rápida)

También puedes cambiar la capitalización mínima.

## Opcional: evaluación con la API de Claude

Añade `ANTHROPIC_API_KEY` en **Settings → Secrets and variables → Actions**. Sin clave, la calidad se mide con 15 indicadores fundamentales. Ojo con el coste: con la lista de la SEC son unas 3.000 llamadas a la API en cada ejecución.

> Herramienta educativa, no es asesoramiento de inversión.
