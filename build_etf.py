"""
build_etf.py  (구 thematic_etf_weekly.py)
==========================================
전제: thematic_etf_scraper.py 실행 후 etf_analysis/thematic_etfs_above_3b_v2.csv 존재
출력: etf_analysis/index.html  (ETF + Stock 탭 통합)
"""

import yfinance as yf
import pandas as pd
import json
import math
import time
import warnings
warnings.filterwarnings('ignore')

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
TOP_N       = 10
DAILY_DAYS  = '3mo'
WEEKLY_DAYS = '1y'

SECTOR_TICKERS = ['SPY', 'XLB', 'XLC', 'XLE', 'XLF', 'XLI', 'XLK', 'XLP', 'XLU', 'XLV', 'XLY', 'XLRE']
RRG_TICKERS    = ['XLB', 'XLC', 'XLE', 'XLF', 'XLI', 'XLK', 'XLP', 'XLU', 'XLV', 'XLY', 'XLRE']
RRG_BENCHMARK  = '^SPX'
RRG_DAYS       = 360
RRG_PERIOD     = 5
RRG_WINDOW     = 14
RRG_TAIL       = 7
SECTOR_NAMES = {
    'SPY':  'S&P 500', 'XLB': 'Materials',       'XLC': 'Comm. Services',
    'XLE':  'Energy',  'XLF': 'Financials',       'XLI': 'Industrials',
    'XLK':  'Technology','XLP':'Consumer Staples', 'XLU': 'Utilities',
    'XLV':  'Health Care','XLY':'Consumer Discr.', 'XLRE':'Real Estate',
}

# ─────────────────────────────────────────────
# 1. 티커 리스트 로드
# ─────────────────────────────────────────────
df = pd.read_csv('etf_analysis/thematic_etfs_above_3b_v2.csv')
tickers  = df['symbol'].tolist()
name_map = df.set_index('symbol')['name'].to_dict()
theme_map= df.set_index('symbol')['theme'].to_dict()
print(f"총 {len(tickers)}개 티커 로드")

# ─────────────────────────────────────────────
# 2. 수익률 계산
# ─────────────────────────────────────────────
print("수익률 다운로드 중...")
ret_1d = {}; ret_1w = {}
date_1d_from = date_1d_to = date_1w_from = date_1w_to = None

for i, sym in enumerate(tickers):
    print(f"  [{i+1}/{len(tickers)}] {sym}", end='\r')
    for attempt in range(3):
        try:
            hist = yf.Ticker(sym).history(period='1mo', interval='1d', auto_adjust=True)
            if hist is None or len(hist) < 2: break
            hist = hist.dropna(subset=['Close'])
            if len(hist) < 2: break
            r1d = (hist['Close'].iloc[-1] / hist['Close'].iloc[-2] - 1) * 100
            if not math.isnan(float(r1d)):
                ret_1d[sym] = round(float(r1d), 2)
            if date_1d_from is None:
                date_1d_from = hist.index[-2].strftime('%Y-%m-%d')
                date_1d_to   = hist.index[-1].strftime('%Y-%m-%d')
            hist_week = hist.tail(5)
            r1w = (hist_week['Close'].iloc[-1] / hist_week['Close'].iloc[0] - 1) * 100
            if not math.isnan(float(r1w)):
                ret_1w[sym] = round(float(r1w), 2)
            if date_1w_from is None:
                date_1w_from = hist_week.index[0].strftime('%Y-%m-%d')
                date_1w_to   = hist_week.index[-1].strftime('%Y-%m-%d')
            break
        except Exception:
            time.sleep(1)

ret_1d_series = pd.Series(ret_1d).sort_values()
ret_1w_series = pd.Series(ret_1w).sort_values()
print(f"\n  1D: {len(ret_1d_series)}개 / 1W: {len(ret_1w_series)}개 완료")

winners_1d = ret_1d_series.nlargest(TOP_N).sort_values()
losers_1d  = ret_1d_series.nsmallest(TOP_N).sort_values(ascending=False)
winners_1w = ret_1w_series.nlargest(TOP_N).sort_values()
losers_1w  = ret_1w_series.nsmallest(TOP_N).sort_values(ascending=False)

