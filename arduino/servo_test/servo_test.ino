/*
 * Servo (MG996R) test, low-power version.
 *   orange (signal) -> D9, red -> external 5-6 V (2 A or more), brown -> external ground and Arduino GND
 *   Serial monitor at 115200 with line ending set to Newline.
 *
 * To keep current down it moves 2 degrees at a time instead of jumping, only drives
 * the servo while moving and detaches once it arrives (almost no idle current), and
 * uses a wide 500-2500 us pulse range so cheap SG90s also reach the full travel.
 *
 * On power-up it swings between 10 and 100 degrees every 3 s. Send a number (0-180)
 * to go to that angle, or "auto" to go back to swinging.
 */
#include <Servo.h>

const int PIN_SERVO = 9;
const int REST = 10;
const int PUSH = 100;
const int STEP_DEG = 2;      // degrees per step (smaller = less current, slower)
const int STEP_MS = 15;      // time between steps
const int MIN_US = 500, MAX_US = 2500;  // MG996R

Servo sv;
int cur = REST;
bool autoMode = true;
bool atPush = false;
unsigned long lastMove = 0;
String rx;

void moveSlow(int target) {
  target = constrain(target, 0, 180);
  Serial.print(F("서보 "));
  Serial.print(cur);
  Serial.print(F("도 -> "));
  Serial.print(target);
  Serial.println(F("도"));
  sv.attach(PIN_SERVO, MIN_US, MAX_US);
  sv.write(cur);
  delay(20);
  while (cur != target) {
    int d = target - cur;
    cur += (d > 0) ? min(STEP_DEG, d) : max(-STEP_DEG, d);
    sv.write(cur);
    delay(STEP_MS);
  }
  delay(300);      // let it arrive
  sv.detach();     // detach to cut current
}

void setup() {
  Serial.begin(115200);
  Serial.println(F("서보 테스트 시작 (천천히, 3초마다 왕복)"));
  moveSlow(REST);
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      rx.trim();
      if (rx == "auto") {
        autoMode = true;
        Serial.println(F("자동 모드"));
      } else if (rx.length()) {
        autoMode = false;
        moveSlow(rx.toInt());
      }
      rx = "";
    } else {
      rx += c;
    }
  }
  if (autoMode && millis() - lastMove >= 3000) {
    lastMove = millis();
    atPush = !atPush;
    moveSlow(atPush ? PUSH : REST);
  }
}
