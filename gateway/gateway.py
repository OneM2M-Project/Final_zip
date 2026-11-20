"""X-Guard gateway: Arduino (USB serial) <-> oneM2M MN-CSE (MQTT).

One process plays two AEs:
  LineSensor-01 (CLineSensor01)  step 1 DET,seq,0 from serial -> step 2 arrivals cin and lineStatus cin
  Rejector-01   (CRejector01)    step 7 verdict notification -> step 8 send OK,seq / NG,seq,
                                 step 9 REJ/PASS from serial -> rejectLog cin
Both AEs also get the dashboard's group commands through their cmd containers and pass
STOP/START (and the test commands T1/T2/SERVO) on to the Arduino.

The Arduino side is the hardware team's arduino/xguard_2sensor sketch (same as
arduino/src/main.cpp, 2026-11-15): D2 = S1 entry sensor, D3 = S2 sensor in front of the
rejector, D9 = SG90 servo, D13 = LED, no e-stop button. When S1 sees a product it sends DET
and remembers the verdict we send back. When S2 fires, the product at the head of the queue
is passed or pushed off according to that verdict.

Serial protocol, 115200 baud, one message per line:
  Arduino -> PC  DET,17,0        product 17 seen at S1 (last field is always 0, no belt encoder)
                 REJ,17,DONE     product 17 was NG and got pushed off
                 REJ,17,TIMEOUT  reached S2 with no verdict yet, pushed off to be safe
                 PASS,17         product 17 was OK and passed
                 STATUS,RUNNING|STOPPED
                 CNT,0           reply to CNT, kept for compatibility
  PC -> Arduino  OK,17 / NG,17   verdict
                 STOP / START    stop or restart (S1 is ignored while stopped)
                 T1 / T2 / SERVO tests: fake an S1 hit, fake an S2 hit, push the servo once
ARRIVE and AT_REJECTOR from the older firmware are still understood.

With SERIAL_PORT=SIM in .env it runs against a fake Arduino (SimArduino).
Run: python gateway/gateway.py
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
    with _log_lock:  # keep serial and MQTT thread output from interleaving
        print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


# ====================================================================== fake Arduino
class SimArduino:
    """Fake Arduino that follows the same rules as arduino/src/main.cpp (two-sensor version).

    Every SIM_ARRIVE_SEC it fakes an S1 hit (DET). SIM_TRAVEL_SEC later it fakes S2 and
    handles the head of the queue: PASS or REJ,DONE by verdict, REJ,TIMEOUT if there is none.
    Accepts the same T1/T2/SERVO/CNT/V commands as the real board.
    """

    def __init__(self, arrive_sec, travel_sec):
        self.out = queue.Queue()
        self.arrive_sec, self.travel_sec = arrive_sec, travel_sec
        self.seq, self.running = 0, True
        self.q = []                   # [seq, verdict]: '?' none yet, 'P' pass, 'F' reject
        self.lock = threading.Lock()
        self.out.put("STATUS,RUNNING")
        if arrive_sec > 0:
            threading.Thread(target=self._line, daemon=True).start()

    def _line(self):
        while True:
            time.sleep(self.arrive_sec)
            self.on_s1()

    def on_s1(self):
        if not self.running:          # ignore S1 while stopped, like the firmware
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

    # same interface as serial.Serial
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
        # SERVO just moves the servo once and sends nothing back


def open_serial():
    """Return the fake Arduino for SIM. If the COM port won't open, list ports and retry every 3 s."""
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
    time.sleep(2)  # the Uno resets when the port opens (servo homes, STATUS,RUNNING ~0.4 s later)
    log(f"시리얼 연결: {SERIAL_PORT} @ {SERIAL_BAUD}")
    return s


# ====================================================================== gateway
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
            client.subscribe(f"/oneM2M/resp/{orig}/{CSE_ID}/json")   # responses to our requests
            client.subscribe(f"/oneM2M/req/{CSE_ID}/{orig}/json")    # notifications from the CSE

    def send_req(self, orig, to, ty, pc, op=1):
        """Send a oneM2M request over MQTT (steps 2 and 9)."""
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
        # notification (op 5)
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

    # ---------------------------------------------------------------- handlers
    def handle_verdict(self, con):
        """Steps 7 -> 8. Anything other than PASS is rejected, ERROR included."""
        seq = int(con.get("seq", 0))
        if seq <= 0:
            return
        ok = "OK" if con.get("verdict") == "PASS" else "NG"
        self.serial_write(f"{ok},{seq}")
        log(f"⑦ 판정 #{seq} {con.get('verdict')} {con.get('defectClass', '')} -> ⑧ {ok},{seq}")

    def handle_cmd(self, cmd):
        if cmd not in ("STOP", "START", "T1", "T2", "SERVO"):   # T1/T2/SERVO come from the dashboard's hardware test page
            return
        # The group fanout delivers the same command through both cmd containers,
        # so drop a repeat within 1 s.
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
        self.push_status()   # dashboard counters (passed / rejected / timeout)
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
            reason = "OK" if len(p) < 3 or p[2] == "DONE" else p[2]   # DONE = rejected because of the verdict
            self.reject_log(int(p[1]), "REJECTED", reason, line)
        elif p[0] == "PASS" and len(p) >= 2 and p[1].isdigit():
            self.reject_log(int(p[1]), "PASSED", "OK", line)
        elif p[0] == "AT_REJECTOR" and len(p) >= 4 and p[1].isdigit():   # older firmware
            self.reject_log(int(p[1]), p[2], p[3], line)
        elif p[0] == "CNT":   # always 0 since there is no belt encoder; kept for compatibility
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