# ─────────────────────────────────────────────
# 2-1. 섹터 수익률
# ─────────────────────────────────────────────
print("섹터 수익률 다운로드 중...")
sector_1d = {}; sector_1w = {}
for sym in SECTOR_TICKERS:
    for attempt in range(3):
        try:
            hist = yf.Ticker(sym).history(period='1mo', interval='1d', auto_adjust=True)
            if hist is None or len(hist) < 2: break
            hist = hist.dropna(subset=['Close'])
            if len(hist) < 2: break
            r1d = (hist['Close'].iloc[-1] / hist['Close'].iloc[-2] - 1) * 100
            if not math.isnan(float(r1d)): sector_1d[sym] = round(float(r1d), 2)
            hist_week = hist.tail(5)
            r1w = (hist_week['Close'].iloc[-1] / hist_week['Close'].iloc[0] - 1) * 100
            if not math.isnan(float(r1w)): sector_1w[sym] = round(float(r1w), 2)
            break
        except Exception:
            time.sleep(1)
print(f"  섹터 완료: {len(sector_1d)}개")
ret_1d.update(sector_1d)
ret_1w.update(sector_1w)

# ─────────────────────────────────────────────
# 2-2. RRG 데이터
# ─────────────────────────────────────────────
import datetime
print("RRG 데이터 계산 중...")
from_date = datetime.datetime.today() - datetime.timedelta(days=RRG_DAYS)
to_date   = datetime.datetime.today()

rrg_price     = yf.download(RRG_TICKERS,   start=from_date, end=to_date, auto_adjust=True, progress=False)['Close']
rrg_benchmark = yf.download(RRG_BENCHMARK, start=from_date, end=to_date, auto_adjust=True, progress=False)['Close']
common_idx    = rrg_price.index.intersection(rrg_benchmark.index)
rrg_price     = rrg_price.loc[common_idx]
rrg_benchmark = rrg_benchmark.loc[common_idx]
if isinstance(rrg_benchmark, pd.DataFrame):
    rrg_benchmark = rrg_benchmark.iloc[:, 0]

def resample_by_period(df, p):
    rev = df.iloc[::-1]
    return rev.iloc[::p].iloc[::-1]

rp = resample_by_period(rrg_price, RRG_PERIOD)
rb = resample_by_period(rrg_benchmark, RRG_PERIOD)
if isinstance(rp, pd.Series): rp = rp.to_frame()

rrg_traces = []
for ticker in RRG_TICKERS:
    if ticker not in rp.columns: continue
    rs  = 100 * (rp[ticker] / rb)
    rsr = (100 + (rs - rs.rolling(window=RRG_WINDOW).mean()) /
           rs.rolling(window=RRG_WINDOW).std(ddof=0)).dropna()
    if len(rsr) < 2: continue
    rsr_roc = 100 * ((rsr / rsr.iloc[0]) - 1)
    rsm = (101 + ((rsr_roc - rsr_roc.rolling(window=RRG_WINDOW).mean()) /
                  rsr_roc.rolling(window=RRG_WINDOW).std(ddof=0))).dropna()
    if len(rsm) < 2: continue
    rsr = rsr[rsr.index.isin(rsm.index)].iloc[-RRG_TAIL:]
    rsm = rsm[rsm.index.isin(rsr.index)].iloc[-RRG_TAIL:]
    if len(rsr) == 0: continue
    def get_quadrant(x, y):
        if x>=100 and y>=100: return 'leading'
        if x<100  and y>=100: return 'improving'
        if x>=100 and y<100:  return 'weakening'
        return 'lagging'
    color_map = {'leading':'#00c48c','improving':'#3a6fff','weakening':'#f5a623','lagging':'#ff4d6d'}
    quad  = get_quadrant(float(rsr.iloc[-1]), float(rsm.iloc[-1]))
    rrg_traces.append({
        'ticker': ticker, 'name': SECTOR_NAMES.get(ticker, ticker),
        'x': [round(v,4) for v in rsr.tolist()],
        'y': [round(v,4) for v in rsm.tolist()],
        'dates': rsr.index.strftime('%Y-%m-%d').tolist(),
        'color': color_map[quad], 'quad': quad,
    })
print(f"  RRG 완료: {len(rrg_traces)}개")

# ─────────────────────────────────────────────
# 2-3. 섹터 내 종목 Winner/Loser (SSGA holdings 기반, 1D + 1W)
# ─────────────────────────────────────────────
import requests
from io import BytesIO

print("섹터 내 종목 Winner/Loser 계산 중...")
sector_stocks = {}

