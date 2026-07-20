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
# 1. IWV holdings → df_loop
# ─────────────────────────────────────────────
import ssl
ssl._create_default_https_context = ssl._create_unverified_context

import io
import time
import requests
import pandas as pd
from datetime import datetime, timedelta

print("IWV holdings 다운로드 중...")

# Known ticker exceptions where the iShares raw ticker (no separator)
# differs from the yfinance-compatible ticker (uses a hyphen).
# Most "CLASS A/B" names (GOOGL, FOXA, NWSA, LBTYA, UAA, RUSHA...) already
# trade under their literal ticker on yfinance — do NOT blanket-convert.
TICKER_OVERRIDES = {
    'BRKB': 'BRK-B',
    'BFB': 'BF-B',
    'BFA': 'BF-A',
    'GEFB': 'GEF-B',
    'HEIA': 'HEI-A',
    'LENB': 'LEN-B',
    'STZB': 'STZ-B',
}


def _try_fetch_iwv_csv(date_str: str) -> bytes | None:
    """Attempt to download the IWV holdings CSV for a given YYYYMMDD date."""
    url = (
        'https://www.blackrock.com/varnish-api/blk-one01-product-data/'
        'product-data/api/v1/get-fund-document'
        '?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=us-ishares'
        '&locale=en_US&portfolioId=239714&userType=individual'
        f'&asOfDate={date_str}&component=holdings'
    )
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
        if r.status_code == 200 and len(r.content) > 500:
            return r.content
    except Exception as e:
        print(f"  {date_str}: 연결 오류 ({e})")
    return None


def _parse_iwv_csv(raw_bytes: bytes) -> pd.DataFrame:
    """
    Locate the real header row (starts with 'Ticker,Name,Sector')
    instead of hardcoding a skiprows count, since the metadata block
    length can shift.
    """
    text = raw_bytes.decode('utf-8', errors='replace')
    lines = text.splitlines()

    header_idx = next(
        (i for i, line in enumerate(lines) if line.startswith('Ticker,Name,Sector')),
        None
    )
    if header_idx is None:
        raise ValueError("헤더 행을 찾을 수 없습니다 (파일 포맷이 변경되었을 수 있음)")

    csv_body = "\n".join(lines[header_idx:])
    df = pd.read_csv(io.StringIO(csv_body))

    # Confirm the as-of date embedded in the file matches what we requested,
    # to catch cases where BlackRock silently serves a stale/previous file.
    as_of_line = next((l for l in lines[:header_idx] if 'Fund Holdings as of' in l), None)

    return df, as_of_line


def get_iwv_holdings(as_of_date: datetime = None, max_lookback_days: int = 10):
    """
    Downloads IWV holdings, walking backward through business days
    (skipping weekends) if today's file isn't published yet.
    """
    if as_of_date is None:
        as_of_date = datetime.today()

    current_date = as_of_date

    for attempt in range(max_lookback_days):
        while current_date.weekday() >= 5:  # 5=Sat, 6=Sun
            current_date -= timedelta(days=1)

        date_str = current_date.strftime('%Y%m%d')
        raw_bytes = _try_fetch_iwv_csv(date_str)

        if raw_bytes is not None:
            try:
                df, as_of_line = _parse_iwv_csv(raw_bytes)
                print(f"  성공: {date_str} 기준 요청 → 파일 내 실제 기준일: {as_of_line}")
                return df, date_str
            except Exception as e:
                print(f"  {date_str}: 파싱 오류 ({e}), 이전 영업일로 재시도...")
        else:
            print(f"  {date_str}: 데이터 없음, 이전 영업일로 재시도...")

        current_date -= timedelta(days=1)
        time.sleep(1)

    raise Exception(f"{max_lookback_days}일 내 유효한 holdings 데이터를 찾지 못했습니다.")


def clean_iwv_holdings(raw_df: pd.DataFrame, us_only: bool = True) -> pd.DataFrame:
    """
    Filters to real equity holdings and normalizes tickers for yfinance.
    us_only=True keeps only US-listed rows (matches SPY loop's scope);
    set False to keep ADRs/foreign listings too (won't all resolve on yfinance).
    """
    df = raw_df.copy()

    # Drop non-equity rows (Futures, Cash, Money Market, disclaimer footer, etc.)
    df = df[df['Asset Class'] == 'Equity']
    df = df[df['Ticker'].astype(str) != '-']
    df = df.dropna(subset=['Ticker'])

    if us_only:
        df = df[df['Location'] == 'United States']

    df['Ticker'] = df['Ticker'].astype(str).str.strip()
    df['Ticker'] = df['Ticker'].replace(TICKER_OVERRIDES)

    df_loop = df[['Ticker', 'Name', 'Sector']].copy().reset_index(drop=True)
    df_loop = df_loop.drop_duplicates(subset='Ticker').reset_index(drop=True)

    return df_loop


