/*
 * X-Guard 컨베이어 컨트롤러 - Arduino Uno (감지 센서 2개 버전)
 *
 * 우리 하드웨어 기준 (2026-11-15 하드웨어 팀 수정)
 *   D2  S1 E18-D80NK #1 검정 선  입구 · 제품 진입 (내부 풀업)
 *   D3  S2 E18-D80NK #2 검정 선  리젝터 바로 앞 (내부 풀업)
 *       우리 센서는 비어 있을 때 LOW 로 읽혀요(설명서와 반대). 그래서 켤 때(센서 앞을 비워 두고)
 *       읽은 값을 '비어 있음'으로 기억하고, 그 반대 값으로 바뀌는 순간을 감지로 봐요.
 *       -> 아두이노를 켜거나 리셋할 때는 센서 앞에 물건을 두지 마세요.
 *   D9  서보 MG996R 주황(신호)    빨강 = 외부 5~6V(2A 이상) +, 갈색 = 외부 전원 - 와 아두이노 GND 같이 연결
 *                                 (MG996R 은 최대 2.5A 라 아두이노 5V/USB 로는 못 돌려요)
 *                                 USB 전원을 아끼려고 밀 때만 서보에 신호를 주고, 제자리로 오면 신호를 끊어요(detach).
 *                                 1000uF 콘덴서를 아두이노 5V-GND 사이에 꽂아 두면 더 안정적이에요(극성 주의).
 *   D12 비상정지 버튼 없음        USE_ESTOP = false. 정지는 노트북/대시보드의 STOP 명령으로.
 *   D13 내장 L LED               켜짐 = 가동, 깜빡임 = 정지, 서보가 밀 때 잠깐 꺼짐
 *   센서 갈색 = 5V, 파랑 = GND
 *   IR 조명(LED-36 850nm)은 12V 어댑터로 따로 (아두이노와 연결 안 함).
 *   카메라(ELP OV9281)는 노트북 USB, 벨트 옆에서 제품을 옆으로 찍음 (조명은 반대편 옆).
 *     -> S1(입구)은 카메라 화면 가운데 근처에 두면 CAPTURE_DELAY_MS=0 으로 바로 찍혀요.
 *        카메라보다 앞에 두면 .env 의 CAPTURE_DELAY_MS 에 S1 -> 화면 가운데 이동 시간을 넣어요.
 *
 * 시리얼 115200bps, 한 줄에 한 메시지 ('\n' 끝) — gateway/gateway.py 와 같은 형식
 *   보냄  DET,<seq>,<pulse>    S1 감지. 벨트 펄스 센서가 없어서 pulse 는 항상 0   예) DET,17,0
 *         REJ,<seq>,DONE       NG 판정 -> 배출
 *         REJ,<seq>,TIMEOUT    S2 에 왔는데 판정 없음 -> 안전하게 배출
 *         PASS,<seq>           OK 판정 -> 통과
 *         STATUS,RUNNING | STATUS,STOPPED
 *         CNT,0                (호환용) CNT 명령 응답
 *   받음  OK,<seq> / NG,<seq>  판정 (예전 형식 V,<seq>,P|F 도 받음)
 *         STOP / START         비상 정지 / 재시작
 *         T1                   (시험) S1 감지 흉내
 *         T2                   (시험) S2 감지 흉내 = 맨 앞 제품이 리젝터에 옴
 *         SERVO                (시험) 서보 한 번 밀기 (로그 메시지 없음)
 *
 * 흐름
 *   S1 감지 -> DET 전송 -> 게이트웨이/AI 판정 -> OK/NG 수신 -> 대기열의 그 제품에 기록
 *   S2 감지 -> 대기열 맨 앞 제품 꺼냄 -> NG 나 판정 없음이면 서보로 밀고 REJ, OK 면 PASS
 *   (S1 ~ S2 사이에 여러 제품이 있어도 들어온 순서대로 처리)
 *   S1 -> S2 이동 시간이 AI 판정 시간(촬영 + 업로드 + 추론)보다 길어야 TIMEOUT 이 안 나요.
 */
#include <Arduino.h>
#include <Servo.h>

