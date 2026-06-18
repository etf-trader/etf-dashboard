"""
build_financials.py
====================
SPY 구성종목 연간 재무제표(최근 4년) 다운로드 → JSON 저장
※ 재무데이터는 분기 단위로만 갱신되므로 매일 돌릴 필요 없음 (매주 토요일 1회 실행 권장)
※ etf_analysis/stocks/manifest.json (build_stock.py 결과물)을 그대로 재사용하므로
  build_financials.py 실행 전에 build_stock.py가 먼저 실행되어 있어야 함

출력:
  etf_analysis/financials/manifest.json   ← 생성 시각 / 종목 수
  etf_analysis/financials/{TICKER}.json   ← 종목별 연간 재무 데이터 (최근 4개년)
"""

import yfinance as yf
import pandas as pd
import numpy as np
import json
import math
import time
import datetime
import warnings
import os
import sys
import ssl

warnings.filterwarnings('ignore')

# build_stock.py와 동일한 SSL 우회 설정
ssl._create_default_https_context = ssl._create_unverified_context

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
OUT_DIR      = 'etf_analysis/financials'
STOCKS_DIR   = 'etf_analysis/stocks'
MANIFEST_IN  = os.path.join(STOCKS_DIR, 'manifest.json')

os.makedirs(OUT_DIR, exist_ok=True)


def safe(val):
    """NaN/Inf → None 변환 (build_stock.py와 동일)"""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 4)
    except Exception:
        return None


# ─────────────────────────────────────────────
# 1. 티커 목록 — build_stock.py가 만든 manifest.json 재사용
# ─────────────────────────────────────────────
print("manifest.json에서 티커 목록 로드 중...")

if not os.path.exists(MANIFEST_IN):
    print(f"  ✗ {MANIFEST_IN} 없음. build_stock.py를 먼저 실행하세요.")
    sys.exit(1)

with open(MANIFEST_IN, 'r', encoding='utf-8') as f:
    stock_manifest = json.load(f)

stocks_meta = {s['ticker']: s for s in stock_manifest['stocks']}
tickers = list(stocks_meta.keys())
print(f"  총 {len(tickers)}개 종목")


def load_meta(ticker: str):
    """
    name/sector는 manifest.json에서, industry는 build_stock.py가 만든
    etf_analysis/stocks/{TICKER}.json에서 가져와 yfinance .info 재호출을 피함.
    """
    meta = stocks_meta.get(ticker, {})
    name   = meta.get('name', ticker)
    sector = meta.get('sector', '')
    industry = ''
    try:
        with open(os.path.join(STOCKS_DIR, f'{ticker}.json'), 'r', encoding='utf-8') as f:
            stock_payload = json.load(f)
        industry = stock_payload.get('industry', '') or ''
    except Exception:
        pass
    return name, sector, industry


# ─────────────────────────────────────────────
# 2. 종목별 연간 재무제표 다운로드 + 계산
# ─────────────────────────────────────────────
def col_or_none(df: pd.DataFrame, col_name: str) -> pd.Series:
    """컬럼이 없으면 None으로 채운 동일 길이 Series 반환 (회사별 보고 항목 차이 대응)"""
    if col_name in df.columns:
        return df[col_name]
    return pd.Series([None] * len(df), index=df.index)


def to_millions(series: pd.Series):
    out = []
    for v in series:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            out.append(None)
        else:
            out.append(safe(float(v) / 1_000_000))
    return out


def to_values(series: pd.Series):
    out = []
    for v in series:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            out.append(None)
        else:
            out.append(safe(float(v)))
    return out


def margin_pct(numer: pd.Series, denom: pd.Series):
    out = []
    for n, d in zip(numer, denom):
        n_bad = n is None or (isinstance(n, float) and pd.isna(n))
        d_bad = d is None or (isinstance(d, float) and pd.isna(d)) or d == 0
        if n_bad or d_bad:
            out.append(None)
        else:
            out.append(safe(float(n) / float(d) * 100))
    return out


saved  = []
failed = []

total = len(tickers)
for i, ticker in enumerate(tickers):
    try:
        name, sector, industry = load_meta(ticker)

        df_income = None
        for attempt in range(2):
            try:
                obj = yf.Ticker(ticker)
                df_income = obj.income_stmt
                break
            except Exception as e:
                if attempt == 0:
                    time.sleep(2)
                    continue
                print(f"\n  {ticker}: income_stmt 조회 실패 ({e})")

        if df_income is None or df_income.empty:
            print(f"\n  {ticker}: 재무 데이터 없음, 스킵")
            failed.append(ticker)
            continue

        df_income = df_income.T.sort_index()
        dates = [str(d)[:10] for d in df_income.index]

        revenue          = col_or_none(df_income, 'Total Revenue')
        gross_profit     = col_or_none(df_income, 'Gross Profit')
        operating_income = col_or_none(df_income, 'Operating Income')
        net_income       = col_or_none(df_income, 'Net Income')
        diluted_eps      = col_or_none(df_income, 'Diluted EPS')

        payload = {
            'ticker':   ticker,
            'name':     name,
            'sector':   sector,
            'industry': industry,
            'dates':    dates,
            # Key Financials (백만 달러 단위)
            'revenue':          to_millions(revenue),
            'gross_profit':     to_millions(gross_profit),
            'operating_income': to_millions(operating_income),
            'net_income':       to_millions(net_income),
            # EPS
            'diluted_eps': to_values(diluted_eps),
            # Margins (%)
            'gross_margin':     margin_pct(gross_profit, revenue),
            'operating_margin': margin_pct(operating_income, revenue),
            'net_margin':       margin_pct(net_income, revenue),
        }

        out_path = os.path.join(OUT_DIR, f'{ticker}.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)

        saved.append(ticker)
        print(f"  ✓ {ticker} ({i+1}/{total})", end='\r')

        time.sleep(0.3)  # yfinance 과다 호출 방지용 소폭 지연

    except Exception as e:
        print(f"\n  ✗ {ticker}: {e}")
        failed.append(ticker)

# ─────────────────────────────────────────────
# 3. financials/manifest.json 저장
# ─────────────────────────────────────────────
manifest_path = os.path.join(OUT_DIR, 'manifest.json')
with open(manifest_path, 'w', encoding='utf-8') as f:
    json.dump({
        'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'count': len(saved),
        'tickers': saved,
    }, f, ensure_ascii=False)

print(f"\n\n✓ 완료: {len(saved)}개 저장, {len(failed)}개 실패")
if failed:
    print(f"  실패 목록: {failed}")
print(f"  → {OUT_DIR}/manifest.json")
print(f"  → {OUT_DIR}/{{TICKER}}.json")