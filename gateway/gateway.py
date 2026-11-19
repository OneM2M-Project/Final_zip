"""X-Guard 게이트웨이: 아두이노(USB 시리얼) <-> oneM2M MN-CSE(MQTT).

하나의 프로그램이 두 AE 역할을 해요.
  * LineSensor-01 (CLineSensor01): ① DET,seq,0 수신 -> ② arrivals cin, lineStatus cin
  * Rejector-01   (CRejector01)  : ⑦ 판정 알림 -> ⑧ OK,seq / NG,seq 전송, ⑨ REJ/PASS -> rejectLog cin
  * 두 AE 모두 cmd 컨테이너 알림(대시보드 그룹 명령) -> 아두이노에 STOP/START (시험용 T1/T2/SERVO)

아두이노는 하드웨어 팀 코드 arduino/xguard_2sensor (= arduino/src/main.cpp, 2026-11-15) 기준이에요.
  D2 S1 E18-D80NK 입구 · D3 S2 E18-D80NK 리젝터 앞 · D9 SG90 서보 · D13 LED (비상정지 버튼 없음)
  S1 감지 -> DET -> 판정(OK/NG) 기록 -> S2 감지 때 대기열 맨 앞 제품을 판정대로 PASS / REJ(배출)

시리얼 메시지 (115200bps, 한 줄에 하나)
  아두이노 -> PC  DET,17,0            17번 제품 S1 감지 (벨트 펄스 센서가 없어서 항상 0)
                  REJ,17,DONE         17번 NG 라서 배출 (TIMEOUT = S2 에 왔는데 판정이 없어 안전하게 배출)
                  PASS,17             17번 OK 라서 통과
                  STATUS,RUNNING|STOPPED
                  CNT,0               (호환용) CNT 응답
  PC -> 아두이노  OK,17 / NG,17       판정
                  STOP / START        정지 / 재시작 (정지 중에는 S1 감지를 무시)
                  T1 / T2 / SERVO     (시험) S1 감지 흉내 / S2 감지 흉내 / 서보 한 번 밀기
  (예전 펌웨어의 ARRIVE / AT_REJECTOR 도 읽어요)

.env 의 SERIAL_PORT=SIM 이면 아두이노 없이 가짜 아두이노(SimArduino)로 돌아가요.
실행: python gateway/gateway.py
"""
import json
import os
import queue
import random
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import m2m  # noqa: E402

CSE_ID = m2m.CSE_ID
CSE_RN = m2m.CSE_RN
LS, RJ = "CLineSensor01", "CRejector01"
MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
SERIAL_PORT = os.getenv("SERIAL_PORT", "SIM")
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "115200"))


_log_lock = threading.Lock()


def log(*a):
    with _log_lock:  # 시리얼/MQTT 스레드가 동시에 찍어도 줄이 섞이지 않게
        print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


# ====================================================================== 가짜 아두이노
class SimArduino:
    """arduino/src/main.cpp (하드웨어 팀 2센서 버전)와 같은 규칙으로 동작하는 가짜 아두이노.

    SIM_ARRIVE_SEC 마다 S1 감지(DET), SIM_TRAVEL_SEC 뒤 S2 감지 -> 대기열 맨 앞 제품을
    판정대로 PASS / REJ,DONE, 판정이 없으면 REJ,TIMEOUT. T1/T2/SERVO/CNT/V 명령도 똑같이 받아요.
    """

    def __init__(self, arrive_sec, travel_sec):
        self.out = queue.Queue()
        self.arrive_sec, self.travel_sec = arrive_sec, travel_sec
        self.seq, self.running = 0, True
        self.q = []                   # [seq, verdict] ('?' 아직 없음, 'P' 통과, 'F' 배출)
        self.lock = threading.Lock()
        self.out.put("STATUS,RUNNING")
        if arrive_sec > 0:
            threading.Thread(target=self._line, daemon=True).start()

    def _line(self):
        while True:
            time.sleep(self.arrive_sec)
            self.on_s1()

    def on_s1(self):
        if not self.running:          # 정지 중에는 S1 감지 무시 (펌웨어와 같음)
            return
        with self.lock:
            self.seq = self.seq % 65535 + 1
            self.q = self.q[-15:] + [[self.seq, "?"]]   # QSIZE 16
            s = self.seq
        self.out.put(f"DET,{s},0")
        if self.travel_sec > 0:
            threading.Timer(self.travel_sec, self.on_s2).start()

    def on_s2(self):
        with self.lock:
            if not self.q:
                return
            s, v = self.q.pop(0)
        if v == "P":
            self.out.put(f"PASS,{s}")
        else:
            self.out.put(f"REJ,{s},{'DONE' if v == 'F' else 'TIMEOUT'}")

    def set_verdict(self, s, v):
        with self.lock:
            for it in self.q:
                if it[0] == s:
                    it[1] = v
                    return

    # serial.Serial 과 같은 모양의 인터페이스
    def readline(self):
        try:
            return (self.out.get(timeout=0.2) + "\n").encode()
        except queue.Empty:
            return b""

    def write(self, data):
        line = data.decode().strip()
        p = line.split(",")
        if p[0] in ("OK", "NG") and len(p) == 2 and p[1].isdigit():
            self.set_verdict(int(p[1]), "P" if p[0] == "OK" else "F")
        elif p[0] == "V" and len(p) == 3 and p[1].isdigit() and p[2] in ("P", "F"):
            self.set_verdict(int(p[1]), p[2])
        elif line in ("STOP", "START"):
            self.running = line == "START"
            self.out.put(f"STATUS,{'RUNNING' if self.running else 'STOPPED'}")
        elif line == "T1":
            self.on_s1()
        elif line == "T2":
            self.on_s2()
        elif line == "CNT":
            self.out.put("CNT,0")
        # SERVO: 서보만 한 번 밀고 메시지는 없음