// ---------------- 핀 ----------------
const uint8_t PIN_S1 = 2;
const uint8_t PIN_S2 = 3;
const uint8_t PIN_SERVO = 9;
const uint8_t PIN_ESTOP = 12;
const uint8_t PIN_LED = 13;

// ---------------- 현장에서 맞추는 값 ----------------
const int SERVO_REST = 10;                // 날개 대기 각도 (벨트 가장자리)
const int SERVO_PUSH = 100;               // 날개 미는 각도 (벨트 가로질러)
const unsigned long PUSH_DELAY_MS = 0;    // S2 감지 -> 날개 앞 도착까지 시간. S2 가 날개 바로 앞이면 0
const unsigned long PUSH_HOLD_MS = 5000;   // 밀고 있는 시간 (끝까지 간 뒤 버티는 시간)
const int SERVO_STEP_DEG = 3;             // 한 번에 움직이는 각도 (작을수록 순간 전류가 작아요)
const unsigned long SERVO_STEP_MS = 10;   // 한 걸음 사이 시간 (90도 ≈ 0.3초)
const int SERVO_MIN_US = 500, SERVO_MAX_US = 2500;  // MG996R 펄스 폭
const unsigned long DET_GAP_MS = 300;     // 한 제품이 두 번 잡히지 않게 (제품 간격보다 짧게)
const unsigned long DEBOUNCE_MS = 15;     // 센서 떨림 무시
const unsigned long ESTOP_DEBOUNCE_MS = 50;
const bool USE_ESTOP = false;             // 비상정지 버튼을 달면 true (D12 - GND)
const unsigned long REST_SETTLE_MS = 400; // 제자리로 돌아오는 시간. 그 뒤 서보 신호를 끊어 전류·떨림을 줄여요

// S2 를 아직 안 달았으면 false -> S1 감지 후 TRAVEL_MS 지나면 리젝터에 온 것으로 처리
const bool USE_S2 = true;
const unsigned long TRAVEL_MS = 3000;

// ---------------- 대기열 (S1 ~ S2 사이 제품) ----------------
const uint8_t QSIZE = 16;
struct Item {
  uint16_t seq;
  unsigned long ms;  // S1 감지 시각
  char verdict;      // '?' 아직 없음, 'P' 통과, 'F' 배출
};
Item q[QSIZE];
uint8_t qHead = 0, qCount = 0;

uint16_t seqNo = 0;
bool running = true;

// ---------------- 서보 (밀기 예약은 여러 개 가능) ----------------
Servo rejector;
const uint8_t PSIZE = 8;
unsigned long pushAt[PSIZE];
uint8_t pHead = 0, pCount = 0;
unsigned long pushUntil = 0;  // 0 이 아니면 밀고 버티는 중 (끝나는 시각)
unsigned long detachAt = 0;   // 0 이 아니면 그 시각에 서보 신호 끊기
int servoCur = SERVO_REST;    // 지금 각도
int servoTarget = SERVO_REST; // 가려는 각도
unsigned long servoStepAt = 0;

char rx[32];
uint8_t rxLen = 0;

// ---------------- 센서 ----------------
struct Sensor {
  uint8_t pin;
  bool stable, last;
  unsigned long changedAt, lastHitAt;
  bool idle;  // 비어 있을 때 읽히는 값 (켤 때 자동으로 정해요)
};
Sensor s1 = {PIN_S1, HIGH, HIGH, 0, 0, HIGH};
Sensor s2 = {PIN_S2, HIGH, HIGH, 0, 0, HIGH};

// 켤 때 센서 앞이 비어 있다고 보고 지금 값을 '비어 있음'으로 기억
void initSensor(Sensor &s) {
  bool v = digitalRead(s.pin);
  s.idle = s.stable = s.last = v;
  s.changedAt = millis();
}

// 감지가 새로 시작된 순간에만 true
bool sensorHit(Sensor &s) {
  bool raw = digitalRead(s.pin);
  unsigned long now = millis();
  if (raw != s.last) {
    s.last = raw;
    s.changedAt = now;
  }
  if (now - s.changedAt >= DEBOUNCE_MS && raw != s.stable) {
    s.stable = raw;
    if (s.stable != s.idle && now - s.lastHitAt >= DET_GAP_MS) {
      s.lastHitAt = now;
      return true;
    }
  }
  return false;
}

