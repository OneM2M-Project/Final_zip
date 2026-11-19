# X-Guard — oneM2M 컨베이어 X-ray 이물질 검사

아두이노(감지 센서 2개·SG90 서보) → 게이트웨이 → ACME MN-CSE → 이미지 AE → AI(MEC) → 리젝터/대시보드.
모든 프로그램은 서로 직접 부르지 않고 MN-CSE에 쓰고 구독 알림으로 받아요. 값 형식은 "X-Guard 최종 시스템 명세서"와 같아요.

```
onem2m-conveyor/
├─ arduino/                 하드웨어 팀 코드(11/15, 수정 금지): S1·S2 E18-D80NK, 대기열, SG90 리젝터
│   ├─ src/main.cpp          PlatformIO 용 (xguard_2sensor.ino 와 내용 동일)
│   └─ xguard_2sensor/       Arduino IDE 용 스케치
├─ cse/acme.ini             ACME MN-CSE 설정 (MQTT 켜짐)
├─ cse/init/xguard.fcp      flexContainer 특수화 xg:inRet
├─ setup/setup_resources.py AE·컨테이너·flexContainer·그룹·ACP·구독 생성
├─ common/m2m.py            oneM2M HTTP 공통 헬퍼 (.env 읽기)
├─ gateway/gateway.py       시리얼 ↔ MQTT (LineSensor-01, Rejector-01). SERIAL_PORT=SIM 이면 가짜 아두이노
├─ gateway/image_source.py  도착 알림 → 카메라(OV9281 850nm) 또는 dataset 이미지 업로드 (ImageSource-01, :9200)
├─ ai/app.py                이미지 알림 → models/detect.pt → 판정 (Inspection, :9000)
├─ ai/Dockerfile            루트에서 빌드
├─ train/                   YOLOv8n 학습 (train/dataset 에 데이터 → 자동 학습 → models/detect.pt)
├─ train/colab_train.ipynb  ★ 모델 학습 노트북 (Colab GPU, Drive 데이터 → models/detect.pt)
├─ models/detect.pt         ← 학습 결과 (train/train.py 가 저장, 규격은 아래)
├─ models/classes.txt       클래스 이름 (번호 순서)
├─ mec/                     k8s Deployment + NodePort 30900
├─ dashboard/app.py         Streamlit (:8501) + 알림 수신 (:9100)
├─ .streamlit/config.toml   대시보드 테마
├─ tests/requests.http      REST Client 시험 요청
├─ dataset/                 시연 이미지 (IMAGE_SOURCE=dataset, 이름순 = seq 순)
└─ .env                     주소·포트·모델 설정 한 곳
```

