# Frozen boot.py: mount the user-data SD card before the product starts.
# Product imports stay on .frozen; the card never enters sys.path.
import vfs
from machine import Pin, SPI
import sdcard


try:
    # SD and SSD1322 share GPIO18/23 and therefore the same SPI2 host.  Their
    # separate CS pins (4 and 5) isolate transactions.
    sd_spi = SPI(2, baudrate=10_000_000, polarity=0, phase=0, bits=8,
                 sck=Pin(18), mosi=Pin(23), miso=Pin(19))
    sd = sdcard.SDCard(sd_spi, Pin(4), baudrate=10_000_000)
    vfs.mount(sd, "/sd")
except Exception as error:
    print("SD mount failed: " + str(error))
