#!/bin/bash
set -e

echo "▶ build_all.py 실행..."
python build_all.py

echo "▶ git push..."
git add etf_analysis/
git commit -m "auto: update dashboard $(date '+%Y-%m-%d %H:%M')"
git push

echo "✓ 배포 완료"
