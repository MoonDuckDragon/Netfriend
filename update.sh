#!/bin/bash
# filename: update.sh

cd ~/capstone-dashboard

echo "1. Git 원격 저장소 최신화"
git fetch
git reset --hard origin/main

echo "2. 기존 서버 강제 종료"
pkill -f uvicorn

echo "3. 서버 백그라운드 재시작"
source venv/bin/activate
nohup python -m uvicorn main:app --host 0.0.0.0 --port 8000 > server.log 2>&1 &

echo "완료. tail -f server.log 로 확인."
