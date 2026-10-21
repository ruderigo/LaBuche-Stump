# The board's hardware (machine), on a computer (docs/PC.md): there are no
# pins, no SD slot (the Stump folder's sd/ is the card), and the clock is
# the computer's. Anything that would drive a pin says so instead.
import sys


class _NoHardware(OSError):
    pass


def _none(*args, **kwargs):
    raise _NoHardware("no %s on a computer" % "GPIO")


class Pin:
    IN = OUT = PULL_UP = PULL_DOWN = OPEN_DRAIN = IRQ_RISING = IRQ_FALLING = 0

    def __init__(self, *args, **kwargs):
        _none()


ADC = SPI = I2C = UART = PWM = Pin


class SDCard:
    """The card is the sd/ folder: mounting it is a no-op (os.mount too)."""
    def __init__(self, *args, **kwargs):
        pass


class WDT:
    def __init__(self, *args, **kwargs):
        pass

    def feed(self):
        pass


class RTC:
    """The computer keeps its own time: setting it from the mesh is ignored."""
    def datetime(self, *args):
        if args:
            return None
        import time
        t = time.localtime()
        return (t[0], t[1], t[2], t[6], t[3], t[4], t[5], 0)


def reset():
    print("[stump] restart requested")
    sys.exit(3)                            # the start-at-login service starts it again


soft_reset = reset


def freq(*args):
    return 0


def unique_id():
    import uuid
    return uuid.getnode().to_bytes(6, "big")


def idle():
    pass


def reset_cause():
    return 0


PWRON_RESET = HARD_RESET = WDT_RESET = DEEPSLEEP_RESET = SOFT_RESET = 0
