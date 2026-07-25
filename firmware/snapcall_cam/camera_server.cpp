#include <Arduino.h>
#include <WiFi.h>
#include "esp_camera.h"
#include "esp_http_server.h"
#include "esp_timer.h"
#include "img_converters.h"

namespace {

constexpr char kBoundary[] = "snapcall-frame-boundary";
constexpr char kStreamContentType[] =
    "multipart/x-mixed-replace;boundary=snapcall-frame-boundary";
constexpr char kStreamBoundary[] = "\r\n--snapcall-frame-boundary\r\n";
constexpr char kStreamPart[] =
    "Content-Type: image/jpeg\r\n"
    "Content-Length: %u\r\n"
    "X-Timestamp: %lld\r\n\r\n";

const char kIndexHtml[] PROGMEM = R"HTML(
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>SnapCall Camera</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { margin: 0; padding: 24px; background: #101418; color: #eef2f5; text-align: center; }
    main { max-width: 800px; margin: auto; }
    img { width: 100%; height: auto; border-radius: 14px; background: #000; }
    a { display: inline-block; margin: 16px 8px; color: #86d5ff; }
    p { color: #b9c3ca; }
  </style>
</head>
<body>
  <main>
    <h1>SnapCall Camera</h1>
    <img src="/stream" alt="Live camera stream">
    <div><a href="/capture" target="_blank">Open one JPEG frame</a><a href="/status">Status JSON</a></div>
    <p>The stream is 640x480 JPEG when PSRAM is enabled.</p>
  </main>
</body>
</html>
)HTML";

httpd_handle_t server = nullptr;

void setCommonHeaders(httpd_req_t *request) {
  httpd_resp_set_hdr(request, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(request, "Cache-Control", "no-store, no-cache, must-revalidate");
}

esp_err_t indexHandler(httpd_req_t *request) {
  setCommonHeaders(request);
  httpd_resp_set_type(request, "text/html");
  return httpd_resp_send(request, kIndexHtml, HTTPD_RESP_USE_STRLEN);
}

esp_err_t statusHandler(httpd_req_t *request) {
  char response[256];
  const IPAddress ip = WiFi.localIP();
  const int length = snprintf(
      response,
      sizeof(response),
      "{\"camera\":\"ready\",\"wifi\":\"%s\",\"ip\":\"%s\","
      "\"rssi\":%d,\"free_heap\":%u,\"free_psram\":%u}",
      WiFi.status() == WL_CONNECTED ? "connected" : "disconnected",
      ip.toString().c_str(),
      WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0,
      static_cast<unsigned int>(ESP.getFreeHeap()),
      static_cast<unsigned int>(ESP.getFreePsram()));

  setCommonHeaders(request);
  httpd_resp_set_type(request, "application/json");
  return httpd_resp_send(request, response, length);
}

esp_err_t captureHandler(httpd_req_t *request) {
  camera_fb_t *frame = esp_camera_fb_get();
  if (frame == nullptr) {
    Serial.println("Camera capture failed.");
    httpd_resp_send_500(request);
    return ESP_FAIL;
  }

  setCommonHeaders(request);
  httpd_resp_set_type(request, "image/jpeg");
  httpd_resp_set_hdr(
      request, "Content-Disposition", "inline; filename=snapcall-capture.jpg");

  esp_err_t result = ESP_OK;
  if (frame->format == PIXFORMAT_JPEG) {
    result = httpd_resp_send(
        request, reinterpret_cast<const char *>(frame->buf), frame->len);
  } else {
    uint8_t *jpeg = nullptr;
    size_t jpegLength = 0;
    if (!frame2jpg(frame, 80, &jpeg, &jpegLength)) {
      httpd_resp_send_500(request);
      result = ESP_FAIL;
    } else {
      result = httpd_resp_send(
          request, reinterpret_cast<const char *>(jpeg), jpegLength);
      free(jpeg);
    }
  }

  esp_camera_fb_return(frame);
  return result;
}

esp_err_t streamHandler(httpd_req_t *request) {
  esp_err_t result = httpd_resp_set_type(request, kStreamContentType);
  if (result != ESP_OK) {
    return result;
  }
  setCommonHeaders(request);
  httpd_resp_set_hdr(request, "X-Framerate", "20");

  while (result == ESP_OK) {
    camera_fb_t *frame = esp_camera_fb_get();
    if (frame == nullptr) {
      Serial.println("Camera stream capture failed.");
      result = ESP_FAIL;
      break;
    }

    uint8_t *jpeg = frame->buf;
    size_t jpegLength = frame->len;
    bool converted = false;

    if (frame->format != PIXFORMAT_JPEG) {
      converted = frame2jpg(frame, 80, &jpeg, &jpegLength);
      if (!converted) {
        esp_camera_fb_return(frame);
        Serial.println("JPEG conversion failed.");
        result = ESP_FAIL;
        break;
      }
    }

    char partHeader[128];
    const int headerLength = snprintf(
        partHeader,
        sizeof(partHeader),
        kStreamPart,
        static_cast<unsigned int>(jpegLength),
        static_cast<long long>(esp_timer_get_time()));

    result = httpd_resp_send_chunk(
        request, kStreamBoundary, strlen(kStreamBoundary));
    if (result == ESP_OK) {
      result = httpd_resp_send_chunk(request, partHeader, headerLength);
    }
    if (result == ESP_OK) {
      result = httpd_resp_send_chunk(
          request, reinterpret_cast<const char *>(jpeg), jpegLength);
    }

    if (converted) {
      free(jpeg);
    }
    esp_camera_fb_return(frame);
  }

  // This is expected when a browser closes or reloads the stream.
  return result;
}

}  // namespace

bool startCameraServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.max_uri_handlers = 4;
  config.lru_purge_enable = true;
  config.stack_size = 8192;

  if (httpd_start(&server, &config) != ESP_OK) {
    return false;
  }

  httpd_uri_t indexUri = {};
  indexUri.uri = "/";
  indexUri.method = HTTP_GET;
  indexUri.handler = indexHandler;

  httpd_uri_t captureUri = {};
  captureUri.uri = "/capture";
  captureUri.method = HTTP_GET;
  captureUri.handler = captureHandler;

  httpd_uri_t streamUri = {};
  streamUri.uri = "/stream";
  streamUri.method = HTTP_GET;
  streamUri.handler = streamHandler;

  httpd_uri_t statusUri = {};
  statusUri.uri = "/status";
  statusUri.method = HTTP_GET;
  statusUri.handler = statusHandler;

  if (httpd_register_uri_handler(server, &indexUri) != ESP_OK ||
      httpd_register_uri_handler(server, &captureUri) != ESP_OK ||
      httpd_register_uri_handler(server, &streamUri) != ESP_OK ||
      httpd_register_uri_handler(server, &statusUri) != ESP_OK) {
    httpd_stop(server);
    server = nullptr;
    return false;
  }

  Serial.println("HTTP camera server ready; waiting for Wi-Fi.");
  return true;
}

