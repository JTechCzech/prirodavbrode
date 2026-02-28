#include "ota.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <Update.h>

static bool otaInProgress = false;
extern void sendOTAProgress(int progress, String message);

void initOTA() {
    Serial.println("[OTA] OTA module initialized");
}

bool isOTAInProgress() {
    return otaInProgress;
}

void startOTAUpdate(String url) {
    if (otaInProgress) {
        Serial.println("[OTA] Update already in progress");
        return;
    }

    otaInProgress = true;
    Serial.printf("[OTA] Starting OTA update from: %s\n", url.c_str());
    sendOTAProgress(0, "Starting OTA update...");

    HTTPClient http;
    http.begin(url);
    int httpCode = http.GET();

    if (httpCode != HTTP_CODE_OK) {
        Serial.printf("[OTA] HTTP GET failed: %d\n", httpCode);
        sendOTAProgress(0, "Failed to download firmware");
        http.end();
        otaInProgress = false;
        return;
    }

    int contentLength = http.getSize();
    if (contentLength <= 0) {
        Serial.println("[OTA] Invalid content length");
        sendOTAProgress(0, "Invalid firmware size");
        http.end();
        otaInProgress = false;
        return;
    }

    Serial.printf("[OTA] Firmware size: %d bytes\n", contentLength);

    WiFiClient* stream = http.getStreamPtr();

    if (!Update.begin(contentLength)) {
        Serial.println("[OTA] Not enough space for update");
        sendOTAProgress(0, "Not enough space");
        http.end();
        otaInProgress = false;
        return;
    }

    uint8_t buffer[1024];
    size_t written = 0;
    int lastProgress = 0;

    sendOTAProgress(5, "Downloading firmware...");

    while (http.connected() && written < contentLength) {
        size_t available = stream->available();
        if (available) {
            int bytesRead = stream->readBytes(buffer, (available > sizeof(buffer) ? sizeof(buffer) : available));
            if (bytesRead > 0) {
                Update.write(buffer, bytesRead);
                written += bytesRead;

                int progress = (written * 100) / contentLength;
                if (progress != lastProgress && progress % 5 == 0) { // callback každých 5 %
                    sendOTAProgress(progress, "Downloading...");
                    lastProgress = progress;
                    Serial.printf("[OTA] Progress: %d%%\n", progress);
                }
            }
        }
        delay(1); // malé uvolnění pro WiFi stack
    }

    if (written != contentLength) {
        Serial.printf("[OTA] Download incomplete: %d/%d\n", written, contentLength);
        sendOTAProgress(0, "Download incomplete");
        Update.abort();
        http.end();
        otaInProgress = false;
        return;
    }

    sendOTAProgress(95, "Verifying firmware...");

    if (Update.end(true)) {
        Serial.println("[OTA] Update Success! Rebooting...");
        sendOTAProgress(100, "Update complete! Rebooting...");
        delay(1000);
        ESP.restart();
    } else {
        Serial.printf("[OTA] Update Error: %s\n", Update.errorString());
        sendOTAProgress(0, "Update failed");
    }

    http.end();
    otaInProgress = false;
}
