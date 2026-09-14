"""ADC analog input reader (battery, light, moisture, ...).

Each channel is named and mapped to a GPIO plus an optional voltage divider.
`ATTN_11DB` is set on ESP32 so the full ~0-3.3 V range is usable — the ESP32
ADC default clamps near ~1.1 V and silently saturates anything above it.

process(content):
  - "sensor"  (the trigger the NomadNet page / a broadcast sends) -> every channel
  - a channel name in the text, e.g. "battery" -> just that channel

Reported voltage is scaled by the channel's divider:  vbat = vpin * divider
(use 2.0 for a 1:1 external resistor divider, 1.0 for a direct connection).

Some boards gate their battery-sense divider behind a separate enable pin
rather than wiring it permanently across the ADC input -- the Heltec V3 is
one: its 390k/100k divider only actually connects to GPIO1 while its enable
pin is asserted, confirmed against Heltec's own datasheet and matching a
real, filed community bug report of "battery always reads 0.00V" on this
exact board from someone who read the ADC without knowing the enable pin
existed. An optional "enable_pin" in the board's battery block handles this.

The enable POLARITY is itself board-revision-specific, not a fixed
constant -- confirmed from a real, filed hardware issue (ropg/
heltec_esp32_lora_v3 #67): Heltec V3.2 boards reversed this from the
original LOW-enable logic (used on V3/V3.1, and what RNode's own
Config.h still ships) to HIGH-enable, and reading with the wrong
polarity silently returns 0.00V rather than erroring -- a wrong reading
looks identical to "no battery connected" with nothing to distinguish
them. "enable_active_low" in the board's battery block controls this,
defaulting to True (the original polarity) -- set it to False for a
V3.2 board specifically.

NOTE: not every board can sense its battery. The Seeed XIAO ESP32-S3 (incl. the
Wio-SX1262 "Meshtastic" kit) has NO battery->ADC path — Meshtastic itself ships
it with battery monitoring disabled. Only declare a battery channel on a board
that actually has the divider wired. See lora_boards.battery_config().
"""

channels = {}          # name -> (ADC, divider, enable_Pin_or_None, enable_active_low)
_SAMPLES = 8           # averaged per read; the ESP32-S3 ADC is noisy
_ENABLE_SETTLE_MS = 2  # brief settle time after asserting an enable pin


def init(adc_map, dividers=None, atten="11db", enable_pins=None, enable_polarity=None):
    """adc_map = {"battery": 1, "light": 2}; dividers = {"battery": 2.0}.
    enable_pins = {"battery": 37} for boards that gate their divider behind
    a separate enable line (see module docstring) -- omit for channels
    without one. enable_polarity = {"battery": True} for active-low (the
    default if omitted), False for active-high -- see the module
    docstring's note on the V3.2 hardware revision.

    Each channel stores (ADC, divider, enable_pin_or_None,
    enable_active_low); divider defaults to 1.0 (direct connect).
    """
    from machine import ADC, Pin
    dividers = dividers or {}
    enable_pins = enable_pins or {}
    enable_polarity = enable_polarity or {}
    for name, pin_num in adc_map.items():
        adc = ADC(Pin(pin_num))
        try:
            _attn = {"0db": ADC.ATTN_0DB, "2_5db": ADC.ATTN_2_5DB,
                     "6db": ADC.ATTN_6DB, "11db": ADC.ATTN_11DB}
            adc.atten(_attn.get(atten, ADC.ATTN_11DB))
        except AttributeError:
            pass   # ports without configurable attenuation (e.g. RP2040)
        ep = enable_pins.get(name)
        active_low = enable_polarity.get(name, True)
        # Idle value is whatever "disabled" means for this polarity --
        # active-low idles HIGH, active-high idles LOW. Getting this
        # backwards would mean the divider sits ENABLED by default
        # between reads, the opposite of the point of having an enable
        # pin at all (continuous, needless current draw on a
        # battery-powered board).
        idle_value = 1 if active_low else 0
        ep_obj = Pin(ep, Pin.OUT, value=idle_value) if ep is not None else None
        channels[name] = (adc, float(dividers.get(name, 1.0)), ep_obj, active_low)


