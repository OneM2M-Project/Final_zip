/*
 * X-Guard conveyor controller for Arduino Uno, two-sensor version.
 *
 * Wiring (updated by the hardware team, 2026-11-15)
 *   D2  S1 E18-D80NK #1, black wire. Entry sensor (internal pull-up).
 *   D3  S2 E18-D80NK #2, black wire. Right in front of the rejector (internal pull-up).
 *       Our sensors read LOW when nothing is in front of them, the opposite of the datasheet.
 *       So at power-up we store whatever we read as "empty" and treat a change away from it
 *       as a hit. Keep the sensors clear when powering up or resetting the board.
 *   D9  Servo MG996R, orange = signal. Red = external 5-6 V supply (2 A or more), brown =
 *       external supply ground tied to Arduino GND. The MG996R can pull 2.5 A, too much for
 *       the Arduino 5 V pin or USB. To go easy on power the servo only gets a signal while
 *       pushing and is detached once it's back at rest. A 1000 uF cap across 5 V and GND
 *       helps (watch the polarity).
 *   D12 No e-stop button fitted (USE_ESTOP = false). Stop the line with the STOP command
 *       from the laptop or dashboard.
 *   D13 Onboard LED: on = running, blinking = stopped, briefly off while the servo pushes.
 *   Sensor brown = 5 V, blue = GND.
 *   The IR light (LED-36, 850 nm) runs off its own 12 V adapter, not the Arduino.
 *   The camera (ELP OV9281) is on the laptop's USB and shoots the product side-on from
 *   beside the belt, with the light on the far side. If S1 sits near the middle of the
 *   camera's view, CAPTURE_DELAY_MS=0 works. If S1 is upstream of the camera, set
 *   CAPTURE_DELAY_MS in .env to the travel time from S1 to the middle of the frame.
 *
 * Serial at 115200 baud, one message per line ending in '\n'. Same format as gateway/gateway.py.
 *   Sends     DET,<seq>,<pulse>    S1 hit. No belt encoder, so pulse is always 0, e.g. DET,17,0
 *             REJ,<seq>,DONE       NG verdict, pushed off
 *             REJ,<seq>,TIMEOUT    reached S2 with no verdict, pushed off to be safe
 *             PASS,<seq>           OK verdict, passed
 *             STATUS,RUNNING | STATUS,STOPPED
 *             CNT,0                reply to CNT, kept for compatibility
 *   Receives  OK,<seq> / NG,<seq>  verdict (the old V,<seq>,P|F form also works)
 *             STOP / START         stop / restart
 *             T1                   test: fake an S1 hit
 *             T2                   test: fake an S2 hit (head of the queue reaches the rejector)
 *             SERVO                test: push the servo once (prints nothing)
 *
 * Flow
 *   S1 hit -> send DET -> gateway/AI decide -> OK/NG comes back -> stored on that queue item.
 *   S2 hit -> pop the head of the queue -> NG or no verdict: push and send REJ; OK: send PASS.
 *   Several products can be between S1 and S2; they're handled in the order they came in.
 *   The S1 -> S2 travel time has to be longer than the AI round trip (capture + upload +
 *   inference), otherwise products come out as TIMEOUT.
 */
#include <Arduino.h>
#include <Servo.h>

// ---------------- pins ----------------
const uint8_t PIN_S1 = 2;
const uint8_t PIN_S2 = 3;
const uint8_t PIN_SERVO = 9;
const uint8_t PIN_ESTOP = 12;
const uint8_t PIN_LED = 13;

// ---------------- tune these on the rig ----------------
const int SERVO_REST = 10;                // arm at rest (edge of the belt)
const int SERVO_PUSH = 100;               // arm pushed (across the belt)
const unsigned long PUSH_DELAY_MS = 0;    // time from S2 hit to the product reaching the arm; 0 if S2 is right at the arm
const unsigned long PUSH_HOLD_MS = 5000;   // how long to hold at full push
const int SERVO_STEP_DEG = 3;             // degrees per step; smaller steps draw less peak current
const unsigned long SERVO_STEP_MS = 10;   // time between steps (90 degrees takes about 0.3 s)
const int SERVO_MIN_US = 500, SERVO_MAX_US = 2500;  // MG996R pulse width
const unsigned long DET_GAP_MS = 300;     // stops one product counting twice; keep it shorter than the gap between products
const unsigned long DEBOUNCE_MS = 15;     // ignore sensor chatter
const unsigned long ESTOP_DEBOUNCE_MS = 50;
const bool USE_ESTOP = false;             // set true if an e-stop button is wired (D12 to GND)
const unsigned long REST_SETTLE_MS = 400; // time to settle at rest before detaching, which cuts current and jitter

// false if S2 isn't fitted yet: treat a product as at the rejector TRAVEL_MS after S1
const bool USE_S2 = true;
const unsigned long TRAVEL_MS = 3000;

// ---------------- queue of products between S1 and S2 ----------------
const uint8_t QSIZE = 16;
struct Item {
  uint16_t seq;
  unsigned long ms;  // when S1 saw it
  char verdict;      // '?' none yet, 'P' pass, 'F' reject
};
Item q[QSIZE];
uint8_t qHead = 0, qCount = 0;

uint16_t seqNo = 0;
bool running = true;

