@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Starts all of X-Guard (double-click). The first run creates the venv and installs packages.
rem torch index for GPU (change to cu126, cu130 etc. for a different CUDA version)
set TORCH_GPU=https://download.pytorch.org/whl/cu128
set TORCH_CPU=https://download.pytorch.org/whl/cpu

rem A .venv copied from another PC won't work. If it's broken, move it aside and make a new one.
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

rem An older venv may be missing packages added later (opencv etc.), so check and install.
.venv\Scripts\python -c "import cv2, serial, paho.mqtt, streamlit, flask, acmecse" >nul 2>nul || (
  echo 빠진 패키지 설치 중... opencv 등
  .venv\Scripts\python -m pip install -r requirements.txt
)

rem If models\detect.pt exists, the AI needs torch and ultralytics.
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
