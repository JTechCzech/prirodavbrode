#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "detection.h"
#include "iot.h"
#include "ota.h"

// WiFi credentials
const char* ssid = "CAM";
const char* password = "spehujute";

// MQTT broker
const char* mqtt_broker = "192.168.0.50";
const int mqtt_port = 1883;
const char* mqtt_username = "esp";
const char* mqtt_password = "ESP918273*";

// Device info
const char* DEVICE_ID = "ESP32_ORECH";  // Change for each device
const char* FIRMWARE_VERSION = "0.0.2";

void setup() {
  Serial.begin(115200);
  Serial.println("\n[ESP32] Starting...");

  // Initialize bird detection
  initDetection();

  // Initialize OTA
  initOTA();

  // Connect to WiFi
  Serial.printf("[WiFi] Connecting to %s\n", ssid);
  WiFi.begin(ssid, password);
  //WiFi.setTxPower(WIFI_POWER_8_5dBm);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\n[WiFi] Connected!");
  Serial.printf("  IP: %s\n", WiFi.localIP().toString().c_str());
  Serial.printf("  SSID: %s\n", WiFi.SSID().c_str());
  Serial.printf("  BSSID: %s\n", WiFi.BSSIDstr().c_str());
  Serial.printf("  RSSI: %d dBm\n", WiFi.RSSI());

  // Initialize MQTT connection
  initIoT(mqtt_broker, mqtt_port, mqtt_username, mqtt_password, DEVICE_ID, FIRMWARE_VERSION);
}

void loop() {
  // Handle IoT communication
  loopIoT();

  // Check bird sensor
  if (checkBirdSensor()) {
    // Bird detected, send to server
    sendBirdDetection();
  }

  // Send WiFi status every 30 seconds
  static unsigned long lastStatus = 0;
  if (millis() - lastStatus >= 30000) {
    sendWiFiStatus(WiFi.SSID(), WiFi.BSSIDstr(), WiFi.RSSI(), WiFi.localIP().toString());
    lastStatus = millis();
  }
}
