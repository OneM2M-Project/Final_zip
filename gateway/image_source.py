"""X-Guard image AE (ImageSource-01, origin CImageSource01).

When an arrivals notification comes in (sub-image, step 3), it grabs the image for that
product, shrinks it to a JPEG no bigger than 640x640, and posts it as a base64 cin to the
xrayImages container (step 4).

IMAGE_SOURCE in .env picks where images come from:
  dataset  default. Files in dataset/ sorted by name; seq 1 is the first file, and it wraps
           around at the end (e.g. 01_normal.jpg, 02_knife.jpg). Empty folder -> gray test image.
  camera   USB camera (ELP OV9281, 850 nm, CAMERA_INDEX), frame taken CAPTURE_DELAY_MS after
           the arrival. Falls back to dataset if the camera won't open. SAVE_CAPTURES=1 also
           writes each frame to captures/ so it can be used for training.

Our rig, as confirmed by the hardware team on 2026-11-15:
  - The camera sits beside the belt and the 850 nm light is on the opposite side, so products
    are shot side-on with light coming through them.
  - On Windows the ELP only gives a picture when opened as index 1 with MSMF. DSHOW gave a
    black frame. So CAMERA_BACKEND=MSMF, CAMERA_INDEX=1, CAMERA_FOURCC=MJPG.
  - The camera is mounted upside down and the training photos were rotated 180 degrees, so
    the line uses CAMERA_ROTATE=180 too. If the orientation doesn't match the training photos
    the model gets it wrong.

Run: python gateway/image_source.py
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
# capture backend: MSMF | DSHOW | ANY (our ELP needs MSMF on Windows)
CAMERA_BACKEND = os.getenv("CAMERA_BACKEND", "MSMF").upper()
CAMERA_FOURCC = os.getenv("CAMERA_FOURCC", "MJPG").upper()      # MJPG | YUY2 | empty = camera default
CAMERA_ROTATE = int(os.getenv("CAMERA_ROTATE", "0") or 0)       # 0 | 90 | 180 | 270, clockwise
CAMERA_FLIP = os.getenv("CAMERA_FLIP", "").lower()              # empty | h (horizontal) | v (vertical)
CAMERA_EXPOSURE = os.getenv("CAMERA_EXPOSURE", "").strip()      # empty = auto exposure, e.g. -6
SAVE_CAPTURES = os.getenv("SAVE_CAPTURES", "0") == "1"
CAPTURE_DIR = ROOT / "captures"

app = Flask(__name__)


class Camera:
    """Reads frames nonstop and keeps only the newest, so we never send a stale buffered frame."""

    def __init__(self, index):
        # MSMF hardware transforms can add 10-30 s to the first open. Turn them off before importing cv2.
        os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")
        import cv2  # opencv-python, installed along with ultralytics
        self.cv2 = cv2
        backends = {"MSMF": cv2.CAP_MSMF, "DSHOW": cv2.CAP_DSHOW, "ANY": cv2.CAP_ANY}
        order = [CAMERA_BACKEND] if os.name == "nt" else ["ANY"]
        order += [b for b in ("MSMF", "DSHOW", "ANY") if b not in order and os.name == "nt"]
        self.cap, self.backend = None, None
        for b in order:  # if the chosen backend fails, try each of the others once
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
        if CAMERA_EXPOSURE:  # manual exposure; some backends ignore it
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            self.cap.set(cv2.CAP_PROP_EXPOSURE, float(CAMERA_EXPOSURE))
        self.frame, self.lock = None, threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self):
        """After 3 s, print the frame size and brightness. A black frame usually means the wrong backend or no light."""
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
        """Newest frame as a PIL RGB image (grayscale is expanded to 3 channels)."""
        with self.lock:
            f = None if self.frame is None else self.frame.copy()
        if f is None:
            return None
        f = self._orient(f)  # match the orientation of the training photos
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
    """Shrink an image (path or PIL) to at most 640 px and return it as a base64 JPEG string."""
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
        time.sleep(CAPTURE_DELAY)  # travel time from the sensor to the middle of the frame
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
    if src is None:  # dataset mode, or no camera frame yet
        files = image_files()
        path = files[(seq - 1) % len(files)] if files else None
        name, src = (path.name if path else "blank.jpg"), path
    body = {"seq": seq, "productId": con.get("productId", f"A-{seq:05d}"),
            "file": name, "img": encode(src)}
    r = m2m.add_cin(TARGET, ORIG, body)
    print(f"③ 도착 #{seq} -> ④ xrayImages {body['file']} ({len(body['img']) // 1024}KB) rsc={m2m.rsc(r)}",
          flush=True)


@app.post("/notify")
@app.post("/notify/<path:_rest>")  # ACME may append the nu path to the poa
def notify(_rest=None):
    rep, sur, vrq = m2m.parse_notification(request.get_json(silent=True))
    if not vrq:
        con = m2m.content_of(rep)
        if con and "seq" in con:
            # reply 200 right away and upload in the background so the CSE isn't kept waiting
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