## 1. 설치 (Windows, VS Code 터미널)

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu   # AI를 PC에서 돌릴 때
```

Mosquitto 를 설치하고 Windows 서비스로 켜 두세요(기본 1883). VS Code 확장은 `.vscode/extensions.json` 추천 목록을 설치하면 돼요.

## 2. 한 번에 실행

`run_all.bat` 를 더블클릭하거나 터미널에서 `python run_all.py` (DB를 비우고 시작: `--reset`).
MQTT 브로커(Mosquitto가 없으면 amqtt) → ACME → 리소스 → 게이트웨이·이미지·AI·대시보드 → 구독까지 순서대로 켜고, 브라우저로 대시보드를 열어요.
`models/detect.pt` 가 없으면 AI는 dummy 모드로 돌아요. 로그는 `logs/` 에 있어요. Ctrl+C 로 전부 꺼요.

## 2-0. 모델 학습 (YOLOv8n, Colab)

학습은 Colab GPU 에서 노트북 하나로 해요: **`train/colab_train.ipynb`** (이 프로젝트 안, 제출용).
1. Google Drive 폴더(예: `내 드라이브/xguard/`)에 데이터 zip(`Xguard_defect.zip` 라벨, `defect.zip` 이미지)을 올려요.
2. Colab 에서 노트북을 열고 T4 GPU 런타임 → `ROOT` 만 그 폴더로 바꾸고 모두 실행 → `ROOT/model/` 에 저장.
3. `detect.pt`, `classes.txt`, `model_info.json` 을 이 폴더의 **`models/`** 에 복사 → `run_all.bat`.

`models/detect.pt` 가 없으면 AI 는 dummy 모드(파일 이름으로 판정)로 켜져요.
PC 에서 바로 학습하는 `train/train.py` 도 남아 있어요 (GPU PC 에서 `.env` `AUTO_TRAIN=1` 또는 `python train/train.py`).

## 2-1. 하나씩 실행 (디버깅할 때)

| 터미널 | 명령 | 확인 |
|---|---|---|
| 1 | `cd cse` → `python -m acmecse --headless` | `CSE started` (http://127.0.0.1:8080/webui) |
| 2 | `python setup/setup_resources.py base` | 모두 2001 (다시 하면 4105) |
| 3 | `python gateway/gateway.py` | `① DET,1,0 -> ② arrivals A-00001` |
| 4 | `python gateway/image_source.py` | |
| 5 | `python ai/app.py` (또는 docker, 아래) | `모델 로드: detect.pt` |
| 6 | `streamlit run dashboard/app.py` → 브라우저로 http://localhost:8501 한 번 열기 | 알림 수신기(:9100)가 이때 켜져요 |
| 2 | `python setup/setup_resources.py subs` | 6개 모두 2001 |

그 뒤로는 제품마다 로그에 ①→②→③④→⑤⑥→⑦⑧→⑨ 가 차례로 찍혀요. 모두 끄고 다시 켤 때 리소스·구독은 CSE DB에 남아 있으니 `base`/`subs` 는 다시 안 해도 돼요. 처음부터 하려면 `setup_resources.py clean` 또는 `python -m acmecse --db-reset`.

**하드웨어 없이 시험**: `.env` 에 `SERIAL_PORT=SIM`(기본값)이면 가짜 아두이노가 4초마다 제품을 만들어요. 모델 없이 흐름만 보려면 `MODEL_BACKEND=dummy` (파일 이름에 `knife` 같은 클래스 이름이 있으면 FAIL).

**아두이노 연결** (하드웨어 팀 코드 2026-11-15, `arduino/HARDWARE_NOTES_1115.txt`)

| 핀 | 부품 | 역할 |
|---|---|---|
| D2 | E18-D80NK #1 (S1) | 입구 · 제품 진입 → `DET,seq,0` (감지 시 LOW, 내부 풀업) |
| D3 | E18-D80NK #2 (S2) | 리젝터 바로 앞 · 대기열 맨 앞 제품을 판정대로 처리 |
| D9 | SG90 서보 | 아두이노 5V/GND 직결, 밀 때만 신호 후 detach |
| D13 | 내장 LED | 켜짐 가동 · 깜빡임 정지 · 밀 때 잠깐 꺼짐 |
| - | 비상정지 버튼 없음 | 정지는 대시보드 **정지** 버튼(STOP) |

시리얼(115200): 아두이노 → PC `DET,seq,0` · `REJ,seq,DONE|TIMEOUT` · `PASS,seq` · `STATUS,RUNNING|STOPPED`,
PC → 아두이노 `OK,seq` / `NG,seq` · `STOP` / `START` · 시험용 `T1`(S1 흉내) `T2`(S2 흉내) `SERVO`(서보 한 번).
`arduino/` 를 PlatformIO로 업로드(또는 Arduino IDE 로 `arduino/xguard_2sensor/` 열기) → 시리얼 모니터에서 `T1` → `DET,1,0`, `NG,1`, `T2` → `REJ,1,DONE` 확인 → **모니터를 닫고** `.env` 의 `SERIAL_PORT=COM4` 처럼 바꿔요.
포트를 못 열면 게이트웨이가 연결된 포트 목록을 보여 주고 3초마다 다시 시도해요. 대시보드 **하드웨어 시험** 메뉴에서도 T1/T2/SERVO 를 보낼 수 있어요.
S1 → S2 이동 시간이 AI 판정 시간(촬영 + 업로드 + 추론)보다 길어야 `TIMEOUT`(판정 지연 배출)이 안 나요. 서보 각도·시간은 `main.cpp` 맨 위 값이며 하드웨어 팀이 맞춰요.

**카메라** (하드웨어 팀 `gateway/image_source.py`): `.env` 에 `IMAGE_SOURCE=camera`, `CAMERA_INDEX=1`, `CAMERA_BACKEND=MSMF`(DSHOW 는 새까맣게 나옴), `CAMERA_FOURCC=MJPG`, `CAMERA_ROTATE=180`(학습 사진과 같은 방향), `CAPTURE_DELAY_MS=0`(S1 을 카메라 정면에 둘 때).
켜고 3초 뒤 `logs/image_source.log` 에 `카메라 확인: MSMF 1280x800, 밝기 …` 가 찍혀요. `SAVE_CAPTURES=1` 이면 찍힌 사진이 `captures/` 에 쌓여 학습 데이터로 쓸 수 있어요.

**대시보드** (http://localhost:8501): 라인 상태, 검사/통과/배출/판정 지연 배출/불량률 카드, 최근 검사 사진(불량 박스 표시), rejectLog 실시간 이벤트, 불량 이력(discovery), 장비 구성 표. 숫자는 게이트웨이를 켠 뒤부터 센 값이에요.

## 3. AI를 도커 / MEC로

```powershell
docker build -f ai/Dockerfile -t <dockerhub-id>/xguard-ai:1.0 .
docker run --rm -p 9000:9000 -e CSE_URL=http://host.docker.internal:8080 <dockerhub-id>/xguard-ai:1.0
docker push <dockerhub-id>/xguard-ai:1.0
```

MEC Sandbox VM에서 `mec/deployment.yaml` 의 `image`, `CSE_URL`(=`http://<PC_IP>:8080`) 을 고친 뒤 `kubectl -n <SANDBOX_NS> apply -f mec/`.
그다음 PC의 `.env` 에서 `AI_NOTIFY_URL=http://<VM_IP>:30900/notify`, `PC_IP=<PC의 실제 IP>`, `cse/acme.ini` 의 `cseHost` 도 PC IP로 바꾸고 `python setup/setup_resources.py base` (Inspection AE의 poa가 갱신돼요).