def init_battery(config):
    """Configure the 'battery' channel from the active board's preset
    (lora_boards.battery_config) — the board owns the pin + divider (+
    optional enable_pin and its polarity). NO-OP when the board declares
    no battery (e.g. stock XIAO ESP32-S3), so callers can call this
    unconditionally and just list this module as a peripheral;
    process() and battery_voltage() return None when nothing is wired.

    Never raises -- a battery-sense wiring problem or an unexpected
    ADC/Pin failure on this one, entirely optional diagnostic feature
    must never be able to prevent the rest of boot (identity, Transport,
    the radio) from starting. Confirmed this mattered directly: an
    earlier version had no such guard, and a battery-read failure at
    boot propagated all the way up through boot_common's retry logic,
    which gives up and sits silently idle after repeated failures --
    on a board with no visible "I'm alive" indicator otherwise, that is
    indistinguishable from the board simply not turning on at all.
    Returns the resolved battery config dict, or None (whether because
    the board has none, or because setup itself failed)."""
    try:
        from lora_boards import battery_config
        bat = battery_config(config)
        if bat:
            init({"battery": bat["pin"]}, dividers={"battery": bat.get("divider", 1.0)},
                 enable_pins={"battery": bat["enable_pin"]} if bat.get("enable_pin") is not None else None,
                 enable_polarity={"battery": bat.get("enable_active_low", True)})
        return bat
    except Exception as e:
        print("[adc_reader] battery init failed (non-fatal, continuing without it):", e)
        return None


def _raw(adc, enable_pin=None, active_low=True):
    if enable_pin is not None:
        import time
        enable_pin.value(0 if active_low else 1)   # asserted, per this channel's own polarity
        time.sleep_ms(_ENABLE_SETTLE_MS)
    try:
        s = 0
        for _ in range(_SAMPLES):
            s += adc.read_u16()
        return s // _SAMPLES
    finally:
        # Always released, even if the read raised -- an enable pin left
        # asserted after an exception would keep drawing through the
        # divider indefinitely on a board this is meant to save power on.
        if enable_pin is not None:
            enable_pin.value(1 if active_low else 0)


def read_voltage(name):
    """Scaled voltage (float) for one channel, or None if not configured
    OR if the read itself failed for any reason -- never raises. A
    diagnostic reading is not worth taking down a caller over; see
    init_battery()'s own docstring for why this matters concretely,
    not just in principle."""
    ch = channels.get(name)
    if not ch:
        return None
    adc, divider, enable_pin, active_low = ch
    try:
        return _raw(adc, enable_pin, active_low) * 3.3 / 65535 * divider
    except Exception as e:
        print("[adc_reader] read of '%s' failed (non-fatal):" % name, e)
        return None


def battery_voltage():
    """Battery-channel voltage (float), or None if no battery is configured
    or the read failed."""
    return read_voltage("battery")


def _read(name, adc, divider, enable_pin=None, active_low=True):
    raw = _raw(adc, enable_pin, active_low)
    return "{}: {:.2f}V (raw {})".format(name, raw * 3.3 / 65535 * divider, raw)


def process(content):
    if not channels:
        return None
    c = content.lower()
    # Generic trigger -> report all channels (the NomadNet page sends "sensor")
    if "sensor" in c:
        return "\n  ".join(_read(n, a, d, e, al) for n, (a, d, e, al) in channels.items())
    # Named trigger -> just that channel (command text, or explicit process("battery"))
    for name, (adc, divider, enable_pin, active_low) in channels.items():
        if name in c:
            return _read(name, adc, divider, enable_pin, active_low)
    return None
