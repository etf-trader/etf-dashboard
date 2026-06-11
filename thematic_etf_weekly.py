import ssl
ssl._create_default_https_context = ssl._create_unverified_context

"""
테마 ETF 주간 수익률 대시보드 생성기
======================================
전제: thematic_etf_scraper.py 실행 후 etf_analysis/thematic_etfs_top1.csv 존재
출력: etf_analysis/index.html  ← 브라우저에서 바로 열기 (서버 불필요)

팝업 콘텐츠:
  - 탭1: 일봉/주봉 가격 차트 (Plotly)
  - 탭2: 보유 종목 Top 10 (yfinance)
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

# ─────────────────────────────────────────────
# 1. 티커 리스트 로드
# ─────────────────────────────────────────────
df = pd.read_csv('etf_analysis/thematic_etfs_top1.csv')
tickers   = df['symbol'].tolist()
name_map  = df.set_index('symbol')['name'].to_dict()
theme_map = df.set_index('symbol')['theme'].to_dict()
print(f"총 {len(tickers)}개 티커 로드")

# ─────────────────────────────────────────────
# 2. 주간 수익률 계산 (티커별 개별 다운로드)
# ─────────────────────────────────────────────
print("주간 수익률 다운로드 중...")

ret_map   = {}
date_from = None
date_to   = None

for i, sym in enumerate(tickers):
    print(f"  [{i+1}/{len(tickers)}] {sym}", end='\r')
    for attempt in range(3):
        try:
            hist = yf.Ticker(sym).history(period='1mo', interval='1d', auto_adjust=True)
            if hist is None or len(hist) < 2:
                break
            hist = hist.dropna(subset=['Close'])
            if len(hist) < 2:
                break
            # 최근 5 거래일 기준 수익률 계산
            hist_week = hist.tail(5)
            ret = (hist_week['Close'].iloc[-1] / hist_week['Close'].iloc[0] - 1) * 100
            if not math.isnan(float(ret)):
                ret_map[sym] = round(float(ret), 2)
            if date_from is None:
                date_from = hist_week.index[0].strftime('%Y-%m-%d')
                date_to   = hist_week.index[-1].strftime('%Y-%m-%d')
            break
        except Exception:
            time.sleep(1)

weekly_ret = pd.Series(ret_map).sort_values()
print(f"\n  {date_from} ~ {date_to}, {len(weekly_ret)}개 수익률 계산 완료")

winners = weekly_ret.nlargest(TOP_N).sort_values()
losers  = weekly_ret.nsmallest(TOP_N).sort_values(ascending=False)

# ─────────────────────────────────────────────
# 3. 팝업용 상세 데이터 수집 (winners + losers 전체)
# ─────────────────────────────────────────────
all_syms = list(dict.fromkeys(winners.index.tolist() + losers.index.tolist()))
detail   = {}

print(f"상세 데이터 수집 중 ({len(all_syms)}개)...")
for i, sym in enumerate(all_syms):
    print(f"  [{i+1}/{len(all_syms)}] {sym}", end='\r')
    try:
        t = yf.Ticker(sym)

        # 일봉
        daily = t.history(period=DAILY_DAYS, interval='1d', auto_adjust=True)
        daily_data = {
            'dates':  daily.index.strftime('%Y-%m-%d').tolist(),
            'close':  [round(v, 2) for v in daily['Close'].tolist()],
            'volume': [int(v) for v in daily['Volume'].tolist()],
        }

        # 주봉
        weekly = t.history(period=WEEKLY_DAYS, interval='1wk', auto_adjust=True)
        weekly_data = {
            'dates':  weekly.index.strftime('%Y-%m-%d').tolist(),
            'close':  [round(v, 2) for v in weekly['Close'].tolist()],
            'volume': [int(v) for v in weekly['Volume'].tolist()],
        }

        # 보유 종목 Top 10
        holdings = []
        try:
            h = t.funds_data.top_holdings
            if h is not None and not h.empty:
                for _, row in h.head(10).iterrows():
                    holdings.append({
                        'symbol': row.get('Symbol', ''),
                        'name':   row.get('Name', str(row.name)),
                        'weight': round(float(row.get('Holding Percent', 0)) * 100, 2),
                    })
        except Exception:
            pass

        # 기본 정보
        info = t.info
        detail[sym] = {
            'name':       name_map.get(sym, sym),
            'theme':      theme_map.get(sym, ''),
            'aum':        info.get('totalAssets'),
            'expense':    info.get('annualReportExpenseRatio'),
            'weekly_ret': ret_map.get(sym, 0),
            'daily':      daily_data,
            'weekly':     weekly_data,
            'holdings':   holdings,
        }
    except Exception as e:
        print(f"\n  ⚠ {sym} 실패: {e}")
        detail[sym] = {
            'name':       name_map.get(sym, sym),
            'theme':      theme_map.get(sym, ''),
            'weekly_ret': ret_map.get(sym, 0),
            'daily': {}, 'weekly': {}, 'holdings': [],
        }

print(f"\n상세 데이터 수집 완료")

# ─────────────────────────────────────────────
# 4. 차트 데이터 직렬화
# ─────────────────────────────────────────────
def make_bar_data(series):
    return {
        'symbols': series.index.tolist(),
        'names':   [name_map.get(s, s) for s in series.index],
        'returns': [round(float(v), 2) for v in series.values],
    }

payload = {
    'date_range': f'{date_from} ~ {date_to}',
    'winners':    make_bar_data(winners),
    'losers':     make_bar_data(losers),
    'detail':     detail,
}

# None은 null로, NaN/Inf는 0으로 변환
def sanitize(obj):
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize(v) for v in obj]
    elif isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return 0
    return obj

payload = sanitize(payload)
data_json = json.dumps(payload, ensure_ascii=False)

# ─────────────────────────────────────────────
# 5. index.html 생성 (데이터 embed)
# ─────────────────────────────────────────────
html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Thematic ETF Weekly Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.30.0.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f1117; color: #e0e0e0; font-family: -apple-system, 'Segoe UI', sans-serif; }}

  header {{ padding: 24px 32px 12px; border-bottom: 1px solid #222; }}
  header h1 {{ font-size: 1.3rem; font-weight: 600; color: #fff; }}
  header span {{ font-size: 0.85rem; color: #888; margin-left: 12px; }}

  .charts-wrap {{ display: flex; gap: 16px; padding: 20px 24px; }}
  @media (max-width: 768px) {{
    .charts-wrap {{ flex-direction: column; }}
    .chart-box h2 {{ font-size: 0.88rem; }}
    header h1 {{ font-size: 1.05rem; }}
  }}
  .chart-box {{ flex: 1; background: #16181f; border-radius: 10px; padding: 16px; }}
  .chart-box h2 {{ font-size: 0.95rem; font-weight: 600; margin-bottom: 10px; }}
  .chart-box h2.winner {{ color: #00c48c; }}
  .chart-box h2.loser  {{ color: #ff4d6d; }}

  .overlay {{
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,.65); z-index: 100;
    align-items: center; justify-content: center;
  }}
  .overlay.open {{ display: flex; }}
  .modal {{
    background: #1a1d27; border-radius: 14px;
    width: min(860px, 94vw); max-height: 88vh;
    overflow: hidden; display: flex; flex-direction: column;
    box-shadow: 0 8px 40px rgba(0,0,0,.6);
  }}
  .modal-header {{
    padding: 18px 22px 14px; border-bottom: 1px solid #2a2d3a;
    display: flex; justify-content: space-between; align-items: flex-start;
  }}
  .modal-header .meta h3 {{ font-size: 1rem; color: #fff; }}
  .modal-header .meta p  {{ font-size: 0.8rem; color: #888; margin-top: 4px; }}
  .modal-header .ret {{
    font-size: 1.4rem; font-weight: 700; white-space: nowrap; margin-left: 16px;
  }}
  .modal-header .ret.pos {{ color: #00c48c; }}
  .modal-header .ret.neg {{ color: #ff4d6d; }}
  .close-btn {{
    background: none; border: none; color: #888; font-size: 1.4rem;
    cursor: pointer; padding: 0 4px; line-height: 1;
  }}
  .close-btn:hover {{ color: #fff; }}

  .tabs {{ display: flex; gap: 4px; padding: 12px 22px 0; border-bottom: 1px solid #2a2d3a; }}
  .tab {{
    padding: 7px 18px; border-radius: 6px 6px 0 0; font-size: 0.85rem;
    cursor: pointer; color: #888; border: 1px solid transparent;
    border-bottom: none; background: none;
  }}
  .tab.active {{ color: #fff; background: #23263a; border-color: #2a2d3a; }}
  .tab-content {{ display: none; flex: 1; overflow-y: auto; padding: 16px 22px; }}
  .tab-content.active {{ display: block; }}

  .holdings-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  .holdings-table th {{
    text-align: left; color: #888; font-weight: 500;
    padding: 6px 10px; border-bottom: 1px solid #2a2d3a;
  }}
  .holdings-table td {{ padding: 8px 10px; border-bottom: 1px solid #1e2030; color: #ccc; }}
  .holdings-table tr:last-child td {{ border-bottom: none; }}
  .weight-bar {{
    display: inline-block; height: 6px; background: #3a6fff;
    border-radius: 3px; margin-left: 8px; vertical-align: middle;
  }}
  .no-data {{ color: #555; font-size: 0.85rem; padding: 20px 0; }}

  .chips {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px; }}
  .chip {{
    background: #23263a; border-radius: 6px; padding: 4px 10px;
    font-size: 0.78rem; color: #aaa;
  }}
  .chip span {{ color: #ddd; font-weight: 600; }}
</style>
</head>
<body>

<header>
  <h1>📊 Thematic ETF Weekly Dashboard</h1>
  <span id="dateRange"></span>
</header>

<div class="charts-wrap">
  <div class="chart-box">
    <h2 class="winner">📈 Weekly Winners — Top {TOP_N}</h2>
    <div id="chartWinner"></div>
  </div>
  <div class="chart-box">
    <h2 class="loser">📉 Weekly Losers — Bottom {TOP_N}</h2>
    <div id="chartLoser"></div>
  </div>
</div>

<div class="overlay" id="overlay" onclick="closeModal(event)">
  <div class="modal" onclick="event.stopPropagation()">
    <div class="modal-header">
      <div class="meta">
        <h3 id="popSymbol"></h3>
        <p id="popName"></p>
        <div class="chips">
          <div class="chip">Theme <span id="popTheme"></span></div>
          <div class="chip">AUM <span id="popAum"></span></div>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:12px">
        <div class="ret" id="popRet"></div>
        <button class="close-btn" onclick="closeModal()">✕</button>
      </div>
    </div>

    <div class="tabs">
      <button class="tab active" onclick="switchTab(0)">Price Chart</button>
      <button class="tab"        onclick="switchTab(1)">Top Holdings</button>
    </div>

    <div class="tab-content active" id="tab0">
      <div style="display:flex;gap:8px;margin-bottom:10px">
        <button onclick="renderChart('daily')"  id="btnDaily"
          style="padding:4px 14px;border-radius:5px;border:1px solid #3a6fff;
                 background:#3a6fff;color:#fff;cursor:pointer;font-size:0.82rem">Daily</button>
        <button onclick="renderChart('weekly')" id="btnWeekly"
          style="padding:4px 14px;border-radius:5px;border:1px solid #444;
                 background:none;color:#aaa;cursor:pointer;font-size:0.82rem">Weekly</button>
      </div>
      <div id="priceChart" style="height:340px"></div>
    </div>

    <div class="tab-content" id="tab1">
      <div id="holdingsWrap"></div>
    </div>
  </div>
</div>

<script>
const DATA = {data_json};
let currentSym  = null;
let currentMode = 'daily';

document.getElementById('dateRange').textContent = DATA.date_range;

const baseLayout = {{
  paper_bgcolor: 'transparent', plot_bgcolor: '#0f1117',
  margin: {{ t: 4, b: 36, l: 60, r: 16 }},
  font:  {{ color: '#aaa', size: 11 }},
  xaxis: {{ gridcolor: '#1e2030', showgrid: true }},
  yaxis: {{ gridcolor: '#1e2030', showgrid: true }},
}};
const cfg = {{ displayModeBar: false, responsive: true }};

function renderBar(divId, data) {{
  const labels = data.symbols.map((s, i) => {{
    const n = data.names[i] || s;
    const short = n.length > 30 ? n.slice(0, 30) + '…' : n;
    return short + '  (' + s + ')';
  }});

  Plotly.newPlot(divId, [{{
    type: 'bar', orientation: 'h',
    x: data.returns, y: labels,
    marker: {{ color: data.returns.map(v => v >= 0 ? '#00c48c' : '#ff4d6d') }},
    text: data.returns.map(v => (v >= 0 ? '+' : '') + v.toFixed(2) + '%'),
    textposition: 'outside',
    hovertemplate: '<b>%{{y}}</b><br>%{{x:.2f}}%<extra></extra>',
    customdata: data.symbols,
  }}], {{
    ...baseLayout,
    height: {TOP_N} * 44 + 60,
    xaxis: {{ ...baseLayout.xaxis, ticksuffix: '%' }},
    yaxis: {{ ...baseLayout.yaxis, automargin: true }},
  }}, cfg);

  document.getElementById(divId).on('plotly_click', e => {{
    openModal(e.points[0].customdata);
  }});
}}

renderBar('chartWinner', DATA.winners);
renderBar('chartLoser',  DATA.losers);

function openModal(sym) {{
  currentSym  = sym;
  currentMode = 'daily';
  const d = DATA.detail[sym];
  if (!d) return;

  document.getElementById('popSymbol').textContent  = sym;
  document.getElementById('popName').textContent    = d.name || '';
  document.getElementById('popTheme').textContent   = d.theme || '-';
  document.getElementById('popAum').textContent     =
    d.aum ? '$' + (d.aum / 1e9).toFixed(1) + 'B' : '-';
  const retEl = document.getElementById('popRet');
  retEl.textContent = (d.weekly_ret >= 0 ? '+' : '') + d.weekly_ret + '%';
  retEl.className   = 'ret ' + (d.weekly_ret >= 0 ? 'pos' : 'neg');

  switchTab(0);
  document.getElementById('overlay').classList.add('open');
  setTimeout(() => renderChart('daily'), 50);
}}

function closeModal(e) {{
  if (!e || e.target === document.getElementById('overlay'))
    document.getElementById('overlay').classList.remove('open');
}}

function switchTab(idx) {{
  document.querySelectorAll('.tab').forEach((t, i) =>
    t.classList.toggle('active', i === idx));
  document.querySelectorAll('.tab-content').forEach((c, i) =>
    c.classList.toggle('active', i === idx));
  if (idx === 1) renderHoldings();
}}

function renderChart(mode) {{
  currentMode = mode;
  const d   = DATA.detail[currentSym];
  const src = mode === 'daily' ? d.daily : d.weekly;

  ['Daily','Weekly'].forEach(m => {{
    const btn = document.getElementById('btn' + m);
    const on  = (m.toLowerCase() === mode);
    btn.style.background  = on ? '#3a6fff' : 'none';
    btn.style.color       = on ? '#fff'    : '#aaa';
    btn.style.borderColor = on ? '#3a6fff' : '#444';
  }});

  if (!src || !src.dates || src.dates.length === 0) {{
    document.getElementById('priceChart').innerHTML =
      '<p class="no-data">No data available</p>';
    return;
  }}

  Plotly.newPlot('priceChart', [{{
    type: 'scatter', mode: 'lines',
    x: src.dates, y: src.close,
    line: {{ color: '#3a6fff', width: 2 }},
    fill: 'tozeroy', fillcolor: 'rgba(58,111,255,0.08)',
    hovertemplate: '%{{x}}<br><b>$%{{y:.2f}}</b><extra></extra>',
  }}], {{
    ...baseLayout,
    height: 340,
    yaxis: {{ ...baseLayout.yaxis, tickprefix: '$' }},
  }}, cfg);
}}

function renderHoldings() {{
  const holdings = DATA.detail[currentSym]?.holdings || [];
  const wrap     = document.getElementById('holdingsWrap');
  if (holdings.length === 0) {{
    wrap.innerHTML = '<p class="no-data">Holdings data not available.</p>';
    return;
  }}
  const maxW = Math.max(...holdings.map(h => h.weight));
  wrap.innerHTML = `
    <table class="holdings-table">
      <thead><tr>
        <th>#</th><th>Symbol</th><th>Name</th>
        <th style="text-align:right">Weight</th>
      </tr></thead>
      <tbody>
        ${{holdings.map((h, i) => `
          <tr>
            <td style="color:#555">${{i+1}}</td>
            <td style="color:#7eb3ff;font-weight:600">${{h.symbol}}</td>
            <td>${{h.name}}</td>
            <td style="text-align:right">
              ${{h.weight.toFixed(2)}}%
              <span class="weight-bar"
                style="width:${{(h.weight/maxW*60).toFixed(0)}}px"></span>
            </td>
          </tr>`).join('')}}
      </tbody>
    </table>`;
}}

document.addEventListener('keydown', e => {{
  if (e.key === 'Escape') document.getElementById('overlay').classList.remove('open');
}});
</script>
</body>
</html>"""

with open('etf_analysis/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print("✓ etf_analysis/index.html 저장 완료")
print("  → 브라우저에서 index.html 파일을 직접 열면 됩니다")
