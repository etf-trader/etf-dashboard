"""
fair_value.py
=============
개별 종목의 적정가치(Fair Value) 범위 추정
  - 펀더멘털 방법: Graham Number, PE/PEG 배수법, 배당할인모형(DDM), 단순 2단계 DCF
  - 기술적 방법:   Bollinger Band, 이동평균(MA50/MA200) 회귀밴드, 52주 레인지, RSI 위치보정

입력:
  etf_analysis/stocks/{TICKER}.json   ← build_stock.py 산출물 (OHLCV + 지표 + fundamentals)
  etf_analysis/stocks/manifest.json   ← 섹터 중앙값 PE 계산용 (있으면 사용, 없으면 스킵)

출력:
  etf_analysis/fair_value/{TICKER}.json   ← 종목별 방법론별 추정치 + 종합 레인지
  etf_analysis/fair_value/summary.json    ← 전체 종목 요약 (현재가 대비 괴리율 포함)

사용법:
  python fair_value.py AAPL            # 단일 종목
  python fair_value.py --all           # manifest.json 전체 종목
"""

import yfinance as yf
import numpy as np
import json
import math
import os
import sys
import time
import datetime
import warnings

warnings.filterwarnings('ignore')

STOCKS_DIR = 'etf_analysis/stocks'
OUT_DIR = 'etf_analysis/fair_value'
os.makedirs(OUT_DIR, exist_ok=True)

# ─────────────────────────────────────────────
# 가정치 (필요시 조정)
# ─────────────────────────────────────────────
REQUIRED_RETURN = 0.09      # DDM/DCF 할인율 (요구수익률)
TERMINAL_GROWTH = 0.025     # DCF 영구성장률
DCF_HORIZON_YEARS = 5
DCF_GROWTH_TAPER = True     # 성장률을 영구성장률로 선형 수렴시킬지 여부
MAX_GROWTH_ASSUMPTION = 0.25  # 비정상적으로 높은 성장률 입력값 캡핑
MIN_GROWTH_ASSUMPTION = -0.10


def safe_float(x):
    try:
        f = float(x)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except Exception:
        return None


# ─────────────────────────────────────────────
# 1. 데이터 로딩
# ─────────────────────────────────────────────
def load_stock_json(ticker: str) -> dict:
    path = os.path.join(STOCKS_DIR, f'{ticker}.json')
    if not os.path.exists(path):
        raise FileNotFoundError(f'{path} 없음 — build_stock.py를 먼저 실행하세요')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def fetch_extra_fundamentals(ticker: str) -> dict:
    """build_stock.py의 fetch_fundamentals에 없는 밸류에이션 필수 필드 추가 조회."""
    out = {
        'trailingEps': None, 'forwardEps': None, 'bookValue': None,
        'freeCashflow': None, 'sharesOutstanding': None,
        'trailingPE': None, 'beta': None, 'sector': '',
    }
    info = None
    for attempt in range(2):
        try:
            info = yf.Ticker(ticker).info
            break
        except Exception:
            time.sleep(1)
    if not info:
        return out
    for k in out:
        if k == 'sector':
            out[k] = info.get('sector', '') or ''
        else:
            out[k] = safe_float(info.get(k))
    return out


def sector_median_forward_pe(sector: str) -> float:
    """manifest.json + 개별 종목 JSON에서 같은 섹터 forwardPE 중앙값 계산 (없으면 None)."""
    manifest_path = os.path.join(STOCKS_DIR, 'manifest.json')
    if not sector or not os.path.exists(manifest_path):
        return None
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    pes = []
    for row in manifest.get('stocks', []):
        if row.get('sector') != sector:
            continue
        try:
            with open(os.path.join(STOCKS_DIR, f"{row['ticker']}.json"), 'r', encoding='utf-8') as f2:
                pe = json.load(f2).get('fundamentals', {}).get('forwardPE')
            if pe and 0 < pe < 100:
                pes.append(pe)
        except Exception:
            continue
    return round(float(np.median(pes)), 2) if len(pes) >= 5 else None


# ─────────────────────────────────────────────
# 2. 펀더멘털 밸류에이션 방법론
# ─────────────────────────────────────────────
def method_graham_number(eps, bvps):
    """벤저민 그레이엄 공식: sqrt(22.5 * EPS * BVPS). 보수적 하한선 성격."""
    if eps is None or bvps is None or eps <= 0 or bvps <= 0:
        return None
    return round(math.sqrt(22.5 * eps * bvps), 2)


def method_pe_multiple(eps, current_pe, sector_pe):
    """
    현재 PE와 섹터 중앙값 PE 중 더 보수적인 값으로 밴드 산출.
    fair_low  = EPS * min(current_pe, sector_pe) * 0.85
    fair_high = EPS * max(current_pe, sector_pe) * 1.10
    """
    if eps is None or eps <= 0:
        return None
    candidates = [p for p in [current_pe, sector_pe] if p and p > 0]
    if not candidates:
        return None
    low_pe = min(candidates) * 0.85
    high_pe = max(candidates) * 1.10
    return {'low': round(eps * low_pe, 2), 'high': round(eps * high_pe, 2)}


