"""X-Guard oneM2M 리소스 생성기.

사용법 (프로젝트 루트에서):
    python setup/setup_resources.py base    # AE, 컨테이너, flexContainer(라벨), 그룹, ACP
    python setup/setup_resources.py subs    # 구독 (받는 프로그램들이 먼저 켜져 있어야 해요)
    python setup/setup_resources.py acp     # ACP만 다시 적용 (권한을 바꿨을 때)
    python setup/setup_resources.py all     # base + subs
    python setup/setup_resources.py show    # 만들어진 리소스 목록 보기
    python setup/setup_resources.py clean   # 전부 지우기

ACME는 enableACPChecks=true가 기본이라, 다른 AE의 컨테이너를 구독하려면 ACP가 먼저 있어야 해요.
그래서 base 단계에서 ACP까지 같이 만들고, acp 명령은 권한을 다시 덮어쓸 때만 써요.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import m2m  # noqa: E402

ADMIN = os.getenv("ADMIN_ORIGIN", "CAdmin")
PC_IP = os.getenv("PC_IP", "127.0.0.1")
MQTT_POA = f"mqtt://{os.getenv('MQTT_HOST', '127.0.0.1')}:{os.getenv('MQTT_PORT', '1883')}"
RESULT_MODE = os.getenv("RESULT_MODE", "flex")

# ---------------------------------------------------------------- AE 정의
AES = {
    "LineSensor-01":  {"orig": "CLineSensor01",  "api": "Nxguard.linesensor", "poa": [MQTT_POA]},
    "Rejector-01":    {"orig": "CRejector01",    "api": "Nxguard.rejector",   "poa": [MQTT_POA]},
    "ImageSource-01": {"orig": "CImageSource01", "api": "Nxguard.image",
                       "poa": [f"http://{PC_IP}:{os.getenv('IMAGE_PORT', '9200')}/notify"]},
    "Inspection":     {"orig": "CInspection",    "api": "Nxguard.inspection",
                       "poa": [os.getenv("AI_NOTIFY_URL", f"http://{PC_IP}:9000/notify")]},
    "Dashboard":      {"orig": "CDashboard",     "api": "Nxguard.dashboard",
                       "poa": [f"http://{PC_IP}:{os.getenv('DASH_NOTIFY_PORT', '9100')}/notify"]},
}

# (AE, 컨테이너 이름, 추가 속성)
CONTAINERS = [
    ("LineSensor-01", "arrivals",   {"mni": 500, "lbl": ["line:A", "type:arrival"]}),
    ("LineSensor-01", "lineStatus", {"mni": 50,  "lbl": ["line:A", "type:status"]}),
    ("LineSensor-01", "cmd",        {"mni": 20,  "lbl": ["line:A", "type:cmd"]}),
    ("Rejector-01",   "rejectLog",  {"mni": 500, "lbl": ["line:A", "type:reject"]}),
    ("Rejector-01",   "cmd",        {"mni": 20,  "lbl": ["line:A", "type:cmd"]}),
    ("ImageSource-01", "xrayImages", {"mni": 100, "mbs": 2_000_000, "lbl": ["line:A", "type:image"]}),
    ("Inspection",    "defects",    {"mni": 500, "lbl": ["line:A", "type:defect"]}),
]

GROUP = "lineA-devices"
GROUP_MEMBERS = [f"{m2m.CSE_RN}/LineSensor-01/cmd", f"{m2m.CSE_RN}/Rejector-01/cmd"]

# 구독: (대상 경로, 구독 이름, 알림 받을 AE, 이벤트 타입, 만드는 originator)
#  net 1=리소스 수정, 3=자식 생성
def subscriptions():
    res_net = [1] if RESULT_MODE == "flex" else [3]
    return [
        ("LineSensor-01/arrivals",       "sub-image",    "ImageSource-01", [3], "CImageSource01"),
        ("ImageSource-01/xrayImages",    "sub-ai",       "Inspection",     [3], "CInspection"),
        ("Inspection/inspectionResult",  "sub-rejector", "Rejector-01",    res_net, "CRejector01"),
        ("Inspection/defects",           "sub-dash",     "Dashboard",      [3], "CDashboard"),
        ("LineSensor-01/cmd",            "sub-gw",       "LineSensor-01",  [3], "CLineSensor01"),
        ("Rejector-01/cmd",              "sub-gw",       "Rejector-01",    [3], "CRejector01"),
    ]


# ACP 권한 비트: C=1 R=2 U=4 D=8 N=16 Disc=32  (63=전부)
C, R, U, D, N, DISC = 1, 2, 4, 8, 16, 32
READ = R | N | DISC
ACPS = {
    # ACP 이름: (적용할 리소스들, [(originator 목록, 권한)])
    "acp-linesensor": (["LineSensor-01"], [
        (["CLineSensor01"], 63), (["CImageSource01", "CDashboard"], READ | C)]),
    "acp-rejector": (["Rejector-01"], [
        (["CRejector01"], 63), (["CDashboard"], READ | C)]),
    "acp-image": (["ImageSource-01"], [
        (["CImageSource01"], 63), (["CInspection", "CDashboard"], READ | C)]),
    "acp-inspection": (["Inspection"], [
        (["CInspection"], 63), (["CRejector01", "CDashboard"], READ | C)]),
    "acp-dashboard": (["Dashboard"], [(["CDashboard"], 63)]),
    # 비상 정지 명령: 대시보드가 그룹을 통해 두 cmd 컨테이너에 cin을 쓸 수 있어야 해요
    "acp-cmd": (["LineSensor-01/cmd", "Rejector-01/cmd"], [
        (["CLineSensor01", "CRejector01"], 63), (["CDashboard"], C | R)]),
    "acp-group": ([GROUP], [(["CDashboard"], C | R | DISC)]),
}


# ---------------------------------------------------------------- helpers
def show(tag, resp):
    code = m2m.rsc(resp)
    mark = {2000: "OK", 2001: "생성", 2004: "수정", 2002: "삭제", 4105: "이미 있음",
            4117: "이미 등록됨"}.get(code, "실패")
    print(f"  [{code} {mark}] {tag}")
    if mark == "실패":
        print("     ", resp.text[:300])
    return code


def owner_of(path):
    """경로의 첫 AE 이름으로 originator를 찾는다. 그룹·ACP 등은 CAdmin."""
    ae = path.split("/")[0]
    return AES[ae]["orig"] if ae in AES else ADMIN


# ---------------------------------------------------------------- 단계
def base():
    print("== AE 등록")
    for rn, a in AES.items():
        body = {"m2m:ae": {"rn": rn, "api": a["api"], "rr": True, "srv": ["3"], "poa": a["poa"],
                           "lbl": ["line:A"]}}
        code = show(rn, m2m.create("", a["orig"], m2m.TY["ae"], body))
        if code in (4105, 4117):  # 이미 있으면 poa만 최신으로 (ACME는 같은 originator 재등록에 4117)
            show(f"{rn} poa 갱신", m2m.update(rn, a["orig"], {"m2m:ae": {"poa": a["poa"]}}))

    print("== 컨테이너")
    for ae, rn, extra in CONTAINERS:
        body = {"m2m:cnt": {"rn": rn, **extra}}
        show(f"{ae}/{rn}", m2m.create(ae, AES[ae]["orig"], m2m.TY["cnt"], body))

    print("== 판정 결과 inspectionResult")
    if RESULT_MODE == "flex":
        body = {"xg:inRet": {"rn": "inspectionResult", "cnd": "org.alom.xguard.inspectionResult",
                             "lbl": ["line:A", "type:result"],
                             "seq": 0, "productId": "", "verdict": "PASS", "defectClass": "",
                             "confidence": 0.0, "inferenceMs": 0, "modelVer": ""}}
        code = show("Inspection/inspectionResult (flexContainer xg:inRet)",
                    m2m.create("Inspection", "CInspection", m2m.TY["fcnt"], body))
        if code not in (2001, 4105):
            print("  !! flexContainer 생성 실패. cse/init/xguard.fcp 가 ACME에 읽혔는지 확인하거나 "
                  ".env 에서 RESULT_MODE=cin 으로 바꾸고 다시 실행하세요.")
    else:
        body = {"m2m:cnt": {"rn": "inspectionResult", "mni": 500, "lbl": ["line:A", "type:result"]}}
        show("Inspection/inspectionResult (container, RESULT_MODE=cin)",
             m2m.create("Inspection", "CInspection", m2m.TY["cnt"], body))

    print("== 그룹")
    body = {"m2m:grp": {"rn": GROUP, "mt": 3, "mnm": 10, "mid": GROUP_MEMBERS,
                        "lbl": ["line:A", "type:group"]}}
    show(GROUP, m2m.create("", ADMIN, m2m.TY["grp"], body))

    acp()


def _acp_ri(name):
    r = m2m.retrieve(name, ADMIN)
    if m2m.rsc(r) == 2000:
        return r.json()["m2m:acp"]["ri"]
    return None


def acp():
    print("== ACP")
    for name, (targets, rules) in ACPS.items():
        acr = [{"acor": orig + [ADMIN], "acop": op} for orig, op in rules]
        pv = {"acr": acr}
        pvs = {"acr": [{"acor": [ADMIN] + [o for orig, _ in rules for o in orig], "acop": 63}]}
        r = m2m.create("", ADMIN, m2m.TY["acp"], {"m2m:acp": {"rn": name, "pv": pv, "pvs": pvs}})
        code = show(name, r)
        if code == 4105:
            show(f"{name} 갱신", m2m.update(name, ADMIN, {"m2m:acp": {"pv": pv, "pvs": pvs}}))
        ri = _acp_ri(name)
        for t in expand(targets):
            show(f"  acpi {t} <- {name}", m2m.update(t, owner_of(t), {res_key(t): {"acpi": [ri]}}))


def res_key(path):
    if path == GROUP:
        return "m2m:grp"
    if "/" not in path:
        return "m2m:ae"
    if path.endswith("inspectionResult") and RESULT_MODE == "flex":
        return "xg:inRet"
    return "m2m:cnt"


def expand(targets):
    """ACME는 acpi가 없는 컨테이너를 부모 AE의 ACP로 보지 않아서, AE의 컨테이너에도 같은 ACP를 붙여요.
    (cmd 컨테이너는 acp-cmd 를 따로 써요)"""
    out = []
    for t in targets:
        out.append(t)
        if t in AES:
            out += [f"{ae}/{rn}" for ae, rn, _ in CONTAINERS if ae == t and rn != "cmd"]
            if t == "Inspection":
                out.append("Inspection/inspectionResult")
    return out


def subs(only=None):
    """only='http' -> HTTP로 받는 AE(이미지·AI·대시보드)만, 'mqtt' -> 게이트웨이(MQTT)만, None -> 전부."""
    print("== 구독 (알림 받을 프로그램이 켜져 있어야 2001이 나와요)" + (f" [{only}]" if only else ""))
    # nu 는 AE-ID(예: CRejector01)로 둬요. 그래야 ACME가 AE의 poa로 바로 보내고,
    # MQTT 토픽도 명세서처럼 /oneM2M/req/id-mn/CRejector01/json 이 돼요.
    for target, rn, ae, net, orig in subscriptions():
        if only and not AES[ae]["poa"][0].startswith(only):
            continue
        body = {"m2m:sub": {"rn": rn, "enc": {"net": net}, "nu": [AES[ae]["orig"]], "nct": 1}}
        code = show(f"{target}/{rn} -> {ae}", m2m.create(target, orig, m2m.TY["sub"], body))
        if code not in (2001, 4105):
            print(f"     -> {ae} 프로그램이 켜져 있는지, poa 주소({AES[ae]['poa'][0]})가 맞는지 확인하세요.")


def show_all():
    r = m2m.retrieve("", ADMIN, fu=1)  # discovery 는 rcn 을 붙이면 ACME 가 거절해요
    for uri in r.json().get("m2m:uril", []):
        print(" ", uri)


def clean():
    print("== 삭제")
    for rn, a in AES.items():
        show(rn, m2m.delete(rn, a["orig"]))
    show(GROUP, m2m.delete(GROUP, ADMIN))
    for name in ACPS:
        show(name, m2m.delete(name, ADMIN))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(f"CSE: {m2m.CSE_URL}/{m2m.CSE_RN}   RESULT_MODE={RESULT_MODE}")
    {"base": base, "subs": subs, "subs-http": lambda: subs("http"), "subs-mqtt": lambda: subs("mqtt"), "acp": acp, "show": show_all, "clean": clean,
     "all": lambda: (base(), subs())}[cmd]()
