"""Shared oneM2M HTTP helpers.

Every Python AE imports this so they all use the same headers and addressing,
as described under "common rules" in the spec.
"""
import json
import os
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    """Load the project's .env into os.environ. Values already set are kept."""
    env = ROOT / ".env"
    if not env.exists():
        env = ROOT / "env.example"  # no .env yet, so run with the example settings
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
    """Accepts both 'LineSensor-01/arrivals' and 'cse-mn/...'."""
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
    """Pull (rep, sur, is_verification) out of an m2m:sgn body.

    rep looks like {"m2m:cin": {...}} or {"xg:inRet": {...}}.
    """
    sgn = (body or {}).get("m2m:sgn", {})
    if sgn.get("vrq"):
        return None, sgn.get("sur"), True
    rep = sgn.get("nev", {}).get("rep") or {}
    return rep, sgn.get("sur"), False


def content_of(rep):
    """Get the payload dict from rep: con for a cin, the attributes for an xg:inRet flexContainer."""
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
    """OK reply to a notification (HTTP 200 with X-M2M-RSC: 2000)."""
    return "", 200, {"X-M2M-RSC": "2000"}
