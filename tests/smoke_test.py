#!/usr/bin/env python3
"""Headless geometry, firmware catalog, and input smoke test for R36S (640x480) and R36MAX (720x720)."""
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as flasher


class HeadlessCanvas:
    """Records draw calls and verifies every rectangle and text run stays inside the screen geometry."""
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.r = 1
        self.controller_ids = {0}
        self.rects = []
        self.texts = []
        self.overflows = []
        self._events = []

    def set_color(self, color):
        assert color in flasher.COLORS or (isinstance(color, tuple) and len(color) == 3), f"Invalid color: {color}"

    def clear(self, _r):
        self.rects.clear()
        self.texts.clear()

    def rect(self, x, y, w, h, color, border=None):
        self.set_color(color)
        if border:
            self.set_color(border)
        x, y, w, h = int(x), int(y), max(1, int(w)), max(1, int(h))
        self.rects.append((x, y, w, h, color, border))
        if x < 0 or y < 0 or x + w > self.w or y + h > self.h:
            self.overflows.append(f"rect({x}, {y}, {w}, {h}) overflows {self.w}x{self.h}")

    def text(self, x, y, value, color='ink', scale=1.0, max_chars=None):
        self.set_color(color)
        text_str = str(value).encode('ascii', 'replace').decode('ascii')
        if max_chars is not None:
            text_str = flasher.shorten(text_str, max_chars)
        px_w = int(len(text_str) * 8 * scale)
        px_h = int(12 * scale)
        self.texts.append((int(x), int(y), text_str, px_w, px_h, color))
        if int(x) < 0 or int(y) < 0 or int(x) + px_w > self.w or int(y) + px_h > self.h:
            self.overflows.append(f"text({x}, {y}, {text_str!r}) -> {int(x)+px_w}x{int(y)+px_h} overflows {self.w}x{self.h}")

    def finish(self):
        pass

    def event(self, _buf):
        return 0

    def delay(self, _ms):
        pass

    def close(self):
        pass


def verify_bundled_firmware():
    expected = {
        'SIGINT_ESP32_Link.bin': '78c7f4b1c652e519995acfb4e338d2334659052cc7fdf5f3a0c58b9d14024ea4',
        'WLED_16.0.1_ESP32.bin': '628917b0753edcfc9a8408e6387c6d1ace6a360d315441e9563d60299fef8594',
        'MicroPython_FullErase_v1.29.0.bin': 'e67ad6015a0a504c1fec9aa9bbf589d0432ed28e62546f4f8dd8a147f8bd95f6',
    }
    cat = flasher.catalog()
    items = {p.name: p for p in flasher.list_firmware()}
    for name, sha in expected.items():
        assert name in cat, f"Missing {name} from catalog.json"
        assert name in items, f"Missing {name} from discovered firmware list"
        actual = flasher.sha256_file(items[name])
        assert actual == sha, f"SHA-256 mismatch for {name}: {actual} != {sha}"
    return len(items)


def test_geometry(w, h, label):
    os.environ['R36S_SCREEN_SIZE'] = f"{w}x{h}"
    dw, dh = flasher.detect_screen_size()
    assert (dw, dh) == (w, h), f"detect_screen_size returned {dw}x{dh} instead of {w}x{h}"
    canvas = HeadlessCanvas(w, h)
    ui = flasher.App(canvas)
    assert (ui.w, ui.h) == (w, h)
    expected_rows = 9 if h >= 720 else 5
    assert ui.visible_rows() == expected_rows, f"Expected {expected_rows} rows on {w}x{h}, got {ui.visible_rows()}"

    # 1. Draw main firmware screen across all items
    for _ in range(len(ui.items) + 2):
        ui.draw()
        ui.move(1)

    # 2. Draw serial logs screen with 30 synthetic log lines
    ui.section = 'LOGS'
    for idx in range(30):
        ui.add_serial_log(f"ets Jul 29 2019 12:21:46 rst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT) #{idx}")
    ui.draw()
    ui.key('up')
    ui.draw()
    ui.key('b')
    assert ui.section == 'FIRMWARE'

    # 3. Draw options exit menu
    ui.menu_open = True
    ui.draw()
    ui.key('down')
    ui.draw()
    ui.menu_open = False

    # 4. Draw flash progress dialog with synthetic logs
    ui.flash_dialog = True
    ui.flash_target = 'SIGINT ESP32 Link'
    ui.flash_phase = 'WRITING APP'
    ui.flash_percent = 64
    for idx in range(20):
        ui.add_flash_log(f"Writing at 0x000{idx:02x}000... ({idx * 5} %)")
    ui.draw()
    ui.flash_done = True
    ui.flash_result_color = 'green'
    ui.draw()
    ui.flash_dialog = False

    # 5. Random input fuzzing (500 actions)
    rng = random.Random(42)
    keys = ['up', 'down', 'left', 'right', 'x', 'y', 'l', 'r', 'b', 'start']
    for step in range(500):
        k = rng.choice(keys)
        ui.key(k)
        if not ui.running:
            ui.running = True
            ui.menu_open = False
        if step % 25 == 0:
            ui.draw()

    assert not canvas.overflows, f"Layout overflows on {label} ({w}x{h}):\n" + "\n".join(canvas.overflows[:10])
    return {
        "geometry": f"{w}x{h}",
        "device": ui.device_title,
        "visible_firmware_rows": ui.visible_rows(),
        "rects_drawn": len(canvas.rects),
        "texts_drawn": len(canvas.texts),
        "overflows": len(canvas.overflows),
    }


def main():
    fw_count = verify_bundled_firmware()
    results = [
        test_geometry(640, 480, "R36S (4:3 Standard)"),
        test_geometry(720, 720, "R36MAX (1:1 Square IPS)"),
    ]
    summary = {
        "version": flasher.__version__,
        "firmware_verified": fw_count,
        "geometries": results,
        "status": "PASS",
    }
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
