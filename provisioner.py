#!/usr/bin/env python3
"""
THE PROVISIONER — Tier 3 Technician Deployment Utility
=========================================================

A self-contained flasher / config wizard / diagnostic suite for field
technicians deploying Stump (Tier 1) and Firefly (Tier 2) hardware.

Boards it knows how to handle:
  - Heltec V3 (ESP32-S3 + SX1262), Control Plane role -> flashed as an
                                       RNode radio bridged to a CAM, via `rnodeconf`
  - Heltec V3 (ESP32-S3 + SX1262), Standalone Transport role -> flashed with
                                       microReticulum_Firmware (a self-contained
                                       Reticulum node, no CAM, no host), also via
                                       `rnodeconf`, then locked into TNC mode
  - Freenove ESP32-S3-CAM          -> flashed with MicroPython, then loaded
                                       with the Stump substance-engine app
                                       (billboard / RRC chat / BarKeep bot)

Usage:
    python3 provisioner.py                 # interactive wizard (default)
    python3 provisioner.py --scan          # just list connected boards
    python3 provisioner.py --diag PORT     # run diagnostics against a port
    python3 provisioner.py --wipe-sd PORT  # erase + reformat the CAM's SD card
    python3 provisioner.py --upload-app PORT  # re-push app files without reflashing
    python3 provisioner.py --get-ip PORT   # look up the Stump's live AP IP
    python3 provisioner.py --check-tools   # verify esptool/mpremote/rnodeconf present

Nothing here talks to a real board unless you run it on a machine that has
one plugged in. Every external call is wrapped so a missing tool or absent
board produces an explicit, readable message instead of a stack trace.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

# A distinct sentinel, not None -- _generate_config_py needs to tell
# "this parameter wasn't passed, leave that config.py line untouched"
# apart from "this parameter was explicitly passed as None", since for
# ssid_name specifically, None IS a real, meaningful value (it means
# "use the default hosted page"), not an absence of one. Reusing None
# for both would make those two cases indistinguishable.
_UNSET = object()
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUMP_APP_DIR = Path(__file__).resolve().parent / "final_firmware"
CONFIG_OUT_DIR = Path.home() / ".provisioner" / "profiles"
LOCAL_PATHS_FILE = Path.home() / ".provisioner" / "local_paths.json"

# Known USB VID:PID pairs. Real-world values for these two boards; used as a
# best-effort hint only — the technician always gets to confirm/override.
# USB identification is a HEURISTIC, not proof -- both IDs below are
# generic parts used by plenty of other hardware, so nothing here is
# Heltec- or Freenove-specific and the wording stays tentative.
#
#   303a:1001  Espressif's own native-USB ID, present on ANY ESP32-S3
#              that exposes USB directly -- which the Freenove CAM does.
#              This was previously labelled "Heltec V3", which is why a
#              CAM got identified as a Heltec.
#   10c4:ea60  Silicon Labs CP2102 USB-UART bridge. The Heltec V3 has no
#              native USB; it goes through this bridge chip, so it shows
#              up with a Silicon Labs ID rather than an Espressif one.
#
# The practical tell, visible in the port name itself: native USB
# enumerates as usbmodem (macOS) / ttyACM (Linux), a UART bridge as
# usbserial / ttyUSB. _guess_board() uses that as a second signal.
KNOWN_BOARDS = {
    ("303a", "1001"): "ESP32-S3 with native USB — likely the Freenove CAM",
    ("303a", "0002"): "ESP32-S3 with native USB — likely the Freenove CAM",
    ("10c4", "ea60"): "CP2102 UART bridge — likely the Heltec V3",
    ("1a86", "7523"): "CH340 UART bridge — likely the Heltec V3",
}

# Substrings that mark a guess as pointing at the Heltec. Checked against
# the guess string rather than scattering "Heltec" comparisons around.
_HELTEC_MARKERS = ("Heltec",)


def _guess_board(vid, pid, device, description):
    """Best-effort board identification from USB identity plus the port
    name. Returns a human-readable guess string.

    Deliberately conservative: where the two signals disagree, or
    neither is conclusive, this returns "unknown board" so the wizard
    asks instead of confidently getting it wrong -- which is the failure
    that made a CAM get flashed as a Heltec."""
    by_id = KNOWN_BOARDS.get((vid, pid)) if vid and pid else None

    # Port-name signal: native USB vs a UART bridge chip.
    name = (device or "").lower()
    if "usbmodem" in name or "ttyacm" in name:
        by_name = "native USB"
    elif "usbserial" in name or "ttyusb" in name or "wchusbserial" in name:
        by_name = "UART bridge"
    else:
        by_name = None

    if by_id:
        id_says_bridge = "bridge" in by_id
        if by_name and id_says_bridge != (by_name == "UART bridge"):
            # The two signals contradict each other -- don't pick one.
            return "unknown board (USB id and port name disagree)"
        return by_id

    if by_name == "native USB":
        return "native USB device — likely the Freenove CAM"
    if by_name == "UART bridge":
        return "UART bridge device — likely the Heltec V3"
    return "unknown board"

DEFAULT_RADIO = {
    "frequency": 915000000,
    "bandwidth": 125000,
    "txpower": 7,
    "spreadingfactor": 8,
    "codingrate": 5,
}

# The build this Provisioner ships with. Must match STUMP_VERSION in
# final_firmware/example_node.py. Checked whenever a firmware folder is
# resolved -- structural validation ("does main.py exist") happily
# accepts a folder from any previous beta, which is how a remembered
# path to an older build kept silently overriding the current one and
# uploading stale files over a fresh install.
EXPECTED_STUMP_VERSION = "Beta A"


def _folder_version(path):
    """Reads STUMP_VERSION out of a firmware folder's example_node.py.

    Parsed textually rather than imported: the folder is MicroPython
    source that won't import on a laptop, and this has to work on a
    folder that may be broken or from an entirely different build.
    Returns the version string, or None if it can't be determined."""
    try:
        f = Path(path) / "example_node.py"
        if not f.is_file():
            return None
        for line in f.read_text().splitlines():
            line = line.strip()
            if line.startswith("STUMP_VERSION"):
                _, _, val = line.partition("=")
                return val.strip().strip("'\"")
    except Exception:
        pass
    return None


def _is_current_firmware_folder(path):
    """Structure AND version. Used as the resolver's validate_fn so a
    folder from an older beta simply doesn't qualify, instead of being
    accepted and then failing later as a pile of 'file doesn't match'
    warnings that look like corruption rather than a wrong folder."""
    p = Path(path)
    if not ((p / "main.py").is_file() and (p / "urns" / "reticulum.py").is_file()):
        return False
    return _folder_version(p) == EXPECTED_STUMP_VERSION


def _describe_folder(path):
    """Human-readable reason a folder was or wasn't accepted -- so the
    prompt says 'that's Beta 5' rather than just refusing."""
    p = Path(path)
    if not p.is_dir():
        return "not a folder"
    if not (p / "main.py").is_file():
        return "no main.py -- not a firmware folder (unzipped to a subfolder?)"
    if not (p / "urns" / "reticulum.py").is_file():
        return "missing urns/ -- incomplete copy"
    v = _folder_version(p)
    if v is None:
        return "no version stamp -- predates versioned builds"
    if v != EXPECTED_STUMP_VERSION:
        return "that folder is %s, this Provisioner ships %s" % (v, EXPECTED_STUMP_VERSION)
    return "OK (%s)" % v


REQUIRED_TOOLS = ["esptool", "mpremote", "rnodeconf"]

# Mirrors fserv.py's own fallback weights -- shown as the pre-filled
# defaults in the wizard, so pressing Enter through the custom prompts
# reproduces the standard economy exactly.
DEFAULT_CREDIT_WEIGHTS = {"video": 3, "music": 2, "document": 1, "other": 1}

# SHA256 fingerprints (first 16 hex chars) of every current canonical
# file, checked against the LOCAL folder before any upload starts --
# catches a stale local copy before wasting a flash+upload cycle, not
# after. Regenerate with:
#   python3 -c "import hashlib; from pathlib import Path
#   [print(f'    {str(f.relative_to(\"final_firmware\"))!r}: {hashlib.sha256(f.read_bytes()).hexdigest()[:16]!r},')
#    for f in sorted(Path('final_firmware').rglob('*')) if f.is_file()]"
EXPECTED_FILE_HASHES = {
    "barkeep.py": "a67486f0dd0f7cfc",
    "billboard.py": "a351fc95e0ffdf0c",
    "boot_common.py": "d1f60dd4434752bf",
    "captive_portal.py": "8c2a0ee90cdc1e04",
    "config.py": "40524df2178b7afe",
    "docs/CLIENT_QUICKSTART.md": "22f09055cb4c65f1",
    "docs/COMMANDS.md": "4735cd01e77cdbd0",
    "docs/HIDDEN_FEATURES.md": "1a530af86fb36e05",
    "example_node.py": "e9c4849ce755a3f2",
    "flasher_ui.py": "f0af338707d46d25",
    "fserv.py": "88a2722727eed728",
    "fservbot/README.md": "4ab77b9e51dd5edc",
    "fservbot/__init__.py": "f164090d81312df8",
    "fservbot/core.py": "18ed35f2e0710d37",
    "fservbot/install.py": "8d6f5e5870ce5437",
    "fservbot/plugin.json": "bc8aca2fefb8b9e7",
    "fservbot/templates.py": "120b25ba5d326958",
    "i18n.py": "4e8a737716401af6",
    "lib/bz2_fast_xtensawin.mpy": "ac55d9eda2126432",
    "lib/ed25519_fast_xtensawin.mpy": "96e74dac45f91687",
    "lib/ed25519_iram.mpy": "96e74dac45f91687",
    "lora_boards.py": "d60ef896cd1a0ff9",
    "main.py": "1aa56ed560be50d7",
    "node_common.py": "e1e748a3283da2f4",
    "peripherals/__init__.py": "d2bdac4de6de79de",
    "peripherals/adc_reader.py": "50a393bfa61641d4",
    "rrc.py": "afff9f4da003f317",
    "rrc_mesh.py": "f981f4669737a96e",
    "rrc_ui.py": "0438e18394467c08",
    "stumpid/README.md": "da60b797b09855c3",
    "stumpid/__init__.py": "83462abf471caca1",
    "stumpid/core.py": "d7f755af8f6b5494",
    "stumpid/install.py": "7b218d5825cf1ecc",
    "stumpid/plugin.json": "049f73bbf3ccfd03",
    "tools_payload/flasher/catalog.json": "8dc38061d6bf49a3",
    "tools_payload/flasher/esptool-bundle.js": "ef7d5a237d3f273e",
    "tools_payload/flasher/esptool-js-LICENSE.txt": "1c25f29242785d63",
    "tools_payload/images/README.txt": "10dbf1ed3e3eee26",
    "urns/__init__.py": "4a83ee3f5cd42ca4",
    "urns/buffer.py": "b1da1d0723340421",
    "urns/bz2dec.py": "8149a39deee822c2",
    "urns/channel.py": "0f57d5ec378333c7",
    "urns/const.py": "4d9daaabfbbd93d7",
    "urns/crypto/__init__.py": "dec8540a87c232a4",
    "urns/crypto/aes.py": "274f35d97de9d852",
    "urns/crypto/ed25519.py": "ebc40e75bb966c26",
    "urns/crypto/hashes.py": "4c44d02dbdf161c9",
    "urns/crypto/hkdf.py": "a42c6930a4ffcdfa",
    "urns/crypto/hmac.py": "f429e6f7db68c93c",
    "urns/crypto/pkcs7.py": "42f61d13a1723f94",
    "urns/crypto/pure25519/__init__.py": "08e1671537509493",
    "urns/crypto/pure25519/_ed25519.py": "4e4eca4dfcc4c5b2",
    "urns/crypto/pure25519/basic.py": "aa922fb5f1f21f2f",
    "urns/crypto/pure25519/ed25519_oop.py": "c38c8cc0022c096f",
    "urns/crypto/pure25519/eddsa.py": "ab45a899d05312f4",
    "urns/crypto/sha512.py": "80ac504ccc9ac829",
    "urns/crypto/token.py": "f9f758331688eb78",
    "urns/crypto/x25519.py": "62998f40d4e0976f",
    "urns/destination.py": "29eadd36dbfe9e3e",
    "urns/identity.py": "1d5cb6c98a9b8fe0",
    "urns/interfaces/__init__.py": "e95b13adb0414a0c",
    "urns/interfaces/e32.py": "c5cbc68e04a2c30a",
    "urns/interfaces/lora.py": "47a36c7c61d712b9",
    "urns/interfaces/serial.py": "4c31a883a20f54c8",
    "urns/interfaces/tcp.py": "a29d90caa017764a",
    "urns/interfaces/udp.py": "1de3688c42ad0eb1",
    "urns/interfaces/wifi_serial.py": "fdb89bf39095a2fe",
    "urns/link.py": "a562be337b6f8137",
    "urns/log.py": "4b5576693991c7a6",
    "urns/lxmf.py": "0d0c1d4bb42449c2",
    "urns/packet.py": "7a14b682d9b1c619",
    "urns/resource.py": "97b1fa47b676a517",
    "urns/reticulum.py": "43d0722fde6cb715",
    "urns/transport.py": "33734cf8b72a91a0",
    "urns/umsgpack.py": "7df3983d6abcf149",
}

# The exact set of files (relative paths, including subdirectories) that
# make up the Reticulum-rooted build. upload_stump_app() uploads only
# these -- by manifest, not a blind glob of whatever else happens to
# share the folder.
# Files that live in the firmware tree but have no business on the
# board. plugin.json is a spec the Provisioner reads on the HOST, and
# READMEs are for people -- uploading either just spends flash on a
# memory-constrained device to store text nothing there will ever read.
BOARD_EXCLUDE_SUFFIXES = (".md",)
BOARD_EXCLUDE_NAMES = ("plugin.json",)


# Whole directories that are staged on the technician's machine and
# pushed to the node's SD card, never written to its flash. The board
# has megabytes; the flash partition does not, and a 218KB JS bundle
# plus firmware images have no business competing with the app for it.
BOARD_EXCLUDE_DIRS = ("tools_payload",)


def _belongs_on_board(relpath):
    if relpath.split("/")[0] in BOARD_EXCLUDE_DIRS:
        return False
    name = relpath.split("/")[-1]
    if name in BOARD_EXCLUDE_NAMES:
        return False
    return not any(name.endswith(sfx) for sfx in BOARD_EXCLUDE_SUFFIXES)


STUMP_APP_FILES = [f for f in EXPECTED_FILE_HASHES.keys() if _belongs_on_board(f)]

# Derived from STUMP_APP_FILES itself (not hand-written) so this can't
# drift out of sync -- the old flat-file build's equivalent string did
# exactly that once. Many files across nested subdirectories makes a
# full listing unwieldy, so this summarizes the top-level structure and
# file count rather than enumerating everything.
_STUMP_TOP_LEVEL = sorted({f.split("/")[0] for f in STUMP_APP_FILES})
_STUMP_FILES_DESC = (
    "Reticulum-rooted firmware folder (should contain %d files: %s)"
    % (len(STUMP_APP_FILES), ", ".join(_STUMP_TOP_LEVEL))
)


DEFAULT_AP_IP = "192.168.4.1"   # MicroPython's ESP32 AP default

