/*
 * 서보(MG996R) 테스트 - 전력 적게 쓰는 버전
 *   주황(신호) -> D9,  빨강 -> 외부 5~6V(2A 이상) +,  갈색 -> 외부 전원 - 와 아두이노 GND
 *   시리얼 모니터 115200, 줄 끝 "새 줄(Newline)"
 *
 * 전력 줄이는 방법
 *   - 한 번에 확 돌지 않고 2도씩 천천히 움직여요 (순간 전류가 작아져요)
 *   - 움직일 때만 신호를 주고, 도착하면 신호를 끊어요(detach) -> 대기 중 전류 거의 0
 *   - 펄스 폭을 500~2400us 로 넓혀서 싸구려 SG90 도 끝까지 가게 해요
 *
 * 켜면 3초마다 10도 <-> 100도 자동 왕복.  숫자(0~180)를 보내면 그 각도로, "auto" 면 다시 자동.
 */
#include <Servo.h>

const int PIN_SERVO = 9;
const int REST = 10;
const int PUSH = 100;
const int STEP_DEG = 2;      // 한 번에 움직이는 각도 (작을수록 전류 작고 느림)
const int STEP_MS = 15;      // 한 걸음 사이 시간
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
  delay(300);      // 도착할 시간
  sv.detach();     // 신호 끊기 -> 전류 줄이기
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
