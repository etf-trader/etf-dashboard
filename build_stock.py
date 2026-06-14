import ssl
ssl._create_default_https_context = ssl._create_unverified_context

"""
build_stock.py
==============
SPY 구성종목 전체 다운로드 → 지표 계산 → JSON 저장
출력:
  etf_analysis/stocks/manifest.json   ← 티커/이름/섹터 목록
  etf_analysis/stocks/{TICKER}.json   ← 종목별 OHLCV + 지표 데이터
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
# 설정
# ─────────────────────────────────────────────
DAYS_BEFORE   = 365 * 2
OUT_DIR       = 'etf_analysis/stocks'
BATCH_SIZE    = 50          # yf.download 배치 크기
DONCHIAN_PERIOD = 20
RSI_PERIOD    = 14
STOCH_N       = 5
WILLIAMS_N    = 14
MACD_FAST     = 12
MACD_SLOW     = 26
MACD_SIGNAL   = 9
MA_WINDOWS    = [5, 25, 50, 100, 150, 200]
OBV_EMA_COM   = 20
UDVR_PERIOD   = 20

os.makedirs(OUT_DIR, exist_ok=True)

from_date = datetime.datetime.today() - datetime.timedelta(days=DAYS_BEFORE)
to_date   = datetime.datetime.today()

# ─────────────────────────────────────────────
# 1. SPY holdings → df_loop
# ─────────────────────────────────────────────
print("SPY holdings 다운로드 중...")

rows = []
while True:
    try:
        holdings = pd.read_csv(
            'https://www.blackrock.com/varnish-api/blk-one01-product-data/product-data/api/v1/get-fund-document'
            '?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=us-ishares&locale=en_US'
            '&portfolioId=239726&userType=individual&asOfDate=20260518&component=holdings',
            skiprows=[0, 1, 2, 3, 4, 5, 6, 7, 8]
        )
        break
    except Exception as e:
        print(f"  holdings 연결 오류: {e}. 5초 후 재시도...")
        time.sleep(5)

# Equity만 필터
index_equity = holdings[(holdings['Asset Class'] != 'Equity')].index
holdings.drop(index_equity, inplace=True)

# yfinance용 티커 변환
holdings['Ticker'] = holdings['Ticker'].replace({'BRKB': 'BRK-B', 'BFB': 'BF-B'})

df_loop = holdings[['Ticker', 'Name', 'Sector']].copy()
df_loop = df_loop.reset_index(drop=True)
df_loop = df_loop.dropna(subset=['Ticker'])
df_loop['Ticker'] = df_loop['Ticker'].astype(str).str.strip()
df_loop = df_loop[df_loop['Ticker'] != '']

tickers = df_loop['Ticker'].tolist()
print(f"  총 {len(tickers)}개 종목 로드")

# ─────────────────────────────────────────────
# 2. SPY(Index) 다운로드
# ─────────────────────────────────────────────
print("SPY(벤치마크) 다운로드 중...")
spy_df = yf.download('SPY', start=from_date, end=to_date, progress=False, auto_adjust=True)
spy_close = spy_df['Close'].squeeze()  # Series
spy_close.name = 'SPY'

# ─────────────────────────────────────────────
# 3. 지표 계산 함수
# ─────────────────────────────────────────────
def safe(val):
    """NaN/Inf → None 변환"""
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
    """
    ohlcv: MultiIndex 없이 Open/High/Low/Close/Volume 컬럼
    spy  : SPY Close Series (같은 인덱스 기준)
    반환: 차트에 필요한 모든 시리즈 dict
    """
    close  = ohlcv['Close'].squeeze()
    high   = ohlcv['High'].squeeze()
    low    = ohlcv['Low'].squeeze()
    volume = ohlcv['Volume'].squeeze()

    dates = close.index.strftime('%y.%m.%d').tolist()

    # ── Donchian ──────────────────────────────
    upper_don = high.rolling(DONCHIAN_PERIOD).max()
    lower_don = low.rolling(DONCHIAN_PERIOD).min()
    mid_don   = (upper_don + lower_don) / 2

    # ── Stochastic ────────────────────────────
    low_min  = low.rolling(STOCH_N).min()
    high_max = high.rolling(STOCH_N).max()
    fast_k   = (close - low_min) / (high_max - low_min) * 100
    slow_k   = fast_k.rolling(STOCH_N).mean()
    slow_d   = slow_k.rolling(STOCH_N).mean()

    # ── RSI ───────────────────────────────────
    change      = close.diff()
    change_up   = change.clip(lower=0)
    change_down = (-change).clip(lower=0)
    avg_up      = change_up.rolling(RSI_PERIOD).mean()
    avg_down    = change_down.rolling(RSI_PERIOD).mean()
    rsi         = 100 * avg_up / (avg_up + avg_down)

    # ── Williams %R ───────────────────────────
    h14        = high.rolling(WILLIAMS_N).max()
    l14        = low.rolling(WILLIAMS_N).min()
    williams_r = (h14 - close) / (h14 - l14) * -100

    # ── Relative Strength vs SPY ──────────────
    spy_aligned = spy.reindex(close.index).ffill()
    rs_index    = (close / spy_aligned)
    rs_index    = rs_index / rs_index.iloc[0]
    rs_ma50     = rs_index.rolling(50).mean()

    # ── MACD ──────────────────────────────────
    ema_fast    = close.ewm(span=MACD_FAST,   adjust=False).mean()
    ema_slow    = close.ewm(span=MACD_SLOW,   adjust=False).mean()
    macd_spread = ema_fast - ema_slow
    macd_signal = macd_spread.ewm(span=MACD_SIGNAL, adjust=False).mean()
    macd_oscill = macd_spread - macd_signal

    # ── MA50 Deviation ────────────────────────
    ma50         = close.rolling(50).mean()
    ma50_dev     = (close - ma50) / ma50 * 100

    # ── Moving Averages ───────────────────────
    mas = {w: close.rolling(w).mean() for w in MA_WINDOWS}

    # ── OBV ───────────────────────────────────
    direction = change.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv       = (volume * direction).cumsum()
    obv_ema   = obv.ewm(com=OBV_EMA_COM).mean()

    # ── UDVR ──────────────────────────────────
    up_mask   = (change > 0).astype(int)
    down_mask = (change < 0).astype(int)
    up_vol    = volume.multiply(up_mask).rolling(UDVR_PERIOD).sum()
    down_vol  = volume.multiply(down_mask).rolling(UDVR_PERIOD).sum()
    udvr      = up_vol / down_vol

    return {
        'dates':      dates,
        'close':      series_to_list(close),
        'open':       series_to_list(ohlcv['Open'].squeeze()),
        'high':       series_to_list(high),
        'low':        series_to_list(low),
        'volume':     [int(v) if not math.isnan(float(v)) else 0 for v in volume.tolist()],
        # Donchian
        'upper_don':  series_to_list(upper_don),
        'lower_don':  series_to_list(lower_don),
        'mid_don':    series_to_list(mid_don),
        # Stochastic
        'slow_k':     series_to_list(slow_k),
        'slow_d':     series_to_list(slow_d),
        # RSI
        'rsi':        series_to_list(rsi),
        # Williams %R
        'williams_r': series_to_list(williams_r),
        # RS
        'rs_index':   series_to_list(rs_index),
        'rs_ma50':    series_to_list(rs_ma50),
        # MACD
        'macd_spread': series_to_list(macd_spread),
        'macd_signal': series_to_list(macd_signal),
        'macd_oscill': series_to_list(macd_oscill),
        # MA50 Dev
        'ma50_dev':   series_to_list(ma50_dev),
        # MAs
        'ma5':   series_to_list(mas[5]),
        'ma25':  series_to_list(mas[25]),
        'ma50':  series_to_list(mas[50]),
        'ma100': series_to_list(mas[100]),
        'ma150': series_to_list(mas[150]),
        'ma200': series_to_list(mas[200]),
        # OBV
        'obv':     series_to_list(obv),
        'obv_ema': series_to_list(obv_ema),
        # UDVR
        'udvr': series_to_list(udvr),
    }

# ─────────────────────────────────────────────
# 4. 배치 다운로드 + 지표 계산 + JSON 저장
# ─────────────────────────────────────────────
manifest = []
failed   = []

total_batches = math.ceil(len(tickers) / BATCH_SIZE)

for batch_idx in range(total_batches):
    batch = tickers[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]
    print(f"\n배치 [{batch_idx+1}/{total_batches}] {len(batch)}개 다운로드...")

    for attempt in range(3):
        try:
            raw = yf.download(batch, start=from_date, end=to_date,
                              progress=False, auto_adjust=True)
            break
        except Exception as e:
            print(f"  다운로드 오류: {e}. 5초 후 재시도...")
            time.sleep(5)
    else:
        print(f"  배치 {batch_idx+1} 실패, 스킵")
        failed.extend(batch)
        continue

    # 단일 종목 배치면 MultiIndex가 없음
    single = (len(batch) == 1)

    for ticker in batch:
        row = df_loop[df_loop['Ticker'] == ticker].iloc[0]
        name   = str(row.get('Name',   ticker))
        sector = str(row.get('Sector', ''))

        try:
            if single:
                ohlcv = raw[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
            else:
                ohlcv = raw.xs(ticker, axis=1, level=1)[
                    ['Open', 'High', 'Low', 'Close', 'Volume']
                ].copy()

            ohlcv = ohlcv.dropna(subset=['Close'])

            if len(ohlcv) < 60:
                print(f"  {ticker}: 데이터 부족 ({len(ohlcv)}행), 스킵")
                continue

            data = calc_indicators(ohlcv, spy_close)

            # 기본 정보 추가
            last_close  = ohlcv['Close'].iloc[-1]
            prev_close  = ohlcv['Close'].iloc[-2] if len(ohlcv) >= 2 else last_close
            ret_1d = round((float(last_close) / float(prev_close) - 1) * 100, 2)

            week_ago   = ohlcv['Close'].iloc[-6] if len(ohlcv) >= 6 else ohlcv['Close'].iloc[0]
            ret_1w = round((float(last_close) / float(week_ago) - 1) * 100, 2)

            payload = {
                'ticker': ticker,
                'name':   name,
                'sector': sector,
                'ret_1d': ret_1d,
                'ret_1w': ret_1w,
                'last_close': round(float(last_close), 2),
                'data': data,
            }

            out_path = os.path.join(OUT_DIR, f'{ticker}.json')
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)

            manifest.append({
                'ticker': ticker,
                'name':   name,
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
if failed:
    print(f"  실패 목록: {failed}")
print(f"  → {OUT_DIR}/manifest.json")
print(f"  → {OUT_DIR}/{{TICKER}}.json")