# The Freenove ESP32-S3-CAM carries OCTAL SPI PSRAM, so it needs the
# SPIRAM_OCT build specifically -- the plain ESP32_GENERIC_S3 image
# boots but never sees the extra RAM, which this app depends on.
CAM_FIRMWARE_LATEST_URL = (
    "https://micropython.org/resources/firmware/"
    "ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin"
)
# Both markers must appear in a filename for it to be offered as a
# candidate, so a plain GENERIC_S3 image sitting in Downloads isn't
# suggested for a board that would silently lose its PSRAM to it.
CAM_FIRMWARE_NAME_MARKERS = ("esp32_generic_s3", "spiram_oct")

FIRMWARE_CACHE_DIR = Path.home() / ".provisioner" / "firmware"


def discover_plugins(fw_dir):
    """Finds every plugin folder in a firmware tree.

    A plugin is a directory containing plugin.json. Discovering them
    means a new add-on is installed by dropping its folder in and
    re-running the wizard -- the Provisioner needs no edit to know
    about it, and neither does the firmware.

    Returns a list of (folder_name, spec_dict), skipping anything whose
    spec won't parse rather than failing the whole run for one bad file.
    """
    found = []
    try:
        for d in sorted(Path(fw_dir).iterdir()):
            if not d.is_dir():
                continue
            spec_file = d / "plugin.json"
            if not spec_file.is_file():
                continue
            try:
                with open(spec_file) as f:
                    found.append((d.name, json.load(f)))
            except Exception as e:
                print(f"  (skipping {d.name}: plugin.json won't parse -- {e})")
    except Exception:
        pass
    return found


def configure_plugins(fw_dir, show_advanced=False):
    """Runs the wizard prompts each discovered plugin declares.

    Returns {CONFIG_KEY: value} to be written into config.py.

    Only non-advanced prompts are asked by default. Plugin authors mark
    the settings with working defaults as advanced precisely so a
    technician isn't walked through six questions to install one thing;
    those stay changeable later, either by editing config.py or from
    whatever in-app surface the plugin provides.
    """
    plugins = discover_plugins(fw_dir)
    if not plugins:
        return {}

    banner("PLUGINS FOUND")
    for name, spec in plugins:
        title = spec.get("title", name)
        summary = spec.get("summary", "")
        print(f"  {title}  (v{spec.get('version','?')})")
        if summary:
            print(f"    {summary}")
        # Surface the claims that matter for a constrained board, from
        # the plugin's own declaration -- worth seeing before install.
        flags = []
        if spec.get("modifies_core_files"):
            flags.append("MODIFIES CORE FILES")
        if spec.get("binds_ports"):
            flags.append("binds a port")
        if spec.get("starts_tasks"):
            flags.append("starts a background task")
        want = spec.get("stump_version")
        if want and want != EXPECTED_STUMP_VERSION:
            flags.append(f"built for {want}, this is {EXPECTED_STUMP_VERSION}")
        if flags:
            print("    NOTE: " + "; ".join(flags))
        print()

    values = {}

    def _ask_prompt(pr):
        """Asks one declared prompt and records it, validating by type."""
        key = pr.get("key")
        if not key:
            return
        default = pr.get("default", "")
        ptype = pr.get("type", "string")

        if ptype == "choice":
            # A closed set (e.g. AUTH_MODE: open/hybrid/mandatory) written
            # to config.py as free text was one string away from a plugin
            # reading garbage and falling back silently. ask_choice()
            # can't type garbage in the first place.
            options = pr.get("options") or [str(default)]
            picked = ask_choice("    " + pr.get("prompt", key), options)
            values[key] = picked
            return

        while True:
            raw = ask("    " + pr.get("prompt", key), str(default))
            if raw is None:
                raw = ""
            if ptype == "int":
                try:
                    values[key] = int(raw)
                    return
                except ValueError:
                    print(f"      '{raw}' isn't a whole number -- try again.")
                    continue
            values[key] = raw
            return

    for name, spec in plugins:
        prompts = (spec.get("wizard") or {}).get("prompts") or []
        basic = [pr for pr in prompts if not pr.get("advanced")]
        advanced = [pr for pr in prompts if pr.get("advanced")]
        if not basic and not advanced:
            continue

        title = spec.get("title", name)
        print(f"  Settings for {title}:")
        note = (spec.get("wizard") or {}).get("note")
        if note:
            for line in _wrap(note, 68):
                print(f"    {line}")

        for pr in basic:
            _ask_prompt(pr)

        # Advanced settings come AFTER the ones that matter, and only on
        # request -- installing one thing shouldn't be six questions.
        # But they must stay reachable here: otherwise a technician who
        # wants a different trigger prefix or a broadcast interval has
        # no way to set it at provision time and has to hand-edit
        # config.py afterwards, which is exactly the manual step this
        # wizard exists to remove.
        if advanced:
            if show_advanced:
                for pr in advanced:
                    _ask_prompt(pr)
            else:
                print(f"    {len(advanced)} optional setting(s), working defaults shown:")
                for pr in advanced:
                    print(f"      {pr.get('key')} = {pr.get('default')!r}")
                if ask_yes_no("    Change any of those now?", False):
                    for pr in advanced:
                        _ask_prompt(pr)
        print()

    return values


def _wrap(text, width):
    """Minimal word wrap for help text -- no textwrap dependency."""
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return out


def preview_ssid(node_name, ap_ip=DEFAULT_AP_IP):
    """Mirrors captive_portal.setup_ap()'s SSID logic so the wizard can
    show what will actually be broadcast.

    Worth showing rather than leaving to discovery: 802.11 caps the SSID
    at 32 characters, so a long node name silently loses its tail. The
    tail here is a fixed 12 characters ("-192.168.4.1"), leaving 20 for
    the name -- ample, but seeing it beats finding out from the Wi-Fi
    list later.

    Returns (ssid, was_clipped).
    """
    ap_part = "-" + ap_ip
    keep = 32 - len(ap_part)
    clipped = len(node_name) > keep
    name = node_name[:keep].rstrip("-") if keep > 0 else ""
    return (name + ap_part)[:32], clipped


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def banner(text):
    print("\n" + "=" * 70)
    print(text)
    print("=" * 70)


def ask(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val if val else default


_YES = ("y", "yes", "yeah", "yep", "ok", "okay", "sure", "true", "1")
_NO = ("n", "no", "nope", "nah", "false", "0")


def ask_yes_no(prompt, default=True):
    """A yes/no prompt that actually validates.

    Callers used to test raw input with .startswith("y"), which quietly
    turned anything unrecognised into NO -- so on a [y] prompt a typo
    silently did the opposite of the default, and nothing said so. Here
    an unrecognised answer is re-asked instead of guessed at, and a
    blank line takes the default.

    Returns a real bool, so no caller has to parse strings again.
    """
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} [{hint}]: ").strip().lower()
        if not raw:
            return default
        if raw in _YES:
            return True
        if raw in _NO:
            return False
        print(f"  '{raw}' isn't a yes or a no -- please answer y or n.")