def open_serial():
    """SIM 이면 가짜 아두이노. COM 포트를 못 열면 포트 목록을 보여 주고 3초마다 다시 시도."""
    if SERIAL_PORT.upper() == "SIM":
        log("SERIAL_PORT=SIM -> 가짜 아두이노로 실행")
        return SimArduino(float(os.getenv("SIM_ARRIVE_SEC", "4")), float(os.getenv("SIM_TRAVEL_SEC", "3")))
    import serial  # pyserial
    from serial.tools import list_ports
    while True:
        try:
            s = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.2)
            break
        except serial.SerialException as e:
            ports = ", ".join(f"{p.device}({p.description})" for p in list_ports.comports()) or "없음"
            log(f"!! {SERIAL_PORT} 를 못 열었어요: {e}")
            log(f"   연결된 포트: {ports}  -> .env 의 SERIAL_PORT 를 고치거나, 시리얼 모니터를 닫으세요. 3초 뒤 다시 시도")
            time.sleep(3)
    time.sleep(2)  # 우노는 포트를 열면 리셋돼요 (서보 제자리 0.4초 뒤 STATUS,RUNNING)
    log(f"시리얼 연결: {SERIAL_PORT} @ {SERIAL_BAUD}")
    return s


# ====================================================================== 게이트웨이
class Gateway:
    def __init__(self):
        self.ser = open_serial()
        self.ser_lock = threading.Lock()
        self.running, self.count, self.last_seq = True, 0, 0
        self.passed, self.rejected, self.timeout = 0, 0, 0
        self.last_cmd = ("", 0.0)
        self.mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="xguard-gateway-" + uuid.uuid4().hex[:6])
        self.mq.on_connect = self.on_connect
        self.mq.on_message = self.on_message

    # ---------------------------------------------------------------- MQTT
    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        log("MQTT 연결됨", reason_code)
        for orig in (LS, RJ):
            client.subscribe(f"/oneM2M/resp/{orig}/{CSE_ID}/json")   # 내가 보낸 요청의 응답
            client.subscribe(f"/oneM2M/req/{CSE_ID}/{orig}/json")    # CSE가 보내는 알림

    def send_req(self, orig, to, ty, pc, op=1):
        """② ⑨: oneM2M 요청을 MQTT로 보낸다."""
        msg = {"op": op, "to": f"{CSE_RN}/{to}", "fr": orig, "rqi": "r-" + uuid.uuid4().hex[:8],
               "rvi": "3", "pc": pc}
        if ty:
            msg["ty"] = ty
        self.mq.publish(f"/oneM2M/req/{orig}/{CSE_ID}/json", json.dumps(msg))

    def add_cin(self, orig, to, con):
        self.send_req(orig, to, 4, {"m2m:cin": {"con": con}})

    def on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except json.JSONDecodeError:
            return
        parts = msg.topic.split("/")
        if parts[2] == "resp":
            if data.get("rsc") not in (2000, 2001, 2004):
                log("!! CSE 응답 오류", data.get("rsc"), "to=" + str(data.get("to", "")), str(data.get("pc", ""))[-600:])
            return
        # 알림 (op 5)
        me = parts[4]
        client.publish(f"/oneM2M/resp/{CSE_ID}/{me}/json",
                       json.dumps({"rsc": 2000, "rqi": data.get("rqi"), "rvi": "3", "to": data.get("fr"),
                                   "fr": me}))
        rep, sur, vrq = m2m.parse_notification(data.get("pc"))
        if vrq:
            log(f"구독 확인 요청 OK ({me}) {sur}")
            return
        con = m2m.content_of(rep)
        if not con:
            return
        sur = sur or ""
        if "cmd" in sur or "cmd" in con:
            self.handle_cmd(str(con.get("cmd", "")).upper())
        elif "verdict" in con:
            self.handle_verdict(con)

    # ---------------------------------------------------------------- 처리
    def handle_verdict(self, con):
        """⑦ -> ⑧: PASS가 아니면 모두 리젝 (ERROR 포함)."""
        seq = int(con.get("seq", 0))
        if seq <= 0:
            return
        ok = "OK" if con.get("verdict") == "PASS" else "NG"
        self.serial_write(f"{ok},{seq}")
        log(f"⑦ 판정 #{seq} {con.get('verdict')} {con.get('defectClass', '')} -> ⑧ {ok},{seq}")

    def handle_cmd(self, cmd):
        if cmd not in ("STOP", "START", "T1", "T2", "SERVO"):   # T1/T2/SERVO 는 대시보드 '하드웨어 시험'
            return
        # 그룹 fanout으로 두 cmd 컨테이너에서 같은 명령이 오므로 1초 안 중복은 무시
        if self.last_cmd[0] == cmd and time.time() - self.last_cmd[1] < 1.0:
            return
        self.last_cmd = (cmd, time.time())
        log(f"그룹 명령 {cmd} -> 아두이노")
        self.serial_write(cmd)

    def serial_write(self, line):
        with self.ser_lock:
            self.ser.write((line + "\n").encode())

    def push_status(self):
        self.add_cin(LS, "LineSensor-01/lineStatus",
                     {"running": self.running, "count": self.count, "lastSeq": self.last_seq,
                      "passed": self.passed, "rejected": self.rejected, "timeout": self.timeout})

    def reject_log(self, seq, action, reason, raw):
        con = {"seq": seq, "productId": f"A-{seq:05d}", "action": action, "reason": reason}
        self.add_cin(RJ, "Rejector-01/rejectLog", con)
        if action == "PASSED":
            self.passed += 1
        else:
            self.rejected += 1
            self.timeout += reason == "TIMEOUT"
        self.push_status()   # 대시보드 카운터 (통과/배출/TIMEOUT)
        log(f"{raw} -> ⑨ rejectLog #{seq} {action} {reason}")

    def handle_serial(self, line):
        p = line.split(",")
        if p[0] in ("DET", "ARRIVE") and len(p) >= 2 and p[1].isdigit():
            seq = int(p[1])
            self.count += 1
            self.last_seq = seq
            con = {"seq": seq, "productId": f"A-{seq:05d}", "ts": datetime.now().strftime("%Y%m%dT%H%M%S")}
            self.add_cin(LS, "LineSensor-01/arrivals", con)
            self.push_status()
            log(f"① {line} -> ② arrivals {con['productId']}")
        elif p[0] == "REJ" and len(p) >= 2 and p[1].isdigit():
            reason = "OK" if len(p) < 3 or p[2] == "DONE" else p[2]   # DONE = 판정대로 배출
            self.reject_log(int(p[1]), "REJECTED", reason, line)
        elif p[0] == "PASS" and len(p) >= 2 and p[1].isdigit():
            self.reject_log(int(p[1]), "PASSED", "OK", line)
        elif p[0] == "AT_REJECTOR" and len(p) >= 4 and p[1].isdigit():   # 예전 펌웨어
            self.reject_log(int(p[1]), p[2], p[3], line)
        elif p[0] == "CNT":   # 벨트 펄스 센서가 없어서 항상 0 (호환용)
            log("아두이노:", line)
        elif p[0] == "STATUS" and len(p) >= 2:
            self.running = p[1] == "RUNNING"
            self.push_status()
            log(f"STATUS {p[1]}")
        elif line:
            log("아두이노:", line)

    def run(self):
        self.mq.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
        self.mq.loop_start()
        log(f"게이트웨이 시작 (MQTT {MQTT_HOST}:{MQTT_PORT}, CSE {CSE_ID})")
        while True:
            raw = self.ser.readline()
            if raw:
                self.handle_serial(raw.decode(errors="ignore").strip())


if __name__ == "__main__":
    random.seed()
    try:
        Gateway().run()
    except KeyboardInterrupt:
        pass
