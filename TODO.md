# Performance TODO

- [ ] Add a device-side benchmark runner for cold-boot phase timings, input-to-present latency, frame p95/max, GC pauses, and heap stability across repeated navigation.
- [ ] Generate compact binary X-GLCD font assets during host checks so the device can load glyph data directly instead of parsing C source at boot.
- [ ] Build and deploy `.mpy` files only after verifying an mpy-cross build that matches the device interpreter ABI; the checked-in compiler is newer than the deployed MicroPython runtime.
- [ ] Cache compiled plot expressions across pan and zoom operations, then consider incremental sampling for expensive functions.
- [ ] Move settings and variable SD writes out of the key-handling critical path while preserving the current atomic commit and recovery behavior.
- [ ] Compare host and device SHA-256 values for deployed fonts and other runtime assets, not only entry-point files.
- [ ] Evaluate SSD1322 dirty-region updates after collecting device frame measurements; column addresses require the existing offset and four-pixel alignment rules.
