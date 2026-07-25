#include <Arduino.h>
#include <ESPmDNS.h>
#include <WiFi.h>
#include "esp_camera.h"

#include "camera_pins.h"
#include "secrets.h"

bool startCameraServer();

namespace {

constexpr uint32_t kReconnectIntervalMs = 10000;
constexpr uint32_t kServerRetryIntervalMs = 5000;
constexpr uint32_t kConnectingBlinkMs = 500;
constexpr uint32_t kFatalBlinkMs = 120;

bool systemReady = false;
bool wasConnected = false;
bool mdnsRunning = false;
bool serverStarted = false;
uint32_t lastReconnectAttemptMs = 0;
uint32_t lastServerStartAttemptMs = 0;
uint32_t lastBlinkMs = 0;
bool ledOn = false;

void setStatusLed(bool on) {
  ledOn = on;
  digitalWrite(STATUS_LED_PIN, on ? STATUS_LED_ON : STATUS_LED_OFF);
}

void printCameraError(esp_err_t error) {
  Serial.printf(
      "\nERROR: Camera initialization failed (0x%04x).\n"
      "Check that Tools > PSRAM is set to \"OPI PSRAM\", then reseat the\n"
      "camera ribbon cable and press Reset.\n",
      static_cast<unsigned int>(error));
}

bool initializeCamera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  // VGA is detailed enough for laptop-side hand landmarks while remaining
  // responsive over a phone hotspot.
  config.frame_size = FRAMESIZE_VGA;
  config.jpeg_quality = 12;
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.fb_count = 2;

  if (!psramFound()) {
    Serial.println(
        "WARNING: PSRAM was not found. Falling back to QVGA/DRAM. "
        "Enable Tools > PSRAM > OPI PSRAM for the intended setup.");
    config.frame_size = FRAMESIZE_QVGA;
    config.fb_location = CAMERA_FB_IN_DRAM;
    config.fb_count = 1;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  }

  const esp_err_t error = esp_camera_init(&config);
  if (error != ESP_OK) {
    printCameraError(error);
    return false;
  }

  sensor_t *sensor = esp_camera_sensor_get();
  if (sensor == nullptr) {
    Serial.println("ERROR: Camera initialized, but no image sensor was found.");
    return false;
  }

  // This corrects the default orientation and color tuning of an OV3660.
  // The calls are intentionally skipped for OV2640 camera modules.
  if (sensor->id.PID == OV3660_PID) {
    sensor->set_vflip(sensor, 1);
    sensor->set_brightness(sensor, 1);
    sensor->set_saturation(sensor, -2);
  }

  Serial.printf(
      "Camera ready: PID 0x%04x, %s frame buffers\n",
      sensor->id.PID,
      psramFound() ? "PSRAM" : "DRAM");
  return true;
}

void beginWiFi() {
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(WIFI_HOSTNAME);
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(false);

  Serial.printf("Connecting to Wi-Fi \"%s\"", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  lastReconnectAttemptMs = millis();
}

bool startServerIfNeeded() {
  if (serverStarted) {
    return true;
  }

  lastServerStartAttemptMs = millis();
  Serial.println("Starting HTTP camera server...");
  serverStarted = startCameraServer();
  if (!serverStarted) {
    Serial.println(
        "HTTP camera server could not start; retrying in 5 seconds.");
  }
  return serverStarted;
}

void printServerUrls() {
  const IPAddress ip = WiFi.localIP();
  Serial.printf("  Home:     http://%s/\n", ip.toString().c_str());
  Serial.printf("  Snapshot: http://%s/capture\n", ip.toString().c_str());
  Serial.printf("  Stream:   http://%s/stream\n", ip.toString().c_str());
}

void announceConnection() {
  Serial.println("\nWi-Fi connected.");

  // esp_http_server uses synchronization objects created by the networking
  // stack. Starting it before Wi-Fi is connected can assert inside
  // xQueueSemaphoreTake on ESP32 Arduino core 3.x.
  if (startServerIfNeeded()) {
    printServerUrls();
  }

  MDNS.end();
  mdnsRunning = MDNS.begin(WIFI_HOSTNAME);
  if (mdnsRunning) {
    MDNS.addService("http", "tcp", 80);
    Serial.printf("  mDNS:     http://%s.local/\n", WIFI_HOSTNAME);
  } else {
    Serial.println("  mDNS could not start; use the numeric IP above.");
  }
}

void maintainWiFi() {
  const bool connected = WiFi.status() == WL_CONNECTED;
  const uint32_t now = millis();

  if (connected && !wasConnected) {
    announceConnection();
    setStatusLed(true);
  } else if (!connected && wasConnected) {
    Serial.println("\nWi-Fi lost; reconnecting in the background...");
    if (mdnsRunning) {
      MDNS.end();
      mdnsRunning = false;
    }
    setStatusLed(false);
  }
  wasConnected = connected;

  if (connected) {
    // If the server failed for a transient resource reason, retry without
    // requiring another Wi-Fi disconnect or a board reset.
    if (!serverStarted &&
        now - lastServerStartAttemptMs >= kServerRetryIntervalMs &&
        startServerIfNeeded()) {
      printServerUrls();
    }
    return;
  }

  if (now - lastBlinkMs >= kConnectingBlinkMs) {
    lastBlinkMs = now;
    setStatusLed(!ledOn);
    Serial.print(".");
  }

  if (now - lastReconnectAttemptMs >= kReconnectIntervalMs) {
    lastReconnectAttemptMs = now;
    WiFi.reconnect();
  }
}

}  // namespace

void setup() {
  pinMode(STATUS_LED_PIN, OUTPUT);
  setStatusLed(false);

  Serial.begin(115200);
  delay(1000);
  Serial.println("\n\nSnapCall camera firmware starting...");

  if (!initializeCamera()) {
    return;
  }

  beginWiFi();
  systemReady = true;
}

void loop() {
  if (!systemReady) {
    const uint32_t now = millis();
    if (now - lastBlinkMs >= kFatalBlinkMs) {
      lastBlinkMs = now;
      setStatusLed(!ledOn);
    }
    delay(10);
    return;
  }

  maintainWiFi();
  delay(10);
}