## 4. detect.pt 규격 (직접 만들어야 하는 부분)

`ai/app.py` 는 `.env` 의 `MODEL_BACKEND` 에 따라 모델을 불러요.

### 기본: `torchscript`
- **파일**: `models/detect.pt` = `torch.jit.save(torch.jit.trace(model.eval(), torch.zeros(1,3,640,640)), "detect.pt")` 로 만든 TorchScript. (모델 클래스 코드 없이 불러오기 위해서예요. `torch.save(model)` 로 저장한 파일은 안 돼요.)
- **입력**: `float32` 텐서 `[1, 3, 640, 640]`, **RGB**, 값 **0~1** (픽셀/255). 원본은 비율을 유지해 640에 맞추고 남는 곳을 회색 `(114,114,114)` 로 채운 **letterbox**. mean/std 정규화는 없음 (학습 때 했다면 `.env` 에 `NORM_MEAN`, `NORM_STD`).
  크기는 `IMG_SIZE` 로 바꿀 수 있어요.
- **출력** (둘 중 하나):
  - **A (권장)** NMS까지 끝난 `float` 텐서 `[N, 6]` (또는 `[1, N, 6]`), 각 행 `[x1, y1, x2, y2, conf, cls]`
    - 좌표: **입력 640×640 이미지의 픽셀 좌표** (letterbox 된 그림 기준, 0~640). 원본 좌표로 되돌리는 건 app.py가 해요.
    - `conf`: 0~1 확률 (sigmoid/softmax 후)
    - `cls`: 클래스 번호 (0부터, `models/classes.txt` 의 줄 순서)
    - 박스가 없으면 `[0, 6]` 빈 텐서
  - **B** YOLOv8 원본 head 출력 `[1, 4+클래스수, 8400]` (`cx, cy, w, h` + 클래스별 점수 0~1). `yolo export format=torchscript` 결과가 이 모양이고, app.py가 NMS(IoU `IOU_TH`=0.45)를 해 줘요.
  - 튜플/리스트로 내보내면 첫 번째 원소를 써요.
- **클래스 이름**: `models/classes.txt` 한 줄에 하나, 영어 소문자 한 단어 (`knife`, `gun`, `scissors` …). 이게 `defectClass` 값이에요.

### `ultralytics`
ultralytics로 학습한 `best.pt` 를 쓰려면 `MODEL_PATH=models/best.pt`, `MODEL_BACKEND=ultralytics`, `pip install ultralytics`. 클래스 이름은 모델 안의 것을 써요.
(`torchscript` 인데 파일이 TorchScript 가 아니면 app.py 가 알아서 ultralytics 로 읽어요. `train/train.py` 가 만든 `detect.pt` 는 형식 B 의 TorchScript 라 기본값 그대로 돼요.)

### 판정 규칙 (app.py가 처리)
`conf ≥ CONF_TH(0.5)` 박스가 하나라도 있으면 FAIL, 없으면 PASS, 이미지를 못 읽거나 추론 오류면 ERROR. `defectClass` = 가장 높은 박스의 클래스, `confidence` = 가장 높은 점수(소수 3자리). FAIL이면 `defects` 에 `boxes: [[x1,y1,x2,y2,conf,"class"], ...]`(원본 좌표)를 더해 써요.

## 5. 참고
- IN-CSE 등록: IN-CSE 가 정해지면 `cse/acme.ini` 의 `registrarCse*` 를 채우고 `[cse] enableRemoteCSE=true`.
- flexContainer 를 못 쓰는 환경이면 `.env` 의 `RESULT_MODE=cin` (inspectionResult 가 일반 컨테이너가 되고 나머지는 그대로 동작).
- 구독 알림 대상(`nu`)은 AE-ID(`CRejector01` 등)로 둬서 MQTT 알림 토픽이 `/oneM2M/req/id-mn/CRejector01/json` 이 돼요.
- `mosquitto_sub -t "/oneM2M/#" -v` 로 MQTT 흐름을, `tests/requests.http` 로 각 단계를 손으로 확인할 수 있어요.
