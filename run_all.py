"""Start the whole X-Guard stack in one go.

    python run_all.py            # start everything (Ctrl+C stops everything)
    python run_all.py --reset    # wipe the CSE database first

Order of startup:
  0. If train/dataset/ has data, train YOLOv8n into models/detect.pt (skipped if the data hasn't changed).
  1. MQTT broker. If port 1883 is free, start amqtt; if Mosquitto is already running, use that.
  2. ACME MN-CSE (cse/ folder, headless).
  3. setup_resources.py base
  4. AI, image AE, dashboard.
  5. Open the dashboard in a browser, wait for its receiver on :9100, then create the subscriptions.
  6. Gateway, then the gateway's MQTT subscriptions.

If models/detect.pt is missing or torch isn't installed, the AI runs with MODEL_BACKEND=dummy.
Set AUTO_TRAIN=0 in .env to skip training. If dataset/ is empty, six test images are created.
Each program logs to logs/<name>.log.
"""
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from common import m2m  # noqa: E402  (loads .env or env.example)

PY = sys.executable
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
procs = []


def port_open(port, host="127.0.0.1"):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def wait_port(port, name, timeout=60, proc=None, log=None):
    """Wait for a port to open. If proc dies first, stop waiting and print the end of its log."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if port_open(port):
            print(f"  ✓ {name} (:{port})")
            return True
        if proc is not None and proc.poll() is not None:
            print(f"  ✗ {name} 프로그램이 켜지다가 꺼졌어요 (코드 {proc.returncode}).")
            if log and (LOGS / log).exists():
                tail = (LOGS / log).read_text(encoding="utf-8", errors="replace").strip().splitlines()[-8:]
                print("    --- logs/" + log + " 끝부분 ---")
                for t in tail:
                    print("    " + t)
            return False
        time.sleep(0.5)
    print(f"  ✗ {name} (:{port}) 가 {timeout}초 안에 안 켜졌어요. logs/ 를 확인하세요.")
    return False


def start(name, args, cwd=ROOT, env=None):
    log = open(LOGS / f"{name}.log", "w", encoding="utf-8")
    e = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", **(env or {})}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    p = subprocess.Popen(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, env=e, creationflags=flags)
    procs.append((name, p))
    print(f"  ▶ {name} 시작 (logs/{name}.log)")
    return p


def setup(cmd):
    r = subprocess.run([PY, "setup/setup_resources.py", cmd], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    print(r.stdout.strip())
    if r.returncode:
        print(r.stderr.strip())


def auto_train():
    """Run train/train.py --auto, echoing output to the console and logs/train.log."""
    if os.getenv("AUTO_TRAIN", "1").strip() in ("0", "false", "False", "no"):
        print("  AUTO_TRAIN=0 -> 건너뜀")
        return
    log = open(LOGS / "train.log", "w", encoding="utf-8")
    p = subprocess.Popen([PY, "train/train.py", "--auto"], cwd=ROOT, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    for line in p.stdout:
        print("  │ " + line.rstrip())
        log.write(line)
    p.wait()
    log.close()
    if p.returncode:
        print(f"  ! 학습이 실패했어요 (코드 {p.returncode}). logs/train.log 를 보세요. 있던 모델로 계속해요.")


def ai_health():
    """Ask the AI which model it actually loaded and print it."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{os.getenv('AI_PORT', '9000')}/health", timeout=5) as r:
            h = json.loads(r.read().decode("utf-8"))
        mark = "!" if h.get("backend") == "dummy" else "✓"
        print(f"  {mark} AI 모델: {h.get('model')} ({h.get('backend')}) 버전={h.get('modelVer')} 클래스={h.get('classes')}")
    except Exception as e:
        print(f"  ! AI 상태 확인 실패: {e!r} -> logs/ai.log 를 확인하세요")


def cse_db_broken():
    """True if the ACME tinydb has half-written resources (missing ct or ty).

    Once a container holding one of these hits mni, ACME fails with 5000 while
    trying to delete the oldest entry.
    """
    import glob
    import json
    for f in glob.glob(str(ROOT / "cse" / "data" / "resources-*.json")):
        try:
            res = json.loads(Path(f).read_text(encoding="utf-8")).get("resources", {})
        except (ValueError, OSError):
            return True  # the file itself is corrupt, so a reset is right
        for v in res.values():
            if isinstance(v, dict) and v.get("__rtype__") and ("ct" not in v or "ty" not in v):
                return True
    return False


