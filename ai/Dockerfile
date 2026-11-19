# 프로젝트 루트에서 빌드:  docker build -f ai/Dockerfile -t <dockerhub-id>/xguard-ai:1.0 .
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
COPY ai/requirements.txt ai/requirements.txt
RUN pip install -r ai/requirements.txt

COPY common/ common/
COPY ai/app.py ai/app.py
COPY models/ models/

# 기본값 (docker run --env-file .env 또는 k8s env 로 덮어쓰기)
ENV CSE_URL=http://host.docker.internal:8080 \
    CSE_RN=cse-mn \
    AI_PORT=9000 \
    MODEL_PATH=models/detect.pt \
    CLASSES_PATH=models/classes.txt \
    MODEL_BACKEND=torchscript \
    IMG_SIZE=640 \
    CONF_TH=0.5 \
    MODEL_VER=yolov8n-xguard-1.0 \
    RESULT_MODE=flex

EXPOSE 9000
CMD ["python", "ai/app.py"]