for etf in RRG_TICKERS:  # XLB~XLRE (SPY 제외)
    try:
        holdings_url = f"https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{etf.lower()}.xlsx"
        r = requests.get(holdings_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)

        if r.status_code != 200:
            print(f"  [{etf}] holdings 다운로드 실패: HTTP {r.status_code}")
            continue

        h = pd.read_excel(BytesIO(r.content), skiprows=4)

        if 'Local Currency' in h.columns:
            h = h[h['Local Currency'] == 'USD']
        if 'SEDOL' in h.columns:
            h = h[h['SEDOL'] != '-']

        ticker_col = next((c for c in h.columns if 'ticker' in str(c).lower() or 'symbol' in str(c).lower()), None)
        name_col   = next((c for c in h.columns if 'name' in str(c).lower()), None)

        if not ticker_col or not name_col:
            print(f"  [{etf}] 컬럼 찾기 실패: {h.columns.tolist()}")
            continue

        h = h[[ticker_col, name_col]].dropna()
        h.columns = ['Ticker', 'Name']
        h['Ticker'] = h['Ticker'].astype(str).str.strip().str.replace('.', '-', regex=False)
        h = h[h['Ticker'] != '']

        ticker_list = h['Ticker'].tolist()
        print(f"  [{etf}] {len(ticker_list)}개 종목 데이터 벌크 다운로드 중...")
        
        # 벌크 다운로드로 야후 차단 회피
        group_hist = yf.download(ticker_list, period='1mo', interval='1d', auto_adjust=True, group_by_ticker=True, progress=False)

        stock_returns = []
        name_dict = h.set_index('Ticker')['Name'].to_dict()

        for ticker in ticker_list:
            try:
                if ticker not in group_hist.columns.levels[0]: continue
                hist = group_hist[ticker].dropna(subset=['Close'])
                if len(hist) < 2: continue

                # 임시 변수에 먼저 계산 (에러 시 전역 딕셔너리 오염 방지)
                val_1d = (hist['Close'].iloc[-1] / hist['Close'].iloc[-2] - 1) * 100
                
                hist_week = hist.tail(5)
                if len(hist_week) < 2: continue
                val_1w = (hist_week['Close'].iloc[-1] / hist_week['Close'].iloc[0] - 1) * 100

                if math.isnan(float(val_1d)) or math.isnan(float(val_1w)): continue

                # ✨ 1D, 1W 둘 다 안전하게 계산 완료된 청정 데이터만 최종 반영
                calc_1d = round(float(val_1d), 2)
                calc_1w = round(float(val_1w), 2)

                ret_1d[ticker] = calc_1d
                ret_1w[ticker] = calc_1w

                stock_returns.append({
                    'ticker': ticker,
                    'name':   name_dict.get(ticker, ticker),
                    'ret_1d': calc_1d,
                    'ret_1w': calc_1w,
                })
            except Exception:
                continue

        # 야후 서버 차단 방지를 위한 휴식
        time.sleep(1)

        if not stock_returns:
            print(f"  [{etf}] 종목 수익률 없음")
            continue

        stock_df = pd.DataFrame(stock_returns)

        top10_1d    = stock_df.sort_values('ret_1d', ascending=False).head(TOP_N)[['ticker','name','ret_1d']].rename(columns={'ret_1d':'ret'}).to_dict('records')
        bottom10_1d = stock_df.sort_values('ret_1d', ascending=True).head(TOP_N)[['ticker','name','ret_1d']].rename(columns={'ret_1d':'ret'}).to_dict('records')
        top10_1w    = stock_df.sort_values('ret_1w', ascending=False).head(TOP_N)[['ticker','name','ret_1w']].rename(columns={'ret_1w':'ret'}).to_dict('records')
        bottom10_1w = stock_df.sort_values('ret_1w', ascending=True).head(TOP_N)[['ticker','name','ret_1w']].rename(columns={'ret_1w':'ret'}).to_dict('records')

        sector_stocks[etf] = {
            'top_1d':    top10_1d,
            'bottom_1d': bottom10_1d,
            'top_1w':    top10_1w,
            'bottom_1w': bottom10_1w,
        }
        print(f"  [{etf}] {len(stock_returns)}개 종목 처리 완료")

    except Exception as e:
        print(f"  [{etf}] 처리 중 에러: {e}")
        continue

print(f"  섹터 종목 Winner/Loser 완료: {len(sector_stocks)}개 섹터")



