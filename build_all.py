"""
build_all.py
============
build_etf.py → build_stock.py 순서대로 실행
"""
import subprocess
import sys

scripts = ['build_etf.py', 'build_stock.py']

for script in scripts:
    print(f"\n{'='*50}")
    print(f"  실행: {script}")
    print('='*50)
    result = subprocess.run([sys.executable, script], check=False)
    if result.returncode != 0:
        print(f"⚠ {script} 실패 (returncode={result.returncode}), 계속 진행...")

print("\n✓ build_all.py 완료")
