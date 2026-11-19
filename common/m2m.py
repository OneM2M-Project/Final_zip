"""oneM2M HTTP 공통 헬퍼 (명세서 '공통 규칙' 그대로).

모든 Python AE가 이 파일을 import 해서 같은 헤더/주소 규칙을 씁니다.
"""
import json
import os
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    """프로젝트 루트의 .env를 읽어 os.environ에 넣는다(이미 있는 값은 유지)."""
    env = ROOT / ".env"
    if not env.exists():
        env = ROOT / "env.example"  # .env 를 아직 안 만들었으면 예시 설정으로 실행
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


load_env()

CSE_URL = os.getenv("CSE_URL", "http://127.0.0.1:8080").rstrip("/")
CSE_RN = os.getenv("CSE_RN", "cse-mn")
CSE_ID = os.getenv("CSE_ID", "id-mn")

TY = {"ae": 2, "cnt": 3, "cin": 4, "grp": 9, "sub": 23, "fcnt": 28, "acp": 1}


def headers(origin, ty=None):
    h = {
        "X-M2M-Origin": origin,
        "X-M2M-RI": "r-" + uuid.uuid4().hex[:12],
        "X-M2M-RVI": "3",
        "Accept": "application/json",
    }
    h["Content-Type"] = f"application/json;ty={ty}" if ty else "application/json"
    return h


def url(path):
    """'LineSensor-01/arrivals' 또는 'cse-mn/...' 모두 허용."""
    path = path.strip("/")
    if not path.startswith(CSE_RN):
        path = f"{CSE_RN}/{path}" if path else CSE_RN
    return f"{CSE_URL}/{path}"


def rsc(resp):
    try:
        return int(resp.headers.get("X-M2M-RSC", "0"))
    except ValueError:
        return 0


def create(path, origin, ty, body, **params):
    return requests.post(url(path), headers=headers(origin, ty), data=json.dumps(body),
                         params=params or None, timeout=10)


def update(path, origin, body):
    return requests.put(url(path), headers=headers(origin), data=json.dumps(body), timeout=10)


def retrieve(path, origin, **params):
    return requests.get(url(path), headers=headers(origin), params=params or None, timeout=10)


def delete(path, origin):
    return requests.delete(url(path), headers=headers(origin), timeout=10)


def add_cin(path, origin, con):
    return create(path, origin, TY["cin"], {"m2m:cin": {"con": con}})


def parse_notification(body):
    """m2m:sgn에서 (rep 딕셔너리, sur, vrq여부)를 꺼낸다.

    rep 예: {"m2m:cin": {...}} 또는 {"xg:inRet": {...}}
    """
    sgn = (body or {}).get("m2m:sgn", {})
    if sgn.get("vrq"):
        return None, sgn.get("sur"), True
    rep = sgn.get("nev", {}).get("rep") or {}
    return rep, sgn.get("sur"), False


def content_of(rep):
    """rep에서 실제 값(dict)을 꺼낸다. cin이면 con, flexContainer(xg:inRet)면 속성들."""
    if not rep:
        return None
    if "m2m:cin" in rep:
        con = rep["m2m:cin"].get("con")
        if isinstance(con, str):
            try:
                con = json.loads(con)
            except json.JSONDecodeError:
                pass
        return con
    for k, v in rep.items():
        if k.startswith("xg:") and isinstance(v, dict):
            return v
    return None


def notify_ok():
    """구독 알림에 대한 정상 응답 (HTTP 200 + X-M2M-RSC: 2000)."""
    return "", 200, {"X-M2M-RSC": "2000"}
