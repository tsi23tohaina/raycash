// test_servo.ino — Sketch minimal pour verifier que le servo tourne.
//
// CABLAGE :
//   Signal (fil jaune/orange) -> GPIO 19
//   +5V    (fil rouge)        -> 5V de l'ESP32
//   GND    (fil marron/noir)  -> GND de l'ESP32
//
// Le servo balaye 0° -> 180° -> 0° en boucle.
// Serial monitor : 115200 baud.

#include <ESP32Servo.h>

const int PIN_SERVO = 19;
Servo servo;

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("=== test_servo : balayage ===");

  ESP32PWM::allocateTimer(0);
  servo.setPeriodHertz(50);
  servo.attach(PIN_SERVO, 500, 2400);
}

void loop() {
  for (int a = 0; a <= 180; a += 10) {
    servo.write(a);
    Serial.printf("angle = %d\n", a);
    delay(100);
  }
  for (int a = 180; a >= 0; a -= 10) {
    servo.write(a);
    Serial.printf("angle = %d\n", a);
    delay(100);
  }
}
