/*
 * 적외선 센서 테스트 (E18-D80NK 2개)
 *   S1 검정 선 -> D2,  S2 검정 선 -> D3,  갈색 -> 5V,  파랑 -> GND
 *   시리얼 모니터 115200 으로 열기
 *
 * 값이 바뀔 때마다 한 줄 출력 + 1초마다 현재 상태 출력
 *   예) S1=H S2=L
 *   센서 앞에 손을 넣었다 뺐을 때 H <-> L 이 바뀌면 센서가 동작하는 거예요.
 *   손을 넣어도 안 바뀌면 그 센서는 배선/전원/감도 문제예요.
 */
const int PIN_S1 = 2;
const int PIN_S2 = 3;

int last1, last2;
unsigned long lastPrint = 0;

void printState(const char *why) {
  Serial.print(why);
  Serial.print(F("  S1="));
  Serial.print(digitalRead(PIN_S1) ? 'H' : 'L');
  Serial.print(F("  S2="));
  Serial.println(digitalRead(PIN_S2) ? 'H' : 'L');
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_S1, INPUT_PULLUP);
  pinMode(PIN_S2, INPUT_PULLUP);
  pinMode(LED_BUILTIN, OUTPUT);
  last1 = digitalRead(PIN_S1);
  last2 = digitalRead(PIN_S2);
  Serial.println(F("센서 테스트 시작"));
  printState("[처음]");
}

void loop() {
  int v1 = digitalRead(PIN_S1);
  int v2 = digitalRead(PIN_S2);

  if (v1 != last1) {
    last1 = v1;
    printState("[S1 바뀜]");
  }
  if (v2 != last2) {
    last2 = v2;
    printState("[S2 바뀜]");
  }

  // 보드 L LED: S1 이 LOW 면 켜짐 (시리얼 없이도 눈으로 확인)
  digitalWrite(LED_BUILTIN, v1 == LOW ? HIGH : LOW);

  if (millis() - lastPrint >= 1000) {
    lastPrint = millis();
    printState("[현재]");
  }
  delay(5);
}
