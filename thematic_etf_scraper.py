import ssl
ssl._create_default_https_context = ssl._create_unverified_context

"""
thematic_etfs_top1.csv 재생성
- 테마별 AUM 1위 ETF 선정 후 yfinance 실제 수신 가능 여부 검증
- 실패 시 동일 테마 2위, 3위로 자동 fallback
- 끝자리 F 티커(OTC 비상장) 제외
- 목표: 최소 MIN_VALID개 유효 티커 확보
"""

import financedatabase as fd
import pandas as pd
import yfinance as yf
import warnings
warnings.filterwarnings('ignore')

MIN_VALID = 40
FALLBACK_N = 5

US_EXCHANGES = {'NMS', 'NYQ', 'PCX', 'ASE', 'NGM', 'NCM', 'BTS', 'PNK'}

LEV_KEYWORDS = [
    'leveraged', 'inverse', 'ultra short', 'ultrashort',
    '2x', '3x', '-1x', '-2x', '-3x', '1.5x',
    'bull 2', 'bull 3', 'bear 2', 'bear 3',
    'daily bear', 'daily bull',
]
NONUS_NAME_KEYWORDS = [
    'china', 'chinese', 'japan', 'japanese', 'india', 'indian',
    'korea', 'europe', 'european', 'germany', 'german', 'france', 'french',
    'brazil', 'russia', 'mexico', 'latin america', 'africa', 'middle east',
    'asia', 'asian', 'pacific', 'australia', 'canada', 'taiwan', 'vietnam',
    'indonesia', 'thailand', 'ex-u.s.', 'ex u.s.', 'non-u.s.',
    'acwi', 'eafe', 'msci world', 'ftse developed', 'international equity',
    'emerging market', 'developed market',
]
EXCLUDE_THEMES = ['DeFi', 'ESG', 'Sustainable', 'Socially Responsible']

THEME_KEYWORDS = {
    "AI":                    ["artificial intelligence", "ai-powered", "ai powered"],
    "Machine Learning":      ["machine learning"],
    "Innovation":            ["innovation", "disruptive technology", "next generation technology"],
    "Cybersecurity":         ["cybersecurity", "cyber security", "information security"],
    "Cloud Computing":       ["cloud computing", "saas"],
    "Big Data":              ["big data"],
    "Data Center":           ["data center"],
    "Semiconductor":         ["semiconductor", "microchip"],
    "Robotics":              ["robotics"],
    "Automation":            ["automation"],
    "Autonomous Vehicle":    ["autonomous vehicle", "autonomous tech", "self-driving"],
    "Electric Vehicle":      ["electric vehicle", " ev "],
    "Battery":               ["battery technology", "lithium battery"],
    "Lithium":               ["lithium"],
    "Clean Energy":          ["clean energy", "renewable energy", "green energy"],
    "Solar":                 ["solar energy"],
    "Wind Energy":           ["wind energy"],
    "Genomics":              ["genomics", "gene editing", "genome"],
    "Biotech":               ["biotech", "biopharma", "biopharmaceutical"],
    "Precision Medicine":    ["precision medicine"],
    "Digital Health":        ["digital health"],
    "Space":                 ["space exploration", "space technology"],
    "Aerospace":             ["aerospace"],
    "Satellite":             ["satellite"],
    "Defense":               ["defense", "national defense"],
    "Military":              ["military"],
    "Fintech":               ["fintech", "financial technology"],
    "Blockchain":            ["blockchain"],
    "Cryptocurrency":        ["cryptocurrency", "digital asset", "crypto"],
    "Gaming":                ["gaming", "video game"],
    "Esports":               ["esports", "e-sports"],
    "Water":                 ["water purification", "water infrastructure", "water technology"],
    "Infrastructure":        ["infrastructure development", "global infrastructure"],
    "5G":                    ["5g network", "5g technology"],
    "IoT":                   ["internet of things", "iot"],
    "Telecom":               ["telecom infrastructure", "telecommunications"],
    "Aging Population":      ["aging population", "longevity"],
    "Healthcare Innovation": ["healthcare innovation"],
    "Cannabis":              ["cannabis", "marijuana", "hemp"],
    "E-Commerce":            ["e-commerce", "online retail"],
    "Consumer Trends":       ["consumer trends"],
    "Pet Care":              ["pet care"],
    "Travel & Leisure":      ["travel & leisure", "travel and leisure"],
    "Metaverse":             ["metaverse", "virtual reality", "augmented reality"],
    "Clean Water":           ["clean water"],
    "Nuclear Energy":        ["nuclear energy", "uranium"],
    "Hydrogen":              ["hydrogen energy", "hydrogen fuel"],
}

# ─────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────
def has_any(text, keywords):
    return any(kw in text for kw in keywords)

def is_listable(sym: str) -> bool:
    """끝자리 F = OTC 핑크시트 비상장 → 제외"""
    return not sym.endswith('F')

def is_valid_ticker(sym: str) -> bool:
    """yfinance로 최근 5일 데이터가 실제로 수신되는지 확인"""
    try:
        df = yf.download(sym, period='5d', interval='1d',
                         auto_adjust=True, progress=False)
        return len(df) >= 2
    except Exception:
        return False