def has_module(name):
    return importlib.util.find_spec(name) is not None


def is_torchscript(path):
    """True for a torch.jit.save file, False for an ultralytics best.pt (torch.save)."""
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            return any(n.endswith("constants.pkl") for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def sample_images():
    d = ROOT / os.getenv("IMAGE_DIR", "dataset")
    d.mkdir(exist_ok=True)
    if any(p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"} for p in d.iterdir()):
        return
    from PIL import Image, ImageDraw
    for i, n in enumerate(["normal", "stone", "normal", "metal", "plastic", "normal"], 1):
        im = Image.new("RGB", (800, 600), (200, 200, 200))
        ImageDraw.Draw(im).text((20, 20), n, fill=(0, 0, 0))
        im.save(d / f"{i:02d}_{n}.jpg")
    print("  dataset/ 이 비어 있어서 시험용 이미지 6장을 만들었어요.")


def hw_summary():
    """Print the hardware settings (2026-11-15 rig: S1 D2, S2 D3, SG90 D9, ELP camera)."""
    port = os.getenv("SERIAL_PORT", "SIM")
    if port.upper() == "SIM":
        print("  아두이노: SIM (가짜 아두이노, 실제 장비는 .env 의 SERIAL_PORT=COM4 처럼)")
    else:
        try:
            from serial.tools import list_ports
            found = [p.device for p in list_ports.comports()]
        except Exception:
            found = []
        mark = "✓" if port in found else "!"
        print(f"  {mark} 아두이노: {port} @ {os.getenv('SERIAL_BAUD', '115200')} (연결된 포트: {', '.join(found) or '없음'})")
        if port not in found:
            print("    -> 포트가 안 보여요. 장치 관리자에서 COM 번호를 확인하고, 시리얼 모니터는 닫아 두세요.")
    src = os.getenv("IMAGE_SOURCE", "dataset")
    if src == "camera":
        print(f"  카메라: {os.getenv('CAMERA_INDEX', '0')}번 {os.getenv('CAMERA_BACKEND', 'MSMF')}/"
              f"{os.getenv('CAMERA_FOURCC', 'MJPG')}, 회전 {os.getenv('CAMERA_ROTATE', '0')}, "
              f"지연 {os.getenv('CAPTURE_DELAY_MS', '0')}ms (확인 결과는 logs/image_source.log)")
    else:
        print(f"  이미지: {os.getenv('IMAGE_DIR', 'dataset')}/ 폴더 (실제 카메라는 IMAGE_SOURCE=camera)")


def main():
    reset = "--reset" in sys.argv
    print("하드웨어 설정")
    hw_summary()
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))

    print("0) 모델 자동 학습 (train/dataset)")
    auto_train()

    print("1) MQTT 브로커")
    if port_open(mqtt_port):
        print(f"  ✓ 이미 켜진 브로커 사용 (:{mqtt_port})")
    else:
        cfg = LOGS / "amqtt.yaml"
        cfg.write_text(f"listeners:\n  default:\n    type: tcp\n    bind: 0.0.0.0:{mqtt_port}\n"
                       "plugins:\n  amqtt.plugins.authentication.AnonymousAuthPlugin:\n    allow_anonymous: true\n",
                       encoding="utf-8")
        exe = shutil.which("amqtt") or str(Path(PY).parent / ("amqtt.exe" if os.name == "nt" else "amqtt"))
        start("mqtt", [exe, "-c", str(cfg)])
        if not wait_port(mqtt_port, "MQTT 브로커", 20):
            return

    print("2) ACME MN-CSE")
    # A CSE DB copied from another PC (old test runs, old model names) gets wiped once
    # the first time it runs on this machine.
    owner = ROOT / "cse" / "data" / ".owner"
    me = socket.gethostname()
    if not reset and (not owner.exists() or owner.read_text(encoding="utf-8").strip() != me):
        print(f"  이 PC({me})에서 처음 쓰는 CSE DB라 예전 기록을 비우고 시작해요 (--db-reset)")
        reset = True
    if not reset and cse_db_broken():
        print("  ! CSE DB 에 반쯤 저장된 기록이 있어요 (창을 강제로 닫았을 때 생겨요). "
              "그대로 두면 lineStatus 저장이 5000 오류로 실패해서 대시보드 검사 수가 안 올라가요 -> DB 를 비우고 시작해요")
        reset = True
    acme_args = [PY, "-m", "acmecse", "--headless"] + (["--db-reset"] if reset else [])
    start("acme", acme_args, cwd=ROOT / "cse")
    if not wait_port(8080, "MN-CSE HTTP", 60):
        return
    owner.parent.mkdir(parents=True, exist_ok=True)
    owner.write_text(me, encoding="utf-8")
    time.sleep(2)

    print("3) 리소스 생성 (base)")
    setup("base")

    print("4) 프로그램들")
    sample_images()
    ai_env = {}
    model = ROOT / os.getenv("MODEL_PATH", "models/detect.pt")
    if os.getenv("MODEL_BACKEND", "torchscript") != "dummy":
        if not model.exists():
            print(f"  ! {model.name} 이 없어서 AI를 MODEL_BACKEND=dummy 로 실행해요 (파일 이름으로 판정).")
            ai_env["MODEL_BACKEND"] = "dummy"
        elif not has_module("torch"):
            print("  ! torch 가 없어서 AI를 MODEL_BACKEND=dummy 로 실행해요. "
                  "pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu")
            ai_env["MODEL_BACKEND"] = "dummy"
        elif not is_torchscript(model) and not has_module("ultralytics"):
            print(f"  ! {model.name} 은 ultralytics 체크포인트(best.pt)라 ultralytics 가 필요한데 없어요. "
                  "AI를 dummy 로 실행해요 -> pip install ultralytics")
            ai_env["MODEL_BACKEND"] = "dummy"
        else:
            kind = "TorchScript" if is_torchscript(model) else "ultralytics 체크포인트"
            print(f"  모델: {model.name} ({kind}, {model.stat().st_size / 1e6:.1f}MB)")
    if os.getenv("IMAGE_SOURCE", "dataset") == "camera" and not has_module("cv2"):
        print("  ! opencv 가 없어서 카메라 대신 dataset/ 사진을 써요 -> pip install opencv-python")
    # Start the receivers first. If the gateway came up first, products arriving while
    # the AI is still loading its model would never get a verdict and would time out.
    ai_proc = start("ai", [PY, "ai/app.py"], env=ai_env)
    if not wait_port(int(os.getenv("AI_PORT", "9000")), "AI AE (모델 로드 포함)", 300, ai_proc, "ai.log"):
        return
    start("image_source", [PY, "gateway/image_source.py"])
    wait_port(int(os.getenv("IMAGE_PORT", "9200")), "이미지 AE", 60)
    start("dashboard", [PY, "-m", "streamlit", "run", "dashboard/app.py", "--server.headless", "true",
                        "--server.port", "8501"])
    wait_port(8501, "대시보드 화면", 60)
    webbrowser.open("http://localhost:8501")  # the dashboard's :9100 receiver only starts once the page is opened
    wait_port(int(os.getenv("DASH_NOTIFY_PORT", "9100")), "대시보드 알림 수신기", 60)

    print("5) 구독 생성 (이미지·AI·대시보드)")
    setup("subs-http")
    ai_health()

    print("6) 게이트웨이 (아두이노) + 게이트웨이 구독")
    start("gateway", [PY, "gateway/gateway.py"])
    time.sleep(3)  # give the gateway time to connect; MQTT subscriptions need it online
    setup("subs-mqtt")

    print("\n전부 켜졌어요. 대시보드 http://localhost:8501 · ACME 웹 UI http://localhost:8080/webui")
    print("흐름 로그: logs/gateway.log, logs/ai.log  |  끄기: Ctrl+C\n")
    while True:
        for name, p in procs:
            if p.poll() is not None:
                print(f"  ! {name} 이(가) 꺼졌어요 (코드 {p.returncode}). logs/{name}.log 를 확인하세요.")
                procs.remove((name, p))
        time.sleep(2)


def stop_all():
    # Stop the clients first and ACME (which writes the DB) last. Killing everything at
    # once can leave a half-written record that causes 5000 errors on the next run.
    first = [(n, p) for n, p in procs if n not in ("acme", "mqtt")]
    last = [(n, p) for n, p in procs if n in ("acme", "mqtt")]
    for group in (list(reversed(first)), last):
        for name, p in group:
            if p.poll() is None:
                p.terminate()
        for name, p in group:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        time.sleep(1)
    print("전부 껐어요.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        stop_all()
