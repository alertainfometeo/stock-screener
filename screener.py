"""
Quality & Valuation Stock Screener
Score 0-100 = Calidad (15 puntos de Phil Fisher) + Salud financiera + Valuación.

Uso:
  python screener.py universe                 -> universe.json (lista de tickers)
  python screener.py scan --shard 0 --shards 10  -> shards/shard_0.json
  python screener.py merge                    -> site/ (web) + screener_results.csv
  python screener.py                          -> todo seguido (uso local)

Universo (variable UNIVERSE): "sec" (todas las cotizadas en EE. UU. según la SEC,
por defecto), "sp500" o "curated" (solo DEFAULT_TICKERS).
"""
import os
import re
import sys
import glob
import json
import time
import argparse
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from typing import Dict, Any, List, Optional, Tuple

import requests
import pandas as pd
import yfinance as yf

# ==============================================================================
# CONFIGURACIÓN DE GATES Y PESOS (TOTAL = 100 PUNTOS)
# ==============================================================================
CONFIG = {
    "weights": {
        "quality_fisher": 30.0,   # 15 puntos de Phil Fisher
        "financial_gates": 30.0,  # ROIC + Crecimiento EPS (media 3 años)
        "valuation": 30.0,        # PEG Forward, P/E, P/FCF, FCF Yield
        "forecast": 10.0,         # Previsiones de analistas: crecimiento previsto + revisiones
    },
    "financial_thresholds": {
        "min_roic": 0.15,         # ROIC > 15%
        "min_eps_growth": 0.10,   # Crecimiento EPS medio 3 años > 10%
    },
    "valuation_thresholds": {
        "max_peg_strict": 1.5,    # PEG Forward ideal < 1.5
        "max_peg_relaxed": 2.0,   # PEG Forward tolerado < 2.0 (puntuación parcial)
        "max_pe": 25.0,           # P/E < 25
        "max_p_fcf": 25.0,        # Price / Free Cash Flow < 25
        "min_fcf_yield": 0.04,    # Free Cash Flow Yield > 4%
    },
    # Pilar de previsiones de analistas (cada criterio de 0 a 1, neutro = 0,5)
    "forecast": {
        "growth_zero": -0.05,       # Crecimiento previsto de -5% o menos -> 0
        "growth_full": 0.15,        # 15% o más -> 1   (5% -> 0,5)
        "revision_full": 0.10,      # Revisión +10% -> 1, 0% -> 0,5, -10% -> 0
        "min_analysts": 3,          # Con menos analistas: neutro
        "max_growth": 1.00,         # Previsión > 100%: base demasiado baja -> neutro
    },
    "min_score_to_display": 60.0,
    # Universo SEC
    "sec_url": "https://www.sec.gov/files/company_tickers_exchange.json",
    "sec_exchanges": {"NYSE", "Nasdaq", "CBOE", "TXSE"},  # fuera OTC
    # Empresas por debajo de esta capitalización (en su divisa) no se evalúan
    "min_market_cap": float(os.getenv("MIN_MARKET_CAP", "1000000000")),
    "workers_per_shard": int(os.getenv("WORKERS", "3")),
}

# Universo base global (puedes ampliarlo: cualquier ticker de Yahoo Finance)
DEFAULT_TICKERS = [
    # US Tech & Quality
    "ADBE", "BKNG", "MSFT", "GOOGL", "AAPL", "META", "NVDA", "V", "MA",
    "CRM", "INTU", "SNPS", "CDNS", "NOW", "ORCL", "TXN", "QCOM", "AMAT",
    # US Consumer, Healthcare & Industrials
    "COST", "UNH", "ISRG", "SYK", "TMO", "DHR", "SPGI", "MCO", "UNP", "CPRT",
    # Europa & Internacional
    "ASML", "NVO", "MC.PA", "RMS.PA", "OR.PA", "ITX.MC", "AMS.MC",
    "TSM", "MELI", "CSU.TO",
]

