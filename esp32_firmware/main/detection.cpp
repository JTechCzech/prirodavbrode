#include "detection.h"

// Bird detection pins
#define LED_PIN     A0
#define SENSOR_PIN  A1

// Timing
static const unsigned long CLUSTER_GAP_MS = 300;

// State
static unsigned long lastDetectTime = 0;
static unsigned long birdCount = 0;
static bool inCluster = false;

void initDetection() {
  pinMode(LED_PIN, OUTPUT);
  tone(LED_PIN, 38000);        // 38 kHz IR LED
  pinMode(SENSOR_PIN, INPUT);
  Serial.println("[Sensor] Bird detection initialized (cluster mode)");
}

bool checkBirdSensor() {
  static int lastState = HIGH;
  bool newDetection = false;

  int state = digitalRead(SENSOR_PIN);
  unsigned long now = millis();

  // Detect pulse (HIGH -> LOW)
  if (lastState == HIGH && state == LOW) {
    lastDetectTime = now;

    // New cluster = real event
    if (!inCluster) {
      inCluster = true;
      birdCount++;
      Serial.printf("Průchod ptáka: %lu\n", birdCount);
      newDetection = true;
    }
  }

  // End of cluster after silence
  if (inCluster && (now - lastDetectTime >= CLUSTER_GAP_MS)) {
    inCluster = false;
  }

  lastState = state;
  return newDetection;
}

unsigned long getBirdCount() {
  return birdCount;
}