# ─────────────────────────────────────────────
# 3. 팝업 상세 데이터
# ─────────────────────────────────────────────
all_syms = list(dict.fromkeys(
    winners_1d.index.tolist() + losers_1d.index.tolist() +
    winners_1w.index.tolist() + losers_1w.index.tolist() + SECTOR_TICKERS
))
detail = {}
print(f"상세 데이터 수집 중 ({len(all_syms)}개)...")
for i, sym in enumerate(all_syms):
    print(f"  [{i+1}/{len(all_syms)}] {sym}", end='\r')
    try:
        t = yf.Ticker(sym)
        daily  = t.history(period=DAILY_DAYS,  interval='1d',  auto_adjust=True)
        weekly = t.history(period=WEEKLY_DAYS, interval='1wk', auto_adjust=True)
        holdings = []
        try:
            h = t.funds_data.top_holdings
            if h is not None and not h.empty:
                for _, row in h.head(10).iterrows():
                    holdings.append({'symbol':row.get('Symbol',''),
                                     'name':row.get('Name', str(row.name)),
                                     'weight':round(float(row.get('Holding Percent',0))*100,2)})
        except Exception:
            pass
        info = t.info
        
        # 이제 전역 ret_1d와 ret_1w가 완벽하게 딕셔너리로 유지되므로 안심하고 원래 코드 그대로 사용 가능합니다.
        detail[sym] = {
            'name':    name_map.get(sym, sym),
            'theme':   theme_map.get(sym, ''),
            'aum':     info.get('totalAssets'),
            'expense': info.get('annualReportExpenseRatio'),
            'ret_1d':  ret_1d.get(sym, 0),
            'ret_1w':  ret_1w.get(sym, 0),
            'daily':   {'dates': daily.index.strftime('%Y-%m-%d').tolist(),
                        'close': [round(v,2) for v in daily['Close'].tolist()],
                        'volume':[int(v) for v in daily['Volume'].tolist()]},
            'weekly':  {'dates': weekly.index.strftime('%Y-%m-%d').tolist(),
                        'close': [round(v,2) for v in weekly['Close'].tolist()],
                        'volume':[int(v) for v in weekly['Volume'].tolist()]},
            'holdings': holdings,
        }
    except Exception as e:
        print(f"\n  ⚠ {sym} 실패: {e}")
        detail[sym] = {'name':name_map.get(sym,sym),'theme':theme_map.get(sym,''),
                       'ret_1d':ret_1d.get(sym,0),'ret_1w':ret_1w.get(sym,0),
                       'daily':{},'weekly':{},'holdings':[]}
print(f"\n상세 수집 완료")

# ─────────────────────────────────────────────
# 4. 직렬화
# ─────────────────────────────────────────────
def make_bar_data(series):
    return {'symbols': series.index.tolist(),
            'names':   [name_map.get(s,s) for s in series.index],
            'returns': [round(float(v),2) for v in series.values]}
def make_sector_data(ret_map):
    syms = list(ret_map.keys())
    return {'symbols': syms,
            'names':   [SECTOR_NAMES.get(s,s) for s in syms],
            'returns': [ret_map[s] for s in syms]}

payload = {
    'date_1d':    f'{date_1d_from} ~ {date_1d_to}',
    'date_1w':    f'{date_1w_from} ~ {date_1w_to}',
    'winners_1d': make_bar_data(winners_1d),
    'losers_1d':  make_bar_data(losers_1d),
    'winners_1w': make_bar_data(winners_1w),
    'losers_1w':  make_bar_data(losers_1w),
    'sector_1d':  make_sector_data(sector_1d),
    'sector_1w':  make_sector_data(sector_1w),
    'detail':     detail,
    'rrg':        rrg_traces,
    'sector_stocks': sector_stocks,
}

def sanitize(obj):
    if isinstance(obj, dict):   return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):   return [sanitize(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)): return 0
    return obj

payload    = sanitize(payload)
data_json  = json.dumps(payload, ensure_ascii=False)

# ─────────────────────────────────────────────
# 5. index.html 생성 (템플릿에 데이터 embed)
# ─────────────────────────────────────────────
with open('etf_analysis/index.template.html', 'r', encoding='utf-8') as f:
    html = f.read()

html = html.replace('__ETF_DATA_PLACEHOLDER__', data_json)

with open('etf_analysis/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print("✓ etf_analysis/index.html 저장 완료")