FISHER_LABELS = [
    "Crecimiento de ventas > 8%",
    "Margen bruto > 45% (pricing power)",
    "Margen operativo > 18%",
    "Margen neto > 12%",
    "ROE > 18%",
    "ROA > 8%",
    "Crecimiento EPS medio 3 años > 10%",
    "Free cash flow positivo",
    "Caja operativa ≥ 90% del beneficio",
    "Deuda/Patrimonio < 100%",
    "Liquidez corriente > 1,2",
    "Insiders > 1% del capital",
    "Institucionales > 40%",
    "Margen EBITDA > 22%",
    "Cotización 52 semanas > -15%",
]


def get_sp500_tickers() -> List[str]:
    """Obtiene tickers del S&P 500 desde Wikipedia y los combina con globales."""
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        headers = {"User-Agent": "Mozilla/5.0"}
        html = requests.get(url, headers=headers, timeout=15).text
        tables = pd.read_html(StringIO(html))
        sp500 = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
        return list(dict.fromkeys(DEFAULT_TICKERS + sp500))
    except Exception:
        return DEFAULT_TICKERS


# Sufijos de warrants, units, rights y preferentes en el formato de la SEC
_SEC_JUNK = re.compile(r"-(WT|WS|W|UN|U|RT|R|P.*)$")


