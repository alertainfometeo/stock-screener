import os
import json
import time
import requests
from io import StringIO
import pandas as pd
import yfinance as yf
from typing import Dict, Any, List

# ==============================================================================
# CONFIGURACIÓN DE GATES Y PESOS (TOTAL = 100 PUNTOS)
# ==============================================================================
CONFIG = {
    "weights": {
        "quality_fisher": 35.0,   # 15 puntos de Phil Fisher
        "financial_gates": 30.0,  # ROIC + Crecimiento EPS
        "valuation": 35.0,        # PEG Forward, P/E, P/FCF, FCF Yield
    },
    "financial_thresholds": {
        "min_roic": 0.15,         # ROIC > 15%
        "min_eps_growth": 0.10,   # Crecimiento EPS > 10%
    },
    "valuation_thresholds": {
        "max_peg_strict": 1.5,    # PEG Forward ideal < 1.5
        "max_peg_relaxed": 2.0,   # PEG Forward tolerado < 2.0 (puntuación parcial)
        "max_pe": 25.0,           # P/E < 25
        "max_p_fcf": 25.0,        # Price / Free Cash Flow < 25
        "min_fcf_yield": 0.04,    # Free Cash Flow Yield > 4%
    },
    "min_score_to_display": 60.0, # Mostrar compañías a partir de 60 puntos
}

# Universo base global (puedes ampliarlo o cargarlo desde un CSV)
DEFAULT_TICKERS = [
    # US Tech & Quality
    "ADBE", "BKNG", "MSFT", "GOOGL", "AAPL", "META", "NVDA", "V", "MA",
    "CRM", "INTU", "SNPS", "CDNS", "NOW", "ORCL", "TXN", "QCOM", "AMAT",
    # US Consumer, Healthcare & Industrials
    "COST", "UNH", "ISRG", "SYK", "TMO", "DHR", "SPGI", "MCO", "UNP", "CPRT",
    # Europa & Internacional
    "ASML", "NOVO-B.CO", "LVMH.PA", "RMS.PA", "OR.PA", "ITX.MC", "AMS.MC",
    "NVO", "TSM", "MELI", "CSU.TO"
]


def get_sp500_tickers() -> List[str]:
    """Obtiene tickers del S&P 500 desde Wikipedia y los combina con globales."""
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        headers = {"User-Agent": "Mozilla/5.0"}
        html = requests.get(url, headers=headers, timeout=15).text
        tables = pd.read_html(StringIO(html))
        sp500 = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
        combined = list(dict.fromkeys(DEFAULT_TICKERS + sp500))
        return combined
    except Exception:
        return DEFAULT_TICKERS


