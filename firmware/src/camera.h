// CoreS3 のカメラ(GC0308)。別タスク(core0)で撮影→JPEG 化し、メインループが Mac へ送る。
// SCCB(設定用 I2C)は M5Unified が持っている内部 I2C を共有する(電池・ランプ・タッチと共存させるため)。
#pragma once
#include <M5Unified.h>
#include "esp_camera.h"
#include "img_converters.h"

namespace cam {

static uint8_t *jpg = nullptr;                  // 最新の JPEG(PSRAM)
static volatile size_t jpgLen = 0;
static volatile bool ready = false;
static volatile bool enabled = false;
static volatile uint32_t frames = 0, failures = 0;
static volatile bool fastMode = false;           // ゲーム中は半分の大きさで、たくさん送る
static uint8_t *half = nullptr;
static volatile uint32_t periodMs = 250;        // 撮影間隔(120ms にしても送られる枚数は増えず、負荷だけ増えたので 250ms)

static bool initCam() {
  camera_config_t c = {};
  c.pin_pwdn = -1; c.pin_reset = -1; c.pin_xclk = -1;
  c.pin_sccb_sda = -1; c.pin_sccb_scl = -1;     // 既に動いている I2C バスを共有
  c.sccb_i2c_port = M5.In_I2C.getPort();
  c.pin_d7 = 47; c.pin_d6 = 48; c.pin_d5 = 16; c.pin_d4 = 15; c.pin_d3 = 42; c.pin_d2 = 41; c.pin_d1 = 40; c.pin_d0 = 39;
  c.pin_vsync = 46; c.pin_href = 38; c.pin_pclk = 45;
  c.xclk_freq_hz = 20000000;
  c.ledc_timer = LEDC_TIMER_0; c.ledc_channel = LEDC_CHANNEL_0;
  c.pixel_format = PIXFORMAT_RGB565;
  c.frame_size = FRAMESIZE_QVGA;                // 320x240
  c.jpeg_quality = 0;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  return esp_camera_init(&c) == ESP_OK;
}

static void task(void *) {
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(periodMs));
    if (!enabled || ready) continue;            // 前の1枚がまだ送られていない間は撮らない
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) { failures++; continue; }
    uint8_t *out = nullptr; size_t len = 0;
    bool ok;
    if (fastMode && half && fb->format == PIXFORMAT_RGB565 && fb->width == 320 && fb->height == 240) {
      const uint16_t *src = (const uint16_t *)fb->buf; uint16_t *dst = (uint16_t *)half;
      for (int y = 0; y < 120; y++) for (int x = 0; x < 160; x++) dst[y * 160 + x] = src[(y * 2) * 320 + x * 2];
      ok = fmt2jpg(half, 160 * 120 * 2, 160, 120, PIXFORMAT_RGB565, 45, &out, &len);
    } else {
      ok = fmt2jpg(fb->buf, fb->len, fb->width, fb->height, fb->format, 45, &out, &len);
    }
    esp_camera_fb_return(fb);
    if (!ok || !out) { failures++; continue; }
    if (len + 1 <= 24000) { memcpy(jpg + 1, out, len); jpgLen = len; ready = true; frames++; }
    free(out);
  }
}

// ゲーム中(からだジャンプ)は、半分の大きさ(160x120)に縮めて送る(JPEG にするのが速くなり、たくさん送れる)。センサーの設定は変えない(変えると撮影に失敗した)
static void fast(bool on) {
  fastMode = on;
  periodMs = on ? 90 : 250;
}

static bool begin() {
  jpg = (uint8_t *)ps_malloc(24000);
  if (!jpg) return false;
  half = (uint8_t *)ps_malloc(160 * 120 * 2);
  jpg[0] = 0x02;                                // 送信用の先頭タグ(JPEG)
  if (!initCam()) return false;
  enabled = true;
  xTaskCreatePinnedToCore(task, "cam", 6144, nullptr, 1, nullptr, 0);
  return true;
}

}  // namespace cam
