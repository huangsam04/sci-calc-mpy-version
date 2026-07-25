# Internal Flash boot.py: mount the application SD card and then exit.
# The boot supervisor owns sys.path from here on; /sd must never sit on the
# global import path or stale card files could shadow the trusted base.
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
