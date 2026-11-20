// アナログ・ジョイスティック(KY-023 など)→ パソコンへ送る。StackChan の「ずんだの チャリはしり」を、スティックでジャンプさせる。
// 配線(Arduino UNO): GND→GND / +5V→5V / VRx→A0 / VRy→A1 / SW→D2
// 送る形: "J,x,y,b" を 50 回/秒(x・y は 0〜1023、b は ボタンを押している間 1)。115200bps。
const int PIN_X = A0, PIN_Y = A1, PIN_SW = 2;

void setup() {
  Serial.begin(115200);
  pinMode(PIN_SW, INPUT_PULLUP);          // ボタンは、押すと LOW
}

void loop() {
  int x = analogRead(PIN_X), y = analogRead(PIN_Y), b = digitalRead(PIN_SW) == LOW ? 1 : 0;
  Serial.print("J,"); Serial.print(x); Serial.print(','); Serial.print(y); Serial.print(','); Serial.println(b);
  delay(20);
}
