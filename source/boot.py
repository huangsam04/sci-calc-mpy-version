# SCI-CALC boot script
# This runs before main.py on MicroPython startup.
# Set up SD card and filesystem.

import machine
import os

# Try to mount SD card
try:
    import sdcard
    from machine import Pin, SPI

    sd_spi = SPI(1,
                 baudrate=10_000_000,
                 polarity=0,
                 phase=0,
                 bits=8,
                 sck=Pin(18),   # share clock with display
                 mosi=Pin(23),  # share MOSI with display
                 miso=Pin(19))
    sd = sdcard.SDCard(sd_spi, Pin(4))
    os.mount(sd, '/sd')
    print("SD card mounted at /sd")
except Exception as e:
    print(f"SD card mount failed: {e}")
    print("Running without SD card")

# Ensure function directories exist
try:
    os.mkdir('/sd/functions')
except Exception:
    pass  # already exists or no SD card