def ask_choice(prompt, options):
    print(prompt)
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    while True:
        raw = input(f"Choose 1-{len(options)}: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("  invalid choice, try again")


def run(cmd, **kwargs):
    """Run a subprocess, always returning (ok, stdout+stderr) instead of raising."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=kwargs.pop("timeout", 60), **kwargs
        )
        out = (result.stdout or "") + (result.stderr or "")
        return result.returncode == 0, out
    except FileNotFoundError:
        return False, f"'{cmd[0]}' is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return False, f"command timed out: {' '.join(cmd)}"


def run_interactive(cmd, **kwargs):
    """
    Run a subprocess with the technician's own terminal passed straight
    through (stdin/stdout/stderr inherited, nothing captured) -- for
    external tools that are themselves interactive wizards, not one-shot
    commands. rnodeconf's autoinstall flow is exactly this: it asks its
    own series of questions about the hardware. Using the plain run()
    helper here would capture and hide those prompts while still leaving
    the process waiting on stdin -- the technician would see nothing and
    the run would appear to hang. Returns True/False; doesn't return
    captured output, since none is captured.
    """
    try:
        result = subprocess.run(cmd, timeout=kwargs.pop("timeout", None))
        return result.returncode == 0
    except FileNotFoundError:
        print(f"'{cmd[0]}' is not installed or not on PATH")
        return False
    except subprocess.TimeoutExpired:
        print(f"command timed out: {' '.join(cmd)}")
        return False


def which_or_missing(tool):
    path = shutil.which(tool)
    return path if path else None


# ---------------------------------------------------------------------------
# Path fail-safes — every local file/folder this tool depends on (the Stump
# app source folder, a MicroPython firmware image, etc.) goes through this
# instead of a bare os.path check. A wrong path becomes a friendly prompt
# with auto-detected suggestions, not a failed run — and once corrected,
# it's remembered per-machine so nobody has to fix it twice.
# ---------------------------------------------------------------------------

def _load_local_paths():
    try:
        with open(LOCAL_PATHS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _forget_path(key):
    """Drops a remembered location. Called when a saved path stops
    qualifying, so a stale entry can't keep winning over the folder the
    technician is actually pointing at."""
    paths = _load_local_paths()
    if key not in paths:
        return
    del paths[key]
    try:
        LOCAL_PATHS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCAL_PATHS_FILE, "w") as f:
            json.dump(paths, f, indent=2)
    except Exception as e:
        print(f"  (couldn't clear the saved location: {e})")


def _remember_path(key, path):
    paths = _load_local_paths()
    paths[key] = str(path)
    try:
        LOCAL_PATHS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCAL_PATHS_FILE, "w") as f:
            json.dump(paths, f, indent=2)
    except Exception as e:
        print(f"  (couldn't save this location for next time: {e})")


def _remembered_path(key):
    val = _load_local_paths().get(key)
    return Path(val) if val else None


def _bounded_search(root, name, max_depth=2, cap=500):
    """Look for a directory or file literally named `name` within `max_depth`
    levels of `root`. Bounded so a technician's whole home folder doesn't
    get walked by accident."""
    root = Path(root)
    if not root.is_dir():
        return []
    matches = []
    scanned = 0
    root_depth = len(root.parts)
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            scanned += 1
            if scanned > cap:
                break
            depth = len(Path(dirpath).parts) - root_depth
            # Check the current directory before deciding whether to stop
            # descending -- otherwise a match sitting exactly at max_depth
            # gets excluded instead of found.
            if Path(dirpath).name == name:
                matches.append(Path(dirpath))
            if name in filenames:
                matches.append(Path(dirpath) / name)
            if depth >= max_depth:
                dirnames[:] = []
    except Exception:
        pass
    return matches


def _dedupe_paths(paths):
    seen, unique = set(), []
    for p in paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _find_cam_firmware_candidates(roots, max_depth=2, cap=500):
    """
    Looks for the *correct* MicroPython build for this board specifically
    -- filenames vary by version/date (e.g.
    ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin), so this can't be an
    exact-name search like the Stump app folder gets. Matches on the
    marker substrings that identify the Octal-SPIRAM variant, so a
    same-folder download of the *wrong* variant (plain GENERIC_S3, no
    OCT) won't get suggested as if it were fine.
    """
    matches = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        scanned = 0
        root_depth = len(root.parts)
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                scanned += 1
                if scanned > cap:
                    break
                depth = len(Path(dirpath).parts) - root_depth
                for fn in filenames:
                    lower = fn.lower()
                    if lower.endswith(".bin") and all(m in lower for m in CAM_FIRMWARE_NAME_MARKERS):
                        matches.append(Path(dirpath) / fn)
                if depth >= max_depth:
                    dirnames[:] = []
        except Exception:
            pass
    return matches


def _download_cam_firmware(url=None):
    """
    Downloads the correct Octal-SPIRAM MicroPython build for the Freenove
    ESP32-S3-CAM directly from micropython.org (stdlib urllib only -- no
    extra dependency), caching it under ~/.provisioner/firmware/ so a
    second run on this machine reuses the same file instead of
    re-downloading. Returns the local Path on success, None on failure --
    never raises, so a network hiccup falls back to manual entry instead
    of crashing the whole run.
    """
    import urllib.request
    import urllib.error

    url = url or CAM_FIRMWARE_LATEST_URL
    FIRMWARE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = FIRMWARE_CACHE_DIR / url.rsplit("/", 1)[-1]

    if dest.is_file() and dest.stat().st_size > 0:
        print(f"  Already downloaded: {dest}")
        return dest

    print(f"  Downloading {url}")
    tmp_dest = dest.with_suffix(dest.suffix + ".part")

    def _progress(block_num, block_size, total_size):
        done = block_num * block_size
        if total_size > 0:
            pct = min(100, done * 100 // total_size)
            print(f"\r  {pct}%  ({done // 1024}KB / {total_size // 1024}KB)", end="", flush=True)
        else:
            print(f"\r  {done // 1024}KB downloaded", end="", flush=True)

    try:
        urllib.request.urlretrieve(url, tmp_dest, reporthook=_progress)
        print()
        if not tmp_dest.is_file() or tmp_dest.stat().st_size == 0:
            raise RuntimeError("downloaded file is empty")
        tmp_dest.rename(dest)
        print(f"  Saved to {dest}")
        return dest
    except (urllib.error.URLError, OSError, RuntimeError) as e:
        print(f"\n  Download failed: {e}")
        try:
            if tmp_dest.exists():
                tmp_dest.unlink()
        except Exception:
            pass
        return None


def _find_unextracted_zips(roots, max_depth=2):
    """Looks for firmware archives that were downloaded but never
    extracted. Bounded to a shallow scan of the same roots already
    searched for folders, so it costs nothing extra in the common case
    where a valid folder is found immediately."""
    out = []
    names = ("stump", "final_firmware", "firmware", "beta")
    for root in roots or []:
        try:
            root = Path(root)
            if not root.is_dir():
                continue
            for depth_glob in ("*.zip", "*/*.zip"):
                for z in root.glob(depth_glob):
                    if any(n in z.name.lower() for n in names):
                        out.append(z)
        except Exception:
            continue
    return _dedupe_paths(out)


def resolve_path(default_path, description, remember_key=None, is_dir=True,
                  search_roots=None, search_name=None, validate_fn=None, hint_finder=None,
                  download_fn=None, download_label=None):
    """
    Ensures a required local path exists (and optionally passes validate_fn)
    before the caller uses it. If it doesn't, this prompts the technician
    with auto-detected nearby candidates and a chance to type a corrected
    path, instead of the whole step just failing.

    Check order: a previously-remembered correction (if remember_key is
    given) > default_path. If neither is valid, offers candidates -- from
    hint_finder(search_roots) if given (for pattern-based discovery, e.g.
    firmware filenames that vary by version), else an exact-name bounded
    search for search_name under search_roots -- then, if nothing local was
    found at all and download_fn is given, proactively offers to fetch it
    automatically (default yes -- one Enter press). Manual path entry is
    always available too, in a loop until something valid is given or the
    technician chooses to abort (blank input).
    """
    def is_valid(p):
        if p is None:
            return False
        p = Path(p)
        exists = p.is_dir() if is_dir else p.is_file()
        if not exists:
            return False
        return validate_fn(p) if validate_fn else True

    def try_download():
        downloaded = download_fn()
        if downloaded and is_valid(downloaded):
            if remember_key:
                _remember_path(remember_key, downloaded)
                print("  Remembered — future runs on this machine will use this location automatically.")
            return downloaded
        print("  Download didn't produce a usable file.")
        return None

    if remember_key:
        remembered = _remembered_path(remember_key)
        if is_valid(remembered):
            return remembered
        if remembered:
            # A remembered path that no longer qualifies must NOT be used.
            # This is the bug that corrupted installs: a path saved during
            # an earlier beta stayed valid-looking forever (main.py was
            # still there), so it silently beat the folder actually being
            # asked for, and old files went onto a freshly flashed board.
            print(f"\n  Ignoring remembered location -- it no longer qualifies:")
            print(f"    {remembered}")
            print(f"    ({_describe_folder(remembered)})")
            _forget_path(remember_key)
            print("  Forgotten. You'll be asked for the correct one below.")
    if is_valid(default_path):
        return Path(default_path)
    candidate = Path(default_path) if default_path else None

    while True:
        if candidate is not None:
            print(f"\n  Could not find a valid {description} at:")
            print(f"    {candidate}")
        else:
            print(f"\n  No {description} configured yet.")

        hints = []
        if hint_finder and search_roots:
            hints = [h for h in hint_finder(search_roots) if is_valid(h)]
            hints = _dedupe_paths(hints)[:6]
        elif search_roots and search_name:
            for root in search_roots:
                for found in _bounded_search(root, search_name):
                    if is_valid(found):
                        hints.append(found)
            hints = _dedupe_paths(hints)[:6]

        if hints:
            print("  Found possible matches nearby:")
            for i, h in enumerate(hints, 1):
                # Say what each one IS, not just where it is. A list of
                # near-identical sibling paths (B2/B3/B4/B5...) gives no
                # basis for choosing, and picking the wrong one puts an
                # older build's files onto a freshly flashed board.
                note = ""
                if is_dir:
                    desc = _describe_folder(h)
                    if desc.startswith("OK"):
                        note = f"   <-- matches this Provisioner {desc[3:]}"
                    else:
                        note = f"   ({desc})"
                print(f"    {i}) {h}{note}")
        else:
            # No usable folder anywhere -- but is there an un-extracted
            # zip sitting right there? That's the most likely state
            # immediately after downloading a new build, and it needs a
            # completely different fix (unzip it) from the one a generic
            # "not found" implies (go hunting for a path).
            zips = _find_unextracted_zips(search_roots) if search_roots else []
            if zips:
                print("  No usable folder found, but these archives look unextracted:")
                for z in zips[:4]:
                    print(f"    {z}")
                print("  Unzip one of those first, then point this at the folder inside it.")
            elif download_fn:
                # Nothing found locally at all -- offer the automatic fetch
                # first, since typing a path is rarely what's actually wanted
                # when the real answer is "I don't have it yet."
                label = f" ({download_label})" if download_label else ""
                if ask_yes_no(f"  Nothing found locally. Download it automatically now?{label}", True):
                    result = try_download()
                    if result:
                        return result
                    # falls through to manual entry below on failure

        extra = []
        if hints:
            extra.append("a number above")
        if download_fn:
            extra.append("'d' to download automatically")
        suffix = (", or " + ", or ".join(extra)) if extra else ""
        raw = ask(f"  Enter the correct path{suffix} (blank to skip)")
        if not raw:
            return None
        if download_fn and raw.strip().lower() in ("d", "download"):
            result = try_download()
            if result:
                return result
            candidate = None
            continue
        if hints and raw.isdigit() and 1 <= int(raw) <= len(hints):
            chosen = hints[int(raw) - 1]
        else:
            chosen = Path(raw).expanduser()

        if is_valid(chosen):
            if remember_key:
                # Ask, don't assume. Silently persisting a location is
                # exactly how a path saved during an older beta kept
                # winning over the folder actually being pointed at.
                # "Just this once" is the safer default while several
                # build folders exist side by side.
                if ask_yes_no("  Use this location for future runs too?", False):
                    _remember_path(remember_key, chosen)
                    print("  Saved. Re-run and pick a different path to change it.")
                else:
                    print("  Using it for this run only.")
            return chosen
        else:
            # Say WHY. "doesn't look valid" leaves the technician
            # guessing between wrong build, incomplete copy, and a zip
            # that was never extracted -- three different fixes.
            reason = _describe_folder(chosen) if is_dir else "not a valid file"
            print(f"  '{chosen}' won't work: {reason}")
            candidate = chosen


# ---------------------------------------------------------------------------
# Tool check
# ---------------------------------------------------------------------------

def check_tools():
    banner("TOOLCHAIN CHECK")
    all_ok = True
    for tool in REQUIRED_TOOLS:
        path = which_or_missing(tool)
        status = f"FOUND ({path})" if path else "MISSING"
        print(f"  {tool:<14} {status}")
        if not path:
            all_ok = False
    if not all_ok:
        print(
            "\nMissing tools can usually be installed with:\n"
            "  pip3 install esptool mpremote rns --break-system-packages\n"
            "(rnodeconf ships inside the 'rns' package on PyPI)"
        )
    return all_ok


# ---------------------------------------------------------------------------
# Board discovery
# ---------------------------------------------------------------------------

def list_serial_ports():
    """List serial ports using pyserial if available, else fall back to /dev glob."""
    try:
        from serial.tools import list_ports
        ports = []
        for p in list_ports.comports():
            vid = f"{p.vid:04x}" if p.vid else None
            pid = f"{p.pid:04x}" if p.pid else None
            guess = _guess_board(vid, pid, p.device, p.description)
            ports.append({"device": p.device, "description": p.description, "guess": guess})
        return ports
    except ImportError:
        # pyserial not installed — fall back to a raw filesystem scan (Linux/Mac only)
        candidates = []
        for pattern_dir in ("/dev",):
            if os.path.isdir(pattern_dir):
                for name in os.listdir(pattern_dir):
                    if name.startswith("ttyUSB") or name.startswith("ttyACM") or name.startswith("cu.usb"):
                        candidates.append({"device": f"/dev/{name}", "description": "(pyserial not installed — no VID/PID info)", "guess": "unknown board"})
        return candidates


def scan_boards():
    banner("SCANNING FOR CONNECTED BOARDS")
    ports = list_serial_ports()
    if not ports:
        print("  No serial devices found. Check the cable (see handover notes: a")
        print("  power-only USB-C cable will show a lit LED but zero enumeration).")
        return []
    for p in ports:
        print(f"  {p['device']:<18} guess: {p['guess']:<28} {p['description']}")
    return ports


def choose_board():
    ports = scan_boards()
    if not ports:
        manual = ask("No boards auto-detected. Enter a serial port path manually (blank to abort)")
        if not manual:
            return None, None
        ports = [{"device": manual, "description": "manual entry", "guess": "unknown board"}]

    device_list = [p["device"] for p in ports]
    chosen_device = ask_choice("Which port is the target board on?", device_list)
    chosen = next(p for p in ports if p["device"] == chosen_device)

    # Always ask outright which board this is, rather than offering the
    # guess as a y/n with "y" pre-filled. USB identification here is a
    # heuristic on generic parts (see KNOWN_BOARDS), flashing the wrong
    # firmware is destructive, and "Detected as X. Correct? [y]" is
    # precisely the prompt people accept without reading. The guess is
    # still shown, and pre-selects the default -- it just no longer gets
    # to decide by itself.
    heltec_label = "Heltec V3 (Control Plane — RNS/LoRa mesh, bridged to a CAM)"
    heltec_transport_label = "Heltec V3 (Standalone Transport — microReticulum firmware, no CAM)"
    cam_label = "ESP32-S3-CAM (Data Plane — local vault + web)"
    guess = chosen["guess"]
    if guess and guess != "unknown board":
        print(f"\n  USB identification suggests: {guess}")
        print("  (a guess from generic USB ids -- confirm below)")
    else:
        print(f"\n  Couldn't identify this board from USB alone ({chosen['description']}).")

    # An exact-label lookup, not a substring match against "Heltec" --
    # that approach broke the moment a second Heltec-labeled choice
    # existed, since both labels legitimately contain the word. Matching
    # the precise, unique label a person actually picked is the fix,
    # not a more elaborate substring rule.
    board_choice = ask_choice("What kind of board is this?",
                               [heltec_label, heltec_transport_label, cam_label])
    board_type_map = {
        heltec_label: "heltec",
        heltec_transport_label: "heltec_transport",
        cam_label: "cam",
    }
    return chosen_device, board_type_map[board_choice]


# ---------------------------------------------------------------------------
# Flashing
# ---------------------------------------------------------------------------

def flash_heltec_rnode(port):
    banner(f"FLASHING RNODE FIRMWARE — {port}")
    print("This runs rnodeconf's autoinstall flow, which detects the exact")
    print("Heltec V3 variant and writes the matching RNode firmware image.")
    print("rnodeconf asks its own questions below -- answer them directly.\n")
    # port is a positional argument to rnodeconf, not a --port flag (its own
    # usage text confirmed this: "--port" is rejected as unrecognized).
    # run_interactive (not run) because autoinstall is an interactive wizard
    # that needs to show its prompts and read real answers, not have its
    # output silently captured while stdin sits unanswered.
    ok = run_interactive(["rnodeconf", "--autoinstall", port], timeout=180)
    print("\nOK — RNode firmware flashed." if ok else "\nFAILED or cancelled — see rnodeconf's own output above.")
    return ok


STANDALONE_RETICULUM_FW_URL = "https://github.com/attermann/microReticulum_Firmware/releases/"


def flash_heltec_standalone_reticulum(port, radio):
    """
    Flashes the standalone Heltec V3 transport role using
    microReticulum_Firmware -- a real, actively maintained fork of
    RNode_Firmware with the microReticulum C++ Reticulum stack built in,
    confirmed to actually work on this hardware in the field. This
    REPLACES an earlier, custom MicroPython-based approach for this
    role (identity + urns + a hand-written LoRa interface) that hit a
    real, reproducible MemoryError on actual (PSRAM-less) hardware,
    traced to native crypto module loading, and never got past it --
    while this pre-built firmware, designed from the start around
    exactly this board's memory constraints, worked immediately.

    Three steps, run in this order for a reason:

    1. --clear-cache: rnodeconf caches firmware downloads locally: if a
       previous attempt (this role's old flow, or an earlier partial
       run) already cached a build, autoinstall would silently reuse
       stale bytes instead of fetching the current release.

    2. --autoinstall --fw-url <url>: interactive (asks rnodeconf's own
       hardware questions), so this uses run_interactive like the
       existing flash_heltec_rnode() above, not the output-capturing
       run() helper.

    3. -T --freq/--bw/--txp/--sf/--cr set TOGETHER in ONE command, not
       as separate steps -- confirmed this matters: setting the
       operating mode and radio parameters separately risks rnodeconf
       dropping the write cycle or reverting to Normal (host-
       controlled) mode instead of TNC. This step deliberately treats
       what LOOKS LIKE a failure as success: switching to -T makes the
       board write to EEPROM and immediately reboot standalone, which
       drops the serial connection and surfaces as a timeout/non-zero
       exit from rnodeconf's own perspective. That's confirmed expected
       behavior for this exact transition, not a fault -- so this
       treats it as fine even when the run() call itself looks failed,
       and includes a real wait (mirroring how esptool's own hard
       reset needed a similar wait elsewhere in this file) before
       trying to talk to the board again.

    Ends with rnodeconf --info to actually confirm Device mode reads
    TNC -- not just assumed from the lock command appearing to run,
    since that command's own success/failure signal is unreliable here
    by design (see above).
    """
    banner(f"FLASHING STANDALONE RETICULUM FIRMWARE — {port}")
    print(f"Source: {STANDALONE_RETICULUM_FW_URL}")
    print("(microReticulum_Firmware -- a self-contained Reticulum transport")
    print(" node, not stock RNode firmware. Confirmed working on this exact")
    print(" role's hardware in the field.)\n")

    print("Clearing rnodeconf's firmware cache...")
    run(["rnodeconf", "--clear-cache"], timeout=30)

    print("\nRunning autoinstall -- rnodeconf will ask its own questions below.")
    ok = run_interactive(["rnodeconf", "--autoinstall", "--fw-url", STANDALONE_RETICULUM_FW_URL, port], timeout=240)
    if not ok:
        print("\nFAILED or cancelled during autoinstall -- see rnodeconf's own output above.")
        return False
    print("\nOK — firmware flashed.")

    print("\nLocking TNC mode and radio parameters together (one command --")
    print("setting these separately risks rnodeconf reverting to host-controlled")
    print("mode instead)...")
    lock_cmd = [
        "rnodeconf", port, "-T",
        "--freq", str(int(radio["frequency"])),
        "--bw", str(int(radio["bandwidth"])),
        "--txp", str(int(radio["txpower"])),
        "--sf", str(int(radio["spreadingfactor"])),
        "--cr", str(int(radio["codingrate"])),
    ]
    ok, out = run(lock_cmd, timeout=30)
    # A timeout/non-zero exit HERE is the expected shape of success, not
    # a fault -- see this function's own docstring. Printed either way
    # so a technician sees what actually happened, not a silent guess.
    if ok:
        print("  (command completed without the usual reboot-triggered timeout --")
        print("   also fine, just less common for this particular transition)")
    else:
        print("  Serial read timeout / non-zero exit -- EXPECTED here. The board")
        print("  writes to EEPROM and reboots standalone the moment -T is set,")
        print("  which drops the connection mid-command. Not a failure on its own.")

    print("\nWaiting for the board to finish rebooting into standalone mode...")
    time.sleep(6)

    print("Verifying...")
    ok, out = run(["rnodeconf", port, "--info"], timeout=30)
    if not ok:
        print(f"  Could not reach the board for verification: {out.strip()}")
        print(f"  Retry manually once it's settled: rnodeconf {port} --info")
        return False
    print(out.strip())
    if "TNC" in out:
        print("\nOK — Device mode confirmed as TNC (standalone). Board is a real transport node now.")
        return True
    else:
        print("\nDevice mode does NOT read TNC -- still Normal (host-controlled) or unclear.")
        print(f"  Retry the lock step manually: {' '.join(lock_cmd)}")
        return False


def _bootloader_recovery(port):
    """
    Walks the technician through manual bootloader entry after a failed
    connect, then RE-SCANS for the port.

    Re-scanning is the important part. On an ESP32-S3 with native USB
    the chip itself provides the USB device, so entering the ROM
    bootloader tears down the old USB device and enumerates a new one --
    under a different name (/dev/cu.usbmodem<serial> while running
    firmware, /dev/cu.usbmodem<location> in ROM download mode, and the
    number changes again between sessions). Retrying the original port
    is guaranteed to fail: that device node no longer exists. This is
    also why the failure reads "No serial data received" rather than
    "port not found" -- something still answers, it just isn't the chip
    in a state that can be flashed.

    Returns a port to retry with, or None to give up.
    """
    print()
    print("  Two common causes, cheapest first:")
    print()
    print("  A) WRONG USB PORT. The Freenove ESP32-S3-CAM has TWO USB-C")
    print("     connectors and only one is wired to the chip's USB data lines.")
    print("     The other supplies power only -- the LED lights, the board")
    print("     looks alive, and nothing enumerates for flashing. If a port")
    print("     appeared in the scan but won't connect, try the other socket")
    print("     before anything else.")
    print()
    print("  B) NOT IN BOOTLOADER MODE. Put the board there by hand:")
    print()
    print("       1. Hold down the BOOT button (sometimes labelled IO0)")
    print("       2. While still holding BOOT, briefly press and release RESET (EN)")
    print("       3. Release BOOT")
    print()
    print("  Either way the board may enumerate as a DIFFERENT serial port than")
    print("  the one above -- that's expected, not a fault -- so this re-scans.")
    print()
    if not ask_yes_no("  Ready to re-scan for the board?", True):
        return None

    ports = list_serial_ports()
    if not ports:
        print("  No serial ports found at all. Check the cable is a DATA cable --")
        print("  a power-only USB-C cable lights the board's LED but enumerates")
        print("  nothing, which looks identical to a dead board.")
        return None

    print()
    for p in ports:
        marker = "  (same as before)" if p["device"] == port else "  <- new since the failure"
        print(f"    {p['device']:<32}{marker}")
    print()
    return ask_choice("Which port is the board on now?", [p["device"] for p in ports])


def flash_cam_micropython(port, firmware_path=None):
    banner(f"FLASHING MICROPYTHON — {port}")

    resolved = resolve_path(
        firmware_path,
        "MicroPython .bin firmware for ESP32-S3-CAM (Octal-SPIRAM build)",
        remember_key="cam_firmware_bin",
        is_dir=False,
        search_roots=[Path.cwd(), Path.home() / "Downloads", Path.home() / "Desktop"],
        hint_finder=_find_cam_firmware_candidates,
        download_fn=_download_cam_firmware,
        download_label="fetches the Octal-SPIRAM build from micropython.org",
    )
    if resolved is None:
        print("FAILED — no valid firmware file given. This board needs the")
        print("Octal-SPIRAM build specifically (Freenove's ESP32-S3-CAM uses Octal")
        print("SPI PSRAM) -- the plain/standard build will boot but likely crash.")
        print(f"Direct download: {CAM_FIRMWARE_LATEST_URL}")
        print("(or browse micropython.org/download/ESP32_GENERIC_S3/ and pick the")
        print(" file under \"Firmware (Support for Octal-SPIRAM)\", not the plain one)")
        return (False, None)

    fname_lower = resolved.name.lower()
    if not all(m in fname_lower for m in CAM_FIRMWARE_NAME_MARKERS):
        print(f"  Note: '{resolved.name}' doesn't look like the Octal-SPIRAM build")
        print("  (no 'spiram_oct' in the filename). This board needs that variant --")
        print("  the plain build tends to boot into a broken/crashing REPL instead")
        print("  of failing cleanly. Continuing anyway since you pointed at it")
        print(f"  directly, but if the board misbehaves after flashing, get:")
        print(f"  {CAM_FIRMWARE_LATEST_URL}")

    firmware_path = str(resolved)

    print("Erasing flash...")
    # esptool v5 renamed every underscore-style command/option to hyphens
    # (erase_flash -> erase-flash, write_flash -> write-flash below, and
    # the esptool.py entry point itself deprecated in favor of plain
    # esptool) -- confirmed directly against a real flash run and against
    # esptool's own v5 migration guide, which states this covers ALL
    # commands and options, not just this one. The old spellings still
    # work today (that's why a real run using them succeeded, just with
    # deprecation warnings on every line), but esptool's own warning says
    # the .py suffix specifically will be removed entirely in a future
    # major release -- worth fixing now rather than after that.
    ok, out = run(["esptool", "--port", port, "erase-flash"], timeout=120)
    print(out.strip())
    if not ok:
        # "No serial data received" / "Failed to connect" almost always
        # means the chip isn't in its download mode, not that anything is
        # broken. Offer the manual entry sequence and a re-scan rather
        # than just reporting failure and stopping.
        if "Failed to connect" in out or "No serial data received" in out or "Timed out" in out:
            new_port = _bootloader_recovery(port)
            if new_port:
                port = new_port          # everything below must use the NEW port
                print(f"\nRetrying erase on {port} ...")
                ok, out = run(["esptool", "--port", port, "erase-flash"], timeout=120)
                print(out.strip())
        if not ok:
            print("FAILED at erase step.")
            print("If it still won't connect: try a different USB cable (data, not")
            print("power-only), a different port, and hold BOOT for the whole")
            print("connect attempt rather than releasing it immediately.")
            return (False, None)

    print("Writing MicroPython image...")
    ok, out = run(
        ["esptool", "--port", port, "--baud", "460800", "write-flash", "-z", "0x0", firmware_path],
        timeout=180,
    )
    print(out.strip())
    print("OK — MicroPython flashed." if ok else "FAILED — see output above.")
    # Return the port that actually worked, not just a bool. Bootloader
    # recovery can change it, and everything after this (upload, config
    # push, diagnostics) has to target the port the board really is on.
    return (ok, port if ok else None)



HASH_CHECK_EXEMPT = {"config.py"}

# Files that live in the firmware folder but must NEVER be part of the
# manifest. The regeneration snippet is a blind rglob, so anything
# sitting in the folder gets absorbed -- including files the node
# WRITES AT RUNTIME. boot_fail_count is main.py's failed-boot counter;
# it appeared in the manifest because a test run left one behind, which
# then told the uploader to expect a runtime counter as canonical
# firmware. Editor backups and OS turds land the same way.
MANIFEST_EXCLUDE = {"boot_fail_count"}
MANIFEST_EXCLUDE_SUFFIXES = (".pyc", ".bak", ".orig", ".swp", ".DS_Store")


def manifest_candidates(fw_dir):
    """Every file that legitimately belongs in the manifest.

    Use this instead of a bare rglob when regenerating hashes, so
    runtime state and stray local files can't be blessed as firmware."""
    out = []
    for f in sorted(Path(fw_dir).rglob("*")):
        if not f.is_file():
            continue
        rel = str(f.relative_to(fw_dir))
        if rel in MANIFEST_EXCLUDE:
            continue
        if any(rel.endswith(sfx) for sfx in MANIFEST_EXCLUDE_SUFFIXES):
            continue
        out.append(rel)
    return out


def _check_local_files_current(resolved):
    """
    Compares each local file's content hash against EXPECTED_FILE_HASHES
    -- this is what actually catches a stale local copy, and it does so
    BEFORE wasting a flash+upload cycle, not after. The post-upload
    local-vs-device comparison can only prove a transfer succeeded; it
    can't tell a stale local file from a current one, since a stale file
    uploaded correctly still "matches the device" perfectly. This check
    doesn't trust the local folder at all -- it trusts a hash I control.

    Returns a list of filenames that are stale or don't match.
    """
    import hashlib
    stale = []
    for fname, expected_hash in EXPECTED_FILE_HASHES.items():
        if fname in HASH_CHECK_EXEMPT:
            continue
        f = resolved / fname
        if not f.is_file():
            continue  # already reported separately as "missing"
        actual_hash = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
        if actual_hash != expected_hash:
            stale.append(fname)
    return stale


def upload_stump_app(port, app_dir=None):
    """Push the Reticulum-rooted firmware tree via mpremote.

    Uploads only the files in STUMP_APP_FILES, by manifest -- not a blind
    walk of the resolved folder. Handles nested subdirectories (urns/,
    urns/crypto/, urns/crypto/pure25519/, urns/interfaces/, lib/,
    peripherals/), creating each destination directory on the device
    before pushing files into it.
    """
    banner(f"UPLOADING FIRMWARE — {port}")

    resolved = resolve_path(
        app_dir or STUMP_APP_DIR,
        _STUMP_FILES_DESC,
        remember_key="stump_app_dir",
        is_dir=True,
        search_roots=[Path.cwd(), Path.cwd().parent, Path(__file__).resolve().parent,
                       Path.home() / "Desktop", Path.home() / "Downloads"],
        search_name="final_firmware",
        validate_fn=_is_current_firmware_folder,
    )
    if resolved is None:
        print("FAILED — no valid firmware folder given. Skipping upload;")
        print("  MicroPython itself is still flashed, so you can retry the upload later")
        print("  with the board already prepared.")
        return False

    present = [f for f in STUMP_APP_FILES if (resolved / f).is_file()]
    missing = [f for f in STUMP_APP_FILES if f not in present]

    if missing:
        print(f"  Note: {len(missing)} expected file(s) not found here, skipping: {', '.join(missing)}")

    # Content-hash check: catches ANY stale file by comparing against
    # known-good hashes, not the local folder's own say-so, and does it
    # before wasting a flash+upload cycle.
    stale = _check_local_files_current(resolved)
    if stale:
        found_version = _folder_version(resolved)
        print()
        if found_version and found_version != EXPECTED_STUMP_VERSION:
            # Name the build. "3 files don't match" reads like damaged
            # files; "this folder is Beta 5" says plainly that it's the
            # wrong folder, which is what it almost always is.
            print(f"  ⚠ WRONG BUILD. That folder is {found_version}; this Provisioner")
            print(f"    ships {EXPECTED_STUMP_VERSION}. Uploading it would put older files")
            print("    onto the board and leave a mixed, broken install.")
        else:
            print(f"  ⚠ {len(stale)} file(s) don't match the current known-good version:")
            for fname in stale:
                print(f"      {fname}")
        print(f"    Folder: {resolved}")
        print("    These will still upload if you continue, but they're likely outdated.")
        if not ask_yes_no("  Continue uploading anyway?", False):
            print("  Aborted -- point --upload-app at the %s folder, or re-run the" % EXPECTED_STUMP_VERSION)
            print("  wizard, which will now ask again rather than reusing a saved path.")
            return False

    # Create every destination directory that will be needed, in a stable
    # shallow-to-deep order, before pushing any files -- mpremote's fs cp
    # can't create missing parent directories on its own. A directory
    # that already exists just fails harmlessly here; that's expected on
    # a re-run, not a real error.
    dest_dirs = sorted({str(Path(f).parent) for f in present if Path(f).parent != Path(".")},
                        key=lambda d: d.count("/"))
    for d in dest_dirs:
        run(["mpremote", "connect", port, "fs", "mkdir", f":{d}"], timeout=15)

    all_ok = True
    for fname in present:
        f = resolved / fname
        print(f"  uploading {fname} ...")
        ok, out = run(["mpremote", "connect", port, "fs", "cp", str(f), f":{fname}"], timeout=60)
        if not ok:
            print(f"    FAILED: {out.strip()}")
            all_ok = False
        else:
            print("    ok")

    # Verify by reading back each file's actual size on the device
    # (recursively, since the tree now has real subdirectories) and
    # comparing to the local source.
    print("\n  Verifying upload...")
    device_sizes = _get_device_file_sizes(port)
    if device_sizes is None:
        print(f"  Could not verify (couldn't list device files) -- check manually with:")
        print(f"    mpremote connect {port} fs ls")
    else:
        problems = _diff_against_device(resolved, present, device_sizes)
        if problems:
            all_ok = False
            print("  PROBLEMS after upload:")
            for fname, reason in problems:
                print(f"    {fname}: {reason}")
            print("  Retrying these...")
            for fname, _ in problems:
                f = resolved / fname
                print(f"    retrying {fname} ...")
                run(["mpremote", "connect", port, "fs", "cp", str(f), f":{fname}"], timeout=60)
            device_sizes2 = _get_device_file_sizes(port) or {}
            still_bad = _diff_against_device(resolved, [f for f, _ in problems], device_sizes2)
            if still_bad:
                print(f"  STILL WRONG: {', '.join(f for f, _ in still_bad)}")
                print(f"  Try again with: python3 provisioner.py --upload-app {port}")
            else:
                print("  Retry succeeded — all files now confirmed present and matching.")
                all_ok = True
        else:
            print(f"  OK — all {len(present)} files confirmed present and matching local source.")
            install_tools_on_node(port)

    return all_ok


def _build_firmware_zip(dest_path, source_dir):
    """Builds Stump_Beta_A.zip fresh from source_dir (final_firmware/,
    the SAME directory this run of provisioner.py is already using for
    everything else) rather than requiring the technician to have kept
    their original downloaded zip around. Two things that matters
    follow from building it fresh instead of copying a stale file:
    it can never go stale relative to what's actually being
    provisioned, and it doesn't fail outright just because the
    original zip was deleted after extracting -- final_firmware/ is
    the one thing that has to exist anyway for provisioning to work at
    all.

    Uses zipfile directly rather than shelling out to a zip binary --
    this runs on whatever OS the technician's laptop happens to be,
    and a zip command isn't guaranteed to exist there (Windows without
    WSL or Git Bash, notably).

    Same top-level layout as the zip shipped for download (a
    final_firmware/ directory at the archive root), so extracting the
    copy pulled back off a node reproduces exactly the folder
    structure a fresh download would.
    """
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(source_dir.rglob("*")):
            if f.is_file():
                zf.write(f, arcname=str(Path("final_firmware") / f.relative_to(source_dir)))


def install_tools_on_node(port):
    """Copies this Provisioner onto the node's SD card.

    So the node carries the tool that configures it. A technician can
    then walk up with only a laptop, pull the Provisioner off the Stump
    over HTTP, and run it there -- no USB stick to forget, and no doubt
    about whether the copy on their desktop matches this build, because
    the node hands back the version it was provisioned with.

    Best-effort: a node with no SD card still provisions fine, it just
    can't hand the tool back. Never fail the run over this.
    """
    me = Path(__file__).resolve()
    here = me.parent

    # THE THING THAT BIT US: mpremote soft-resets into the raw REPL,
    # which deliberately does NOT run main.py. fserv.mount_sd() is
    # therefore never called, /sd does not exist, and every copy to
    # /sd/... fails while copies to flash succeed -- which is exactly
    # the asymmetry that showed up in the field.
    #
    # Each mpremote invocation is its own session, so a mount done in
    # one call is gone by the next. The mount and the copy have to be
    # chained into a SINGLE invocation to share a session.
    MOUNT = "import fserv\ntry:\n fserv.mount_sd()\nexcept Exception as e:\n print('mount failed:', e)\n"

    def sd_ready():
        ok, out = run(["mpremote", "connect", port, "exec",
                       "import fserv\nprint('SD:' + ('yes' if fserv.mount_sd() else 'no'))\n"],
                      timeout=25)
        return ok and "SD:yes" in out, out

    print("\n  Installing technician tools onto the node...")
    ready, detail = sd_ready()
    if not ready:
        # Say WHY, and say it once, instead of four identical failures
        # with no reason -- that log cost real debugging time.
        print("  No usable SD card on the node, so there is nowhere to put them.")
        print("  The node runs fine without this; it just can't hand tools back.")
        print("  Fix the card (python3 provisioner.py --wipe-sd %s) and re-run" % port)
        print("  --upload-app to install them.")
        if detail.strip():
            print("  (%s)" % detail.strip().splitlines()[-1][:90])
        return False

    for d in (":/sd/tools", ":/sd/fw"):
        run(["mpremote", "connect", port, "exec", MOUNT, "fs", "mkdir", d], timeout=25)

    installed, failed = [], []

    def push(src, dest, label, timeout=180):
        if not Path(src).is_file():
            return
        # exec + fs cp in ONE invocation so the mount is still live when
        # the copy runs.
        ok, out = run(["mpremote", "connect", port, "exec", MOUNT,
                       "fs", "cp", str(src), dest], timeout=timeout)
        if ok:
            installed.append(label)
        else:
            # "command timed out: <the whole mpremote invocation>" is
            # run()'s own message on a real subprocess timeout -- and
            # since MOUNT is itself a multi-line Python snippet embedded
            # in that command, splitlines()[-1] on it doesn't land on
            # anything resembling an error, it lands on whatever
            # fragment of the command happened to follow the last
            # newline. Confirmed directly: this is EXACTLY what produced
            # the garbled "fs cp /var/folders/.../tmpXXXX/St" a real
            # failed run reported -- the truncated tail of the command
            # itself, not a description of what went wrong. Detected and
            # given a real message instead of extracting a "last line"
            # that was never a line describing an error in the first
            # place.
            if out.startswith("command timed out"):
                reason = "timed out after %ds" % timeout
            elif out.strip():
                reason = out.strip().splitlines()[-1][:70]
            else:
                reason = "no output"
            failed.append((label, reason))

    if me.is_file():
        push(me, ":/sd/tools/provisioner.py", "provisioner.py")

    # Same reasoning as provisioner.py itself: the node hands back
    # exactly the reference doc it was provisioned with, rather than a
    # technician needing to remember to keep a separate copy on hand.
    # README.md sits right next to provisioner.py in every download,
    # same as final_firmware/ does -- best-effort like everything else
    # here, a missing README.md still lets the rest of this function
    # proceed normally.
    readme = here / "README.md"
    if readme.is_file():
        push(readme, ":/sd/tools/README.md", "README.md")

    # Same reasoning again: whoever ends up with a copy of this build
    # off the node gets the actual license it ships under, not just the
    # code. Named LICENSE (no extension) to match the standard,
    # tool-recognized convention (GitHub, package managers, and license
    # scanners all look for this exact name) rather than LICENSE.md or
    # LICENSE.txt.
    license_file = here / "LICENSE"
    if license_file.is_file():
        push(license_file, ":/sd/tools/LICENSE", "LICENSE")

    # The full firmware source, built fresh rather than copied from
    # wherever the technician's original download happened to land --
    # see _build_firmware_zip's own docstring for why that's the right
    # call here. Best-effort like everything else in this function: a
    # zip-building failure (a permissions issue on the temp dir, say)
    # is recorded and skipped, never lets an exception end the whole
    # provisioning run over a file this secondary.
    fw_dir = here / "final_firmware"
    if fw_dir.is_dir():
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                zip_path = Path(tmpdir) / "Stump_Beta_A.zip"
                _build_firmware_zip(zip_path, fw_dir)
                # A real run timed out pushing this at the same 180s
                # every other, much smaller tool push uses -- confirmed
                # directly, this is genuinely just a bigger file (well
                # over double esptool-bundle.js, the next largest thing
                # pushed here) taking a real SD card write longer than
                # that budget, not a hung command. Printed up front
                # since a silent multi-minute wait reads as a hang
                # otherwise -- the other pushes are fast enough that
                # nobody's watched the clock on them before now.
                size_kb = zip_path.stat().st_size // 1024
                print(f"  Copying Stump_Beta_A.zip ({size_kb} KB) onto the SD card --")
                print("  this one's bigger than the other tools, give it a minute...")
                push(zip_path, ":/sd/tools/Stump_Beta_A.zip", "Stump_Beta_A.zip", timeout=600)
        except Exception as e:
            failed.append(("Stump_Beta_A.zip", str(e)[:70]))

    payload = here / "final_firmware" / "tools_payload"
    if not payload.is_dir():
        payload = here / "tools_payload"
    if payload.is_dir():
        fl = payload / "flasher"
        push(fl / "esptool-bundle.js", ":/sd/tools/esptool-bundle.js", "esptool-bundle.js")
        push(fl / "esptool-js-LICENSE.txt", ":/sd/tools/esptool-js-LICENSE.txt", "licence")
        push(fl / "catalog.json", ":/sd/fw/catalog.json", "catalog.json")
        img_dir = payload / "images"
        if img_dir.is_dir():
            for img in sorted(img_dir.glob("*.bin")):
                push(img, ":/sd/fw/" + img.name, img.name)
    else:
        print("  (no tools_payload folder found next to the firmware --")
        print("   the browser flasher won't be available on this node)")

    if installed:
        print("  Installed: " + ", ".join(installed))
        print("  Tools at   http://<node>/files")
        # Only claimed if the flasher's own assets are actually among
        # what got installed -- this used to print unconditionally
        # whenever ANYTHING installed, including a run where
        # esptool-bundle.js never made it on at all (no tools_payload
        # folder found, printed and confirmed directly above this very
        # line). Telling someone the flasher is ready right next to a
        # warning that it isn't was a real, self-contradicting bug.
        if "esptool-bundle.js" in installed:
            print("  Flasher at http://<node>/flash")
    for label, why in failed:
        print("  Could not copy %s: %s" % (label, why))
    return bool(installed)


def _get_device_file_sizes(port):
    """Returns {relative_path: size_in_bytes} for every file on the
    device, walked recursively -- the tree now has real subdirectories
    (urns/crypto/pure25519/ etc.), so the old root-only os.listdir()
    isn't enough. Tested against the real interpreter before shipping:
    correctly finds a file 3 levels deep."""
    snippet = (
        "import os\n"
        "def walk(d, rel):\n"
        "    out = {}\n"
        "    for name in os.listdir(d):\n"
        "        full = d + '/' + name\n"
        "        r = (rel + '/' + name) if rel else name\n"
        "        st = os.stat(full)\n"
        "        if st[0] & 0x4000:\n"
        "            out.update(walk(full, r))\n"
        "        else:\n"
        "            out[r] = st[6]\n"
        "    return out\n"
        "print(walk('.', ''))\n"
    )
    ok, out = run(["mpremote", "connect", port, "exec", snippet], timeout=20)
    if not ok:
        return None
    try:
        import ast
        for line in reversed(out.strip().splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                return ast.literal_eval(line)
    except Exception:
        pass
    return None


def _diff_against_device(resolved, filenames, device_sizes):
    """Compares local file sizes against what's actually on the device.
    Returns a list of (filename, reason) for anything missing or
    size-mismatched (stale)."""
    problems = []
    for fname in filenames:
        local_size = (resolved / fname).stat().st_size
        device_size = device_sizes.get(fname)
        if device_size is None:
            problems.append((fname, "missing"))
        elif device_size != local_size:
            problems.append((fname, f"stale — device has {device_size} bytes, local source is {local_size}"))
    return problems


# ---------------------------------------------------------------------------
# Guided config wizard
# ---------------------------------------------------------------------------

def config_wizard(board_type):
    banner("GUIDED CONFIG WIZARD")
    # Three roles, three sensible defaults -- the old binary check here
    # (cam vs. everything-else-is-firefly) silently gave a standalone
    # transport relay the wrong default name ("unnamed-firefly"), since
    # it isn't one. Caught by a review, not by anyone hitting it in the
    # field, but worth fixing before someone did.
    default_names = {
        "cam": "unnamed-stump",
        "heltec_transport": "unnamed-repeater",
    }
    node_name = ask("Node name (shown to peers)", default_names.get(board_type, "unnamed-firefly"))

    profile = {"node_name": node_name, "board_type": board_type}

    if board_type == "heltec_transport":
        # Radio parameters MUST match the rest of the deployed mesh --
        # confirmed explicitly here rather than silently trusted,
        # exactly like the existing "heltec" role already does below.
        # A relay with mismatched radio parameters doesn't error, it
        # just never hears or is heard by anything -- there's no
        # message for that failure mode, only silence, so this is the
        # one place to actually catch it before it ships.
        print("\nRadio parameters (defaults match the deployed mesh):")
        radio = dict(DEFAULT_RADIO)
        radio["frequency"] = int(ask("Frequency (Hz)", radio["frequency"]))
        radio["bandwidth"] = int(ask("Bandwidth (Hz)", radio["bandwidth"]))
        radio["txpower"] = int(ask("TX power", radio["txpower"]))
        radio["spreadingfactor"] = int(ask("Spreading factor", radio["spreadingfactor"]))
        radio["codingrate"] = int(ask("Coding rate", radio["codingrate"]))
        profile["radio"] = radio
        return profile, None

    if board_type == "heltec":
        print("\nRadio parameters (defaults match the deployed mesh):")
        radio = dict(DEFAULT_RADIO)
        radio["frequency"] = int(ask("Frequency (Hz)", radio["frequency"]))
        radio["bandwidth"] = int(ask("Bandwidth (Hz)", radio["bandwidth"]))
        radio["txpower"] = int(ask("TX power", radio["txpower"]))
        radio["spreadingfactor"] = int(ask("Spreading factor", radio["spreadingfactor"]))
        radio["codingrate"] = int(ask("Coding rate", radio["codingrate"]))
        profile["radio"] = radio

        ifac = ask("Interface Access Code / IFAC passphrase (blank = none)", "")
        if ifac:
            profile["ifac"] = ifac

        mode = ask_choice(
            "Network boundary mode for this node",
            ["internal (air-gapped, default)", "boundary (WAN-bridged)"],
        )
        profile["boundary_mode"] = "internal" if mode.startswith("internal") else "boundary"

        # WiFi Station mode -- the step the whole bridge architecture
        # depends on. Without it the Heltec is USB-only and the CAM has
        # no way to reach it, so this belongs in the guided flow rather
        # than as a command the technician is told to remember.
        print("\nWiFi Remote (Station mode) — this is what lets the CAM reach this")
        print("radio over the network instead of a USB cable. Required for the bridge.")
        if ask_yes_no("Configure WiFi Station mode on this Heltec now?", True):
            # Made explicit after a real question about it: the CAM works
            # completely standalone already (its own hotspot comes up
            # regardless of any upstream WiFi -- see example_node.py's own
            # graceful-degradation comment), and rnodeconf's WiFi target
            # here is a genuinely plain SSID/password with no requirement
            # that it be an external router. The CAM's own hotspot is a
            # real, joinable network like any other, and it's open by
            # design (captive_portal.py's setup_ap() docstring: "open, no
            # password") -- so a Heltec CAN join it directly instead,
            # making the pair fully self-contained with no router or
            # internet at all. The one thing that has to be gotten right
            # by hand otherwise: the CAM's own AP always sits at
            # 192.168.4.1, a completely different range from this
            # prompt's old default (192.168.0.222, written assuming a
            # home router) -- asking outright here, instead of leaving
            # that mismatch for someone to discover on their own, is the
            # actual fix.
            pairing = ask_choice(
                "What will this Heltec connect to?",
                ["An existing WiFi network (a router)",
                 "A CAM's own hotspot directly (standalone pair, no router at all)"],
            )
            standalone_pair = pairing.startswith("A CAM's")
            if standalone_pair:
                print("\nThe CAM's own hotspot is open (no password) by design -- nothing")
                print("to enter for that. Its own address is always 192.168.4.1, so this")
                print("Heltec needs a DIFFERENT address in that same 192.168.4.x range.")
                ssid = ask("  The CAM's hotspot name (its Node name, or custom SSID)", "")
                psk = ""
                default_ip = "192.168.4.2"
            else:
                ssid = ask("  WiFi network for the Heltec to join", "")
                psk = ask("  WiFi password", "")
                default_ip = "192.168.0.222"
            ip = ask("  Static IP for the Heltec (must match the CAM's\n"
                      "    'Heltec Bridge' target_host)", default_ip)
            netmask = ask("  Netmask", "255.255.255.0")
            profile["heltec_wifi"] = {"ssid": ssid, "psk": psk, "ip": ip, "netmask": netmask}
            if standalone_pair:
                print(f"\nRemember this address ({ip}) -- enter it as the 'Heltec Bridge IP'")
                print("when you provision the CAM.")
        else:
            profile["heltec_wifi"] = None

    else:  # cam
        print("This build's config.py has the CAM join an EXISTING WiFi network")
        print("(for internet/LXMF reachability) -- it's not creating its own hotspot")
        print("with this name. The local walk-up AP is a separate, fixed 'Stump'")
        print("hotspot that comes up automatically, not something configured here.")
        wifi_ssid = ask("WiFi network to join", "")
        wifi_pass = ask("WiFi password", "")
        profile["wifi_ssid"] = wifi_ssid
        profile["wifi_pass"] = wifi_pass

        # The walk-up hotspot's own name. Option 1 is the default,
        # publicly hosted page -- a real address ("LaBuche-Stump.web.app")
        # someone can read off their phone's WiFi list and type into a
        # browser on their own data before ever joining. It's exactly
        # 21 characters and fits WiFi's 32-byte SSID limit with room to
        # spare -- but ONLY alone. Adding the AP-address suffix makes it
        # 33, one character over, and truncating a real web address by
        # even one character breaks it as something a browser can
        # resolve ("...web.ap" doesn't exist). So the IP question is
        # only ever reached in the custom-name branch, where clipping a
        # free-text name is a cosmetic compromise, not a broken link --
        # confirmed directly, not assumed: the truncated domain-plus-IP
        # combination was tested and produces exactly that broken string.
        # Kept as a literal here rather than parsed out of
        # captive_portal.py -- this is a display string only, and
        # introducing cross-file parsing for one constant is more
        # complexity than the risk warrants. If DEFAULT_SSID ever
        # changes there, this needs a matching update; noted so it
        # isn't a silent trap.
        cp_default_ssid = "LaBuche-Stump.web.app"
        print("\nThe walk-up hotspot can broadcast as the public page")
        print("(\"%s\"), so anyone can look up what this" % cp_default_ssid)
        print("network is before ever joining -- or a custom name instead.")
        use_default_ssid = ask_yes_no(
            "Use the default hosted-page name?", True)
        if use_default_ssid:
            profile["ssid_name"] = None
            # Forced off, not asked: the domain-plus-IP combination
            # does not fit in 32 bytes without breaking the address --
            # see above. Structural prevention, not a warning after
            # the fact.
            profile["ssid_include_ip"] = False
        else:
            profile["ssid_include_ip"] = ask_yes_no(
                "Include the AP's IP address in the hotspot name?", True)
            profile["ssid_name"] = ask("Custom hotspot name", node_name)

        # Same explicit pairing question as the Heltec's own wizard branch
        # above, and for the identical reason: this CAM's own hotspot is a
        # real, joinable network the Heltec can connect to directly
        # instead of a router, but its address (192.168.4.1) is a
        # different range from this prompt's old default -- asking
        # outright, rather than letting the two boards' provisioning runs
        # silently assume different networks, is the actual fix.
        print("\nIs the Heltec reaching this CAM through an existing WiFi network (a")
        print("router), or connected directly to THIS CAM's own hotspot (a")
        print("standalone pair, no router involved)?")
        pairing = ask_choice(
            "Heltec Bridge network setup",
            ["Existing WiFi network (a router)",
             "This CAM's own hotspot (standalone pair, no router)"],
        )
        standalone_pair = pairing.startswith("This CAM's")
        default_bridge_ip = "192.168.4.2" if standalone_pair else "192.168.0.222"
        if standalone_pair:
            print("\nWhen you provision the Heltec, join it to THIS CAM's own hotspot name")
            print("(its Node name / custom SSID -- open network, no password), with a")
            print(f"static IP in the 192.168.4.x range. This CAM is always 192.168.4.1,")
            print(f"so the Heltec needs a different address here -- suggested: {default_bridge_ip}")

        heltec_host = ask(
            "Heltec Bridge IP (the static IP set on the Heltec via\n"
            "  'rnodeconf <port> -w STATION --ip ...')", default_bridge_ip
        )
        heltec_port = ask(
            "Heltec Bridge port (fixed by Reticulum's own protocol --\n"
            "  leave as-is unless you know otherwise)", "7633"
        )
        profile["heltec_host"] = heltec_host
        try:
            profile["heltec_port"] = int(heltec_port)
        except ValueError:
            print(f"  '{heltec_port}' isn't a number -- keeping the default 7633.")
            profile["heltec_port"] = 7633

        # ---- Local greeter name ----
        print("\nThe greeter on the node's own web pages has a name. This is")
        print("cosmetic and separate from the node name above, which is what")
        print("mesh peers see.")
        profile["bot_name"] = ask("  Greeter's name", "BarKeep")

        # ---- What the Wi-Fi list will actually show ----
        preview, clipped = preview_ssid(profile["node_name"])
        print("\n  The node's own Wi-Fi network will appear as:")
        print("    %s   (%d/32 characters)" % (preview, len(preview)))
        if clipped:
            print("    NOTE: the name was shortened to fit both addresses.")
            print("    A shorter node name keeps it whole.")

        # ---- Mesh auto-reply ----
        print("\nWhen a mesh peer messages this node for the FIRST time, it can")
        print("send back a fixed line -- where the node is, what it offers, how")
        print("to reach it. Sent once per peer, not per message: every LXMF send")
        print("costs real airtime, so replying to every message would be noise.")
        print("Leave blank for no auto-reply.")
        while True:
            greeting = ask("  Auto-reply text (max 200 chars)", "")
            if greeting is None:
                greeting = ""
            if len(greeting) <= 200:
                break
            print("    That's %d characters -- 200 is the limit. Please shorten it." % len(greeting))
        profile["mesh_greeting"] = greeting
        if greeting:
            print("    Will send: %r" % greeting)

        # ---- Plugins ----
        # Discovered from the firmware folder, so a plugin dropped in
        # since the last run is picked up here with no change to this
        # tool. Resolved lazily: the folder may not be settled yet when
        # the wizard starts.
        try:
            fw = _remembered_path("stump_app_dir") or STUMP_APP_DIR
            profile["plugin_config"] = configure_plugins(fw)
        except Exception as e:
            print(f"  (plugin scan skipped: {e})")
            profile["plugin_config"] = {}

        # ---- Credit economy ----
        print("\nFile sharing can run as a credit economy (bring something to take")
        print("something) or completely free. Free mode hides the credit UI entirely.")
        credit_choice = ask_choice(
            "How should file sharing work?",
            [
                "Free — nothing costs anything",
                "Standard economy — video 3, music 2, documents 1",
                "Custom economy — set each weight yourself",
            ],
        )
        if credit_choice.startswith("Free"):
            profile["credits_enabled"] = False
            profile["credit_weights"] = DEFAULT_CREDIT_WEIGHTS.copy()
        elif credit_choice.startswith("Standard"):
            profile["credits_enabled"] = True
            profile["credit_weights"] = DEFAULT_CREDIT_WEIGHTS.copy()
        else:
            profile["credits_enabled"] = True
            print("  A file's weight is what uploading it EARNS and what")
            print("  downloading it COSTS. Set all to 1 for a flat one-for-one swap.")
            weights = {}
            for cls, default in DEFAULT_CREDIT_WEIGHTS.items():
                while True:
                    raw = ask(f"  Weight for {cls}", str(default))
                    try:
                        val = int(raw)
                        if val < 0:
                            print("    Weights can't be negative -- try again.")
                            continue
                        weights[cls] = val
                        break
                    except ValueError:
                        print(f"    '{raw}' isn't a whole number -- try again.")
            profile["credit_weights"] = weights

    CONFIG_OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CONFIG_OUT_DIR / f"{node_name.replace(' ', '_')}.json"
    with open(out_path, "w") as f:
        json.dump(profile, f, indent=2)
    print(f"\nProfile saved to {out_path}")
    return profile, out_path


def push_config_to_board(port, board_type, profile):
    """
    Write the appropriate config file. A Firefly's Heltec V3 runs RNode
    firmware, not MicroPython -- mpremote only works against a MicroPython
    REPL, and there is no board filesystem to push to here (this is also
    why the serial-bridge diagnostic below is skipped for this board
    type, for the same underlying reason). radio.json is real and
    needed, but it belongs on the R36S/R36Max handheld next to
    MeshCommanderMax.py -- a different physical device -- so for a
    heltec board this saves it locally and tells the technician exactly
    where it needs to go, instead of attempting a push that can never
    succeed against this hardware.
    """
    banner("PUSHING CONFIG TO BOARD")

    if board_type == "heltec":
        payload = dict(profile["radio"])
        payload["boundary_mode"] = profile["boundary_mode"]
        if "ifac" in profile:
            payload["ifac"] = profile["ifac"]

        CONFIG_OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = CONFIG_OUT_DIR / "radio.json"
        with open(out_path, "w") as f:
            json.dump(payload, f, indent=2)

        print("This Heltec V3 runs RNode firmware, not MicroPython -- there's no")
        print("board filesystem mpremote can push to (same reason the serial-bridge")
        print("diagnostic is skipped for this board type).")
        print()
        print(f"radio.json saved to: {out_path}")
        print("Copy it onto the R36S/R36Max handheld as:")
        print("  /roms/ports/meshcommandermax/radio.json")
        print("(next to MeshCommanderMax.py -- via scp, or onto the SD card directly)")

        # WiFi Station mode IS pushable to this board -- rnodeconf writes
        # it into the RNode firmware's own EEPROM config, no MicroPython
        # filesystem needed. This is the step that makes the Heltec
        # reachable by the CAM at all.
        wifi = profile.get("heltec_wifi")
        if wifi and wifi.get("ssid"):
            print()
            print("Configuring WiFi Station mode on the Heltec...")
            # rnodeconf's OWN documented convention (confirmed directly
            # from markqvist -- the maintainer -- in a recent Reticulum
            # discussion) is the LITERAL STRING "NONE" for "no PSK", not
            # an empty string. These are not the same thing to most WiFi
            # stacks: an empty-string PSK is a zero-length WPA2 key, and
            # a station handed one will try to authenticate with it
            # against an AP that isn't running WPA2 at all -- the CAM's
            # own hotspot specifically, confirmed elsewhere in this file
            # as open, no encryption whatsoever. That mismatch produces
            # exactly "never associates, no handshake, no IP" -- not a
            # loud error, just silence, which is what a real report from
            # the field looked like after this code shipped with a plain
            # "" here for the standalone-pairing case.
            psk = wifi.get("psk") or "NONE"
            cmd = ["rnodeconf", port, "-w", "STATION",
                   "--ssid", wifi["ssid"], "--psk", psk]
            if wifi.get("ip"):
                cmd += ["--ip", wifi["ip"]]
            if wifi.get("netmask"):
                cmd += ["--nm", wifi["netmask"]]
            wok = run_interactive(cmd, timeout=90)
            if wok:
                print(f"OK — Heltec should now come up at {wifi.get('ip', '(DHCP)')}:7633")
                print("Power-cycle it, then confirm the IP shows on its OLED display.")
            else:
                print("FAILED — see rnodeconf's own output above. The radio itself is")
                print("still flashed; only the WiFi step didn't take. Retry manually with:")
                print(f"  rnodeconf {port} -w STATION --ssid <ssid> --psk <pass, or NONE for an open network> \\")
                print(f"            --ip {wifi.get('ip', '192.168.0.222')} --nm {wifi.get('netmask', '255.255.255.0')}")
            return wok
        return True

    # heltec_transport: no longer reaches this function at all -- the
    # standalone Reticulum firmware role locks its radio parameters as
    # part of flashing itself (flash_heltec_standalone_reticulum,
    # rnodeconf -T --freq/--bw/... in one command), not as a separate
    # config-push step against a MicroPython filesystem that no longer
    # exists on this board. See main()'s own dispatch for this role.

    # cam: a real MicroPython board. config.py here is a real Python file
    # with hardcoded constants (WIFI_SSID, WIFI_PASS, NODE_NAME) plus a
    # nested interfaces list -- not a separate JSON file loaded at
    # runtime like the old build. So "pushing config" means generating a
    # customized config.py from the one already in the resolved firmware
    # folder, saving it back there (so future --upload-app runs already
    # have it baked in), and pushing that one file.
    resolved = resolve_path(
        STUMP_APP_DIR, _STUMP_FILES_DESC, remember_key="stump_app_dir", is_dir=True,
        search_roots=[Path.cwd(), Path.cwd().parent, Path(__file__).resolve().parent,
                      Path.home() / "Desktop", Path.home() / "Downloads"],
        search_name="final_firmware",
        validate_fn=lambda p: (p / "config.py").is_file(),
    )
    if resolved is None:
        print("FAILED — no firmware folder found to write config.py into.")
        return False

    local_config = resolved / "config.py"
    try:
        new_content = _generate_config_py(
            local_config, profile["node_name"], profile["wifi_ssid"], profile["wifi_pass"],
            profile["heltec_host"], profile["heltec_port"],
            credits_enabled=profile.get("credits_enabled"),
            credit_weights=profile.get("credit_weights"),
            bot_name=profile.get("bot_name"),
            mesh_greeting=profile.get("mesh_greeting"),
            plugin_config=profile.get("plugin_config"),
            ssid_include_ip=profile.get("ssid_include_ip"),
            ssid_name=profile.get("ssid_name", _UNSET),
        )
    except Exception as e:
        print(f"FAILED to generate config.py: {e}")
        return False

    local_config.write_text(new_content)
    print(f"config.py updated locally at: {local_config}")

    ok, out = run(["mpremote", "connect", port, "fs", "cp", str(local_config), ":config.py"], timeout=30)
    if ok:
        print("OK — config.py written to board.")
    else:
        print(f"FAILED to push config.py over mpremote: {out.strip()}")
        print(f"(the updated file is still saved locally at {local_config} if you need")
        print(" to copy it another way, or just re-run --upload-app once connected)")
    return ok


def _generate_config_py(local_config_path, node_name, wifi_ssid, wifi_pass, heltec_host, heltec_port,
                         credits_enabled=None, credit_weights=None, bot_name=None,
                         mesh_greeting=None, plugin_config=None, ssid_include_ip=None,
                         ssid_name=_UNSET):
    """
    Substitutes WIFI_SSID/WIFI_PASS/NODE_NAME and the Heltec Bridge
    interface's target_host/target_port into the existing config.py
    template, plus the credit-economy settings when given.

    Context-aware (line-by-line, tracking which interface block is
    current) rather than a blind global find-and-replace -- config.py
    has a SECOND, disabled "TCP Client" placeholder block with an
    identically-named target_host key, and a blind replace would corrupt
    that block too or replace the wrong occurrence.
    """
    lines = local_config_path.read_text().splitlines(keepends=True)
    out = []
    in_heltec_block = False
    saw_credits = False
    saw_bot = False
    saw_greeting = False
    saw_ssid_ip = False
    saw_ssid_name = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("WIFI_SSID"):
            out.append("WIFI_SSID = %r\n" % wifi_ssid)
            continue
        if stripped.startswith("WIFI_PASS"):
            out.append("WIFI_PASS = %r\n" % wifi_pass)
            continue
        if stripped.startswith("NODE_NAME"):
            out.append("NODE_NAME = %r\n" % node_name)
            continue
        if stripped.startswith("SSID_INCLUDE_IP") and ssid_include_ip is not None:
            out.append("SSID_INCLUDE_IP = %r\n" % bool(ssid_include_ip))
            saw_ssid_ip = True
            continue
        if stripped.startswith("SSID_NAME") and ssid_name is not _UNSET:
            # ssid_name legitimately CAN be None (meaning "use the
            # default hosted page") -- checked against the _UNSET
            # sentinel, not against None itself, so that real value
            # isn't mistaken for "the wizard didn't touch this".
            out.append("SSID_NAME = %r\n" % ssid_name)
            saw_ssid_name = True
            continue
        if stripped.startswith("BOT_NAME") and bot_name is not None:
            out.append("BOT_NAME = %r\n" % bot_name)
            saw_bot = True
            continue
        if stripped.startswith("MESH_GREETING =") and mesh_greeting is not None:
            out.append("MESH_GREETING = %r\n" % mesh_greeting)
            saw_greeting = True
            continue
        if stripped.startswith("CREDITS_ENABLED") and credits_enabled is not None:
            out.append("CREDITS_ENABLED = %r\n" % bool(credits_enabled))
            saw_credits = True
            continue
        if stripped.startswith("CREDIT_WEIGHTS") and credit_weights is not None:
            out.append("CREDIT_WEIGHTS = %r\n" % (dict(credit_weights),))
            continue
        if "Heltec Bridge" in line:
            in_heltec_block = True
        if in_heltec_block and '"target_host"' in line:
            indent = line[:len(line) - len(line.lstrip())]
            out.append("%s\"target_host\": %r,\n" % (indent, heltec_host))
            continue
        if in_heltec_block and '"target_port"' in line:
            indent = line[:len(line) - len(line.lstrip())]
            out.append("%s\"target_port\": %s,\n" % (indent, heltec_port))
            in_heltec_block = False  # target_port is the last field touched in this block
            continue
        out.append(line)

    # Append the credit settings if the template predates them, so an
    # older config.py still ends up correctly configured rather than
    # silently falling back to fserv.py's built-in defaults and ignoring
    # what the technician just chose.
    # Plugin settings. Replaced in place if the key already exists,
    # appended otherwise, so re-running the wizard updates rather than
    # accumulating duplicate assignments.
    if plugin_config:
        remaining = dict(plugin_config)
        rebuilt = []
        for line in out:
            stripped = line.strip()
            hit = None
            for k in remaining:
                if stripped.startswith(k + " ") or stripped.startswith(k + "="):
                    hit = k
                    break
            if hit:
                rebuilt.append("%s = %r\n" % (hit, remaining.pop(hit)))
            else:
                rebuilt.append(line)
        out = rebuilt
        if remaining:
            out.append("\n# ---- Plugin settings (added by the Provisioner) ----\n")
            for k, v in sorted(remaining.items()):
                out.append("%s = %r\n" % (k, v))

    if mesh_greeting is not None and not saw_greeting:
        out.append("\n# ---- Mesh auto-reply (added by the Provisioner) ----\n")
        out.append("MESH_GREETING = %r\n" % mesh_greeting)
        out.append("MESH_GREETING_MAX = 200\n")
    if bot_name is not None and not saw_bot:
        out.append("\n# ---- Local greeter (added by the Provisioner) ----\n")
        out.append("BOT_NAME = %r\n" % bot_name)
    if credits_enabled is not None and not saw_credits:
        out.append("\n# ---- Credit economy (added by the Provisioner) ----\n")
        out.append("CREDITS_ENABLED = %r\n" % bool(credits_enabled))
        if credit_weights is not None:
            out.append("CREDIT_WEIGHTS = %r\n" % (dict(credit_weights),))
    if ssid_include_ip is not None and not saw_ssid_ip:
        out.append("\n# ---- Walk-up AP hotspot name (added by the Provisioner) ----\n")
        out.append("SSID_INCLUDE_IP = %r\n" % bool(ssid_include_ip))
    if ssid_name is not _UNSET and not saw_ssid_name:
        out.append("\n# ---- Walk-up AP hotspot name (added by the Provisioner) ----\n")
        out.append("SSID_NAME = %r\n" % ssid_name)

    return "".join(out)


def get_stump_ip(port, retries=3, retry_delay=2):
    """
    Asks the CAM board directly for its current network addresses --
    both the STA (LAN, DHCP-assigned, used to reach BarKeep/Billboard/
    fserv from off the node's own AP) and AP (the node's own local
    hotspot) interfaces, matching what captive_portal.py itself embeds
    in the broadcast SSID.

    Unlike the old build, this doesn't try to bring the interfaces up
    itself if they're not active -- this build's real boot sequence
    (WiFi join, NTP sync, RNS/LXMF init, captive portal DNS server) is
    too involved to safely replicate in a one-off diagnostic snippet.
    It reports genuine current state; if nothing's up yet, that means
    the node hasn't actually booted through example_node.py yet, and
    needs a real reset, not a synthetic one.

    Returns a dict {"sta": ip_or_None, "ap": ip_or_None}, or None if
    the query itself couldn't be run at all.
    """
    snippet = (
        "import network\n"
        "sta = network.WLAN(network.STA_IF)\n"
        "ap = network.WLAN(network.AP_IF)\n"
        "sta_ip = sta.ifconfig()[0] if sta.active() and sta.isconnected() else ''\n"
        "ap_ip = ap.ifconfig()[0] if ap.active() else ''\n"
        "print('STA:' + sta_ip + ' AP:' + ap_ip)\n"
    )
    for attempt in range(retries):
        ok, out = run(["mpremote", "connect", port, "exec", snippet], timeout=15)
        out = out.strip()
        if "STA:" in out and "AP:" in out:
            sta_part = out.split("STA:", 1)[1].split(" AP:")[0].strip()
            ap_part = out.split("AP:", 1)[1].strip()
            if sta_part or ap_part:
                return {"sta": sta_part or None, "ap": ap_part or None}
        if attempt < retries - 1:
            time.sleep(retry_delay)
    return None


def sd_card_status(port):
    """
    Runs this build's own mount function (fserv.mount_sd()) via mpremote
    exec, rather than a raw os.listdir('/sd') -- a raw listdir just
    throws ENOENT with no explanation if the card was never mounted in
    the first place. fserv.mount_sd() only reports a bool, not a reason,
    so unlike the old build this can only distinguish mounted/not --
    still real signal, just less detailed than before.

    Returns (ok, msg, genuine_card_failure). That third value is the
    real fix here: "FAIL:" only ever appears in msg when the board
    itself actually ran fserv.mount_sd() and it returned False -- a
    real, board-reported statement about the card. Every OTHER way
    this can fail (mpremote couldn't even reach the port at all,
    fserv.py isn't uploaded so the import itself raised, a timeout)
    says nothing whatsoever about the card's actual state, confirmed
    directly against a real report: "mpremote: failed to access PORT
    (it may be in use by another program)" reached this function's old
    single ok/msg return with no way to tell it apart from a genuine
    "the card won't mount" -- and the caller offered to wipe a card
    that, for all this function actually knows, is completely fine.
    genuine_card_failure is only ever True in the one case where
    offering that prompt is actually justified.
    """
    snippet = (
        "import fserv\n"
        "ok = fserv.mount_sd()\n"
        "print('OK:mounted' if ok else 'FAIL:no card detected, or an incompatible/corrupted filesystem')\n"
    )
    ok, out = run(["mpremote", "connect", port, "exec", snippet], timeout=20)
    out = out.strip()
    if "OK:" in out:
        return True, out.split("OK:", 1)[1].strip(), False
    if "FAIL:" in out:
        return False, out.split("FAIL:", 1)[1].strip(), True
    return False, out or "no response from board (is fserv.py uploaded to it?)", False


def wipe_sd_card(port, skip_confirmation=False, board_type=None):
    """
    Fully erases and reformats the card. This build's fserv.py has no
    wipe function of its own (only mount_sd(), which mounts an
    already-formatted card) -- so this is self-contained, reusing the
    exact same SDCard parameters fserv.py's own mount_sd() uses
    (slot=1, width=1, sck=39, cmd=38, data=(40,)), confirmed against
    MicroPython's own official docs. For a card that arrives with
    leftover files, an incompatible exFAT/NTFS format, or a stale
    partition table that keeps it from mounting cleanly. There is no
    undo, so this always requires an explicit typed confirmation unless
    the caller has already gotten one (skip_confirmation, used when this
    is invoked as a direct non-interactive retry immediately after a
    diagnostic already showed the card unusable and the technician
    already agreed to a wipe there).

    REFUSES OUTRIGHT for any Heltec board, regardless of role, and
    regardless of what the caller passes for skip_confirmation --
    this is a hard safety check inside the function itself, not just
    at its call sites, precisely because a call site can be added
    later (or already existed, at the --wipe-sd CLI flag below) without
    anyone re-deriving this reasoning. sck=39/cmd=38/data=40 are the
    CAM's own SD wiring -- GPIO38 specifically falls inside GPIO33-38,
    which Heltec's own official wiki and datasheet both list, in
    matching wording, as reserved for SPI Flash/SubSPI communication:
    "must not be used as general GPIO." This isn't a theoretical
    concern -- a board went completely dark immediately after this
    exact code path ran against it, and came back only after a power
    cycle, consistent with (though not certain proof of) exactly this
    interference. board_type=None (unknown) also refuses, on the same
    reasoning ask_yes_no() defaults to the safer option under
    uncertainty: a board that hasn't been identified might be a
    Heltec, and there is no safe way to tell from here.
    """
    if board_type != "cam":
        banner(f"WIPE SD CARD — {port}")
        print("REFUSED. This board is" + (" not identified as a CAM" if board_type is None
              else f" a {board_type}") + ", and this function's hardcoded pins")
        print("(sck=39, cmd=38, data=40) are the CAM's own SD wiring specifically.")
        print("GPIO38 falls inside GPIO33-38 -- reserved by Heltec's own hardware")
        print("documentation for SPI Flash/SubSPI communication, explicitly listed as")
        print("\"must not be used as general GPIO.\" Driving it as an SD command line")
        print("risks interfering with the chip's own access to its program flash --")
        print("exactly consistent with a board that went completely dark immediately")
        print("after this code path ran against it in the field.")
        print()
        print("Neither Heltec role has an SD card at all. There is nothing to wipe")
        print("here regardless of board type.")
        return False

    banner(f"WIPE SD CARD — {port}")
    if not skip_confirmation:
        print("This ERASES EVERYTHING currently on the card and lays down a")
        print("fresh filesystem. There is no undo.")
        confirm = ask("Type WIPE (all caps) to confirm, anything else cancels", "")
        if confirm != "WIPE":
            print("Cancelled — card untouched.")
            return False

    snippet = (
        "import os\n"
        "from machine import SDCard\n"
        "try:\n"
        "    os.umount('/sd')\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        "    sd = SDCard(slot=1, width=1, sck=39, cmd=38, data=(40,))\n"
        "    os.VfsFat.mkfs(sd)\n"
        "    os.mount(sd, '/sd')\n"
        "    try:\n"
        "        os.mkdir('/sd/shared')\n"
        "    except Exception:\n"
        "        pass\n"
        "    print('OK:card wiped and reformatted clean')\n"
        "except Exception as e:\n"
        "    print('FAIL:' + str(e))\n"
    )
    ok, out = run(["mpremote", "connect", port, "exec", snippet], timeout=60)
    out = out.strip()
    if "OK:" in out:
        print("OK —", out.split("OK:", 1)[1].strip())
        return True
    reason = out.split("FAIL:", 1)[1].strip() if "FAIL:" in out else (out or "no response from board")
    print("FAILED —", reason)
    return False


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def diagnostics(port, board_type=None, interactive=True):
    banner(f"DIAGNOSTIC SUITE — {port}")
    results = {}

    # 1. Serial bridging (mpremote / MicroPython-specific). Meaningful
    # ONLY for the CAM now -- heltec_transport used to run MicroPython
    # (a now-retired custom stack), but now runs the same kind of
    # pre-built, non-MicroPython firmware as the "heltec" role, reached
    # the same way rnodeconf reaches that one: the radio test below is
    # this role's real check, same as "heltec"'s.
    print("[1/5] Serial bridge test...")
    if board_type in ("heltec", "heltec_transport"):
        print("  SKIPPED — this board runs standalone Reticulum/RNode-family")
        print("  firmware, not MicroPython; mpremote has nothing to connect to")
        print("  here. See the radio test below.")
        results["serial_bridge"] = None
    else:
        ok, out = run(["mpremote", "connect", port, "exec", "print('provisioner-ping')"], timeout=15)
        results["serial_bridge"] = ok
        print("  OK — board responded over serial." if ok else f"  FAILED: {out.strip()}")

    # 2. SD card storage -- meaningful ONLY for the CAM. Neither Heltec
    # role has an SD card at all, confirmed directly: this is also the
    # test that, on a failure, offers to call wipe_sd_card() -- which
    # now refuses outright for any non-CAM board_type on its own, but
    # skipping the whole test here means a heltec_transport board never
    # even reaches a false "card looks unusable" prompt in the first
    # place, rather than relying on the wipe function's own refusal as
    # the only line of defence.
    print("[2/5] SD card storage test...")
    if board_type in ("heltec", "heltec_transport"):
        print("  SKIPPED — neither Heltec role has an SD card.")
        results["sd_card"] = None
    else:
        ok, msg, genuine_card_failure = sd_card_status(port)
        results["sd_card"] = ok
        if ok:
            print(f"  OK — {msg}")
        elif genuine_card_failure:
            # The board itself ran fserv.mount_sd() and it genuinely
            # returned False -- a real, board-reported statement about
            # this card specifically, which is the one case offering a
            # destructive wipe is actually justified.
            print(f"  FAILED: {msg}")
            if interactive:
                do_wipe = ask_yes_no("  Card looks unusable as-is. Wipe and reformat it now?", False)
                if do_wipe:
                    if wipe_sd_card(port, skip_confirmation=True, board_type=board_type):
                        ok2, msg2, _ = sd_card_status(port)
                        results["sd_card"] = ok2
                        print(f"  Re-check after wipe: {'OK' if ok2 else 'FAILED'} — {msg2}")
        else:
            # mpremote couldn't even reach the board to ask -- this says
            # nothing about whether the card is actually fine. Offering
            # a wipe here would be exactly the misdiagnosis a real
            # report caught directly: "mpremote: failed to access PORT
            # (it may be in use by another program)" is a connectivity
            # problem, most often something else on this computer still
            # holding the port open (another provisioner run, a serial
            # monitor, a terminal left attached from an earlier step) --
            # not evidence the SD card needs reformatting. No wipe
            # prompt at all in this branch; the fix is closing whatever
            # else has the port, not touching the card.
            print(f"  FAILED (could not reach the board to check): {msg}")
            print("  This is a connection problem, not necessarily a card problem --")
            print("  close any other program that might have this port open (another")
            print("  provisioner run, a serial monitor, mpremote left connected in")
            print("  another terminal) and re-run --diag before considering a wipe.")

    # 3. Radio TX/RX -- meaningful for any board actually running
    # RNode-family firmware. heltec_transport now does (standalone
    # Reticulum firmware, reached the same way as the "heltec" role's
    # stock RNode) -- this used to be skipped here when this role ran a
    # custom MicroPython stack instead, where rnodeconf --info had
    # nothing real to check; that's no longer the case now that the
    # underlying firmware is genuinely RNode-family.
    print("[3/5] Radio TX/RX test...")
    if board_type == "cam":
        print("  SKIPPED — the CAM has no radio of its own; it reaches the mesh through the Heltec Bridge (tested separately below).")
        results["radio"] = None
    else:
        # port is positional for rnodeconf, not a --port flag (its own
        # usage text confirmed this the same way flash_heltec_rnode did).
        ok, out = run(["rnodeconf", "--info", port], timeout=30)
        results["radio"] = ok
        print("  OK — RNode responded to --info." if ok else f"  FAILED: {out.strip()}")
        if ok and board_type == "heltec_transport" and "TNC" not in out:
            print("  Note: responded, but doesn't show Device mode: TNC -- worth confirming")
            print(f"  with: rnodeconf {port} --info")

    # 4. Heltec Bridge reachability -- the TCP/KISS link the CAM<->Heltec
    # architecture depends on. Meaningless for a standalone transport
    # role: it reads bridge_target from config.py (the CAM's own
    # config) and probes whatever address happens to be THERE --
    # confirmed this can return a completely unrelated "OK" if some
    # other, already-deployed Heltec Bridge happens to be reachable on
    # the network, regardless of whether it has anything to do with the
    # specific board plugged in right now. Still irrelevant here even
    # though heltec_transport is now RNode-family firmware too -- this
    # role has no bridge relationship to any CAM at all, standalone
    # means standalone.
    print("[4/5] Heltec Bridge reachability...")
    if board_type == "heltec_transport":
        print("  SKIPPED — this role has no CAM<->Heltec bridge relationship at all;")
        print("  it's a standalone relay, not a bridge target. This check reads an")
        print("  unrelated config.py value and would test the wrong thing entirely.")
        results["heltec_bridge"] = None
    else:
        bridge_target = _read_bridge_target_from_config()
        if bridge_target is None:
            print("  SKIPPED — couldn't read the bridge target from config.py")
            print("  (run the config wizard first, or check the firmware folder).")
            results["heltec_bridge"] = None
        else:
            host, bport = bridge_target
            ok, detail = _retry_while_booting(
                lambda: _probe_bridge(host, bport), label="the Heltec Bridge"
            )
            results["heltec_bridge"] = ok
            if ok:
                print(f"  OK — {host}:{bport} accepting connections.")
            else:
                print(f"  FAILED: {host}:{bport} — {detail}")
                # A timeout specifically to a 192.168.4.x address is a
                # DIFFERENT failure than "the bridge is actually down":
                # this probe runs from the technician's own laptop, not
                # from the CAM, and 192.168.4.x is the CAM's own
                # standalone hotspot range (see the standalone-pairing
                # wizard flow). A timeout there is the exact signature
                # of THIS COMPUTER having no route to that subnet at
                # all -- not joined to the CAM's own hotspot right now
                # -- which reads identically to a real outage in this
                # test's plain PASS/FAIL output despite being a
                # completely different, much more common situation.
                # Confirmed directly: a real, working bridge reported
                # exactly this failure when the laptop running the
                # diagnostic wasn't on that network, and passed the
                # moment it joined.
                if detail == "timeout" and host.startswith("192.168.4."):
                    print("  This looks like a standalone-pair setup (the CAM's own")
                    print("  hotspot, not an external router). A timeout to a 192.168.4.x")
                    print("  address usually means THIS COMPUTER isn't joined to that")
                    print("  hotspot right now -- join it the same way the Heltec did,")
                    print("  then re-run this check, before assuming the bridge is down.")
                else:
                    print("  Check: is the Heltec powered on, on the same network, and still in")
                    print("  WiFi Station mode? Confirm with: rnodeconf <heltec-port> --info")

    # 5. CAM web server reachability -- a genuinely independent signal
    # from the four checks above: none of them confirm the thing a
    # visitor actually experiences, that the CAM's own HTTP server is
    # up and answering requests correctly. Added after a real report of
    # a fully working, visitor-serving node still showing
    # serial_bridge/sd_card as FAIL from a laptop-side USB port issue
    # that had nothing to do with the device itself (see the summary's
    # own clarifying note below for why this doesn't just get papered
    # over into a blanket PASS). Runs a plain HTTP GET against the
    # CAM's own hotspot address, always up at this fixed address
    # regardless of what else the CAM does or doesn't join -- the same
    # standalone-hotspot design this whole project already relies on
    # for the Heltec Bridge check above -- and confirms the response
    # actually looks like this project's own BarKeep page, not just
    # that something answered on port 80.
    print("[5/5] CAM web server reachability...")
    if board_type != "cam":
        print("  SKIPPED — this checks the CAM's own web server specifically;")
        print("  neither Heltec role runs one.")
        results["cam_online"] = None
    else:
        def _check_cam_http():
            import urllib.request
            try:
                with urllib.request.urlopen("http://192.168.4.1/", timeout=8) as resp:
                    body = resp.read(4096).decode("utf-8", "replace")
            except Exception as e:
                return False, "error", str(e)
            if "Stump" in body:
                return True, "ok", None
            return False, "wrong_content", None

        ok, kind, detail = _retry_while_booting(_check_cam_http, label="the CAM's web server")
        results["cam_online"] = ok
        if ok:
            print("  OK — 192.168.4.1 answered with the real BarKeep page.")
        elif kind == "wrong_content":
            print("  FAILED — something answered on 192.168.4.1, but the response")
            print("  doesn't look like this project's own page.")
        else:
            print(f"  FAILED: {detail}")
            print("  Needs THIS COMPUTER joined to the CAM's own hotspot -- same")
            print("  requirement, and often the same cause, as the Heltec Bridge")
            print("  check above if you haven't joined it yet.")

    banner("DIAGNOSTIC SUMMARY")
    for k, v in results.items():
        label = "SKIPPED" if v is None else ("PASS" if v else "FAIL")
        print(f"  {k:<15} {label}")
    all_relevant_passed = all(v for v in results.values() if v is not None)
    if all_relevant_passed:
        print("\nCERTIFIED FOR FIELD DEPLOYMENT")
    else:
        print("\nNOT CERTIFIED — resolve failures above before deploying.")
        # A meaningfully better-informed response than just repeating
        # the failures above: if the node's own web server is confirmed
        # live and answering correctly right now, visitors are
        # unaffected by whatever else failed, even though this run
        # isn't fully certified. serial_bridge and sd_card specifically
        # matter for RECONFIGURING this board from THIS computer later
        # -- a real, separate concern from whether it currently works
        # for anyone walking up to it. Silently turning this into a
        # blanket PASS would hide that distinction rather than explain
        # it, which is its own kind of misleading in the opposite
        # direction from the report this check exists to address.
        if results.get("cam_online") is True:
            print("\nNote: the CAM's own web server IS confirmed live and answering")
            print("requests correctly right now (checked directly, not inferred) --")
            print("visitors are unaffected by whatever failed above. What's actually")
            print("unconfirmed is whether THIS COMPUTER can reconfigure this board")
            print("over USB serial later, a separate concern from whether it works")
            print("for anyone using it today.")
    return results


def _read_bridge_target_from_config():
    """Reads target_host/target_port out of the firmware folder's own
    config.py, so the bridge test checks what the node will ACTUALLY
    try to reach rather than a value retyped at the prompt (which could
    silently disagree with the deployed config -- exactly the kind of
    drift that makes a green diagnostic meaningless).

    Parsed textually, tracking the "Heltec Bridge" block specifically --
    config.py has a second, disabled TCP Client block with an
    identically-named target_host key, same reason _generate_config_py
    is context-aware. Returns (host, port) or None."""
    try:
        resolved = _remembered_path("stump_app_dir") or STUMP_APP_DIR
        cfg = Path(resolved) / "config.py"
        if not cfg.is_file():
            return None
        host, bport, in_block = None, None, False
        for line in cfg.read_text().splitlines():
            if "Heltec Bridge" in line:
                in_block = True
            if in_block and '"target_host"' in line:
                host = line.split(":", 1)[1].strip().strip(",").strip("'\"")
            if in_block and '"target_port"' in line:
                bport = int(line.split(":", 1)[1].strip().strip(",").strip("'\""))
                break
        if host and bport:
            return host, bport
    except Exception:
        pass
    return None


def _retry_while_booting(fn, max_wait=65, interval=5, label=""):
    """Calls fn() (a zero-arg callable returning a tuple whose first
    element is a success bool) repeatedly until it succeeds or max_wait
    seconds have passed, returning the last result either way.

    Exists specifically for the two network-dependent diagnostic checks
    (Heltec Bridge reachability, CAM web server reachability) -- both
    depend on the CAM having finished its real boot sequence (WiFi
    join, AP bring-up, HTTP server start), which a real report measured
    at close to a full minute, not the few seconds mpremote's own
    serial checks need. Running --diag immediately after a fresh flash
    or a deliberate reboot, as the main flow already does right after
    upload/config, was failing these two specific checks purely because
    they ran before the board had gotten there yet -- a false failure
    from the tool's own timing, not a real problem with the board.

    max_wait=65 specifically to comfortably clear that measured ~60s
    boot time with a small margin, not an arbitrary round number.
    Retrying is harmless when the board is already fully up: the first
    attempt just succeeds immediately and this returns without ever
    sleeping, so this adds no meaningful delay to the common case where
    --diag runs well after boot has already finished.
    """
    import time as _time
    deadline = _time.time() + max_wait
    attempt = 0
    while True:
        attempt += 1
        result = fn()
        if result[0]:
            return result
        if _time.time() >= deadline:
            return result
        remaining = int(deadline - _time.time())
        print(f"  Not ready yet (attempt {attempt}) -- {label} can take close to a")
        print(f"  minute after a fresh boot. Retrying for up to {remaining}s more...")
        _time.sleep(interval)


def _probe_bridge(host, port, timeout=5):
    """Plain TCP connect to the Heltec's WiFi Remote port. Deliberately
    does NOT send KISS frames or try to talk the protocol -- a bare
    connect answers the question this test is actually for ("is the
    radio reachable on the network at all"), and injecting bytes into a
    live RNode's host connection could desync a session the node is
    genuinely using. Returns (ok, detail).

    Distinguishes a timeout from every other connection failure
    explicitly, rather than returning whatever text the exception
    happens to carry -- a timeout means nothing ever answered at all
    (the OS had nowhere to even send the packet), which reads
    completely differently from a refused connection (the subnet WAS
    reachable, just nothing listening on that port). The caller uses
    this distinction directly: a timeout to a 192.168.4.x address is
    the signature of the PROBING COMPUTER not being on that network,
    not of the bridge itself being down.
    """
    import socket as _socket
    s = None
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        return True, "connected"
    except _socket.timeout:
        return False, "timeout"
    except OSError as e:
        return False, str(e)
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Full guided flow
# ---------------------------------------------------------------------------

def interactive_wizard():
    banner("THE PROVISIONER — Technician Deployment Utility")
    print("One-click flashing + guided config + diagnostics for Stump / Firefly hardware.\n")

    if not check_tools():
        if not ask_yes_no("\nSome tools are missing. Continue anyway?", False):
            print("Aborted.")
            return

    port, board_type = choose_board()
    if not port:
        print("Aborted — no board selected.")
        return

    # heltec_transport now has its own complete, self-contained flow --
    # a real, pre-built firmware (microReticulum_Firmware) flashed and
    # RF-locked in one combined step, not MicroPython files uploaded
    # afterward. None of the preflight-firmware-folder, app-upload, or
    # separate config-push logic below applies to it at all, so this
    # branch runs its own sequence and then joins the shared
    # diagnostics/DONE ending below, same as every other role.
    if board_type == "heltec_transport":
        if ask_yes_no(f"\nFlash standalone Reticulum firmware onto {port} now?", True):
            print("\nRadio parameters (defaults match the deployed mesh):")
            radio = dict(DEFAULT_RADIO)
            radio["frequency"] = int(ask("Frequency (Hz)", radio["frequency"]))
            radio["bandwidth"] = int(ask("Bandwidth (Hz)", radio["bandwidth"]))
            radio["txpower"] = int(ask("TX power", radio["txpower"]))
            radio["spreadingfactor"] = int(ask("Spreading factor", radio["spreadingfactor"]))
            radio["codingrate"] = int(ask("Coding rate", radio["codingrate"]))
            flash_heltec_standalone_reticulum(port, radio)
        # Falls through to the shared diagnostics/DONE ending below --
        # radio and serial_bridge checks there now treat this role the
        # same as the existing "heltec" RNode role, since both are now
        # the same kind of firmware, reached the same way.
    else:
        # Preflight: resolve local file dependencies (firmware tree,
        # firmware image) up front, before touching the board at all. Catches
        # a wrong working-directory/layout as a friendly prompt right away
        # instead of after erase-flash has already run.
        resolved_app_dir = None
        if board_type == "cam":
            banner("PREFLIGHT — CHECKING LOCAL FILE LOCATIONS")
            resolved_app_dir = resolve_path(
                STUMP_APP_DIR,
                _STUMP_FILES_DESC,
                remember_key="stump_app_dir",
                is_dir=True,
                search_roots=[Path.cwd(), Path.cwd().parent, Path(__file__).resolve().parent,
                              Path.home() / "Desktop", Path.home() / "Downloads"],
                search_name="final_firmware",
                validate_fn=_is_current_firmware_folder,
            )
            if resolved_app_dir:
                print(f"  Using firmware source: {resolved_app_dir}")
            else:
                print("  No firmware folder found — MicroPython will still flash,")
                print("  but the app upload step will be skipped unless this is fixed.")

        if ask_yes_no(f"\nFlash firmware onto {port} now?", True):
            if board_type == "heltec":
                flash_heltec_rnode(port)
            else:
                flash_ok, flashed_port = flash_cam_micropython(port)
                if flash_ok:
                    # Bootloader recovery can land the board on a different
                    # serial port than the one we started with, so carry the
                    # working port forward -- the upload, config push and IP
                    # query below all need to target where the board
                    # actually is, not where it was before the flash.
                    if flashed_port and flashed_port != port:
                        print(f"\nBoard moved to {flashed_port} during flashing -- using that from here.")
                        port = flashed_port
                    # esptool hard-resets the board via RTS right after writing
                    # the image (visible in its own output: "Hard resetting via
                    # RTS pin..."). Starting the upload immediately races that
                    # reboot -- the board hasn't finished booting MicroPython
                    # yet, so the very first mpremote connection attempt fails
                    # with "could not enter raw repl" almost every time, while
                    # every attempt after succeeds instantly. This wait is the
                    # actual fix for that pattern, not just the retry papering
                    # over it.
                    print("\nWaiting for the board to finish booting after the flash...")
                    time.sleep(5)
                    upload_stump_app(port, app_dir=resolved_app_dir)
                else:
                    print("\nSkipping app upload since flashing failed -- fix that first, then")
                    print(f"retry the upload separately with: python3 provisioner.py --upload-app {port}")

        if ask_yes_no("\nRun guided config wizard?", True):
            profile, _ = config_wizard(board_type)
            push_config_to_board(port, board_type, profile)

            if board_type == "cam":
                banner("FINDING THE NODE'S IP ADDRESS(ES)")
                # Reset first, then wait for the real boot sequence. Querying
                # straight after the config push would almost always miss:
                # the new config.py has only just landed, and the addresses
                # only exist once example_node.py has actually run its WiFi
                # join. Resetting here means the wizard reports a real
                # address instead of handing the technician homework.
                print("Resetting the board so it boots with the new config...")
                run(["mpremote", "connect", port, "reset"], timeout=15)
                print("Waiting for boot (WiFi join, NTP sync, Reticulum startup)...")
                time.sleep(12)
                print("Querying the board for its current network state...")
                addrs = get_stump_ip(port)
                if addrs and (addrs.get("sta") or addrs.get("ap")):
                    print()
                    if addrs.get("sta"):
                        print(f"LAN IP (from '{profile['wifi_ssid']}'): {addrs['sta']}")
                        print(f"  -- reachable from anyone else on that same network:")
                        print(f"     http://{addrs['sta']}/billboard")
                    if addrs.get("ap"):
                        print(f"Local hotspot IP: {addrs['ap']}")
                        print(f"  -- connect a phone to the node's own 'Stump' Wi-Fi network,")
                        print(f"     then browse to: http://{addrs['ap']}/billboard")
                    if not addrs.get("sta"):
                        print("\n(No LAN IP yet -- if this is right after flashing/config, power-cycle")
                        print(" the board so example_node.py actually runs through its real WiFi join.)")
                else:
                    print("\nCouldn't confirm either address yet. That's expected if the board hasn't")
                    print("been power-cycled since the config/upload above -- this build's WiFi join,")
                    print(f"NTP sync, and Reticulum startup all happen in example_node.py's real boot")
                    print(f"sequence, not something this query can fake. Reset the board, wait a few")
                    print(f"seconds, then try: python3 provisioner.py --get-ip {port}")

    ran_diagnostics = False
    diag_passed = None
    if ask_yes_no("\nRun diagnostic test suite now?", True):
        diag_results = diagnostics(port, board_type)
        ran_diagnostics = True
        # Same pass/fail computation diagnostics() itself already printed
        # -- recomputed here because its return value was previously
        # discarded entirely, leaving the final banner below always
        # claiming "Board is ready" even seconds after diagnostics had
        # just printed "NOT CERTIFIED" for the exact same board. Real
        # bug, not cosmetic: whoever's reading only the last few lines
        # of a long run would see nothing but the false reassurance.
        diag_passed = all(v for v in diag_results.values() if v is not None)

    banner("DONE")
    if ran_diagnostics and not diag_passed:
        print("Board is NOT certified -- see the diagnostic summary above for what to")
        print(f"fix, then re-run: python3 provisioner.py --diag {port}")
    else:
        print("Board is ready. Re-run with --diag PORT any time to re-certify.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if "--check-tools" in args:
        check_tools()
    elif "--scan" in args:
        scan_boards()
    elif "--diag" in args:
        idx = args.index("--diag")
        if idx + 1 >= len(args):
            print("Usage: provisioner.py --diag PORT")
            sys.exit(1)
        port = args[idx + 1]
        # Now three genuinely meaningful choices, not two -- heltec_transport
        # used to be deliberately left out here (its old MicroPython-based
        # diagnostics weren't built yet), but now runs the same kind of
        # RNode-family firmware as "heltec" and gets real, correct checks.
        # Exact-label lookup, not substring matching against "Heltec" --
        # that broke once before, the moment two Heltec-labeled choices
        # existed in choose_board(); same fix applied here up front.
        heltec_label = "Heltec V3 (Control Plane — RNS/LoRa mesh)"
        heltec_transport_label = "Heltec V3 (Standalone Transport — microReticulum firmware)"
        cam_label = "ESP32-S3-CAM (Data Plane — local vault + web)"
        board_choice = ask_choice(
            "What kind of board is this?",
            [heltec_label, heltec_transport_label, cam_label],
        )
        board_type_map = {heltec_label: "heltec", heltec_transport_label: "heltec_transport", cam_label: "cam"}
        board_type = board_type_map[board_choice]
        diagnostics(port, board_type)
    elif "--wipe-sd" in args:
        idx = args.index("--wipe-sd")
        if idx + 1 >= len(args):
            print("Usage: provisioner.py --wipe-sd PORT")
            sys.exit(1)
        # Explicitly confirmed here, not left to wipe_sd_card()'s own
        # default-refuse behaviour for an unspecified board_type -- that
        # default is the right safety net, but this flag's whole purpose
        # is wiping a CAM's card (see the --help text above), so asking
        # outright makes the intent explicit rather than accidentally
        # correct because of how a keyword argument happens to default.
        if ask_yes_no("This wipes an SD card -- only the CAM has one. Confirm this port is a CAM?", False):
            wipe_sd_card(args[idx + 1], board_type="cam")
        else:
            print("Cancelled -- card untouched. Neither Heltec role has an SD card at all.")
    elif "--upload-app" in args:
        idx = args.index("--upload-app")
        if idx + 1 >= len(args):
            print("Usage: provisioner.py --upload-app PORT")
            sys.exit(1)
        upload_stump_app(args[idx + 1])
    elif "--get-ip" in args:
        idx = args.index("--get-ip")
        if idx + 1 >= len(args):
            print("Usage: provisioner.py --get-ip PORT")
            sys.exit(1)
        port = args[idx + 1]
        addrs = get_stump_ip(port)
        if addrs and (addrs.get("sta") or addrs.get("ap")):
            if addrs.get("sta"):
                print(f"LAN IP:  {addrs['sta']}  ->  http://{addrs['sta']}/billboard")
            if addrs.get("ap"):
                print(f"AP IP:   {addrs['ap']}  ->  http://{addrs['ap']}/billboard  (node's own 'Stump' hotspot)")
        else:
            print("Couldn't confirm either address. If the board was just flashed/configured,")
            print("power-cycle it first so example_node.py's real boot sequence (WiFi join,")
            print("NTP sync, Reticulum startup) actually runs, then try again.")
    else:
        try:
            interactive_wizard()
        except KeyboardInterrupt:
            print("\nAborted by user.")


if __name__ == "__main__":
    main()
