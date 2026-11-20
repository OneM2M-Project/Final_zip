"""X-Guard dashboard (Dashboard, origin CDashboard).

Takes defects notifications on :9100/notify (step 10) and shows them as live alerts.
The rest of the page is read from the CSE: defect history through discovery
(lbl=type:defect), line state and counters from lineStatus/la, the latest inspection
photo from inspectionResult + xrayImages, and pass/reject events from rejectLog.
Stop/start and the hardware tests (T1/T2/SERVO) go out through the lineA-devices group's fopt.

Hardware (per the hardware team, 2026-11-15): S1 E18-D80NK on D2 at the entry, S2 E18-D80NK
on D3 in front of the rejector, SG90 servo on D9. There is no e-stop button, so the Stop
button on this page (STOP command) is how you halt the line.

Run: streamlit run dashboard/app.py   (then open http://localhost:8501)
"""
import base64
import html
import io
import os
import sys
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
from flask import Flask, request
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from common import m2m  # noqa: E402

ORIG = "CDashboard"
NOTIFY_PORT = int(os.getenv("DASH_NOTIFY_PORT", "9100"))
RESULT_MODE = os.getenv("RESULT_MODE", "flex")
REFRESH_SEC = 2
# streamlit 1.50+ wants width="stretch"; older versions use use_container_width=True
FULL = ({"width": "stretch"} if tuple(int(x) for x in st.__version__.split(".")[:2]) >= (1, 50)
        else {"use_container_width": True})

HARDWARE = [  # same wiring as the top of arduino/src/main.cpp
    ("S1 입구 센서", "E18-D80NK #1", "D2", "감지 시 LOW · 제품 진입 → DET"),
    ("S2 리젝터 앞 센서", "E18-D80NK #2", "D3", "감지 시 LOW · 대기열 맨 앞 처리"),
    ("리젝터", "SG90 서보", "D9", "밀 때만 신호, 복귀 후 detach"),
    ("상태 LED", "내장 L", "D13", "켜짐 가동 · 깜빡임 정지"),
    ("카메라", "ELP OV9281 850nm", "USB", "MSMF · 180° 회전 · 측면 투과"),
    ("IR 조명", "LED-36 850nm", "12V", "아두이노와 별도 전원"),
]


# ---------------------------------------------------------------- notification receiver (started once)
@st.cache_resource
def notify_server():
    alerts = deque(maxlen=50)
    srv = Flask("dash-notify")

    @srv.post("/notify")
    @srv.post("/notify/<path:_rest>")  # ACME may append the nu path to the poa
    def notify(_rest=None):
        rep, _, vrq = m2m.parse_notification(request.get_json(silent=True))
        if not vrq:
            con = m2m.content_of(rep)
            if con:
                alerts.appendleft({**con, "_rx": time.strftime("%H:%M:%S")})
        return m2m.notify_ok()

    threading.Thread(target=lambda: srv.run(host="0.0.0.0", port=NOTIFY_PORT, threaded=True),
                     daemon=True).start()
    return alerts


@st.cache_resource
def image_cache():
    """seq -> (time, PIL image), so the same photo isn't fetched again on every 2 s refresh."""
    return OrderedDict()


# ---------------------------------------------------------------- oneM2M reads
def get_json(path, **params):
    try:
        r = m2m.retrieve(path, ORIG, **params)
        return r.json() if m2m.rsc(r) == 2000 else None
    except Exception:
        return None


def since(minutes):
    """Value for the cra filter (created after). CSE times are UTC."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y%m%dT%H%M%S")


def cins(path, minutes=None):
    """A container's cins as (con, ct), newest first."""
    params = {"rcn": 4, "ty": 4}
    if minutes:
        params["cra"] = since(minutes)
    j = get_json(path, **params) or {}
    rows = []
    for cin in j.get("m2m:cnt", {}).get("m2m:cin", []):
        con = m2m.content_of({"m2m:cin": cin})
        if isinstance(con, dict):
            rows.append((con, cin.get("ct", "")))
    return sorted(rows, key=lambda r: r[1], reverse=True)


