# filename: run.sh
cd capstone-dashboard

# 점프 호스트 우회 접속용 패키지 설치
sudo apt-get update
sudo apt-get install -y sshpass

# 가상환경 활성화 (필수)
source venv/bin/activate

# 필수 모듈 가상환경에 강제 설치 확인 (uvicorn[standard] 추가)
python -m pip install netmiko pydantic ansible fastapi "uvicorn[standard]" websockets paramiko

# 기존 돌아가는 서버 강제 종료 (포트 충돌 방지)
pkill -f uvicorn

# 백그라운드(nohup)로 실행 (가상환경 파이썬 모듈로 강제 실행)
nohup python -m uvicorn main:app --host 0.0.0.0 --port 8000 > server.log 2>&1 &

# 로그 확인
tail -f server.log
