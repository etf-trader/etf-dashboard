import ssl
ssl._create_default_https_context = ssl._create_unverified_context

"""
미국 상장 테마 ETF 리스트 수집기 v2
=====================================
필터: category / category_group / name / symbol 제외 조건
테마: category_group 값을 그대로 사용
출력: etf_analysis/thematic_etfs_v2.csv         ← 전체 ETF (AUM 포함)
      etf_analysis/thematic_etfs_top1_v2.csv   ← 테마(category_group)별 AUM 최대 1개
      etf_analysis/thematic_etfs_above_5b_v2.csv ← AUM 50억 달러 이상 ETF (AUM 내림차순 정렬)
"""

import financedatabase as fd
import pandas as pd
import yfinance as yf
import warnings
warnings.filterwarnings('ignore')

pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 120)
pd.set_option('display.max_colwidth', 50)

# ─────────────────────────────────────────────
# 필터 설정
# ─────────────────────────────────────────────
EXCLUDE_CATEGORY     = 'bond|cash|currencies|market|cap|blend|trading|value|basket|factors|growth'
EXCLUDE_CATEGORY_GRP = 'alternatives|fixed income'
EXCLUDE_NAME         = 'Select Sector SPDR Fund|2x|3x'
EXCLUDE_SYMBOLS      = ['IDU', 'IYE', 'IYF', 'IYH', 'IYJ', 'IYM', 'IYR', 'IYW', 'IYZ']

# ─────────────────────────────────────────────
# 1. 데이터 로드 & 필터링
# ─────────────────────────────────────────────
print("Loading financedatabase ETFs...")
all_etfs = fd.ETFs().select().reset_index()
print(f"  Total in DB : {len(all_etfs):,}")

df = all_etfs[
    (all_etfs['currency'] == 'USD') &
    all_etfs['isin'].notna() &
    all_etfs['category'].notna() &
    ~all_etfs['category'].str.contains(EXCLUDE_CATEGORY, case=False, na=False) &
    ~all_etfs['category_group'].str.contains(EXCLUDE_CATEGORY_GRP, case=False, na=False) &
    ~all_etfs['name'].str.contains(EXCLUDE_NAME, case=False, na=False) &
    ~all_etfs['symbol'].isin(EXCLUDE_SYMBOLS)
].copy()
print(f"  필터 후      : {len(df):,}개")

# theme = category_group 그대로
df = df.rename(columns={'category_group': 'theme'})

# ─────────────────────────────────────────────
# 2. AUM 조회 (yfinance)
# ─────────────────────────────────────────────
print("\nAUM 조회 중 (yfinance)...")
tickers = df['symbol'].tolist()
aum_map = {}
BATCH = 50

for i in range(0, len(tickers), BATCH):
    batch = tickers[i:i+BATCH]
    for sym in batch:
        try:
            info = yf.Ticker(sym).info
            aum_map[sym] = info.get('totalAssets', None)
        except Exception:
            aum_map[sym] = None
    print(f"  {min(i+BATCH, len(tickers))}/{len(tickers)} 완료", end='\r')

print()
df['aum'] = df['symbol'].map(aum_map)

# ─────────────────────────────────────────────
# 3. 전체 저장
# ─────────────────────────────────────────────
out_cols = ['symbol', 'name', 'theme', 'category', 'family', 'exchange', 'aum']
out_cols = [c for c in out_cols if c in df.columns]

df[out_cols].to_csv('etf_analysis/thematic_etfs_v2.csv', index=False, encoding='utf-8-sig')
print(f"✓ etf_analysis/thematic_etfs_v2.csv 저장 ({len(df)}개)")

# ─────────────────────────────────────────────
# 4. 테마(category_group)별 AUM 최대 1개
# ─────────────────────────────────────────────
top1 = (
    df[out_cols]
    .sort_values('aum', ascending=False, na_position='last')
    .groupby('theme', sort=False)
    .first()
    .reset_index()
    .sort_values('theme')
    .reset_index(drop=True)
)

print(f"\n테마별 대표 ETF ({len(top1)}개):")
print(top1[['theme', 'symbol', 'name', 'aum', 'category']].to_string(index=False))

top1.to_csv('etf_analysis/thematic_etfs_top1_v2.csv', index=False, encoding='utf-8-sig')
print("\n✓ etf_analysis/thematic_etfs_top1_v2.csv 저장 완료")

# ─────────────────────────────────────────────
# 5. AUM 30억 달러 이상 테마 ETF 추출 및 정렬 (Added Code)
# ─────────────────────────────────────────────
print("\n[추가 기능] AUM 30억 달러 이상 ETF 정렬 및 저장 중...")

# AUM 3,000,000,000 이상 조건 필터 및 내림차순 정렬
df_above_3b = (
    df[df['aum'] >= 3_000_000_000][out_cols]
    .sort_values('aum', ascending=False)
    .reset_index(drop=True)
)

print(f"\nAUM 3B 이상 테마 ETF ({len(df_above_3b)}개):")
print(df_above_3b[['theme', 'symbol', 'name', 'aum']].to_string(index=False))

df_above_3b.to_csv('etf_analysis/thematic_etfs_above_3b_v2.csv', index=False, encoding='utf-8-sig')
print("\n✓ etf_analysis/thematic_etfs_above_3b_v2.csv 저장 완료")