// ---------------- 대기열 ----------------
void qPush(uint16_t s, unsigned long ms) {
  if (qCount == QSIZE) {  // 넘치면 가장 오래된 것을 버림
    qHead = (qHead + 1) % QSIZE;
    qCount--;
  }
  q[(qHead + qCount) % QSIZE] = {s, ms, '?'};
  qCount++;
}

bool qPop(Item &out) {
  if (qCount == 0) return false;
  out = q[qHead];
  qHead = (qHead + 1) % QSIZE;
  qCount--;
  return true;
}

void setVerdict(uint16_t s, char v) {
  for (uint8_t i = 0; i < qCount; i++) {
    Item &it = q[(qHead + i) % QSIZE];
    if (it.seq == s) {
      it.verdict = v;
      return;
    }
  }
}

// ---------------- 서보 ----------------
void schedulePush() {
  if (pCount == PSIZE) return;
  pushAt[(pHead + pCount) % PSIZE] = millis() + PUSH_DELAY_MS;
  pCount++;
}

bool servoBusy() { return servoCur != SERVO_REST || servoTarget != SERVO_REST; }

void servoOn() {
  if (!rejector.attached()) {
    rejector.attach(PIN_SERVO, SERVO_MIN_US, SERVO_MAX_US);
    rejector.write(servoCur);
  }
}

// 전력 아끼는 서보: 조금씩 천천히 움직이고, 제자리에 오면 신호를 끊어요
void updateServo() {
  unsigned long now = millis();
  // 1) 목표 각도로 한 걸음씩
  if (servoCur != servoTarget && (long)(now - servoStepAt) >= (long)SERVO_STEP_MS) {
    servoStepAt = now;
    int d = servoTarget - servoCur;
    servoCur += (d > 0) ? min(SERVO_STEP_DEG, d) : max(-SERVO_STEP_DEG, d);
    rejector.write(servoCur);
    if (servoCur == servoTarget) {
      if (servoTarget == SERVO_PUSH) {
        pushUntil = now + PUSH_HOLD_MS;            // 끝까지 밀었으면 잠깐 버티기
        if (pushUntil == 0) pushUntil = 1;
      } else {
        detachAt = now + REST_SETTLE_MS;           // 제자리 도착 -> 잠시 뒤 신호 끊기
        if (detachAt == 0) detachAt = 1;
      }
    }
  }
  // 2) 버티기 끝 -> 제자리로
  if (pushUntil && (long)(now - pushUntil) >= 0) {
    pushUntil = 0;
    servoTarget = SERVO_REST;
  }
  // 3) 쉬는 중이고 예약된 밀기가 있으면 시작
  if (!servoBusy() && pCount && (long)(now - pushAt[pHead]) >= 0) {
    pHead = (pHead + 1) % PSIZE;
    pCount--;
    detachAt = 0;
    servoOn();
    servoTarget = SERVO_PUSH;
  }
  // 4) 제자리에서 쉬면 신호 끊기 (대기 전류 줄이기)
  if (detachAt && !servoBusy() && (long)(now - detachAt) >= 0) {
    rejector.detach();
    detachAt = 0;
  }
}

// ---------------- 동작 ----------------
void sendStatus() {
  Serial.print(F("STATUS,"));
  Serial.println(running ? F("RUNNING") : F("STOPPED"));
}

void setRunning(bool on) {
  running = on;
  sendStatus();
}

// SENSOR,S1,<비어있음 값>,<지금>,S2,... (H/L)   예) SENSOR,S1,idle=L,now=L,S2,idle=L,now=H
void reportSensors() {
  Serial.print(F("SENSOR,S1,idle="));
  Serial.print(s1.idle ? 'H' : 'L');
  Serial.print(F(",now="));
  Serial.print(digitalRead(PIN_S1) ? 'H' : 'L');
  Serial.print(F(",S2,idle="));
  Serial.print(s2.idle ? 'H' : 'L');
  Serial.print(F(",now="));
  Serial.println(digitalRead(PIN_S2) ? 'H' : 'L');
}

