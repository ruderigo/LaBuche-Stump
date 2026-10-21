# Keeping a Stump alive on its own: a watchdog for a Stump that stops
# responding, and a rescue start for one that keeps failing to start.
#
#   watchdog   the loop feeds it every FEED_S. On the CAM it's the chip's
#              hardware watchdog (machine.WDT): if the loop stops for
#              WATCHDOG_S (config.py, default 60; 0 = off), the board
#              resets. On a computer the loop touches a heartbeat file and
#              the supervisor (stump_pc.py --service) restarts the Stump
#              when it goes stale.
#   rescue     a start that doesn't reach STABLE_S of running (a crash, a
#              hang the watchdog ended, a power cut) counts as a failed
#              start; RESCUE_AFTER of them in a row and the next start is
#              a rescue start: no radio, no chain, no plugins -- just its
#              Wi-Fi, the pages and /admin, saying what failed. After
#              RESCUE_RETRY_S it tries a normal start again; if even the
#              rescue start keeps failing, it waits RESCUE_WAIT_S before
#              trying from scratch. It never stops trying.
#
# A planned stop or restart (Ctrl+C, the Provisioner, /admin's Restart)
# isn't a failure (planned()). What happened is in the state file and
# /admin (status()).

import time

try:
    import ujson as json
except ImportError:
    import json

STATE_FILE = "/boot_state.json"
FEED_S = 5
STABLE_S = 120
RESCUE_AFTER = 3
RESCUE_RETRY_S = 1800
RESCUE_WAIT_S = 600
DEFAULT_WATCHDOG_S = 60
# "Disarmed": the chip's watchdog can't be switched off, only given a long
# time -- 2 hours (the Provisioner gives the same before each command).
# Not the largest number possible: a huge one may overflow in the chip's
# own settings and come out short. Left at the console, the board starts
# the Stump by itself when it's up.
DISARM_MS = 2 * 3600 * 1000

_st = {"mode": "normal", "short": 0, "t0": None, "wdt": None, "lag_ms": 0, "last": None,
       "stable": False, "cause": None}


def _load():
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d):
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        import os
        try:
            os.rename(tmp, STATE_FILE)
        except OSError:
            os.remove(STATE_FILE)
            os.rename(tmp, STATE_FILE)
    except Exception:
        pass       # a full or read-only filesystem isn't worth failing over


def _now():
    return time.time()


def watchdog_s():
    try:
        import config
        v = getattr(config, "WATCHDOG_S", None)
    except ImportError:
        v = None
    return DEFAULT_WATCHDOG_S if v is None else int(v)


def _reset_cause():
    """Why the board last reset, when it can tell (the CAM)."""
    try:
        import machine
        c = machine.reset_cause()
        if c == getattr(machine, "WDT_RESET", -1):
            return "watchdog"
        if c == getattr(machine, "PWRON_RESET", -1):
            return "power"
    except Exception:
        pass
    return None


def _say_reset():
    """The serial log says why the board last reset (the CAM)."""
    try:
        import machine
        c = machine.reset_cause()
    except Exception:
        return
    names = {}
    for n in ("PWRON_RESET", "HARD_RESET", "WDT_RESET", "DEEPSLEEP_RESET", "SOFT_RESET"):
        v = getattr(machine, n, None)
        if v is not None:
            names[v] = {"PWRON_RESET": "power on / USB", "HARD_RESET": "reset button",
                        "WDT_RESET": "THE WATCHDOG", "DEEPSLEEP_RESET": "deep sleep",
                        "SOFT_RESET": "soft reset"}[n]
    print("[watchdog] last reset: %s (code %s)" % (names.get(c, "?"), c))


