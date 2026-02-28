#ifndef OTA_H
#define OTA_H

#include <Arduino.h>

// Initialize OTA functionality
void initOTA();

// Start OTA update from URL
void startOTAUpdate(String url);

// Check if OTA is in progress
bool isOTAInProgress();

#endif