# ── Run ──
if __name__ == '__main__':
    raw_df, used_date = get_iwv_holdings(datetime.today())
    df_loop = clean_iwv_holdings(raw_df, us_only=True)
    tickers = df_loop['Ticker'].tolist()
    print(f"  총 {len(tickers)}개 종목 로드 (기준일 요청: {used_date})")

    df_loop.to_csv('etf_analysis/iwv_holdings.csv', index=False)

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
RETRY_ATTEMPTS = 3   # 개별 재시도 최대 횟수
RETRY_BACKOFF = 5    # 초 단위, attempt 배수로 증가

manifest = []
failed_details = {}  # {ticker: reason} — 어떤 이유로 실패했는지 추적

def process_ticker(ticker: str, ohlcv: pd.DataFrame, name: str, sector: str) -> dict:
    """단일 티커의 OHLCV로 지표 계산 + JSON 저장. 실패 시 예외를 그대로 던짐(호출부에서 사유 기록)."""
    ohlcv = ohlcv.dropna(subset=['Close'])
    if len(ohlcv) < 60:
        raise ValueError(f'insufficient_history ({len(ohlcv)}행 < 60일)')

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

    return {
        'ticker': ticker,
        'name': name,
        'sector': sector,
        'ret_1d': ret_1d,
        'ret_1w': ret_1w,
        'last_close': round(float(last_close), 2),
    }

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
        for t in batch:
            failed_details[t] = 'batch_download_failed (3회 재시도 후에도 배치 전체 다운로드 실패)'
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
            entry = process_ticker(ticker, ohlcv, name, sector)
            manifest.append(entry)
            print(f"  ✓ {ticker}", end='\r')

        except KeyError:
            reason = 'no_data_in_batch (배치 응답에 해당 티커 없음 — 상장폐지/티커 변경 가능성)'
            print(f"\n  ✗ {ticker}: {reason}")
            failed_details[ticker] = reason
        except Exception as e:
            reason = f'{type(e).__name__}: {e}'
            print(f"\n  ✗ {ticker}: {reason}")
            failed_details[ticker] = reason

# ─────────────────────────────────────────────
# 4b. 실패 티커 재시도 (개별 다운로드, 백오프 적용)
# ─────────────────────────────────────────────
if failed_details:
    print(f"\n\n──── 재시도 단계: {len(failed_details)}개 티커 개별 재다운로드 ────")
    still_failed = {}

    for ticker in list(failed_details.keys()):
        row = df_loop[df_loop['Ticker'] == ticker].iloc[0]
        name = str(row.get('Name', ticker))
        sector = str(row.get('Sector', ''))
        reason = failed_details[ticker]
        recovered = False

        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                raw1 = yf.download(ticker, start=from_date, end=to_date, progress=False, auto_adjust=True)
                if raw1 is None or raw1.empty:
                    reason = 'empty_response (상장폐지/티커 변경 또는 야후파이낸스 미제공 가능성 — 재시도 무의미)'
                    break  # 빈 응답은 재시도해도 소용없으므로 즉시 중단
                ohlcv = raw1[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
                entry = process_ticker(ticker, ohlcv, name, sector)
                manifest.append(entry)
                print(f"  ✓ (재시도 {attempt}회차 성공) {ticker}")
                recovered = True
                break
            except ValueError as e:
                reason = str(e)  # 데이터 부족(insufficient_history) — 재시도해도 소용없음
                break
            except Exception as e:
                reason = f'{type(e).__name__}: {e}'
                print(f"  … {ticker} 재시도 {attempt}/{RETRY_ATTEMPTS} 실패: {reason}")
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF * attempt)

        if not recovered:
            still_failed[ticker] = reason

    failed_details = still_failed

# ─────────────────────────────────────────────
# 5. manifest.json + 실패 진단 로그 저장
# ─────────────────────────────────────────────
manifest_path = os.path.join(OUT_DIR, 'manifest.json')
with open(manifest_path, 'w', encoding='utf-8') as f:
    json.dump({
        'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'count': len(manifest),
        'stocks': manifest,
    }, f, ensure_ascii=False)

failed_path = os.path.join(OUT_DIR, 'failed_tickers.json')
with open(failed_path, 'w', encoding='utf-8') as f:
    json.dump({
        'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'count': len(failed_details),
        'failures': failed_details,  # {ticker: reason}
    }, f, ensure_ascii=False, indent=2)

print(f"\n\n✓ 완료: {len(manifest)}개 저장, {len(failed_details)}개 최종 실패")
if failed_details:
    print(f"  실패 상세: {failed_path}")
    for t, r in failed_details.items():
        print(f"    - {t}: {r}")

