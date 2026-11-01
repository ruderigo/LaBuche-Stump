#!/usr/bin/env python3
"""A pretend board for Arborist's tests: MicroPython's REPL protocol on
stdin/stdout (the friendly REPL, the raw REPL, raw-paste mode with its flow
control, the soft reset), each piece of code run by the real MicroPython
Unix port with its folder as the board's filesystem.

    board_sim.py --root DIR --micropython PATH [--no-paste] [--window N] [--running]

--running: it starts "running a Stump" (a main loop printing), so the
uploader has to stop it first, as on a real Stump."""
import argparse, os, struct, subprocess, sys, threading, time

ap = argparse.ArgumentParser()
ap.add_argument("--root", required=True)
ap.add_argument("--micropython", required=True)
ap.add_argument("--no-paste", action="store_true")
ap.add_argument("--window", type=int, default=128)
ap.add_argument("--running", action="store_true")
ap.add_argument("--boot-log", default="")
A = ap.parse_args()

OUT = sys.stdout.buffer
LOCK = threading.Lock()
BANNER = b"raw REPL; CTRL-B to exit\r\n>"
running = [A.running]


def say(b):
    with LOCK:
        OUT.write(b)
        OUT.flush()


def chatter():
    n = 0
    while running[0]:
        say(b"[stump] tick %d\r\n" % n)
        n += 1
        time.sleep(0.05)


RUNNER = ("import sys\ncode = open(sys.argv[1]).read()\ntry:\n    exec(code, {'__name__': '__main__'})\n"
          "except BaseException as e:\n    sys.print_exception(e, sys.stderr)\n")


def run(code):
    # A traceback goes to the error part, as on the board (the Unix port
    # prints it on stdout otherwise).
    f = os.path.join(A.root, "..", os.path.basename(A.root) + ".code")
    with open(f, "wb") as o:
        o.write(code)
    p = subprocess.run([A.micropython, "-c", RUNNER, f], cwd=A.root, capture_output=True, timeout=60)
    return p.stdout.replace(b"\n", b"\r\n"), p.stderr.replace(b"\n", b"\r\n")


def finish(code):
    out, err = run(code)
    say(out + b"\x04" + err + b"\x04>")


if running[0]:
    threading.Thread(target=chatter, daemon=True).start()
say(b"ESP-ROM:esp32s3-20210327\r\nMicroPython v1.28.0 on 2026-04-06; Generic ESP32S3 module with Octal-SPIRAM\r\n")
if not running[0]:
    say(b">>> ")

BOOT = (open(A.boot_log, "rb").read() if A.boot_log else
        b"ESP-ROM:esp32s3-20210327\r\nMicroPython v1.28.0 on 2026-04-06\r\n==============================================\r\n"
        b"Project Stump -- Beta A (1.0.0-beta.38)\r\n[watchdog] armed: the board restarts if it stops responding for 120 s\r\n"
        b"[dns] captive portal on 192.168.4.1\r\n[web] serving on 192.168.4.1:80\r\n")
line = b""
mode, code, pend, got = "friendly", b"", b"", 0
while True:
    data = os.read(0, 4096)
    if not data:
        break
    for c in data:
        c = bytes([c])
        if mode == "friendly":
            if c == b"\x03":
                running[0] = False
                say(b"\r\nKeyboardInterrupt: \r\n>>> ")
            elif c == b"\x01" and not running[0]:
                mode, code = "raw", b""
                say(b"\r\n" + BANNER)
            elif not running[0]:
                say(c)
                line = (line + c)[-200:]
                if c == b"\n":
                    if line.strip() == b"import machine; machine.reset()":     # the board restarts: a Stump starting
                        running[0] = True
                        say(BOOT)
                        threading.Thread(target=chatter, daemon=True).start()
                    line = b""
        elif mode == "raw":
            if pend or c == b"\x05":
                pend += c
                if len(pend) < 3:
                    continue
                if pend != b"\x05A\x01":
                    raise SystemExit("bad raw-paste request %r" % pend)
                pend = b""
                if A.no_paste:
                    say(b"R\x00")
                else:
                    say(b"R\x01" + struct.pack("<H", A.window))
                    mode, code, got = "paste", b"", 0
            elif c == b"\x04":
                if not code:
                    say(b"OK\r\nMPY: soft reboot\r\n" + BANNER)
                else:
                    say(b"OK")
                    finish(code)
                    code = b""
            elif c == b"\x01":                # Ctrl-A again: the raw REPL afresh, as MicroPython does
                code = b""
                say(BANNER)
            elif c == b"\x02":
                mode = "friendly"
                say(b"\r\nMicroPython v1.28.0\r\n>>> ")
            elif c == b"\x03":
                code = b""
            else:
                code += c
        elif mode == "paste":
            if c == b"\x04":
                say(b"\x04")
                finish(code)
                mode, code = "raw", b""
                continue
            code += c
            got += 1
            if got == A.window:          # it read a window's worth: room for another
                got = 0
                say(b"\x01")