void onS1() {
  if (!running) return;
  seqNo++;
  if (seqNo == 0) seqNo = 1;
  qPush(seqNo, millis());
  Serial.print(F("DET,"));
  Serial.print(seqNo);
  Serial.println(F(",0"));
}

void onS2() {
  Item it;
  if (!qPop(it)) return;  // S1 을 안 거친 물체 (손 등) -> 무시
  if (it.verdict == 'P') {
    Serial.print(F("PASS,"));
    Serial.println(it.seq);
  } else {
    schedulePush();  // NG 또는 판정 없음 -> 배출
    Serial.print(F("REJ,"));
    Serial.print(it.seq);
    Serial.println(it.verdict == 'F' ? F(",DONE") : F(",TIMEOUT"));
  }
}

void handleLine(char *line) {
  while (*line == ' ') line++;
  for (int i = (int)strlen(line) - 1; i >= 0 && line[i] == ' '; i--) line[i] = 0;
  if (!*line) return;

  if (!strncmp(line, "OK,", 3) || !strncmp(line, "NG,", 3)) {  // OK,17 / NG,17
    setVerdict(atoi(line + 3), line[0] == 'O' ? 'P' : 'F');
  } else if (!strncmp(line, "V,", 2)) {  // 예전 형식 V,17,F
    char *c2 = strchr(line + 2, ',');
    if (c2 && (c2[1] == 'P' || c2[1] == 'F')) setVerdict(atoi(line + 2), c2[1]);
  } else if (!strcmp(line, "STOP")) {
    setRunning(false);
  } else if (!strcmp(line, "START")) {
    setRunning(true);
  } else if (!strcmp(line, "T1")) {
    onS1();
  } else if (!strcmp(line, "T2")) {
    onS2();
  } else if (!strcmp(line, "SERVO")) {
    schedulePush();
  } else if (!strcmp(line, "SENS")) {
    reportSensors();
  } else if (!strcmp(line, "CNT")) {
    Serial.println(F("CNT,0"));
  }
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      rx[rxLen] = 0;
      handleLine(rx);
      rxLen = 0;
    } else if (c != '\r' && rxLen < sizeof(rx) - 1) {
      rx[rxLen++] = c;
    }
  }
}

// 비상정지 버튼: 누를 때마다 정지 <-> 가동
void checkEstop() {
  static bool stable = HIGH, last = HIGH;
  static unsigned long changedAt = 0;
  bool raw = digitalRead(PIN_ESTOP);
  if (raw != last) {
    last = raw;
    changedAt = millis();
  }
  if (millis() - changedAt >= ESTOP_DEBOUNCE_MS && raw != stable) {
    stable = raw;
    if (stable == LOW) setRunning(!running);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_S1, INPUT_PULLUP);
  pinMode(PIN_S2, INPUT_PULLUP);
  pinMode(PIN_ESTOP, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);
  rejector.attach(PIN_SERVO, SERVO_MIN_US, SERVO_MAX_US);
  rejector.write(SERVO_REST);
  delay(REST_SETTLE_MS);   // 처음에 제자리로 보낸 뒤
  rejector.detach();       // 신호를 끊어 대기 중 전류를 줄여요
  initSensor(s1);          // 센서 앞이 비어 있는 상태를 기준으로 (가짜 DET/REJ 방지)
  initSensor(s2);
  setRunning(true);
  reportSensors();
}

void loop() {
  readSerial();                         // 1) 판정·명령 수신
  if (sensorHit(s1)) onS1();            // 2) 입구 감지
  if (USE_S2) {                         // 3) 리젝터 앞 도착
    if (sensorHit(s2)) onS2();
  } else {
    while (qCount && millis() - q[qHead].ms >= TRAVEL_MS) onS2();
  }
  updateServo();                        // 4) 서보
  if (USE_ESTOP) checkEstop();          // 5) 비상정지 (버튼 달았을 때만), LED
  if (servoBusy()) digitalWrite(PIN_LED, LOW);
  else digitalWrite(PIN_LED, running ? HIGH : ((millis() / 300) % 2));
}