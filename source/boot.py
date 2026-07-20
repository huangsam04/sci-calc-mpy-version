"""Internal Flash boot.py: mount the application SD card and then exit."""
import os
import sys
import vfs
from machine import SDCard


try:
    sd = SDCard(slot=2, width=1, sck=18, mosi=23, miso=19, cs=4,
                freq=10_000_000)
    vfs.mount(sd, "/sd")
    if "/sd" not in sys.path:
        sys.path.insert(0, "/sd")
except Exception as error:
    print("SD mount failed: " + str(error))
