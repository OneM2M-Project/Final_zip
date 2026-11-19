"""X-Guard 이미지 AE (ImageSource-01, CImageSource01).

③ arrivals 구독 알림(sub-image)을 받으면 ④ 그 제품 번호의 X-ray 이미지를
640x640 이하 JPEG(base64)로 줄여 xrayImages 컨테이너에 cin으로 올려요.

이미지 가져오는 곳 (.env 의 IMAGE_SOURCE)
  dataset (기본) dataset/ 의 이미지를 파일 이름순으로 정렬해 seq번째(1부터, 끝나면 처음부터 반복).
                 예) dataset/01_normal.jpg, 02_knife.jpg, ...  비어 있으면 회색 시험용 이미지.
  camera         USB 카메라(ELP OV9281 850nm, CAMERA_INDEX)에서 도착 알림 CAPTURE_DELAY_MS 뒤의 프레임.
                 카메라를 못 열면 dataset 으로 대신해요. SAVE_CAPTURES=1 이면 captures/ 에 저장 (학습 데이터 모으기).

우리 하드웨어 (2026-11-15 하드웨어 팀 확인)
  - 카메라는 벨트 옆에 달고 반대편 옆에서 850nm 조명을 비춰 제품을 옆으로 투과 촬영해요.
  - ELP 는 Windows 에서 MSMF 방식 1번으로 열어야 화면이 나와요 (DSHOW 로 열면 새까맣게 나왔음).
    -> CAMERA_BACKEND=MSMF, CAMERA_INDEX=1, CAMERA_FOURCC=MJPG
  - 카메라를 거꾸로 달아서 학습 사진은 180도 돌려 찍었어요. 라인에서도 같게 -> CAMERA_ROTATE=180
    (학습 사진과 방향이 다르면 AI 가 못 맞혀요. 방향을 바꾸면 학습 사진도 같은 방향이어야 해요.)

실행: python gateway/image_source.py
"""
import base64
import io
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, request
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from common import m2m  # noqa: E402

ORIG = "CImageSource01"
TARGET = "ImageSource-01/xrayImages"
PORT = int(os.getenv("IMAGE_PORT", "9200"))
IMAGE_DIR = ROOT / os.getenv("IMAGE_DIR", "dataset")
MAX_SIDE = 640
EXTS = {".jpg", ".jpeg", ".png", ".bmp"}
SOURCE = os.getenv("IMAGE_SOURCE", "dataset").lower()
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
CAMERA_W, CAMERA_H = int(os.getenv("CAMERA_WIDTH", "1280")), int(os.getenv("CAMERA_HEIGHT", "800"))
CAPTURE_DELAY = int(os.getenv("CAPTURE_DELAY_MS", "0")) / 1000
# 카메라 여는 방식: MSMF | DSHOW | ANY (Windows 에서 우리 ELP 는 MSMF)
CAMERA_BACKEND = os.getenv("CAMERA_BACKEND", "MSMF").upper()
CAMERA_FOURCC = os.getenv("CAMERA_FOURCC", "MJPG").upper()      # MJPG | YUY2 | 빈칸(카메라 기본)
CAMERA_ROTATE = int(os.getenv("CAMERA_ROTATE", "0") or 0)       # 0 | 90 | 180 | 270 (시계 방향)
CAMERA_FLIP = os.getenv("CAMERA_FLIP", "").lower()              # 빈칸 | h(좌우) | v(상하)
CAMERA_EXPOSURE = os.getenv("CAMERA_EXPOSURE", "").strip()      # 빈칸 = 자동 노출, 예) -6
SAVE_CAPTURES = os.getenv("SAVE_CAPTURES", "0") == "1"
CAPTURE_DIR = ROOT / "captures"

app = Flask(__name__)


