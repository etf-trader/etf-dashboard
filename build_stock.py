"""
build_stock.py
==============
SPY 구성종목 전체 다운로드 → 트레이더 지표 계산 → JSON 저장

출력:
  etf_analysis/stocks/manifest.json  ← 티커/이름/섹터 목록
  etf_analysis/stocks/{TICKER}.json  ← 종목별 OHLCV + 필수 지표 데이터
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

warnings.filterwarnings('ignore')

# ─────────────────────────────────────────────
# 트레이더 중심 설정 (중복 지표 삭제 및 MA 다이어트)
# ─────────────────────────────────────────────
DAYS_BEFORE = 365 * 1
OUT_DIR = 'etf_analysis/stocks'
BATCH_SIZE = 50

RSI_PERIOD = 14
STOCH_N = 5
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MACD2_FAST = 9    # 9-20 EMA MACD (구 Donchian 자리 대체)
MACD2_SLOW = 20
MACD2_SIGNAL = 9
MA_WINDOWS = [5, 9, 20, 50, 200]  # EMA 5/9/20/50/200 (SMA -> EMA 전환, 9일선 추가)
OBV_EMA_COM = 20

os.makedirs(OUT_DIR, exist_ok=True)

from_date = datetime.datetime.today() - datetime.timedelta(days=DAYS_BEFORE)
to_date = datetime.datetime.today()

# ─────────────────────────────────────────────
# 1. SPY holdings → df_loop
# ─────────────────────────────────────────────
print("SPY holdings 다운로드 중...")

rows = []
import io
import ssl
import requests

ssl._create_default_https_context = ssl._create_unverified_context

while True:
    try:
        url = (
            'https://www.ssga.com/us/en/intermediary/library-content/products/'
            'fund-data/etfs/us/holdings-daily-us-en-spy.xlsx'
        )
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
        if r.status_code != 200:
            raise Exception(f"HTTP {r.status_code}")
        holdings = pd.read_excel(io.BytesIO(r.content), skiprows=4, engine='openpyxl')
        break
    except Exception as e:
        print(f"  holdings 연결 오류: {e}. 5초 후 재시도...")
        time.sleep(5)

if 'Local Currency' in holdings.columns:
    holdings = holdings[holdings['Local Currency'] == 'USD']
if 'SEDOL' in holdings.columns:
    holdings = holdings[holdings['SEDOL'] != '-']

ticker_col = next((c for c in holdings.columns if 'ticker' in str(c).lower() or 'symbol' in str(c).lower()), None)
name_col = next((c for c in holdings.columns if 'name' in str(c).lower()), None)
sector_col = next((c for c in holdings.columns if 'sector' in str(c).lower()), None)

use_cols = [col for col in [ticker_col, name_col, sector_col] if col is not None]
holdings = holdings[use_cols].dropna(subset=[ticker_col])
holdings.columns = ['Ticker', 'Name', 'Sector'][:len(use_cols)]
if 'Sector' not in holdings.columns:
    holdings['Sector'] = ''

holdings['Ticker'] = (
    holdings['Ticker']
    .astype(str).str.strip()
    .str.replace('.', '-', regex=False)
    .replace({'BRK-B': 'BRK-B', 'BF-B': 'BF-B'})
)
holdings = holdings[holdings['Ticker'] != '']

df_loop = holdings[['Ticker', 'Name', 'Sector']].copy().reset_index(drop=True)
tickers = df_loop['Ticker'].tolist()
print(f"  총 {len(tickers)}개 종목 로드")

# ─────────────────────────────────────────────
# 2. SPY(Index) 다운로드
# ─────────────────────────────────────────────
print("SPY(벤치마크) 다운로드 중...")
spy_df = yf.download('SPY', start=from_date, end=to_date, progress=False, auto_adjust=True)
spy_close = spy_df['Close'].squeeze()
spy_close.name = 'SPY'

# ─────────────────────────────────────────────
# 3. 지표 계산 함수 (트레이더 버전 최적화)
# ─────────────────────────────────────────────
def safe(val):
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 4)
    except Exception:
        return None

def series_to_list(s):
    return [safe(v) for v in s.tolist()]

def calc_indicators(ohlcv: pd.DataFrame, spy: pd.Series) -> dict:
    close = ohlcv['Close'].squeeze()
    high = ohlcv['High'].squeeze()
    low = ohlcv['Low'].squeeze()
    volume = ohlcv['Volume'].squeeze()
    dates = close.index.strftime('%Y-%m-%d').tolist()

    # ── 9-20 EMA MACD (구 Donchian 채널 대체) ──
    ema9_fast = close.ewm(span=MACD2_FAST, adjust=False).mean()
    ema20_slow = close.ewm(span=MACD2_SLOW, adjust=False).mean()
    macd2_spread = ema9_fast - ema20_slow
    macd2_signal = macd2_spread.ewm(span=MACD2_SIGNAL, adjust=False).mean()
    macd2_oscill = macd2_spread - macd2_signal

    # ── Stochastic ──
    low_min = low.rolling(STOCH_N).min()
    high_max = high.rolling(STOCH_N).max()
    fast_k = (close - low_min) / (high_max - low_min) * 100
    slow_k = fast_k.rolling(STOCH_N).mean()
    slow_d = slow_k.rolling(STOCH_N).mean()

    # ── RSI ──
    change = close.diff()
    change_up = change.clip(lower=0)
    change_down = (-change).clip(lower=0)
    avg_up = change_up.rolling(RSI_PERIOD).mean()
    avg_down = change_down.rolling(RSI_PERIOD).mean()
    rsi = 100 * avg_up / (avg_up + avg_down)

    # ── Relative Strength vs SPY ──
    spy_aligned = spy.reindex(close.index).ffill()
    rs_index = (close / spy_aligned)
    rs_index = rs_index / rs_index.iloc[0]
    rs_ma50 = rs_index.rolling(50).mean()

    # ── MACD ──
    ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
    macd_spread = ema_fast - ema_slow
    macd_signal = macd_spread.ewm(span=MACD_SIGNAL, adjust=False).mean()
    macd_oscill = macd_spread - macd_signal

    # ── Moving Averages (EMA 5, 9, 20, 50, 200 — SMA -> EMA 전환) ──
    mas = {w: close.ewm(span=w, adjust=False).mean() for w in MA_WINDOWS}

    # ── MA50 Deviation (EMA50 기준) ──
    ma50_dev = (close - mas[50]) / mas[50] * 100

    # ── OBV ──
    direction = change.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (volume * direction).cumsum()
    obv_ema = obv.ewm(com=OBV_EMA_COM).mean()

    # [Williams %R 및 UDVR 삭제로 연산 및 리턴 경량화]

    return {
        'dates': dates,
        'close': series_to_list(close),
        'open': series_to_list(ohlcv['Open'].squeeze()),
        'high': series_to_list(high),
        'low': series_to_list(low),
        'volume': [int(v) if not math.isnan(float(v)) else 0 for v in volume.tolist()],
        'macd2_spread': series_to_list(macd2_spread),  # 9-20 EMA MACD (구 upper_don/lower_don/mid_don 대체)
        'macd2_signal': series_to_list(macd2_signal),
        'macd2_oscill': series_to_list(macd2_oscill),
        'slow_k': series_to_list(slow_k),
        'slow_d': series_to_list(slow_d),
        'rsi': series_to_list(rsi),
        'rs_index': series_to_list(rs_index),
        'rs_ma50': series_to_list(rs_ma50),
        'macd_spread': series_to_list(macd_spread),
        'macd_signal': series_to_list(macd_signal),
        'macd_oscill': series_to_list(macd_oscill),
        'ma50_dev': series_to_list(ma50_dev),
        'ma5': series_to_list(mas[5]),
        'ma9': series_to_list(mas[9]),   # 신규: 9일 EMA
        'ma20': series_to_list(mas[20]),
        'ma50': series_to_list(mas[50]),
        'ma200': series_to_list(mas[200]),
        'obv': series_to_list(obv),
        'obv_ema': series_to_list(obv_ema),
    }

def fetch_fundamentals(ticker: str) -> dict:
    result = {
        'industry': '',
        'marketCap': None,
        'forwardPE': None,
        'returnOnEquity': None,
        'totalRevenue': None,
        'revenueGrowth': None,
        'grossMargins': None,
        'profitMargins': None,
        'dividendYield': None,
        'payoutRatio': None,
    }
    info = None
    for attempt in range(2):
        try:
            info = yf.Ticker(ticker).info
            break
        except Exception:
            if attempt == 0:
                time.sleep(1)
            continue
    if not info:
        return result

    def fmt(key, divisor=1, multiplier=1, decimals=1):
        val = info.get(key)
        if val is None:
            return None
        try:
            return round(float(val) / divisor * multiplier, decimals)
        except Exception:
            return None

    result['industry'] = info.get('industry', '') or ''
    result['forwardPE'] = fmt('forwardPE')
    result['marketCap'] = fmt('marketCap', 1e9)
    result['dividendYield'] = fmt('dividendYield')
    result['payoutRatio'] = fmt('payoutRatio', 1, 100)
    result['totalRevenue'] = fmt('totalRevenue', 1e9)
    result['revenueGrowth'] = fmt('revenueGrowth', 1, 100)
    result['grossMargins'] = fmt('grossMargins', 1, 100)
    result['profitMargins'] = fmt('profitMargins', 1, 100)
    result['returnOnEquity'] = fmt('returnOnEquity', 1, 100)
    return result

# ─────────────────────────────────────────────
# 4. 배치 다운로드 + 지표 계산 + JSON 저장
# ─────────────────────────────────────────────
manifest = []
failed = []
total_batches = math.ceil(len(tickers) / BATCH_SIZE)

for batch_idx in range(total_batches):
    batch = tickers[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]
    print(f"\n배치 [{batch_idx+1}/{total_batches}] {len(batch)}개 다운로드...")

    for attempt in range(3):
        try:
            raw = yf.download(batch, start=from_date, end=to_date, progress=False, auto_adjust=True)
            break
        except Exception as e:
            print(f"  다운로드 오류: {e}. 5초 후 재시도...")
            time.sleep(5)
    else:
        print(f"  배치 {batch_idx+1} 실패, 스킵")
        failed.extend(batch)
        continue

    single = (len(batch) == 1)

    for ticker in batch:
        row = df_loop[df_loop['Ticker'] == ticker].iloc[0]
        name = str(row.get('Name', ticker))
        sector = str(row.get('Sector', ''))

        try:
            if single:
                ohlcv = raw[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
            else:
                ohlcv = raw.xs(ticker, axis=1, level=1)[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
            ohlcv = ohlcv.dropna(subset=['Close'])
            if len(ohlcv) < 60:
                continue

            data = calc_indicators(ohlcv, spy_close)

            last_close = ohlcv['Close'].iloc[-1]
            prev_close = ohlcv['Close'].iloc[-2] if len(ohlcv) >= 2 else last_close
            ret_1d = round((float(last_close) / float(prev_close) - 1) * 100, 2)

            week_ago = ohlcv['Close'].iloc[-6] if len(ohlcv) >= 6 else ohlcv['Close'].iloc[0]
            ret_1w = round((float(last_close) / float(week_ago) - 1) * 100, 2)

            fundamentals = fetch_fundamentals(ticker)
            industry = fundamentals.pop('industry')
            time.sleep(0.1)

            payload = {
                'ticker': ticker,
                'name': name,
                'sector': sector,
                'industry': industry,
                'ret_1d': ret_1d,
                'ret_1w': ret_1w,
                'last_close': round(float(last_close), 2),
                'fundamentals': fundamentals,
                'data': data,
            }

            out_path = os.path.join(OUT_DIR, f'{ticker}.json')
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)

            manifest.append({
                'ticker': ticker,
                'name': name,
                'sector': sector,
                'ret_1d': ret_1d,
                'ret_1w': ret_1w,
                'last_close': round(float(last_close), 2),
            })
            print(f"  ✓ {ticker}", end='\r')

        except Exception as e:
            print(f"\n  ✗ {ticker}: {e}")
            failed.append(ticker)

# ─────────────────────────────────────────────
# 5. manifest.json 저장
# ─────────────────────────────────────────────
manifest_path = os.path.join(OUT_DIR, 'manifest.json')
with open(manifest_path, 'w', encoding='utf-8') as f:
    json.dump({
        'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'count': len(manifest),
        'stocks': manifest,
    }, f, ensure_ascii=False)

print(f"\n\n✓ 완료: {len(manifest)}개 저장, {len(failed)}개 실패")