# ── 1. DB 로드 & 필터 ──────────────────────────
print("Loading financedatabase ETFs...")
etfs = fd.ETFs()
all_etfs = etfs.select()
us_etfs = all_etfs[all_etfs['exchange'].isin(US_EXCHANGES)].copy()

summary_lower = us_etfs['summary'].fillna('').str.lower()
name_lower    = us_etfs['name'].fillna('').str.lower()

lev_mask = (
    summary_lower.apply(lambda x: has_any(x, LEV_KEYWORDS)) |
    name_lower.apply(lambda x: has_any(x, LEV_KEYWORDS)) |
    (us_etfs['category'] == 'Trading')
)
us_etfs = us_etfs[~lev_mask]

name_lower = us_etfs['name'].fillna('').str.lower()
nonus_mask = name_lower.apply(lambda x: has_any(x, NONUS_NAME_KEYWORDS))
us_etfs = us_etfs[~nonus_mask].copy()

# ── 2. 테마 키워드 매칭 ────────────────────────
summary_lower = us_etfs['summary'].fillna('').str.lower()
name_lower    = us_etfs['name'].fillna('').str.lower()

records = []
for sym, row in us_etfs.iterrows():
    s, n = summary_lower[sym], name_lower[sym]
    matched = [t for t, kws in THEME_KEYWORDS.items()
               if any(kw in s or kw in n for kw in kws)]
    if matched:
        records.append({
            'symbol': sym, 'name': row['name'],
            'themes': ' | '.join(matched),
            'category': row.get('category', ''),
            'family': row.get('family', ''),
            'exchange': row.get('exchange', ''),
        })

df = pd.DataFrame(records).drop_duplicates(subset='symbol')
exclude_pattern = '|'.join(EXCLUDE_THEMES)
df = df[~df['themes'].str.contains(exclude_pattern)]

# ── F 티커 사전 제거 ───────────────────────────
before = len(df)
df = df[df['symbol'].apply(is_listable)]
print(f"테마 ETF 후보: {len(df):,}개 (F 티커 {before - len(df)}개 제거)")

# ── 3. AUM 조회 ───────────────────────────────
print("AUM 조회 중...")
tickers = df['symbol'].tolist()
aum_map = {}
BATCH = 50
for i in range(0, len(tickers), BATCH):
    for sym in tickers[i:i+BATCH]:
        try:
            aum_map[sym] = yf.Ticker(sym).info.get('totalAssets', None)
        except Exception:
            aum_map[sym] = None
    print(f"  {min(i+BATCH, len(tickers))}/{len(tickers)}", end='\r')

df['aum'] = df['symbol'].map(aum_map)

# ── 4. 테마별 후보 풀 생성 (AUM 내림차순, 최대 FALLBACK_N개) ──
df_exp = df.copy()
df_exp['theme_single'] = df_exp['themes'].str.split(' | ')
df_exp = df_exp.explode('theme_single')
df_exp = df_exp[~df_exp['theme_single'].isin(EXCLUDE_THEMES)]
df_exp = df_exp.sort_values('aum', ascending=False, na_position='last')

theme_candidates = (
    df_exp
    .groupby('theme_single', sort=False)
    .head(FALLBACK_N)
    .reset_index(drop=True)
)

# ── 5. 테마별 유효 티커 검증 (fallback 포함) ──
print("\n\n유효 티커 검증 중 (fallback 포함)...")
results = []
checked_valid = set()

for theme, grp in theme_candidates.groupby('theme_single', sort=True):
    selected = None
    for _, row in grp.iterrows():
        sym = row['symbol']
        if not is_listable(sym):                          # F 티커 skip
            print(f"  [{theme}] {sym} — 비상장(F) skip")
            continue
        if sym in checked_valid:
            selected = row
            break
        print(f"  [{theme}] {sym} 검증 중...", end=' ')
        if is_valid_ticker(sym):
            print("✓")
            checked_valid.add(sym)
            selected = row
            break
        else:
            print("✗ fallback")
    if selected is not None:
        results.append({
            'theme':    theme,
            'symbol':   selected['symbol'],
            'name':     selected['name'],
            'aum':      selected['aum'],
            'family':   selected['family'],
            'category': selected['category'],
        })
    else:
        print(f"  [{theme}] 유효 티커 없음 — 제외")

# ── 6. 결과 출력 & 저장 ───────────────────────
top1 = pd.DataFrame(results).sort_values('theme').reset_index(drop=True)
valid_count = len(top1)
print(f"\n최종 유효 테마 ETF: {valid_count}개 (목표: {MIN_VALID}개)")
if valid_count < MIN_VALID:
    print(f"⚠️  {MIN_VALID - valid_count}개 부족 — FALLBACK_N을 늘리거나 THEME_KEYWORDS 확장 필요")
else:
    print("✓ 목표 달성")

pd.set_option('display.max_colwidth', 40)
pd.set_option('display.width', 120)
print(top1[['theme', 'symbol', 'name', 'aum']].to_string(index=False))

top1.to_csv('etf_analysis/thematic_etfs_top1.csv', index=False, encoding='utf-8-sig')
print("\n✓ etf_analysis/thematic_etfs_top1.csv 저장 완료")