// ---------------- servo (several pushes can be queued) ----------------
Servo rejector;
const uint8_t PSIZE = 8;
unsigned long pushAt[PSIZE];
uint8_t pHead = 0, pCount = 0;
unsigned long pushUntil = 0;  // nonzero while holding a push: when the hold ends
unsigned long detachAt = 0;   // nonzero: when to detach the servo
int servoCur = SERVO_REST;    // current angle
int servoTarget = SERVO_REST; // target angle
unsigned long servoStepAt = 0;

char rx[32];
uint8_t rxLen = 0;

// ---------------- sensors ----------------
struct Sensor {
  uint8_t pin;
  bool stable, last;
  unsigned long changedAt, lastHitAt;
  bool idle;  // reading when nothing is there (set at power-up)
};
Sensor s1 = {PIN_S1, HIGH, HIGH, 0, 0, HIGH};
Sensor s2 = {PIN_S2, HIGH, HIGH, 0, 0, HIGH};

// assume the sensor is clear at power-up and remember this reading as "empty"
void initSensor(Sensor &s) {
  bool v = digitalRead(s.pin);
  s.idle = s.stable = s.last = v;
  s.changedAt = millis();
}

// true only on the moment a new detection starts
bool sensorHit(Sensor &s) {
  bool raw = digitalRead(s.pin);
  unsigned long now = millis();
  if (raw != s.last) {
    s.last = raw;
    s.changedAt = now;
  }
  if (now - s.changedAt >= DEBOUNCE_MS && raw != s.stable) {
    s.stable = raw;
    // debug: report every sensor change; shows up in logs/gateway.log as "아두이노: RAW,S1,L"
    Serial.print(F("RAW,S"));
    Serial.print(s.pin == PIN_S1 ? 1 : 2);
    Serial.println(raw ? F(",H") : F(",L"));
    if (s.stable != s.idle && now - s.lastHitAt >= DET_GAP_MS) {
      s.lastHitAt = now;
      return true;
    }
  }
  return false;
}

// ---------------- queue ----------------
void qPush(uint16_t s, unsigned long ms) {
  if (qCount == QSIZE) {  // full: drop the oldest
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

// ---------------- servo ----------------
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

// low-power servo handling: move in small slow steps and detach once back at rest
void updateServo() {
  unsigned long now = millis();
  // 1) step toward the target angle
  if (servoCur != servoTarget && (long)(now - servoStepAt) >= (long)SERVO_STEP_MS) {
    servoStepAt = now;
    int d = servoTarget - servoCur;
    servoCur += (d > 0) ? min(SERVO_STEP_DEG, d) : max(-SERVO_STEP_DEG, d);
    rejector.write(servoCur);
    if (servoCur == servoTarget) {
      if (servoTarget == SERVO_PUSH) {
        pushUntil = now + PUSH_HOLD_MS;            // fully pushed, hold for a bit
        if (pushUntil == 0) pushUntil = 1;
      } else {
        detachAt = now + REST_SETTLE_MS;           // back at rest, detach shortly
        if (detachAt == 0) detachAt = 1;
      }
    }
  }
  // 2) hold finished, go back to rest
  if (pushUntil && (long)(now - pushUntil) >= 0) {
    pushUntil = 0;
    servoTarget = SERVO_REST;
  }
  // 3) idle and a push is due: start it
  if (!servoBusy() && pCount && (long)(now - pushAt[pHead]) >= 0) {
    pHead = (pHead + 1) % PSIZE;
    pCount--;
    detachAt = 0;
    servoOn();
    servoTarget = SERVO_PUSH;
  }
  // 4) resting: detach to cut idle current
  if (detachAt && !servoBusy() && (long)(now - detachAt) >= 0) {
    rejector.detach();
    detachAt = 0;
  }
}

// ---------------- actions ----------------
void sendStatus() {
  Serial.print(F("STATUS,"));
  Serial.println(running ? F("RUNNING") : F("STOPPED"));
}

void setRunning(bool on) {
  running = on;
  sendStatus();
}

// SENSOR,S1,<empty value>,<now>,S2,... (H/L), e.g. SENSOR,S1,idle=L,now=L,S2,idle=L,now=H
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
  if (!qPop(it)) return;  // something that never passed S1 (a hand, say), ignore it
  if (it.verdict == 'P') {
    Serial.print(F("PASS,"));
    Serial.println(it.seq);
  } else {
    schedulePush();  // NG or no verdict: reject
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
  } else if (!strncmp(line, "V,", 2)) {  // old form V,17,F
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

// e-stop button: each press toggles stopped / running
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
  delay(REST_SETTLE_MS);   // send the arm to rest first,
  rejector.detach();       // then detach to cut idle current
  initSensor(s1);          // take the clear reading as baseline (avoids false DET/REJ)
  initSensor(s2);
  setRunning(true);
  reportSensors();
}

void loop() {
  readSerial();                         // 1) read verdicts and commands
  if (sensorHit(s1)) onS1();            // 2) entry sensor
  if (USE_S2) {                         // 3) arrival at the rejector
    if (sensorHit(s2)) onS2();
  } else {
    while (qCount && millis() - q[qHead].ms >= TRAVEL_MS) onS2();
  }
  updateServo();                        // 4) servo
  if (USE_ESTOP) checkEstop();          // 5) e-stop (only if fitted), LED
  if (servoBusy()) digitalWrite(PIN_LED, LOW);
  else digitalWrite(PIN_LED, running ? HIGH : ((millis() / 300) % 2));
}