/*
 * IR sensor test for the two E18-D80NK sensors.
 *   S1 black -> D2, S2 black -> D3, brown -> 5V, blue -> GND
 *   Open the serial monitor at 115200.
 *
 * Prints a line whenever a value changes, plus the current state every second,
 * e.g. S1=H S2=L. Wave a hand in front of a sensor: if it flips between H and L the
 * sensor works. If it doesn't change, check that sensor's wiring, power or sensitivity.
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

  // onboard LED is on while S1 reads LOW, so you can check it without the serial monitor
  digitalWrite(LED_BUILTIN, v1 == LOW ? HIGH : LOW);

  if (millis() - lastPrint >= 1000) {
    lastPrint = millis();
    printState("[현재]");
  }
  delay(5);
}