def get_sec_tickers() -> List[str]:
    """
    Todas las cotizadas en EE. UU. según la SEC (company_tickers_exchange.json),
    filtrando OTC, warrants/units/rights/preferentes y clases duplicadas (mismo CIK).
    La SEC exige un User-Agent con nombre y email de contacto.
    """
    ua = os.getenv("SEC_USER_AGENT") or "stock-screener contact@example.com"
    resp = requests.get(CONFIG["sec_url"], headers={"User-Agent": ua}, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    fields = payload.get("fields") or ["cik", "name", "ticker", "exchange"]
    i_cik, i_tk, i_ex = fields.index("cik"), fields.index("ticker"), fields.index("exchange")

    seen_cik, tickers = set(), []
    for row in payload["data"]:
        cik, tk, ex = row[i_cik], row[i_tk], row[i_ex]
        if not tk or ex not in CONFIG["sec_exchanges"]:
            continue
        tk = tk.upper().replace(".", "-")
        if _SEC_JUNK.search(tk):
            continue
        # Nasdaq: 5 letras acabadas en W/U/R suelen ser warrants/units/rights
        if ex == "Nasdaq" and len(tk) == 5 and tk[-1] in "WUR":
            continue
        if cik in seen_cik:
            continue
        seen_cik.add(cik)
        tickers.append(tk)
    return tickers


def build_universe() -> List[str]:
    mode = os.getenv("UNIVERSE", "sec").lower()
    if mode == "curated":
        return list(DEFAULT_TICKERS)
    if mode == "sp500":
        return get_sp500_tickers()
    try:
        sec = get_sec_tickers()
        print(f"SEC: {len(sec)} tickers tras filtrar")
        return list(dict.fromkeys(DEFAULT_TICKERS + sec))
    except Exception as e:
        print(f"No se pudo leer la lista de la SEC ({e}); uso S&P 500.")
        return get_sp500_tickers()


# ------------------------------------------------------------------------------
# Divisas: en ADRs y valores extranjeros Yahoo da la capitalización en la divisa
# de cotización y el free cash flow en la divisa contable. Hay que convertir.
# ------------------------------------------------------------------------------
_FX_CACHE: Dict[Tuple[str, str], Optional[float]] = {}


def fx_rate(from_ccy: str, to_ccy: str) -> Optional[float]:
    if not from_ccy or not to_ccy or from_ccy == to_ccy:
        return 1.0
    key = (from_ccy, to_ccy)
    if key not in _FX_CACHE:
        rate = None
        try:
            hist = yf.Ticker(f"{from_ccy}{to_ccy}=X").history(period="5d")
            if not hist.empty:
                rate = float(hist["Close"].dropna().iloc[-1])
        except Exception:
            rate = None
        _FX_CACHE[key] = rate
    return _FX_CACHE[key]


def financials_to_price_ccy(info: Dict[str, Any]) -> Optional[float]:
    """Factor para pasar cifras contables a la divisa de cotización."""
    price_ccy = info.get("currency") or ""
    fin_ccy = info.get("financialCurrency") or price_ccy
    factor = 1.0
    # Bolsa de Londres cotiza en peniques (GBp)
    if price_ccy == "GBp":
        price_ccy, factor = "GBP", 100.0
    rate = fx_rate(fin_ccy.upper(), price_ccy.upper())
    return None if rate is None else rate * factor


def evaluate_fisher_15_points(info: Dict[str, Any], ticker: str) -> Tuple[float, Optional[List[bool]], str]:
    """
    Evalúa los 15 puntos de Phil Fisher. Devuelve (ratio 0-1, checks, método).
    Si existe ANTHROPIC_API_KEY usa Claude; si no, proxies cuantitativos.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        try:
            prompt = (
                f"Evalúa la empresa {ticker} ({info.get('longName', ticker)}) según los "
                "15 puntos de calidad de Phil Fisher (ventaja competitiva, I+D, márgenes, "
                "equipo directivo, crecimiento secular, control de costes). "
                "Responde ÚNICAMENTE con un JSON válido: {\"puntos_cumplidos\": <numero 0 a 15>}"
            )
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"),
                    "max_tokens": 100,
                    "temperature": 0,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=20,
            )
            if resp.status_code == 200:
                text = resp.json()["content"][0]["text"]
                data = json.loads(text[text.find("{"):text.rfind("}") + 1])
                pts = max(0.0, min(15.0, float(data["puntos_cumplidos"])))
                return pts / 15.0, None, "claude"
        except Exception:
            pass  # Fallback automático a proxies cuantitativos

    g = lambda k, d=0: (info.get(k) if info.get(k) is not None else d)  # noqa: E731
    checks = [
        g("revenueGrowth") > 0.08,
        g("grossMargins") > 0.45,
        g("operatingMargins") > 0.18,
        g("profitMargins") > 0.12,
        g("returnOnEquity") > 0.18,
        g("returnOnAssets") > 0.08,
        g("epsGrowthAvg3") > 0.10,
        g("freeCashflow") > 0,
        g("operatingCashflow") > g("netIncomeToCommon") * 0.9,
        g("debtToEquity", 999) < 100,
        g("currentRatio") > 1.2,
        g("heldPercentInsiders") > 0.01,
        g("heldPercentInstitutions") > 0.40,
        g("ebitdaMargins") > 0.22,
        g("52WeekChange") > -0.15,
    ]
    checks = [bool(c) for c in checks]
    return sum(checks) / 15.0, checks, "proxies"


def score_step(value: Optional[float], target: float, higher_is_better: bool = True, soft_margin: float = 0.25) -> float:
    """1.0 si cumple el gate, fracción si está cerca (margen 25%), 0.0 si falla."""
    if value is None:
        return 0.0
    if higher_is_better:
        if value >= target:
            return 1.0
        floor = target * (1.0 - soft_margin)
        if value > floor and target != floor:
            return (value - floor) / (target - floor)
        return 0.0
    if value <= 0:
        return 0.0
    if value <= target:
        return 1.0
    ceiling = target * (1.0 + soft_margin)
    if value < ceiling and ceiling != target:
        return (ceiling - value) / (ceiling - target)
    return 0.0


def eps_growth_avg3(stock: yf.Ticker) -> Tuple[Optional[float], int]:
    """
    Media de los crecimientos anuales del EPS diluido de los últimos 3 años
    (4 ejercicios anuales -> 3 variaciones). Los años en que el EPS de partida
    es cero o negativo no se pueden calcular y se saltan.
    Devuelve (media, nº de años usados).
    """
    try:
        fin = stock.financials
        if fin is None or fin.empty:
            return None, 0
        row = None
        for name in ("Diluted EPS", "Basic EPS"):
            if name in fin.index:
                row = fin.loc[name]
                break
        if row is None:
            return None, 0
        eps = {}
        for col, v in row.items():
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            year = getattr(col, "year", None)
            if year is not None and pd.notna(v) and year not in eps:
                eps[int(year)] = v
        years = sorted(eps)[-4:]
        growths = []
        for prev, cur in zip(years, years[1:]):
            if eps[prev] > 0:
                growths.append(eps[cur] / eps[prev] - 1.0)
        growths = growths[-3:]
        if not growths:
            return None, 0
        return sum(growths) / len(growths), len(growths)
    except Exception:
        return None, 0


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def forecast_scores(stock: yf.Ticker) -> Dict[str, Any]:
    """
    Pilar de previsiones. Dos criterios de 0 a 1 (0,5 = neutro si no hay datos):
      1. Crecimiento del EPS previsto: media del crecimiento esperado para el
         ejercicio actual (0y) y el siguiente (+1y).
      2. Pendiente de las previsiones: cuánto ha cambiado en 90 días la
         previsión de EPS del próximo ejercicio (+1y, o del actual si no hay).
    """
    fc = CONFIG["forecast"]
    out = {"growth": None, "revision": None, "analysts": 0,
           "s_growth": 0.5, "s_revision": 0.5, "note": ""}
    try:
        est = stock.earnings_estimate
        trend = stock.eps_trend
    except Exception:
        out["note"] = "sin previsiones de analistas: neutro"
        return out
    if est is None or est.empty:
        out["note"] = "sin previsiones de analistas: neutro"
        return out

    def val(df, period, col):
        try:
            v = df.loc[period, col]
            return None if v is None or pd.isna(v) else float(v)
        except Exception:
            return None

    analysts = val(est, "+1y", "numberOfAnalysts") or val(est, "0y", "numberOfAnalysts")
    out["analysts"] = int(analysts) if analysts else 0
    if not analysts or analysts < fc["min_analysts"]:
        out["note"] = f"solo {out['analysts']} analista(s): neutro"
        return out

    growths = [g for g in (val(est, "0y", "growth"), val(est, "+1y", "growth")) if g is not None]
    if growths:
        g = sum(growths) / len(growths)
        out["growth"] = g
        if g <= fc["max_growth"]:
            out["s_growth"] = _clip((g - fc["growth_zero"]) / (fc["growth_full"] - fc["growth_zero"]), 0.0, 1.0)
        else:
            out["note"] = "crecimiento previsto > 100%: neutro"

    if trend is not None and not trend.empty:
        for period in ("+1y", "0y"):
            cur, old = val(trend, period, "current"), val(trend, period, "90daysAgo")
            if cur is not None and old is not None and old > 0:
                rev = cur / old - 1.0
                out["revision"] = rev
                out["s_revision"] = _clip((rev + fc["revision_full"]) / (2 * fc["revision_full"]), 0.0, 1.0)
                break

    if not out["note"]:
        out["note"] = f"{out['analysts']} analistas"
    return out


def calculate_roic(stock: yf.Ticker, info: Dict[str, Any]) -> float:
    """ROIC = NOPAT / Capital invertido."""
    try:
        fin = stock.financials
        bs = stock.balance_sheet
        if fin.empty or bs.empty:
            return info.get("returnOnEquity") or 0.0

        ebit = fin.loc["EBIT"].iloc[0] if "EBIT" in fin.index else fin.loc["Operating Income"].iloc[0]
        tax_prov = fin.loc["Tax Provision"].iloc[0] if "Tax Provision" in fin.index else 0
        pretax = fin.loc["Pretax Income"].iloc[0] if "Pretax Income" in fin.index else ebit
        tax_rate = max(0.0, min(0.40, (tax_prov / pretax) if pretax else 0.21))
        nopat = ebit * (1 - tax_rate)

        total_debt = bs.loc["Total Debt"].iloc[0] if "Total Debt" in bs.index else (info.get("totalDebt") or 0)
        equity = bs.loc["Stockholders Equity"].iloc[0] if "Stockholders Equity" in bs.index else 0
        cash = bs.loc["Cash And Cash Equivalents"].iloc[0] if "Cash And Cash Equivalents" in bs.index else (info.get("totalCash") or 0)

        invested_capital = (total_debt + equity) - cash
        if invested_capital > 0:
            return float(nopat / invested_capital)
    except Exception:
        pass
    return 0.0


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def analyze_ticker(ticker: str) -> Optional[Dict[str, Any]]:
    try:
        stock = yf.Ticker(ticker)
        info = None
        for attempt in range(3):  # reintentos si Yahoo limita
            try:
                info = stock.info
                break
            except Exception:
                time.sleep(5 * (attempt + 1))
        if not info or not info.get("marketCap"):
            return None
        if info.get("quoteType") not in (None, "EQUITY"):
            return None  # fuera ETFs, fondos, etc.

        market_cap = float(info["marketCap"])
        ccy = info.get("currency") or "USD"
        if ccy == "GBp":
            conv_usd = (fx_rate("GBP", "USD") or 1.0) / 100.0
        else:
            conv_usd = fx_rate(ccy.upper(), "USD") or 1.0
        if market_cap * conv_usd < CONFIG["min_market_cap"]:
            return None

        # Free cash flow convertido a la divisa de cotización
        fcf_raw = info.get("freeCashflow")
        conv = financials_to_price_ccy(info)
        fcf = (fcf_raw * conv) if (fcf_raw is not None and conv is not None) else None

        pe = info.get("trailingPE") or info.get("forwardPE")
        forward_pe = info.get("forwardPE")
        # Crecimiento EPS: media de los crecimientos anuales de los últimos 3 años.
        # Si no hay estados anuales, se usa el dato trimestral de Yahoo.
        eps_growth, eps_years = eps_growth_avg3(stock)
        if eps_growth is not None:
            eps_note = f"media {eps_years} año" + ("s" if eps_years != 1 else "")
        else:
            eps_growth = info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth") or 0.0
            eps_note = "sin histórico anual: dato trimestral de Yahoo"
        info = {**info, "epsGrowthAvg3": eps_growth}

        peg_forward = info.get("pegRatio")
        if (not peg_forward or peg_forward <= 0) and forward_pe and eps_growth > 0:
            peg_forward = forward_pe / (eps_growth * 100.0)

        p_fcf = (market_cap / fcf) if (fcf and fcf > 0) else None
        fcf_yield = (fcf / market_cap) if (fcf and fcf > 0) else 0.0

        roic = calculate_roic(stock, info)

        # 1. CALIDAD
        fisher_ratio, fisher_checks, fisher_method = evaluate_fisher_15_points(info, ticker)
        score_quality = fisher_ratio * CONFIG["weights"]["quality_fisher"]

        # 2. FINANCIAL GATES
        fin_cfg = CONFIG["financial_thresholds"]
        s_roic = score_step(roic, fin_cfg["min_roic"])
        s_eps = score_step(eps_growth, fin_cfg["min_eps_growth"])
        score_financial = ((s_roic + s_eps) / 2.0) * CONFIG["weights"]["financial_gates"]

        # 3. VALUACIÓN
        val_cfg = CONFIG["valuation_thresholds"]
        if peg_forward and peg_forward > 0:
            if peg_forward <= val_cfg["max_peg_strict"]:
                s_peg = 1.0
            elif peg_forward <= val_cfg["max_peg_relaxed"]:
                s_peg = 0.65
            else:
                s_peg = 0.0
        else:
            s_peg = 0.0
        s_pe = score_step(pe, val_cfg["max_pe"], higher_is_better=False)
        s_pfcf = score_step(p_fcf, val_cfg["max_p_fcf"], higher_is_better=False)
        s_fcf_yield = score_step(fcf_yield, val_cfg["min_fcf_yield"])
        score_valuation = ((s_peg + s_pe + s_pfcf + s_fcf_yield) / 4.0) * CONFIG["weights"]["valuation"]

        # 4. PILAR PREVISIONES (crecimiento previsto + revisiones)
        fca = forecast_scores(stock)
        score_forecast = ((fca["s_growth"] + fca["s_revision"]) / 2.0) * CONFIG["weights"]["forecast"]

        total_score = round(score_quality + score_financial + score_valuation + score_forecast, 1)

        return {
            "ticker": ticker,
            "name": (info.get("shortName") or info.get("longName") or ticker),
            "sector": info.get("sector"),
            "country": info.get("country"),
            "currency": info.get("currency"),
            "price": _r(info.get("currentPrice") or info.get("regularMarketPrice")),
            "market_cap": market_cap,
            "score": total_score,
            "pillars": {
                "quality": round(score_quality, 1),
                "financial": round(score_financial, 1),
                "valuation": round(score_valuation, 1),
                "forecast": round(score_forecast, 1),
            },
            "forecast": fca,
            "fisher_method": fisher_method,
            "fisher_points": round(fisher_ratio * 15, 1),
            "fisher_checks": fisher_checks,
            "metrics": [
                {"group": "financial", "label": "ROIC", "value": _r(roic * 100, 1), "unit": "%", "rule": "> 15%", "score": round(s_roic, 2)},
                {"group": "financial", "label": "Crecimiento EPS (media 3 años)", "value": _r(eps_growth * 100, 1), "unit": "%", "rule": "> 10%", "note": eps_note, "score": round(s_eps, 2)},
                {"group": "valuation", "label": "PEG forward", "value": _r(peg_forward), "unit": "", "rule": "< 1,5 (parcial < 2)", "score": round(s_peg, 2)},
                {"group": "valuation", "label": "P/E", "value": _r(pe, 1), "unit": "", "rule": "< 25", "score": round(s_pe, 2)},
                {"group": "valuation", "label": "P/FCF", "value": _r(p_fcf, 1), "unit": "", "rule": "< 25", "score": round(s_pfcf, 2)},
                {"group": "valuation", "label": "FCF yield", "value": _r(fcf_yield * 100, 2), "unit": "%", "rule": "> 4%", "score": round(s_fcf_yield, 2)},
                {"group": "forecast", "label": "Crecimiento EPS previsto", "value": _r(fca["growth"] * 100, 1) if fca["growth"] is not None else None, "unit": "%", "rule": "≥ 15% (parcial > −5%)", "note": "media ejercicio actual y siguiente · " + fca["note"], "score": round(fca["s_growth"], 2), "points": round(fca["s_growth"] * 5, 1)},
                {"group": "forecast", "label": "Revisión de previsiones (90 días)", "value": _r(fca["revision"] * 100, 1) if fca["revision"] is not None else None, "unit": "%", "rule": "≥ +10% (parcial > −10%)", "note": "EPS previsto del próximo ejercicio, hoy vs hace 90 días", "score": round(fca["s_revision"], 2), "points": round(fca["s_revision"] * 5, 1)},
            ],
        }
    except Exception:
        return None


# ==============================================================================
# SALIDAS
# ==============================================================================
def write_site(results: List[Dict[str, Any]], out_dir: str = "site") -> None:
    """index.json ligero para el ranking + un JSON por empresa en c/."""
    os.makedirs(os.path.join(out_dir, "c"), exist_ok=True)
    index = {
        "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "weights": CONFIG["weights"],
        "fisher_labels": FISHER_LABELS,
        "companies": [
            [r["ticker"], r["name"], r["score"], r["pillars"]["quality"],
             r["pillars"]["financial"], r["pillars"]["valuation"], r.get("sector"),
             r["pillars"].get("forecast")]
            for r in results
        ],
    }
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    for r in results:
        with open(os.path.join(out_dir, "c", f"{r['ticker']}.json"), "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, separators=(",", ":"))


def write_reports(results: List[Dict[str, Any]]) -> None:
    df = pd.DataFrame([{
        "Ticker": r["ticker"],
        "Empresa": r["name"][:28],
        "Sector": r.get("sector"),
        "Score": r["score"],
        "Calidad (30)": r["pillars"]["quality"],
        "Finanzas (30)": r["pillars"]["financial"],
        "Valuación (30)": r["pillars"]["valuation"],
        "Previsiones (10)": r["pillars"].get("forecast"),
        **{m["label"]: m["value"] for m in r["metrics"]},
    } for r in results])
    df.to_csv("screener_results.csv", index=False)

    top_df = df[df["Score"] >= CONFIG["min_score_to_display"]]
    print(f"\n=== {len(df)} compañías evaluadas, {len(top_df)} con score >= {CONFIG['min_score_to_display']:.0f} ===")
    print(top_df.head(50).to_markdown(index=False))

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("## Resultados del Screener (Calidad + Salud Financiera + Valuación)\n\n")
            f.write(f"Compañías evaluadas: **{len(df)}** | Con Score ≥ {CONFIG['min_score_to_display']:.0f}: **{len(top_df)}**\n\n")
            if not top_df.empty:
                f.write(top_df.head(200).to_markdown(index=False))
            else:
                f.write("Ninguna compañía superó el umbral mínimo en esta ejecución.\n")


# ==============================================================================
# PASOS
# ==============================================================================
def cmd_universe() -> None:
    tickers = build_universe()
    with open("universe.json", "w") as f:
        json.dump(tickers, f)
    print(f"Universo: {len(tickers)} tickers")


def load_universe() -> List[str]:
    if os.path.exists("universe.json"):
        with open("universe.json") as f:
            return json.load(f)
    return build_universe()


def cmd_scan(shard: int, shards: int) -> None:
    tickers = load_universe()[shard::shards]
    print(f"Parte {shard + 1}/{shards}: {len(tickers)} tickers")
    results, done = [], 0
    with ThreadPoolExecutor(max_workers=CONFIG["workers_per_shard"]) as pool:
        for res in pool.map(analyze_ticker, tickers):
            done += 1
            if res:
                results.append(res)
            if done % 100 == 0:
                print(f"  {done}/{len(tickers)} procesados, {len(results)} válidos")
    os.makedirs("shards", exist_ok=True)
    with open(f"shards/shard_{shard}.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)
    print(f"Parte {shard + 1}: {len(results)} empresas evaluadas")


def cmd_merge() -> None:
    results: Dict[str, Dict[str, Any]] = {}
    for path in glob.glob("shards/**/*.json", recursive=True):
        with open(path, encoding="utf-8") as f:
            for r in json.load(f):
                results[r["ticker"]] = r
    if not results:
        print("No se obtuvieron resultados.")
        sys.exit(1)
    ordered = sorted(results.values(), key=lambda r: r["score"], reverse=True)
    write_site(ordered)
    write_reports(ordered)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", nargs="?", default="all", choices=["all", "universe", "scan", "merge"])
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    a = ap.parse_args()
    if a.step == "universe":
        cmd_universe()
    elif a.step == "scan":
        cmd_scan(a.shard, a.shards)
    elif a.step == "merge":
        cmd_merge()
    else:
        cmd_universe()
        cmd_scan(0, 1)
        cmd_merge()


if __name__ == "__main__":
    main()
