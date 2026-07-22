# Performance TODO

- [x] Add a device-side benchmark runner for application-startup phase timings, synthetic navigation event-to-first-frame latency, frame p95/max, GC pauses, and heap stability across repeated navigation.
- [x] Generate compact binary X-GLCD font assets during host checks so the device can load glyph data directly instead of parsing C source at boot.
- [x] Build and deploy `.mpy` files only after verifying a native `xtensawin` mpy-cross probe imports on the device ABI; fall back to source when it does not.
- [x] Cache compiled plot expressions across pan and zoom operations, invalidating the cache when the live function registry changes.
- [x] Move settings and variable SD writes out of the key-handling critical path while preserving the current atomic commit and recovery behavior.
- [x] Compare host and device SHA-256 values for every deployed font and runtime asset, not only entry-point files.
- [x] Evaluate SSD1322 dirty-region updates after device frame measurements. On COM5 after warm-up, 50 navigation cycles measured a 28.5 ms frame p95 and 86.3 ms full-run maximum; full-page slides change nearly all content and four-pixel alignment expands regions, so retain full-frame SPI writes rather than add a slower comparison/cache path.
