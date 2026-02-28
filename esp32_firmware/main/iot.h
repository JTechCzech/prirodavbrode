#ifndef IOT_H
#define IOT_H

#include <Arduino.h>

// Initialize MQTT connection
void initIoT(const char* broker, int port, const char* username, const char* password, const char* deviceId, const char* firmware);

// Main MQTT loop - call in loop()
void loopIoT();

// Send bird detection event
void sendBirdDetection();

// Send WiFi status
void sendWiFiStatus(String ssid, String bssid, int rssi, String ip);

// Send OTA progress
void sendOTAProgress(int progress, String message);

#endif