def line_status():
    j = get_json("LineSensor-01/lineStatus/la")
    return m2m.content_of(j) if j else None


def last_result():
    if RESULT_MODE == "flex":
        j = get_json("Inspection/inspectionResult")
        con = m2m.content_of(j) if j else None
    else:
        j = get_json("Inspection/inspectionResult/la")
        con = m2m.content_of(j) if j else None
    return con if con and con.get("seq") else None


def defect_history(limit=30):
    """Find containers labeled type:defect through discovery and list their cins, newest first."""
    uris = (get_json("", fu=1, lbl="type:defect", ty=3) or {}).get("m2m:uril", [])
    rows = []
    for u in uris:
        rows += [{**con, "ct": ct} for con, ct in cins(u)]
    return sorted(rows, key=lambda r: r["ct"], reverse=True)[:limit]


def image_of(seq):
    """Photo for product seq. If the seq shows up more than once, the newest one wins."""
    cache = image_cache()
    if seq in cache:
        return cache[seq][1]
    con = m2m.content_of(get_json("ImageSource-01/xrayImages/la") or {}) or {}
    if con.get("seq") != seq:
        con = next((c for c, _ in cins("ImageSource-01/xrayImages", minutes=3) if c.get("seq") == seq), {})
    if not con.get("img"):
        return None
    img = Image.open(io.BytesIO(base64.b64decode(con["img"]))).convert("RGB")
    cache[seq] = (time.time(), img)
    while len(cache) > 20:
        cache.popitem(last=False)
    return img


def draw_boxes(img, boxes):
    img = img.copy()
    d = ImageDraw.Draw(img)
    for x1, y1, x2, y2, conf, cls in boxes or []:
        d.rectangle([x1, y1, x2, y2], outline=(239, 68, 68), width=3)
        label = f" {cls} {conf:.2f} "
        tw = d.textlength(label)
        ty = max(y1 - 14, 0)
        d.rectangle([x1, ty, x1 + tw, ty + 14], fill=(239, 68, 68))
        d.text((x1, ty + 1), label, fill=(255, 255, 255))
    return img


def send_group(cmd):
    try:
        r = m2m.add_cin("lineA-devices/fopt", ORIG, {"cmd": cmd})
        return m2m.rsc(r)
    except Exception:
        return 0


def esc(v):
    return html.escape(str(v if v is not None else ""))


