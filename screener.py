name: Quality & Valuation Stock Screener

on:
  workflow_dispatch:
    inputs:
      universe:
        description: "Qué empresas analizar"
        required: false
        default: "sec"
        type: choice
        options:
          - "sec"       # todas las cotizadas en EE. UU. (lista de la SEC)
          - "sp500"     # S&P 500 + selección global
          - "curated"   # solo la selección global (prueba rápida)
      min_market_cap:
        description: "Capitalización mínima en USD"
        required: false
        default: "1000000000"
  schedule:
    # Una vez por semana: sábados a las 07:17 UTC (con el cierre del viernes)
    - cron: "17 7 * * 6"

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: screener
  cancel-in-progress: false

env:
  UNIVERSE: ${{ github.event.inputs.universe || 'sec' }}
  MIN_MARKET_CAP: ${{ github.event.inputs.min_market_cap || '1000000000' }}
  # La SEC exige identificarse. Crea la variable SEC_USER_AGENT en
  # Settings > Secrets and variables > Actions > Variables, p. ej. "Nombre tu@email.com"
  SEC_USER_AGENT: ${{ vars.SEC_USER_AGENT }}

jobs:
  universe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"
      - run: pip install -r requirements.txt
      - name: Construir la lista de tickers
        run: python screener.py universe
      - uses: actions/upload-artifact@v4
        with:
          name: universe
          path: universe.json

  scan:
    needs: universe
    runs-on: ubuntu-latest
    timeout-minutes: 120
    strategy:
      fail-fast: false
      matrix:
        shard: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"
      - run: pip install -r requirements.txt
      - uses: actions/download-artifact@v4
        with:
          name: universe
      - name: Calcular parte ${{ matrix.shard }}
        env:
          # Opcional: ANTHROPIC_API_KEY en Settings > Secrets > Actions
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python screener.py scan --shard ${{ matrix.shard }} --shards 10
      - uses: actions/upload-artifact@v4
        with:
          name: shard-${{ matrix.shard }}
          path: shards/shard_${{ matrix.shard }}.json

  publish:
    needs: scan
    if: ${{ !cancelled() }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"
      - run: pip install -r requirements.txt
      - uses: actions/download-artifact@v4
        with:
          pattern: shard-*
          path: shards
      - name: Unir resultados
        run: python screener.py merge
      - name: Preparar la web
        run: |
          cp web/index.html site/index.html
          cp web/index.html site/404.html
          touch site/.nojekyll
      - uses: actions/upload-artifact@v4
        with:
          name: screener-ranking-${{ github.run_number }}
          path: screener_results.csv
      - name: Subir la web
        uses: actions/upload-pages-artifact@v3
        with:
          path: site

  # Publicar en un job aparte (patrón recomendado por GitHub): así el
  # artifact "github-pages" ya está registrado cuando se despliega.
  deploy:
    needs: publish
    if: ${{ !cancelled() && needs.publish.result == 'success' }}
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deploy.outputs.page_url }}
    steps:
      - name: Publicar en GitHub Pages
        id: deploy
        uses: actions/deploy-pages@v4
