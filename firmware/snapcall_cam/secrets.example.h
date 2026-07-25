// Copy this file to `secrets.h` (same folder) and fill in your network.
// `secrets.h` is the only file you should ever need to edit to move networks.
//
// Phone hotspot notes:
//   - The ESP32-S3 radio is 2.4 GHz only. iPhone hotspots default to 5 GHz on
//     newer models: turn ON "Maximize Compatibility" in Settings > Personal
//     Hotspot, or the board will never see the SSID.
//   - SSIDs with emoji or curly apostrophes (iPhone's default "Sami's iPhone"
//     uses U+2019, not ') will silently fail to match. Rename the hotspot to
//     plain ASCII.

#pragma once

#define WIFI_SSID "your-network-name"
#define WIFI_PASS "your-network-password"

// Advertised as http://<WIFI_HOSTNAME>.local so you do not have to chase DHCP
// addresses every time the hotspot reshuffles them.
#define WIFI_HOSTNAME "snapcall"