def method_peg_fair_pe(eps, revenue_growth_pct):
    """PEG=1을 '적정'으로 가정: fair PE = 성장률(%). 고성장주에 왜곡되기 쉬워 캡을 둠."""
    if eps is None or eps <= 0 or revenue_growth_pct is None:
        return None
    g = max(min(revenue_growth_pct, 40), 1)  # 1%~40% 캡
    return round(eps * g, 2)


def method_ddm(dividend_per_share, growth_pct, required_return=REQUIRED_RETURN):
    """고든 성장모형: V = D1 / (r - g). 무배당 종목은 해당 없음."""
    if not dividend_per_share or dividend_per_share <= 0:
        return None
    g = 0.0 if growth_pct is None else growth_pct / 100
    g = max(min(g, required_return - 0.01), MIN_GROWTH_ASSUMPTION)
    d1 = dividend_per_share * (1 + g)
    return round(d1 / (required_return - g), 2)


def method_simple_dcf(fcf_per_share, growth_pct, required_return=REQUIRED_RETURN,
                       terminal_growth=TERMINAL_GROWTH, years=DCF_HORIZON_YEARS):
    """
    2단계 FCF 할인모형 (주당 FCF 기준, 단순화 버전):
      - 향후 `years`년간 성장률을 영구성장률로 선형 수렴(taper)
      - 이후 고든 성장모형으로 터미널 가치 계산
    프리미엄/할인 요인(무형자산, 부채구조 등)은 반영하지 않은 1차 근사치.
    """
    if not fcf_per_share or fcf_per_share <= 0 or growth_pct is None:
        return None
    g0 = max(min(growth_pct / 100, MAX_GROWTH_ASSUMPTION), MIN_GROWTH_ASSUMPTION)

    pv_sum = 0.0
    fcf = fcf_per_share
    for yr in range(1, years + 1):
        g = g0 + (terminal_growth - g0) * (yr / years) if DCF_GROWTH_TAPER else g0
        fcf = fcf * (1 + g)
        pv_sum += fcf / ((1 + required_return) ** yr)

    terminal_value = fcf * (1 + terminal_growth) / (required_return - terminal_growth)
    pv_terminal = terminal_value / ((1 + required_return) ** years)
    return round(pv_sum + pv_terminal, 2)


# ─────────────────────────────────────────────
# 3. 기술적 밸류에이션 방법론
# ─────────────────────────────────────────────
def method_bollinger_range(data: dict):
    """최근 Bollinger Band 상/하단 = 단기 평균회귀 관점의 거래 레인지."""
    lo = next((v for v in reversed(data['bb_lower']) if v is not None), None)
    hi = next((v for v in reversed(data['bb_upper']) if v is not None), None)
    if lo is None or hi is None:
        return None
    return {'low': lo, 'high': hi}


def method_ma_regression_band(data: dict):
    """
    MA50/MA200 중 낮은 값~높은 값을 추세 기준 중심선으로 보고,
    최근 60일 종가의 표준편차만큼 위아래로 밴드를 둔 회귀 레인지.
    """
    ma50 = next((v for v in reversed(data['ma50']) if v is not None), None)
    ma200 = next((v for v in reversed(data['ma200']) if v is not None), None)
    closes = [c for c in data['close'][-60:] if c is not None]
    if ma50 is None or ma200 is None or len(closes) < 20:
        return None
    center_low, center_high = sorted([ma50, ma200])
    std = float(np.std(closes))
    return {'low': round(center_low - std, 2), 'high': round(center_high + std, 2)}


def method_52w_range(data: dict):
    """52주 종가 고점/저점. 극단치이므로 다른 방법과 함께 참고용으로만 사용."""
    closes = [c for c in data['close'][-252:] if c is not None]
    if len(closes) < 60:
        return None
    return {'low': round(min(closes), 2), 'high': round(max(closes), 2)}


def rsi_position_note(data: dict):
    """최근 RSI로 현재가가 기술적 밴드 내 어디쯤인지 짧은 코멘트 생성."""
    rsi = next((v for v in reversed(data['rsi']) if v is not None), None)
    if rsi is None:
        return None
    if rsi >= 70:
        return f'RSI {rsi:.1f} — 단기 과매수권'
    if rsi <= 30:
        return f'RSI {rsi:.1f} — 단기 과매도권'
    return f'RSI {rsi:.1f} — 중립'


# ─────────────────────────────────────────────
# 4. 종합
# ─────────────────────────────────────────────
# 방법론별 가중치: 펀더멘털은 중장기 적정가, 기술적은 단기 트레이딩 레인지 성격이라
# 서로 다른 정보를 담고 있으므로 단순 평균 대신 가중 결합
WEIGHTS = {
    'graham': 1.0,
    'pe_multiple': 1.5,
    'peg': 1.0,
    'ddm': 1.0,
    'dcf': 1.5,
    'bollinger': 0.7,
    'ma_band': 1.0,
    '52w_range': 0.5,
}


