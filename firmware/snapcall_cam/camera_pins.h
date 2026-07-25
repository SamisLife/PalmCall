// Camera pin map for the Seeed Studio XIAO ESP32-S3 Sense expansion board (OV2640/OV3660).
//
// These pins are fixed by the Sense board's FPC connector -- they are not
// configurable. If the camera fails to initialise, the cause is almost always a
// board-setting mistake (PSRAM disabled) or a loose FPC ribbon, not these values.

#pragma once

#define PWDN_GPIO_NUM  -1  // not wired on the Sense board
#define RESET_GPIO_NUM -1  // not wired on the Sense board
#define XCLK_GPIO_NUM  10
#define SIOD_GPIO_NUM  40  // SCCB data
#define SIOC_GPIO_NUM  39  // SCCB clock

#define Y9_GPIO_NUM    48
#define Y8_GPIO_NUM    11
#define Y7_GPIO_NUM    12
#define Y6_GPIO_NUM    14
#define Y5_GPIO_NUM    16
#define Y4_GPIO_NUM    18
#define Y3_GPIO_NUM    17
#define Y2_GPIO_NUM    15

#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM  47
#define PCLK_GPIO_NUM  13

// Onboard user LED. Active LOW: digitalWrite(LOW) turns it ON.
#define STATUS_LED_PIN 21
#define STATUS_LED_ON  LOW
#define STATUS_LED_OFF HIGH
