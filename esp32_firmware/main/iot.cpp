#include "iot.h"
#include "ota.h"
#include <PubSubClient.h>
#include <WiFi.h>
#include <ArduinoJson.h>

static WiFiClient wifiClient;
static PubSubClient mqttClient(wifiClient);

// MQTT Configuration
static const char* MQTT_BASE_TOPIC = "prulety";
static char deviceId[64];
static char firmwareVersion[32];
static const char* mqttBroker;
static int mqttPort;
static const char* mqttUsername;
static const char* mqttPassword;

// Topics
static char topicRegister[128];
static char topicData[128];
static char topicBirdDetection[128];
static char topicOTAProgress[128];
static char topicStatus[128];
static char topicCommand[128];
static char topicResponse[128];

// Timing
static unsigned long lastReconnectAttempt = 0;
static unsigned long lastStatusUpdate = 0;
static const unsigned long RECONNECT_INTERVAL = 5000;  // 5 seconds
static const unsigned long STATUS_INTERVAL = 30000;     // 30 seconds

// MQTT Callback
static void mqttCallback(char* topic, byte* payload, unsigned int length) {
    Serial.printf("[MQTT] Message arrived [%s]: ", topic);

    // Convert payload to string
    char message[length + 1];
    memcpy(message, payload, length);
    message[length] = '\0';
    Serial.println(message);

    StaticJsonDocument<512> doc;
    DeserializationError error = deserializeJson(doc, message);
    if (error) {
        Serial.println("[MQTT] JSON parse error");
        return;
    }

    const char* msgType = doc["type"];
    if (!msgType) return;

    // Handle response messages
    if (strcmp(topic, topicResponse) == 0) {
        if (strcmp(msgType, "registered") == 0) {
            Serial.println("[MQTT] Device registered successfully");
        }
        else if (strcmp(msgType, "ack") == 0) {
            Serial.println("[MQTT] Message acknowledged");
        }
    }
    // Handle command messages
    else if (strcmp(topic, topicCommand) == 0) {
        if (strcmp(msgType, "ota_update") == 0) {
            const char* url = doc["url"];
            Serial.printf("[MQTT] OTA update requested: %s\n", url);
            startOTAUpdate(String(url));
        }
        else if (strcmp(msgType, "command") == 0) {
            Serial.println("[MQTT] Command received from server");
            // Handle other commands here
        }
    }
}

// Connect to MQTT broker
static bool mqttConnect() {
    Serial.printf("[MQTT] Attempting to connect to %s:%d as %s...\n", mqttBroker, mqttPort, mqttUsername);

    // Generate unique client ID
    String clientId = "ESP32_";
    clientId += deviceId;

    if (mqttClient.connect(clientId.c_str(), mqttUsername, mqttPassword)) {
        Serial.println("[MQTT] Connected!");

        // Subscribe to topics
        mqttClient.subscribe(topicCommand);
        mqttClient.subscribe(topicResponse);
        Serial.printf("[MQTT] Subscribed to: %s\n", topicCommand);
        Serial.printf("[MQTT] Subscribed to: %s\n", topicResponse);

        // Send registration
        StaticJsonDocument<256> doc;
        doc["type"] = "register";
        doc["device_id"] = deviceId;
        doc["firmware"] = firmwareVersion;

        String message;
        serializeJson(doc, message);
        mqttClient.publish(topicRegister, message.c_str());
        Serial.println("[MQTT] Registration sent");

        return true;
    } else {
        Serial.printf("[MQTT] Connection failed, rc=%d\n", mqttClient.state());
        return false;
    }
}

void initIoT(const char* broker, int port, const char* username, const char* password, const char* devId, const char* firmware) {
    mqttBroker = broker;
    mqttPort = port;
    mqttUsername = username;
    mqttPassword = password;
    strncpy(deviceId, devId, sizeof(deviceId) - 1);
    strncpy(firmwareVersion, firmware, sizeof(firmwareVersion) - 1);

    // Build topic strings
    snprintf(topicRegister, sizeof(topicRegister), "%s/%s/register", MQTT_BASE_TOPIC, deviceId);
    snprintf(topicData, sizeof(topicData), "%s/%s/data", MQTT_BASE_TOPIC, deviceId);
    snprintf(topicBirdDetection, sizeof(topicBirdDetection), "%s/%s/bird_detection", MQTT_BASE_TOPIC, deviceId);
    snprintf(topicOTAProgress, sizeof(topicOTAProgress), "%s/%s/ota_progress", MQTT_BASE_TOPIC, deviceId);
    snprintf(topicStatus, sizeof(topicStatus), "%s/%s/status", MQTT_BASE_TOPIC, deviceId);
    snprintf(topicCommand, sizeof(topicCommand), "%s/%s/command", MQTT_BASE_TOPIC, deviceId);
    snprintf(topicResponse, sizeof(topicResponse), "%s/%s/response", MQTT_BASE_TOPIC, deviceId);

    // Configure MQTT client
    mqttClient.setServer(broker, port);
    mqttClient.setCallback(mqttCallback);

    Serial.printf("[MQTT] Initialized for device: %s\n", deviceId);
    Serial.printf("[MQTT] Broker: %s:%d\n", broker, port);

    // Initial connection
    mqttConnect();
}

void loopIoT() {
    if (!mqttClient.connected()) {
        unsigned long now = millis();
        if (now - lastReconnectAttempt > RECONNECT_INTERVAL) {
            lastReconnectAttempt = now;
            if (mqttConnect()) {
                lastReconnectAttempt = 0;
            }
        }
    } else {
        mqttClient.loop();

        // Send periodic status update
        unsigned long now = millis();
        if (now - lastStatusUpdate > STATUS_INTERVAL) {
            StaticJsonDocument<128> doc;
            doc["status"] = "online";
            doc["uptime"] = millis();

            String message;
            serializeJson(doc, message);
            mqttClient.publish(topicStatus, message.c_str());

            lastStatusUpdate = now;
        }
    }
}

void sendBirdDetection() {
    if (!mqttClient.connected()) {
        Serial.println("[MQTT] Not connected, cannot send bird detection");
        return;
    }

    StaticJsonDocument<256> doc;
    JsonObject payload = doc.createNestedObject("payload");
    payload["timestamp"] = millis();

    String message;
    serializeJson(doc, message);
    mqttClient.publish(topicBirdDetection, message.c_str());

    Serial.println("[Bird Detection] Detekce odeslána");
}

void sendWiFiStatus(String ssid, String bssid, int rssi, String ip) {
    if (!mqttClient.connected()) {
        Serial.println("[MQTT] Not connected, cannot send WiFi status");
        return;
    }

    StaticJsonDocument<512> doc;
    JsonObject payload = doc.createNestedObject("payload");
    payload["ssid"] = ssid;
    payload["bssid"] = bssid;
    payload["rssi"] = rssi;
    payload["ip"] = ip;
    payload["timestamp"] = millis();

    String message;
    serializeJson(doc, message);
    mqttClient.publish(topicData, message.c_str());

    Serial.println("[WiFi Status] Sent");
}

void sendOTAProgress(int progress, String msg) {
    if (!mqttClient.connected()) {
        Serial.println("[MQTT] Not connected, cannot send OTA progress");
        return;
    }

    StaticJsonDocument<256> doc;
    doc["progress"] = progress;
    doc["message"] = msg;

    String message;
    serializeJson(doc, message);
    mqttClient.publish(topicOTAProgress, message.c_str());

    Serial.printf("[OTA] Progress sent: %d%% - %s\n", progress, msg.c_str());
}