class Camera:
    """프레임을 계속 읽어 최신 것만 들고 있어요 (버퍼에 쌓인 옛 프레임을 보내지 않게)."""

    def __init__(self, index):
        # MSMF 는 처음 열 때 하드웨어 변환 준비로 10~30초 걸릴 수 있어 끈다 (cv2 import 전에)
        os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")
        import cv2  # opencv-python (ultralytics 를 깔면 같이 깔려요)
        self.cv2 = cv2
        backends = {"MSMF": cv2.CAP_MSMF, "DSHOW": cv2.CAP_DSHOW, "ANY": cv2.CAP_ANY}
        order = [CAMERA_BACKEND] if os.name == "nt" else ["ANY"]
        order += [b for b in ("MSMF", "DSHOW", "ANY") if b not in order and os.name == "nt"]
        self.cap, self.backend = None, None
        for b in order:  # 지정한 방식이 안 되면 다른 방식으로 한 번씩 더 시도
            cap = cv2.VideoCapture(index, backends.get(b, cv2.CAP_ANY))
            if cap.isOpened():
                self.cap, self.backend = cap, b
                break
            cap.release()
        if self.cap is None:
            raise RuntimeError(f"카메라 {index} 를 못 열었어요")
        if CAMERA_FOURCC:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*CAMERA_FOURCC[:4].ljust(4)))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_W)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_H)
        if CAMERA_EXPOSURE:  # 수동 노출 (방식에 따라 안 먹힐 수 있음)
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            self.cap.set(cv2.CAP_PROP_EXPOSURE, float(CAMERA_EXPOSURE))
        self.frame, self.lock = None, threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self):
        """3초 뒤 화면 크기·밝기를 찍어 줘요. 새까마면 방식(CAMERA_BACKEND)이나 조명을 확인."""
        time.sleep(3)
        with self.lock:
            f = None if self.frame is None else self.frame.copy()
        if f is None:
            print(f"!! 카메라({self.backend})에서 프레임이 안 와요. CAMERA_INDEX/CAMERA_BACKEND 를 확인하세요.", flush=True)
            return
        h, w = f.shape[:2]
        mean = float(f.mean())
        print(f"카메라 확인: {self.backend} {w}x{h}, 밝기 {mean:.0f}, 회전 {CAMERA_ROTATE}, 반전 {CAMERA_FLIP or '없음'}",
              flush=True)
        if (w, h) != (CAMERA_W, CAMERA_H):
            print(f"!! {CAMERA_W}x{CAMERA_H} 가 아니에요. 노트북 내장 카메라일 수 있어요 -> CAMERA_INDEX 를 바꿔 보세요.",
                  flush=True)
        if mean < 3:
            print("!! 화면이 새까매요. IR 조명(12V, CDS 가림)을 켰는지, CAMERA_BACKEND=MSMF 인지 확인하세요.", flush=True)

    def _orient(self, f):
        cv2 = self.cv2
        rot = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
        if CAMERA_ROTATE in rot:
            f = cv2.rotate(f, rot[CAMERA_ROTATE])
        if CAMERA_FLIP == "h":
            f = cv2.flip(f, 1)
        elif CAMERA_FLIP == "v":
            f = cv2.flip(f, 0)
        return f

    def _loop(self):
        while True:
            ok, f = self.cap.read()
            if ok:
                with self.lock:
                    self.frame = f
            else:
                time.sleep(0.05)

    def grab(self):
        """최신 프레임 -> PIL RGB (흑백 카메라도 3채널로)."""
        with self.lock:
            f = None if self.frame is None else self.frame.copy()
        if f is None:
            return None
        f = self._orient(f)  # 학습 사진과 같은 방향으로
        if f.ndim == 2:
            f = self.cv2.cvtColor(f, self.cv2.COLOR_GRAY2RGB)
        else:
            f = self.cv2.cvtColor(f, self.cv2.COLOR_BGR2RGB)
        return Image.fromarray(f)


camera = None


def image_files():
    if not IMAGE_DIR.exists():
        return []
    return sorted(p for p in IMAGE_DIR.iterdir() if p.suffix.lower() in EXTS)


def encode(path_or_img):
    """이미지(파일 경로 또는 PIL)를 640 이하로 줄이고 JPEG base64 문자열로."""
    if isinstance(path_or_img, Image.Image):
        img = path_or_img
    elif path_or_img:
        img = Image.open(path_or_img).convert("RGB")
    else:
        img = Image.new("RGB", (640, 640), (128, 128, 128))
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def from_camera(seq):
    if CAPTURE_DELAY:
        time.sleep(CAPTURE_DELAY)  # 센서 -> 카메라 화면 가운데까지 가는 시간
    img = camera.grab()
    if img is None:
        return None, None
    name = f"cam_{datetime.now():%Y%m%d_%H%M%S}_{seq:05d}.jpg"
    if SAVE_CAPTURES:
        CAPTURE_DIR.mkdir(exist_ok=True)
        img.save(CAPTURE_DIR / name, quality=95)
    return name, img


def upload(con):
    seq = int(con["seq"])
    name, src = from_camera(seq) if camera else (None, None)
    if src is None:  # dataset 모드 (또는 카메라 프레임이 아직 없음)
        files = image_files()
        path = files[(seq - 1) % len(files)] if files else None
        name, src = (path.name if path else "blank.jpg"), path
    body = {"seq": seq, "productId": con.get("productId", f"A-{seq:05d}"),
            "file": name, "img": encode(src)}
    r = m2m.add_cin(TARGET, ORIG, body)
    print(f"③ 도착 #{seq} -> ④ xrayImages {body['file']} ({len(body['img']) // 1024}KB) rsc={m2m.rsc(r)}",
          flush=True)


@app.post("/notify")
@app.post("/notify/<path:_rest>")  # ACME는 nu 경로를 poa 뒤에 붙여 보낼 수 있어요
def notify(_rest=None):
    rep, sur, vrq = m2m.parse_notification(request.get_json(silent=True))
    if not vrq:
        con = m2m.content_of(rep)
        if con and "seq" in con:
            # 바로 200을 돌려주고 업로드는 따로 (CSE가 알림 응답을 오래 기다리지 않게)
            threading.Thread(target=upload, args=(con,), daemon=True).start()
    return m2m.notify_ok()


if __name__ == "__main__":
    if SOURCE == "camera":
        try:
            camera = Camera(CAMERA_INDEX)
            print(f"카메라 {CAMERA_INDEX} 사용 [{camera.backend}/{CAMERA_FOURCC or '기본'}, 회전 {CAMERA_ROTATE}] "
                  f"({CAMERA_W}x{CAMERA_H}, 지연 {CAPTURE_DELAY * 1000:.0f}ms, "
                  f"저장 {'captures/' if SAVE_CAPTURES else '안 함'})")
        except Exception as e:
            print(f"!! 카메라를 못 써서 dataset 이미지로 대신해요: {e}")
    print(f"이미지 AE: http://0.0.0.0:{PORT}/notify  (이미지 {len(image_files())}장, {IMAGE_DIR})")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
