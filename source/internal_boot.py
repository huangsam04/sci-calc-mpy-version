# Auto-boot: mount SD card and launch SCI-CALC
import os
from machine import SPI

try:
    import sdcard
    sd_spi = SPI(1, baudrate=10_000_000, sck=Pin(18), mosi=Pin(23), miso=Pin(19))
    sd = sdcard.SDCard(sd_spi, Pin(4))
    os.mount(sd, '/sd')
    import sys
    sys.path.insert(0, '/sd')
    import main
except Exception as e:
    print(f"SCI-CALC boot failed: {e}")
    print("Check SD card. Dropping to REPL.")