def build_fair_value(ticker: str) -> dict:
    stock = load_stock_json(ticker)
    data = stock['data']
    fnd = stock['fundamentals']
    last_close = stock['last_close']
    extra = fetch_extra_fundamentals(ticker)
    sector_pe = sector_median_forward_pe(stock.get('sector', ''))

    eps = extra['forwardEps'] or extra['trailingEps']
    bvps = extra['bookValue']
    fcf_ps = (extra['freeCashflow'] / extra['sharesOutstanding']
              if extra['freeCashflow'] and extra['sharesOutstanding'] else None)
    dps = (last_close * (fnd.get('dividendYield') or 0) / 100) if fnd.get('dividendYield') else None
    growth_pct = fnd.get('revenueGrowth')
    current_pe = fnd.get('forwardPE') or extra['trailingPE']

    methods = {}
    graham = method_graham_number(eps, bvps)
    if graham:
        methods['graham'] = {'low': graham, 'high': graham}
    pe_band = method_pe_multiple(eps, current_pe, sector_pe)
    if pe_band:
        methods['pe_multiple'] = pe_band
    peg = method_peg_fair_pe(eps, growth_pct)
    if peg:
        methods['peg'] = {'low': round(peg * 0.9, 2), 'high': round(peg * 1.1, 2)}
    ddm = method_ddm(dps, growth_pct)
    if ddm:
        methods['ddm'] = {'low': round(ddm * 0.9, 2), 'high': round(ddm * 1.1, 2)}
    dcf = method_simple_dcf(fcf_ps, growth_pct)
    if dcf:
        methods['dcf'] = {'low': round(dcf * 0.85, 2), 'high': round(dcf * 1.15, 2)}
    boll = method_bollinger_range(data)
    if boll:
        methods['bollinger'] = boll
    ma_band = method_ma_regression_band(data)
    if ma_band:
        methods['ma_band'] = ma_band
    w52 = method_52w_range(data)
    if w52:
        methods['52w_range'] = w52

    if not methods:
        raise ValueError('사용 가능한 밸류에이션 방법이 하나도 없음 (데이터 부족)')

    lows, highs, weights = [], [], []
    for name, band in methods.items():
        lows.append(band['low'])
        highs.append(band['high'])
        weights.append(WEIGHTS.get(name, 1.0))

    weighted_low = float(np.average(lows, weights=weights))
    weighted_high = float(np.average(highs, weights=weights))
    if weighted_low > weighted_high:
        weighted_low, weighted_high = weighted_high, weighted_low
    midpoint = (weighted_low + weighted_high) / 2
    upside_to_mid = round((midpoint / last_close - 1) * 100, 2)

    if last_close < weighted_low:
        verdict = 'undervalued'
    elif last_close > weighted_high:
        verdict = 'overvalued'
    else:
        verdict = 'fair'

    result = {
        'ticker': ticker,
        'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'last_close': last_close,
        'fair_value_range': {'low': round(weighted_low, 2), 'high': round(weighted_high, 2)},
        'midpoint': round(midpoint, 2),
        'upside_to_midpoint_pct': upside_to_mid,
        'verdict': verdict,
        'rsi_note': rsi_position_note(data),
        'methods': methods,
        'inputs_used': {
            'eps': eps, 'bvps': bvps, 'fcf_per_share': round(fcf_ps, 2) if fcf_ps else None,
            'dividend_per_share': round(dps, 2) if dps else None,
            'revenue_growth_pct': growth_pct, 'current_pe': current_pe,
            'sector_median_pe': sector_pe,
        },
    }

    with open(os.path.join(OUT_DIR, f'{ticker}.json'), 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


# ─────────────────────────────────────────────
# 5. 실행부
# ─────────────────────────────────────────────
def run_all():
    manifest_path = os.path.join(STOCKS_DIR, 'manifest.json')
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    summary = []
    for row in manifest.get('stocks', []):
        ticker = row['ticker']
        try:
            r = build_fair_value(ticker)
            summary.append({
                'ticker': ticker, 'last_close': r['last_close'],
                'fair_value_range': r['fair_value_range'],
                'upside_to_midpoint_pct': r['upside_to_midpoint_pct'],
                'verdict': r['verdict'],
            })
            print(f"  ✓ {ticker}: {r['verdict']} (range {r['fair_value_range']['low']}–{r['fair_value_range']['high']}, close {r['last_close']})")
        except Exception as e:
            print(f"  ✗ {ticker}: {e}")
        time.sleep(0.1)

    with open(os.path.join(OUT_DIR, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump({
            'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
            'count': len(summary),
            'stocks': summary,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 완료: {len(summary)}개 종목 fair value 계산")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('사용법: python fair_value.py TICKER  또는  python fair_value.py --all')
        sys.exit(1)

    if sys.argv[1] == '--all':
        run_all()
    else:
        ticker = sys.argv[1].upper()
        r = build_fair_value(ticker)
        print(json.dumps(r, ensure_ascii=False, indent=2))
