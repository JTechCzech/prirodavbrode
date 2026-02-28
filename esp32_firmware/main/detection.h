#ifndef DETECTION_H
#define DETECTION_H

#include <Arduino.h>

// Initialize bird detection hardware
void initDetection();

// Check if bird was detected (returns true if new detection)
bool checkBirdSensor();

// Get current bird count
unsigned long getBirdCount();

#endif