def ct_time(ct):
    """'20261116T081300,123' (UTC) -> '17:13:00' in this PC's local time."""
    try:
        t = datetime.strptime(ct[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return t.astimezone().strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return ""


# ---------------------------------------------------------------- styles
CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css');
:root {
  --bg:#f5f7fa; --card:#ffffff; --line:#e4e8ee; --ink:#0f172a; --sub:#64748b; --mute:#94a3b8;
  --ok:#16a34a; --ok-bg:#ecfdf3; --ng:#dc2626; --ng-bg:#fef2f2; --warn:#d97706; --warn-bg:#fffbeb;
  --accent:#2563eb; --accent-bg:#eff6ff;
}
html, body, .stMarkdown, .stButton button p, [data-testid="stTab"] p, [data-testid="stCaptionContainer"] {
  font-family: Pretendard, -apple-system, "Segoe UI", "Malgun Gothic", sans-serif !important; }
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { display:none !important; }
[data-testid="stHeader"] { background: transparent; height: 0; }
.block-container { padding-top: 1.6rem !important; padding-bottom: 2rem !important; max-width: 1400px; }
.xg-head { display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
.xg-brand { display:flex; align-items:center; gap:12px; }
.xg-logo { width:40px; height:40px; border-radius:10px; background:var(--ink); color:#fff; display:grid;
  place-items:center; font-weight:800; font-size:18px; letter-spacing:-0.5px; }
.xg-title { font-size:22px; font-weight:750; color:var(--ink); letter-spacing:-0.4px; line-height:1.15; }
.xg-sub { font-size:13px; color:var(--sub); }
.pill { display:inline-flex; align-items:center; gap:7px; padding:5px 12px; border-radius:999px; font-size:13px;
  font-weight:650; border:1px solid transparent; }
.pill .dot { width:8px; height:8px; border-radius:50%; background:currentColor; }
.pill.ok { color:var(--ok); background:var(--ok-bg); border-color:#bbf7d0; }
.pill.ok .dot { animation: pulse 1.6s infinite; }
.pill.ng { color:var(--ng); background:var(--ng-bg); border-color:#fecaca; }
.pill.off { color:var(--sub); background:#f1f5f9; border-color:var(--line); }
@keyframes pulse { 0%{box-shadow:0 0 0 0 rgba(22,163,74,.45)} 70%{box-shadow:0 0 0 7px rgba(22,163,74,0)}
  100%{box-shadow:0 0 0 0 rgba(22,163,74,0)} }
.kpis { display:grid; grid-template-columns:repeat(5, minmax(0,1fr)); gap:12px; margin:6px 0 4px; }
@media (max-width: 900px) { .kpis { grid-template-columns:repeat(2, minmax(0,1fr)); } }
.kpi { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px; }
.kpi .k { font-size:12.5px; color:var(--sub); font-weight:600; }
.kpi .v { font-size:28px; font-weight:750; color:var(--ink); letter-spacing:-0.6px; margin-top:2px;
  font-variant-numeric: tabular-nums; }
.kpi .v small { font-size:14px; color:var(--mute); font-weight:600; margin-left:2px; }
.kpi .h { font-size:12px; color:var(--mute); margin-top:2px; }
.kpi.ok .v { color:var(--ok); } .kpi.ng .v { color:var(--ng); } .kpi.warn .v { color:var(--warn); }
.sec { font-size:14px; font-weight:700; color:var(--ink); margin:2px 0 10px; display:flex; align-items:center;
  justify-content:space-between; }
.sec span { font-size:12px; color:var(--mute); font-weight:500; }
.verdict { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:12px 14px;
  border-radius:12px; margin-bottom:10px; }
.verdict.PASS { background:var(--ok-bg); color:var(--ok); }
.verdict.FAIL { background:var(--ng-bg); color:var(--ng); }
.verdict.ERROR { background:var(--warn-bg); color:var(--warn); }
.verdict .big { font-size:20px; font-weight:800; letter-spacing:-0.3px; }
.verdict .meta { font-size:13px; color:var(--sub); text-align:right; }
.facts { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:8px; margin:10px 0 6px; }
[data-testid="stImage"] img { border-radius:10px; max-height:460px; object-fit:contain; background:#0f172a; }
.fact { background:#f8fafc; border-radius:10px; padding:8px 10px; }
.fact .k { font-size:11.5px; color:var(--mute); } .fact .v { font-size:14px; font-weight:650; color:var(--ink);
  font-variant-numeric: tabular-nums; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.ev { display:flex; align-items:center; gap:10px; padding:9px 2px; border-bottom:1px solid #f1f5f9; font-size:13.5px; }
.ev:last-child { border-bottom:0; }
.ev .tag { font-size:11.5px; font-weight:700; padding:2px 8px; border-radius:6px; min-width:58px; text-align:center; }
.tag.ok { color:var(--ok); background:var(--ok-bg); } .tag.ng { color:var(--ng); background:var(--ng-bg); }
.tag.warn { color:var(--warn); background:var(--warn-bg); } .tag.info { color:var(--accent); background:var(--accent-bg); }
.ev .id { font-weight:650; color:var(--ink); font-variant-numeric: tabular-nums; }
.ev .d { color:var(--sub); flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.ev .t { color:var(--mute); font-size:12px; font-variant-numeric: tabular-nums; }
.empty { color:var(--mute); font-size:13.5px; padding:28px 0; text-align:center; }
.hw { width:100%; border-collapse:collapse; font-size:13.5px; }
.hw td, .hw th { padding:9px 10px; border-bottom:1px solid #f1f5f9; text-align:left; }
.hw th { color:var(--mute); font-weight:600; font-size:12px; }
.hw code { background:#f1f5f9; padding:1px 6px; border-radius:5px; font-size:12.5px; }
div[data-testid="stVerticalBlockBorderWrapper"] { border-radius:14px !important; border-color:var(--line) !important;
  background:var(--card); }
.stButton button { border-radius:10px; font-weight:650; }
</style>
"""


def kpi(label, value, hint="", cls="", unit=""):
    return (f'<div class="kpi {cls}"><div class="k">{esc(label)}</div>'
            f'<div class="v">{esc(value)}<small>{esc(unit)}</small></div><div class="h">{esc(hint)}</div></div>')


# ---------------------------------------------------------------- page
st.set_page_config(page_title="X-Guard", page_icon="🛡️", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
alerts = notify_server()


def command(cmd, label):
    code = send_group(cmd)
    st.toast(f"{label} 명령을 보냈어요" if code in (2000, 2001) else f"{label} 실패 (rsc={code})",
             icon="✅" if code in (2000, 2001) else "⚠️")


head_l, head_r = st.columns([3, 2], vertical_alignment="center")
with head_r:
    b1, b2, b3 = st.columns(3)
    if b1.button("■ 정지", type="primary", **FULL, help="lineA-devices 그룹으로 STOP"):
        command("STOP", "정지")
    if b2.button("▶ 재시작", **FULL, help="lineA-devices 그룹으로 START"):
        command("START", "재시작")
    with b3.popover("하드웨어 시험", **FULL):
        st.caption("센서 없이 아두이노 동작을 확인해요.")
        if st.button("T1 · S1 입구 감지", **FULL):
            command("T1", "T1")
        if st.button("T2 · S2 리젝터 도착", **FULL):
            command("T2", "T2")
        if st.button("SERVO · 서보 한 번 밀기", **FULL):
            command("SERVO", "SERVO")


@st.fragment(run_every=REFRESH_SEC)
def header_status():
    s = line_status()
    if not s:
        pill = '<span class="pill off"><span class="dot"></span>연결 안 됨</span>'
    elif s.get("running"):
        pill = '<span class="pill ok"><span class="dot"></span>가동 중</span>'
    else:
        pill = '<span class="pill ng"><span class="dot"></span>정지</span>'
    st.markdown(f"""<div class="xg-head"><div class="xg-brand"><div class="xg-logo">X</div><div>
        <div class="xg-title">X-Guard</div><div class="xg-sub">oneM2M 컨베이어 이물질 검사 · Line A</div></div>
        <div style="margin-left:8px">{pill}</div></div></div>""", unsafe_allow_html=True)


with head_l:
    header_status()


@st.fragment(run_every=REFRESH_SEC)
def live():
    s = line_status() or {}
    count, passed = s.get("count", 0), s.get("passed", 0)
    rejected, timeout = s.get("rejected", 0), s.get("timeout", 0)
    done = passed + rejected
    rate = f"{(rejected - timeout) / done * 100:.1f}" if done else "-"
    st.markdown('<div class="kpis">'
                + kpi("검사 수", count, f"마지막 #{s.get('lastSeq', '-')}" if s else "게이트웨이 대기")
                + kpi("통과", passed, "OK → PASS", "ok")
                + kpi("배출", rejected, "NG·TIMEOUT → 서보", "ng")
                + kpi("판정 지연 배출", timeout, "S2 도착 때 판정 없음", "warn" if timeout else "")
                + kpi("불량률", rate, "AI 불량 / 처리 완료", "", "%" if done else "")
                + "</div>", unsafe_allow_html=True)

    left, right = st.columns([7, 5], gap="medium")
    with left, st.container(border=True):
        res = last_result()
        st.markdown('<div class="sec">최근 검사<span>inspectionResult · xrayImages</span></div>',
                    unsafe_allow_html=True)
        if res:
            v = res.get("verdict", "PASS")
            seq = res.get("seq")
            boxes = next((a.get("boxes") for a in alerts if a.get("seq") == seq), [])
            word = {"PASS": "정상 · 통과", "FAIL": f"불량 · {res.get('defectClass', '')}",
                    "ERROR": "판정 오류 · 배출"}.get(v, v)
            st.markdown(f'<div class="verdict {esc(v)}"><div class="big">{esc(word)}</div>'
                        f'<div class="meta">#{esc(seq)} · {esc(res.get("productId"))}</div></div>',
                        unsafe_allow_html=True)
            img = image_of(seq)
            if img:
                st.image(draw_boxes(img, boxes), **FULL)
            else:
                st.markdown('<div class="empty">사진을 불러오는 중…</div>', unsafe_allow_html=True)
            st.markdown('<div class="facts">'
                        f'<div class="fact"><div class="k">신뢰도</div><div class="v">{esc(res.get("confidence"))}</div></div>'
                        f'<div class="fact"><div class="k">추론 시간</div><div class="v">{esc(res.get("inferenceMs"))} ms</div></div>'
                        f'<div class="fact"><div class="k">검출 수</div><div class="v">{len(boxes or [])}</div></div>'
                        f'<div class="fact"><div class="k">모델</div><div class="v">{esc(res.get("modelVer"))}</div></div>'
                        '</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="empty">아직 검사 결과가 없어요. 제품이 S1 을 지나면 여기에 나와요.</div>',
                        unsafe_allow_html=True)

    with right, st.container(border=True):
        st.markdown('<div class="sec">실시간 이벤트<span>rejectLog · defects 알림</span></div>',
                    unsafe_allow_html=True)
        events = []
        for con, ct in cins("Rejector-01/rejectLog", minutes=30)[:12]:
            if con.get("action") == "PASSED":
                tag, cls, d = "통과", "ok", "OK 판정 · 그대로 통과"
            elif con.get("reason") == "TIMEOUT":
                tag, cls, d = "지연배출", "warn", "S2 도착 때 판정 없음 → 안전 배출"
            else:
                tag, cls, d = "배출", "ng", "NG 판정 → SG90 배출"
            events.append((ct, tag, cls, con.get("seq"), d, ct_time(ct)))
        for a in list(alerts)[:6]:
            events.append(("~" + a.get("_rx", ""), "불량", "info", a.get("seq"),
                           f"{a.get('defectClass', '')} {a.get('confidence', '')} 검출", a.get("_rx", "")))
        if events:
            events.sort(key=lambda e: e[5], reverse=True)
            st.markdown("".join(
                f'<div class="ev"><span class="tag {c}">{esc(t)}</span><span class="id">#{esc(q)}</span>'
                f'<span class="d">{esc(d)}</span><span class="t">{esc(tm)}</span></div>'
                for _, t, c, q, d, tm in events[:14]), unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="empty">이벤트 대기 중 (알림 :{NOTIFY_PORT}/notify)</div>',
                        unsafe_allow_html=True)


live()

tab1, tab2 = st.tabs(["불량 이력", "장비 구성"])
with tab1:
    @st.fragment(run_every=5)
    def history():
        hist = defect_history()
        if hist:
            st.dataframe([{"시각": ct_time(r["ct"]), "번호": r.get("seq"), "제품": r.get("productId"),
                           "이물": r.get("defectClass"), "신뢰도": r.get("confidence"),
                           "추론(ms)": r.get("inferenceMs"), "모델": r.get("modelVer")} for r in hist],
                         **FULL, hide_index=True)
        else:
            st.markdown('<div class="empty">아직 불량이 없어요. (discovery: lbl=type:defect)</div>',
                        unsafe_allow_html=True)

    history()
with tab2:
    rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td><td><code>{esc(c)}</code></td><td>{esc(d)}</td></tr>"
                   for a, b, c, d in HARDWARE)
    st.markdown(f'<table class="hw"><tr><th>역할</th><th>부품</th><th>핀</th><th>동작</th></tr>{rows}</table>'
                f'<div class="xg-sub" style="margin-top:10px">시리얼 115200bps · 아두이노 → PC '
                f'<code>DET,seq,0</code> <code>REJ,seq,DONE|TIMEOUT</code> <code>PASS,seq</code> · PC → 아두이노 '
                f'<code>OK,seq</code> <code>NG,seq</code> <code>STOP</code> <code>START</code> · 비상정지 버튼 없음 '
                f'(위 정지 버튼 사용)</div>', unsafe_allow_html=True)