def evaluate_fisher_15_points(info: Dict[str, Any], ticker: str) -> float:
    """
    Evalúa los 15 puntos de Phil Fisher (retorna un ratio entre 0.0 y 1.0).
    Si existe ANTHROPIC_API_KEY en el entorno, usa Claude para evaluar los 15 puntos;
    de lo contrario, aplica proxies fundamentales equivalentes.
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
                return pts / 15.0
        except Exception:
            pass  # Fallback automático a proxies cuantitativos

    # Proxies cuantitativos de los 15 puntos de Phil Fisher (15 criterios = 15 pts)
    fisher_checks = [
        (info.get("revenueGrowth") or 0) > 0.08,            # 1. Potencial de crecimiento de ventas
        (info.get("grossMargins") or 0) > 0.45,             # 2. Ventaja competitiva / pricing power
        (info.get("operatingMargins") or 0) > 0.18,         # 3. Márgenes operativos superiores
        (info.get("profitMargins") or 0) > 0.12,            # 4. Rentabilidad neta sostenible
        (info.get("returnOnEquity") or 0) > 0.18,           # 5. Eficiencia del capital
        (info.get("returnOnAssets") or 0) > 0.08,           # 6. Productividad de activos
        (info.get("earningsGrowth") or 0) > 0.10,           # 7. Expansión de beneficios
        (info.get("freeCashflow") or 0) > 0,                # 8. Conversión real a caja
        (info.get("operatingCashflow") or 0) > (info.get("netIncomeToCommon") or 0) * 0.9,  # 9. Calidad contable
        (info.get("debtToEquity") or 999) < 100,            # 10. Solvencia sin dilución forzada
        (info.get("currentRatio") or 0) > 1.2,              # 11. Liquidez a corto plazo
        (info.get("heldPercentInsiders") or 0) > 0.01,      # 12. Alineación directiva (skin in the game)
        (info.get("heldPercentInstitutions") or 0) > 0.40,  # 13. Validación institucional
        (info.get("ebitdaMargins") or 0) > 0.22,            # 14. Eficiencia operativa estructural
        (info.get("52WeekChange") or 0) > -0.15,            # 15. Ejecución consistente en mercado
    ]
    return sum(1 for c in fisher_checks if c) / 15.0


def score_step(value: float, target: float, higher_is_better: bool = True, soft_margin: float = 0.25) -> float:
    """
    Devuelve 1.0 si cumple el gate estricto, una fracción proporcional si está
    dentro del margen de tolerancia, y 0.0 si falla por completo.
    """
    if value is None:
        return 0.0
    if higher_is_better:
        if value >= target:
            return 1.0
        floor = target * (1.0 - soft_margin)
        if value > floor and target != floor:
            return (value - floor) / (target - floor)
        return 0.0
    else:
        if value <= 0:
            return 0.0
        if value <= target:
            return 1.0
        ceiling = target * (1.0 + soft_margin)
        if value < ceiling and ceiling != target:
            return (ceiling - value) / (ceiling - target)
        return 0.0


def calculate_roic(stock: yf.Ticker, info: Dict[str, Any]) -> float:
    """Calcula el ROIC = NOPAT / Capital Invertido."""
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


def analyze_ticker(ticker: str) -> Dict[str, Any] | None:
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        if not info or "marketCap" not in info or not info["marketCap"]:
            return None

        market_cap = float(info["marketCap"])
        fcf = info.get("freeCashflow")
        pe = info.get("trailingPE") or info.get("forwardPE")
        forward_pe = info.get("forwardPE")
        eps_growth = info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth") or 0.0

        # Cálculo de PEG Forward
        peg_forward = info.get("pegRatio")
        if (not peg_forward or peg_forward <= 0) and forward_pe and eps_growth > 0:
            peg_forward = forward_pe / (eps_growth * 100.0)

        # Cálculo de P/FCF y FCF Yield
        p_fcf = (market_cap / fcf) if (fcf and fcf > 0) else None
        fcf_yield = (fcf / market_cap) if (fcf and fcf > 0) else 0.0

        # Cálculo de ROIC
        roic = calculate_roic(stock, info)

        # 1. PILAR CALIDAD (Phil Fisher 15 puntos)
        fisher_ratio = evaluate_fisher_15_points(info, ticker)
        score_quality = fisher_ratio * CONFIG["weights"]["quality_fisher"]

        # 2. PILAR FINANCIAL GATES (ROIC + EPS Growth)
        fin_cfg = CONFIG["financial_thresholds"]
        s_roic = score_step(roic, fin_cfg["min_roic"], higher_is_better=True)
        s_eps = score_step(eps_growth, fin_cfg["min_eps_growth"], higher_is_better=True)
        score_financial = ((s_roic + s_eps) / 2.0) * CONFIG["weights"]["financial_gates"]

        # 3. PILAR VALUACIÓN (PEG Forward, P/E, P/FCF, FCF Yield)
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
        s_fcf_yield = score_step(fcf_yield, val_cfg["min_fcf_yield"], higher_is_better=True)

        score_valuation = ((s_peg + s_pe + s_pfcf + s_fcf_yield) / 4.0) * CONFIG["weights"]["valuation"]

        total_score = round(score_quality + score_financial + score_valuation, 1)

        return {
            "Ticker": ticker,
            "Empresa": info.get("shortName", ticker)[:28],
            "Score": total_score,
            "Calidad (35)": round(score_quality, 1),
            "Finanzas (30)": round(score_financial, 1),
            "Valuación (35)": round(score_valuation, 1),
            "ROIC %": round(roic * 100, 1),
            "EPS Gr %": round(eps_growth * 100, 1),
            "PEG Fwd": round(peg_forward, 2) if peg_forward else None,
            "P/E": round(pe, 1) if pe else None,
            "P/FCF": round(p_fcf, 1) if p_fcf else None,
            "FCF Yield %": round(fcf_yield * 100, 2),
        }
    except Exception:
        return None


def main():
    use_full_sp500 = os.getenv("SCAN_FULL_SP500", "false").lower() == "true"
    tickers = get_sp500_tickers() if use_full_sp500 else DEFAULT_TICKERS

    print(f"Escaneando {len(tickers)} compañías...")
    results = []

    for idx, ticker in enumerate(tickers, 1):
        res = analyze_ticker(ticker)
        if res:
            results.append(res)
        if idx % 20 == 0:
            print(f"Procesadas {idx}/{len(tickers)}...")
            time.sleep(1.0)  # Evitar rate limit en Yahoo Finance

    df = pd.DataFrame(results)
    if df.empty:
        print("No se obtuvieron resultados.")
        return

    df = df.sort_values(by="Score", ascending=False)
    df.to_csv("screener_results.csv", index=False)

    top_df = df[df["Score"] >= CONFIG["min_score_to_display"]]
    print("\n=== TOP COMPAÑÍAS (SCORE >= 60) ===")
    print(top_df.to_markdown(index=False))

    # Publicar tabla directamente en el resumen del run de GitHub Actions
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("## Resultados del Screener (Calidad + Salud Financiera + Valuación)\n\n")
            f.write(f"Compañías analizadas: **{len(df)}** | Con Score ≥ {CONFIG['min_score_to_display']}: **{len(top_df)}**\n\n")
            if not top_df.empty:
                f.write(top_df.to_markdown(index=False))
            else:
                f.write("Ninguna compañía superó el umbral mínimo en esta ejecución.\n")


if __name__ == "__main__":
    main()
