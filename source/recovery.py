"""Small frozen recovery UI used when the product cannot start."""
from machine import Pin, SPI


def show_recovery(error):
    from display.ssd1322 import Display

    spi = SPI(2, baudrate=10_000_000, polarity=0, phase=0, bits=8,
              sck=Pin(18), mosi=Pin(23))
    display = Display(spi, Pin(5, Pin.OUT), Pin(16, Pin.OUT), Pin(17, Pin.OUT))
    display.clear_buffers(0)
    display.draw_text8x8(32, 8, "SCI-CALC RECOVERY", gs=15)
    display.draw_hline(24, 19, 168, 8)
    display.draw_text8x8(8, 27, "STARTUP FAILED", gs=15)
    message = str(error)
    if len(message) > 28:
        message = message[:27] + "~"
    display.draw_text8x8(8, 39, message, gs=10)
    display.draw_text8x8(8, 53, "Press RESET to retry", gs=12)
    display.present()