def begin():
    """At the start of every start: was the last run a failure? Returns
    "normal" or "rescue" (and may wait first: see the top)."""
    _say_reset()
    d = _load()
    short = int(d.get("short", 0) or 0)
    last = d.get("last")                      # the last failure, as /admin says it
    if d.get("running") and not d.get("stable") and not d.get("planned"):
        short += 1
        if d.get("crash"):                    # this run's own error (note_crash)
            last = d["crash"]
        elif d.get("wd") or _reset_cause() == "watchdog":
            last = "it stopped responding (the watchdog restarted it)"
        else:
            last = "it stopped before it was up for %d s (a crash, or the power)" % STABLE_S
    elif d.get("stable") or not d.get("running"):
        short = 0
    # (d["wd"]: the computer's supervisor restarted it, its heartbeat stale)
    _st["cause"] = "watchdog" if d.get("wd") else _reset_cause()
    if short >= 2 * RESCUE_AFTER:
        # Even the rescue start keeps failing: rest, then from scratch.
        print("[watchdog] %d failed starts in a row: waiting %d s, then starting normally" % (short, RESCUE_WAIT_S))
        time.sleep(RESCUE_WAIT_S)
        short = 0
    mode = "rescue" if short >= RESCUE_AFTER else "normal"
    last = last if short else None
    _st.update(mode=mode, short=short, t0=_now(), last=last, stable=False)
    _save({"running": True, "stable": False, "planned": False, "short": short, "mode": mode,
           "crash": None, "last": last, "at": _now()})
    if mode == "rescue":
        print("=" * 52)
        print("RESCUE START: %d starts in a row failed (%s)." % (short, last or "?"))
        print("No radio, no chain, no plugins: the Wi-Fi, the pages and /admin.")
        print("A normal start is tried again in %d min." % (RESCUE_RETRY_S // 60))
        print("=" * 52)
    return mode


def note_crash(text):
    """The node stopped on an error (boot_common): kept for /admin."""
    d = _load()
    d["crash"] = str(text)[:200]
    _save(d)


def planned(fresh=False):
    """A stop or restart someone asked for: not a failed start. fresh: the
    count of failed starts cleared too -- the next start is a normal one."""
    disarm()
    d = _load()
    d["planned"] = True
    if fresh:
        d["short"] = 0
        d["mode"] = "normal"
    _save(d)


def console_stop():
    """Ctrl+C at the console (the Provisioner, mpremote): the watchdog
    disarmed FIRST, before anything slow -- mpremote sends Ctrl+C twice,
    and a second one landing mid-way used to skip the disarm, so the board
    reset a minute into the upload. Ctrl+C is held off while this runs, and
    the disarm retried if one still gets through."""
    try:
        import micropython
        micropython.kbd_intr(-1)
    except Exception:
        pass
    try:
        for _ in range(5):
            try:
                disarm()
                break
            except KeyboardInterrupt:
                continue
        for _ in range(3):
            try:
                # Someone at the console (an upload, a fix): the next start
                # is a normal one, whatever came before -- an upload's own
                # stops and resets once added up to a rescue start.
                planned(fresh=True)
                break
            except KeyboardInterrupt:
                continue
    finally:
        try:
            import micropython
            micropython.kbd_intr(3)
        except Exception:
            pass


def mode():
    return _st["mode"]


def status():
    """For /admin: the mode, how many starts failed, why, and how late
    the loop has run (the longest wait for a turn since the start)."""
    up = int(_now() - _st["t0"]) if _st["t0"] else 0
    retry = max(0, RESCUE_RETRY_S - up) if _st["mode"] == "rescue" else None
    return {"mode": _st["mode"], "failed_starts": _st["short"], "last_failure": _st["last"],
            "cause": _st["cause"], "uptime_s": up, "retry_in_s": retry, "lag_ms": _st["lag_ms"],
            "watchdog_s": watchdog_s(), "armed": _st["wdt"] is not None}


def _arm():
    s = watchdog_s()
    if s <= 0:
        return
    try:
        import stump_host  # noqa: F401  (a computer: the supervisor watches the heartbeat)
        return
    except ImportError:
        pass
    try:
        import machine
        _st["wdt"] = machine.WDT(timeout=s * 1000)
        print("[watchdog] armed: the board restarts if it stops responding for %d s" % s)
    except Exception as e:
        print("[watchdog] not armed:", e)


def disarm():
    """Before a planned stop at the console (Ctrl+C, the Provisioner):
    the chip's watchdog can't be switched off, only given a very long
    time -- so an upload at the REPL isn't cut short by a reset."""
    if _st["wdt"] is None:
        return
    try:
        import machine
        w = machine.WDT(timeout=DISARM_MS)
        w.feed()
        _st["wdt"] = None
        print("[watchdog] disarmed (a stop at the console)")
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print("[watchdog] couldn't disarm:", e)


def _beat():
    if _st["wdt"] is not None:
        try:
            _st["wdt"].feed()
        except Exception:
            pass
    try:
        import stump_host
        stump_host.heartbeat()
    except Exception:
        pass


def _ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except AttributeError:
        return a - b


async def loop():
    """Feeds the watchdog, marks the start stable after STABLE_S, and in
    a rescue start tries a normal one after RESCUE_RETRY_S."""
    try:
        import uasyncio as asyncio
    except ImportError:
        import asyncio
    _arm()
    while True:
        t = _ms()
        _beat()
        await asyncio.sleep(FEED_S)
        check(_diff(_ms(), t) - FEED_S * 1000)


def check(late=0):
    """One turn of loop(): how late it ran, a start that's now stable, a
    rescue start that's had its time."""
    if late > _st["lag_ms"]:
        _st["lag_ms"] = late
        if late > 2000:
            print("[watchdog] the Stump was busy for %d ms (nobody else served meanwhile)" % late)
    if _st["t0"] is None:
        return
    up = _now() - _st["t0"]
    if not _st["stable"] and up >= STABLE_S:
        _st["stable"] = True
        d = _load()
        d.update(stable=True, short=0 if _st["mode"] == "normal" else d.get("short", 0))
        _save(d)
    if _st["mode"] == "rescue" and up >= RESCUE_RETRY_S:
        print("[watchdog] rescue start: trying a normal start now")
        try_normal()


def try_normal():
    """Leave the rescue start: the next start is a normal one."""
    d = _load()
    d.update(running=False, stable=True, planned=True, short=0)
    _save(d)
    disarm()
    import settings
    settings.restart()
