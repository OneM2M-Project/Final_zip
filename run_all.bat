@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem X-Guard 전체 실행 (더블클릭). 처음 한 번은 가상환경과 패키지를 설치해요.
rem GPU용 torch 주소 (CUDA 버전이 다르면 cu126 / cu130 등으로)
set TORCH_GPU=https://download.pytorch.org/whl/cu128
set TORCH_CPU=https://download.pytorch.org/whl/cpu

rem 다른 PC에서 복사해 온 .venv는 동작하지 않아요 -> 깨졌으면 옆으로 치우고 새로 만들어요
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python -c "import sys" >nul 2>nul || (
    echo 기존 .venv 가 이 PC에서 동작하지 않아요. .venv_broken 으로 옮기고 새로 만들어요.
    if exist .venv_broken rmdir /s /q .venv_broken
    move .venv .venv_broken >nul
  )
)

if not exist .venv\Scripts\python.exe (
  echo [1/2] 가상환경 만드는 중...
  py -3.11 -m venv .venv 2>nul || python -m venv .venv
  if not exist .venv\Scripts\python.exe (
    echo [오류] Python 3.11 을 찾지 못했어요. python.org 에서 3.11 을 설치하고 "Add python.exe to PATH" 를 체크한 뒤 다시 실행하세요.
    pause
    exit /b 1
  )
  echo [2/2] 패키지 설치 중... 몇 분 걸려요
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)

rem 가상환경을 예전에 만들었으면 새로 추가된 패키지(opencv 등)가 빠져 있을 수 있어요 -> 확인 후 설치
.venv\Scripts\python -c "import cv2, serial, paho.mqtt, streamlit, flask, acmecse" >nul 2>nul || (
  echo 빠진 패키지 설치 중... opencv 등
  .venv\Scripts\python -m pip install -r requirements.txt
)

rem 모델(models\detect.pt)이 있으면 AI 추론에 torch 와 ultralytics 가 필요해요
set NEED_TORCH=0
if exist models\detect.pt set NEED_TORCH=1
if "%NEED_TORCH%"=="1" (
  .venv\Scripts\python -c "import torch" >nul 2>nul || call :install_train
  .venv\Scripts\python -c "import ultralytics" >nul 2>nul || (
    echo ultralytics 설치 중... detect.pt 가 ultralytics 체크포인트라 필요해요
    .venv\Scripts\python -m pip install "ultralytics>=8.4"
  )
)
.venv\Scripts\python run_all.py %*
pause
goto :eof

:install_train
echo 학습/추론 패키지 설치 중... torch 는 용량이 커서 오래 걸려요
where nvidia-smi >nul 2>nul
if errorlevel 1 (
  .venv\Scripts\python -m pip install torch torchvision --index-url %TORCH_CPU%
) else (
  .venv\Scripts\python -m pip install torch torchvision --index-url %TORCH_GPU%
)
goto :eof
