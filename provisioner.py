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
    python3 provisioner.py --erase-flash PORT  # erase a CAM's or a Heltec's whole flash (start over)
    python3 provisioner.py --upload-app PORT  # re-push app files without reflashing
    python3 provisioner.py --get-ip PORT   # look up the Stump's live AP IP
    python3 provisioner.py --version [PORT] # this folder's release, GitHub's, a CAM's
    python3 provisioner.py --list-packs    # show the packs in packs/ (and their hashes)
    python3 provisioner.py --install-pack PORT PACK   # add a pack (folder or .zip) to a node
    python3 provisioner.py --remove-plugin PORT NAME  # take a plugin off a node
    python3 provisioner.py --hash-pack FOLDER         # pack authors: hash before publishing
    python3 provisioner.py --check-tools   # verify esptool/mpremote/rnodeconf present
    python3 provisioner.py --pc [FOLDER]   # Stump on THIS computer: set up or update (docs/PC.md)
    python3 provisioner.py --pc --reconfigure         # ...and run its config wizard again
    python3 provisioner.py --check-chain [FOLDER|ADDRESS]  # is the chain linked? what to fix if not

Nothing here talks to a real board unless you run it on a machine that has
one plugged in. Every external call is wrapped so a missing tool or absent
board produces an explicit, readable message instead of a stack trace.
"""

import json
import re
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


def _folder_release(path=None):
    """STUMP_RELEASE ("1.0.0-beta.3") of a firmware folder (this download's
    final_firmware/ by default), read as text like _folder_version. None
    if it can't be read."""
    try:
        f = Path(path or STUMP_APP_DIR) / "example_node.py"
        for line in f.read_text().splitlines():
            if line.startswith("STUMP_RELEASE"):
                return line.partition("=")[2].split("#")[0].strip().strip("'\"")
    except Exception:
        pass
    return None


def checkout_status(fetch=None):
    """Is this download the latest? {"release", "git", "behind"}:
    release from final_firmware/example_node.py; outside a git checkout
    git is None; behind is how many commits GitHub has that this folder
    hasn't (0: up to date), or None when it couldn't ask (offline, no
    upstream). fetch=None asks GitHub only from a real terminal (a test
    or a pipe never waits on the network)."""
    here = Path(__file__).resolve().parent
    st = {"release": _folder_release(), "git": None, "behind": None}
    if not (here / ".git").exists():
        return st
    if fetch is None:
        fetch = sys.stdin.isatty()
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")

    def git(*args, timeout=10):
        try:
            r = subprocess.run(["git", "-C", str(here)] + list(args), capture_output=True,
                               text=True, timeout=timeout, env=env)
            return r.stdout.strip() if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    fetched = bool(fetch) and git("fetch", "--quiet", "--tags", timeout=15) is not None
    st["git"] = git("describe", "--tags", "--always") or "?"
    if fetched:
        n = git("rev-list", "--count", "HEAD..@{u}")
        st["behind"] = int(n) if n and n.isdigit() else None
    return st


def release_lines(st=None):
    """The start box's lines about the version: what this folder installs,
    and whether GitHub has newer."""
    st = st or checkout_status()
    rel = st.get("release") or "?"
    lines = ["Installs Stump %s (this folder)" % bold(rel)]
    if st.get("git") is None:
        return lines
    b = st.get("behind")
    if b is None:
        lines.append("  Couldn't ask GitHub for a newer one (offline?). Check: git pull")
    elif b == 0:
        lines.append("  " + green("Up to date with GitHub") + " (%s)" % st["git"])
    else:
        lines.append("  " + yellow("GitHub has %d newer commit%s:" % (b, "" if b == 1 else "s"))
                     + " quit, run git pull, start again")
    return lines


def board_release(port):
    """STUMP_RELEASE of the firmware on a CAM, read from its
    example_node.py over USB. None if it couldn't be read."""
    code = ("r='?'\n"
            "for p in ('example_node.py','/lib/example_node.py'):\n"
            "    try:\n"
            "        for l in open(p):\n"
            "            if l.startswith('STUMP_RELEASE'):\n"
            "                r=l.split('=',1)[1].split('#')[0].strip().strip(chr(34)+chr(39))\n"
            "                break\n"
            "        break\n"
            "    except OSError:\n"
            "        pass\n"
            "print('STUMP_RELEASE:'+r)\n")
    res = run(["mpremote", "connect", port, "exec", code], timeout=20)
    out = res[1] if res else ""
    for line in (out or "").splitlines():
        if line.startswith("STUMP_RELEASE:"):
            v = line.split(":", 1)[1].strip()
            return None if v == "?" else v
    return None


def release_check(port, folder=None):
    """(ok, text, release on the board) comparing the CAM's release with
    the folder's."""
    want = _folder_release(folder)
    have = board_release(port)
    if have is None:
        return False, "couldn't read the board's release (is this a Stump CAM?)", None
    if want and have != want:
        return (False, "the board runs %s, this folder has %s: re-upload the Stump files" % (have, want),
                have)
    return True, "the board runs %s, the same as this folder" % have, have


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
    "_sd_web/billboard.txt": "e3b0c44298fc1c14",
    "barkeep.py": "61e724e4a377960c",
    "billboard.py": "a7d1f3df668997ab",
    "boot_common.py": "dc95827c3989d1ca",
    "captive_portal.py": "35cb3a279ecc42db",
    "chain.py": "3727f30a928f3478",
    "config.py": "ed7b475134824fb3",
    "conn.py": "b17cf4fb4f80fbbf",
    "discovery.py": "d01cc2a1bf23f2ac",
    "docs/CHAIN.md": "7d168225a314cfba",
    "docs/CLIENT_QUICKSTART.md": "4017b0226a8f2478",
    "docs/COMMANDS.md": "1d443ec9b625aebe",
    "docs/ENGINEERING_NOTES.md": "e2246f83f2912a8d",
    "docs/FIELD_TEST.md": "0511af24e1bd4cf4",
    "docs/HIDDEN_FEATURES.md": "8f7ee17eabfd0b6a",
    "docs/HOME_BRANDING.md": "e1a1a9cf854dcdce",
    "docs/HTTPS_SETUP.md": "12cbbf2de7c38810",
    "docs/PACKS.md": "2e89e3c28d80d72d",
    "docs/PC.md": "6f5e431869e8b075",
    "docs/PLUGINS.md": "3f5b4a5ee6844305",
    "example_node.py": "fd3ac044cfbe5004",
    "features.py": "81e8af361c64a911",
    "flasher_ui.py": "1d3035b1132bc87e",
    "fserv.py": "125684b076cb46ad",
    "fservbot/plugin.json": "4e0137362ff327d0",
    "guard.py": "526fac7536abfe7f",
    "i18n.py": "7b3dd8f62c2c3316",
    "keepalive.py": "cd51c863a2284779",
    "layout.py": "fb82a2d1ed9f00da",
    "lib/bz2_fast_xtensawin.mpy": "ac55d9eda2126432",
    "lib/ed25519_fast_xtensawin.mpy": "96e74dac45f91687",
    "lib/ed25519_iram.mpy": "96e74dac45f91687",
    "librarian.py": "1d92d849fe11b612",
    "lora_boards.py": "d60ef896cd1a0ff9",
    "main.py": "1aa56ed560be50d7",
    "node_common.py": "e1e748a3283da2f4",
    "peripherals/__init__.py": "d2bdac4de6de79de",
    "peripherals/adc_reader.py": "50a393bfa61641d4",
    "plugins.py": "ed3db932a3d82f06",
    "profiles.py": "6de48b5b3f2666c2",
    "propagation.py": "e86471653a5036ec",
    "radio.py": "46a5bce1b8a90405",
    "rrc.py": "c94b67a8005d4d8e",
    "rrc_mesh.py": "41b43f6b06e192f1",
    "rrc_ui.py": "bce04bbc1653ae54",
    "settings.py": "81fc227a176bea01",
    "stump_tls.py": "32a9394408ae392e",
    "stumpid/plugin.json": "a096572446001706",
    "theme.py": "79b1b8a3b636d07b",
    "third_party/codec2/COPYING": "9ebb6f82b7380a62",
    "third_party/codec2/build_codec2_wasm.sh": "11aafe7aca8e7a71",
    "third_party/codec2/generated/codebook.c": "be54d9b8e15504d8",
    "third_party/codec2/generated/codebookd.c": "4981d6b5c51b41a5",
    "third_party/codec2/generated/codebookge.c": "0e61b65676858284",
    "third_party/codec2/generated/codebookjmv.c": "622d5a7128cfaf83",
    "third_party/codec2/generated/codebooknewamp1.c": "d4f07fd25bf4d427",
    "third_party/codec2/generated/codebooknewamp1_energy.c": "7b8048e545b797d5",
    "third_party/codec2/generated/codebooknewamp2.c": "d92160a2a80821c0",
    "third_party/codec2/generated/codebooknewamp2_energy.c": "98b479338e8b13ed",
    "third_party/codec2/generated/codec2/version.h": "b0b25e8d1ffc797a",
    "third_party/codec2/src/H2064_516_sparse_test.h": "69d674e949ee0ac0",
    "third_party/codec2/src/HRA_112_112.h": "06e6c3343b739755",
    "third_party/codec2/src/HRA_112_112_test.h": "f3d15938d0a08a4f",
    "third_party/codec2/src/HRA_56_56.h": "bd1c135533e1ecaa",
    "third_party/codec2/src/HRAa_1536_512.h": "753d6dcf2560c5e5",
    "third_party/codec2/src/HRAb_396_504.h": "bcf2e19c177ec4c2",
    "third_party/codec2/src/H_1024_2048_4f.h": "38827d0b55fe063a",
    "third_party/codec2/src/H_128_256_5.h": "552cfee49555bb2d",
    "third_party/codec2/src/H_16200_9720.h": "6f07bdcdb799672a",
    "third_party/codec2/src/H_2064_516_sparse.h": "64b1d2508e276725",
    "third_party/codec2/src/H_212_158.h": "b584670a36b2fc4d",
    "third_party/codec2/src/H_256_512_4.h": "b303e11176ac8fb8",
    "third_party/codec2/src/H_256_768_22.h": "5be708d644fd71c1",
    "third_party/codec2/src/H_4096_8192_3d.h": "4795314f0d754f13",
    "third_party/codec2/src/_kiss_fft_guts.h": "25fbd1b559e52dd2",
    "third_party/codec2/src/bpf.h": "3fd6a2d74603d3ae",
    "third_party/codec2/src/bpfb.h": "92788e0ccc90f6ba",
    "third_party/codec2/src/c2file.h": "d7ef60d096f8709d",
    "third_party/codec2/src/codec2.c": "de5b4f46a2081b3f",
    "third_party/codec2/src/codec2.h": "b8d2c2a18f03aff2",
    "third_party/codec2/src/codec2_cohpsk.h": "4cf45703c76a30ae",
    "third_party/codec2/src/codec2_fdmdv.h": "f99bb1efa5a06fdb",
    "third_party/codec2/src/codec2_fft.c": "9190b741cb5ef609",
    "third_party/codec2/src/codec2_fft.h": "91d88c42998a743d",
    "third_party/codec2/src/codec2_fifo.h": "0c10e4dc3c0d857d",
    "third_party/codec2/src/codec2_fm.h": "3d9705885fdedaf3",
    "third_party/codec2/src/codec2_internal.h": "c0152b27e891d619",
    "third_party/codec2/src/codec2_math.h": "fef02608750794b8",
    "third_party/codec2/src/codec2_ofdm.h": "b1b55861bb516dee",
    "third_party/codec2/src/cohpsk_defs.h": "7f61b54f5b238cab",
    "third_party/codec2/src/cohpsk_internal.h": "1af7efffeb8de988",
    "third_party/codec2/src/comp.h": "72165b1e50364f6b",
    "third_party/codec2/src/comp_prim.h": "47be51e70813ba6d",
    "third_party/codec2/src/debug_alloc.h": "959f7a016f32da86",
    "third_party/codec2/src/defines.h": "2823ad9940f51043",
    "third_party/codec2/src/dump.c": "17286a0aa2427a46",
    "third_party/codec2/src/dump.h": "691d027b44b86870",
    "third_party/codec2/src/fdmdv_internal.h": "8c1886b0cbff6d16",
    "third_party/codec2/src/filter.h": "65e5dc70ffb60b20",
    "third_party/codec2/src/filter_coef.h": "abc1d4e7f7cbf8c9",
    "third_party/codec2/src/fm_fir_coeff.h": "a0849ec015fd4679",
    "third_party/codec2/src/fmfsk.h": "2678c92cfacb188b",
    "third_party/codec2/src/freedv_api.h": "642dd2360a2ca72f",
    "third_party/codec2/src/freedv_api_internal.h": "09f5204bfcdb303a",
    "third_party/codec2/src/freedv_data_channel.h": "bcb0956bba3fff77",
    "third_party/codec2/src/freedv_vhf_framing.h": "dc52c4ccf6a364e8",
    "third_party/codec2/src/fsk.h": "1ce449ee1b016f1f",
    "third_party/codec2/src/golay23.h": "035a3214fadb933f",
    "third_party/codec2/src/golaydectable.h": "d80309086ebee043",
    "third_party/codec2/src/golayenctable.h": "0de9a8cd2d7fcc45",
    "third_party/codec2/src/gp_interleaver.h": "86c618b6619816fd",
    "third_party/codec2/src/hanning.h": "0159dd5ef592493b",
    "third_party/codec2/src/ht_coeff.h": "7c83ba631721f0d8",
    "third_party/codec2/src/interldpc.h": "aad7f4c1fd86f183",
    "third_party/codec2/src/interp.c": "27cac5a3d055d2d6",
    "third_party/codec2/src/interp.h": "6d81020040ab5cc0",
    "third_party/codec2/src/kiss_fft.c": "f16ca65e00a766c8",
    "third_party/codec2/src/kiss_fft.h": "cef46109553a4208",
    "third_party/codec2/src/kiss_fftr.c": "fcd5fb59beae112c",
    "third_party/codec2/src/kiss_fftr.h": "6c44114389a80c55",
    "third_party/codec2/src/ldpc_codes.h": "f654bacf0a989c49",
    "third_party/codec2/src/linreg.h": "9329f742bc7669e3",
    "third_party/codec2/src/lpc.c": "875360ef3b44a06c",
    "third_party/codec2/src/lpc.h": "76abd1c359e204c8",
    "third_party/codec2/src/lpcnet_freq.h": "1c10ea2cf1c1b039",
    "third_party/codec2/src/lsp.c": "81ea0264c9827543",
    "third_party/codec2/src/lsp.h": "bf70456661d9a9f2",
    "third_party/codec2/src/machdep.h": "f6cee89f708494cb",
    "third_party/codec2/src/mbest.c": "91e0126fc7a001d9",
    "third_party/codec2/src/mbest.h": "c501ae2b2443c729",
    "third_party/codec2/src/modem_probe.h": "da48b4e6d8ae2171",
    "third_party/codec2/src/modem_stats.h": "8a822270a1923b77",
    "third_party/codec2/src/mpdecode_core.h": "51c5b962ca63de56",
    "third_party/codec2/src/newamp1.c": "8df144b2ef72abdf",
    "third_party/codec2/src/newamp1.h": "457f8357a8ca2f06",
    "third_party/codec2/src/newamp2.h": "d0848ca72de8a7c3",
    "third_party/codec2/src/nlp.c": "5c35a44bc3093bd7",
    "third_party/codec2/src/nlp.h": "0ba662b3d14ba456",
    "third_party/codec2/src/noise_samples.h": "15a35063401eb6db",
    "third_party/codec2/src/octave.h": "9fdee726408cf69f",
    "third_party/codec2/src/ofdm_internal.h": "9ba259557a4bce69",
    "third_party/codec2/src/optparse.h": "7101d7cf7cc009bf",
    "third_party/codec2/src/os.h": "27115da4a8401b71",
    "third_party/codec2/src/pack.c": "cfeeda4561b6a06a",
    "third_party/codec2/src/phase.c": "751d3123d070c42f",
    "third_party/codec2/src/phase.h": "00a03705088e4767",
    "third_party/codec2/src/phi0.h": "ad90c2854cdbd048",
    "third_party/codec2/src/pilot_coeff.h": "a0461af5299f21e5",
    "third_party/codec2/src/pilots_coh.h": "684447956c021723",
    "third_party/codec2/src/postfilter.c": "52fd7d3131e947b7",
    "third_party/codec2/src/postfilter.h": "4304bc99e33dcdea",
    "third_party/codec2/src/quantise.c": "a9b6dbadfee5c79b",
    "third_party/codec2/src/quantise.h": "97fe76969a82bc1e",
    "third_party/codec2/src/reliable_text.h": "c8b59b2352a19b72",
    "third_party/codec2/src/rn.h": "39b649e96ffdccb8",
    "third_party/codec2/src/rn_coh.h": "4b8364f9cfc4ce6b",
    "third_party/codec2/src/rxdec_coeff.h": "9ec977cd6be0e731",
    "third_party/codec2/src/sd.h": "aad47d6a5899d99d",
    "third_party/codec2/src/sine.c": "9d286a353734f696",
    "third_party/codec2/src/sine.h": "ca6a177e4382bd63",
    "third_party/codec2/src/ssbfilt_coeff.h": "6b3aad615fe7e1a2",
    "third_party/codec2/src/test_bits.h": "0a5b517042e3c0b1",
    "third_party/codec2/src/test_bits_coh.h": "8f1fe41c78e30a22",
    "third_party/codec2/src/test_bits_ofdm.h": "64ddffa5b313f3c4",
    "third_party/codec2/src/varicode.h": "af1752e8758cce05",
    "third_party/codec2/src/varicode_table.h": "47d96cae8e3bb1db",
    "third_party/codec2/src/wval.h": "89b525e480f284ca",
    "third_party/codec2/stump_codec2.c": "7d26c043d8ada58a",
    "third_party/opus/COPYING": "01e1167d54a096d1",
    "third_party/opus/build_opus_wasm.sh": "e42a07ee5e114e14",
    "third_party/opus/stump_opus.c": "833eb43c52d6c1b2",
    "tools_payload/apps/FireFly-RK3326-0.6.2.zip": "8d0277ddec4d715b",
    "tools_payload/apps/README.es.md": "26ec9046d7c4447a",
    "tools_payload/apps/README.fr.md": "afdbb2570e4cb770",
    "tools_payload/apps/README.md": "2e0f5a7aa18abdaf",
    "tools_payload/flasher/README.es.md": "51131571a4aead12",
    "tools_payload/flasher/README.fr.md": "3783d700c0d5a21f",
    "tools_payload/flasher/README.md": "b5d9f0f3808bf523",
    "tools_payload/flasher/catalog.json": "8dc38061d6bf49a3",
    "tools_payload/flasher/esptool-bundle.js": "ef7d5a237d3f273e",
    "tools_payload/flasher/esptool-js-LICENSE.txt": "1c25f29242785d63",
    "tools_payload/images/README.txt": "10dbf1ed3e3eee26",
    "tools_payload/provisioner/README.es.md": "a11e1f5793fa473d",
    "tools_payload/provisioner/README.fr.md": "65c68d1ca42a6c13",
    "tools_payload/provisioner/README.md": "dbf678077919e6c2",
    "urns/__init__.py": "4a83ee3f5cd42ca4",
    "urns/buffer.py": "b1da1d0723340421",
    "urns/bz2dec.py": "8149a39deee822c2",
    "urns/channel.py": "0f57d5ec378333c7",
    "urns/const.py": "4d9daaabfbbd93d7",
    "urns/crypto/__init__.py": "dec8540a87c232a4",
    "urns/crypto/aes.py": "274f35d97de9d852",
    "urns/crypto/ed25519.py": "38fc5b3f532c9186",
    "urns/crypto/hashes.py": "4c44d02dbdf161c9",
    "urns/crypto/hkdf.py": "a42c6930a4ffcdfa",
    "urns/crypto/hmac.py": "ae46b89b811c2511",
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
    "urns/identity.py": "1a98f90e034bfb19",
    "urns/interfaces/__init__.py": "f058f69c73c34294",
    "urns/interfaces/chain.py": "987643f122d08df9",
    "urns/interfaces/e32.py": "4cc3c2994c2d7972",
    "urns/interfaces/lora.py": "34ce816fe69c0b43",
    "urns/interfaces/rnode_ble.py": "3f3e6f3f82eda820",
    "urns/interfaces/rnode_serial.py": "1ca360599e955cce",
    "urns/interfaces/serial.py": "3c7dda5e4ffb5dec",
    "urns/interfaces/tcp.py": "a29d90caa017764a",
    "urns/interfaces/udp.py": "cc5e30ddc0861737",
    "urns/interfaces/wifi_serial.py": "4ce7c80aa6bf0846",
    "urns/link.py": "ef808679cefb1ac0",
    "urns/log.py": "4b5576693991c7a6",
    "urns/lxmf.py": "a67218b713580252",
    "urns/packet.py": "d8a3a0c4a3a9ebbf",
    "urns/resource.py": "e54f10289b5a85e6",
    "urns/reticulum.py": "88295a083390bdc5",
    "urns/transport.py": "7cbfcb1a78587056",
    "urns/umsgpack.py": "7df3983d6abcf149",
    "web/codec2.js": "2caf391c54505c61",
    "web/codec2.wasm": "342264605478d63f",
    "web/opus.js": "1cb7a20504785a6d",
    "web/opus.wasm": "4aecdb72f03d2486",
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
# third_party/ holds the Codec 2 sources web/codec2.wasm is built from
# (LGPL-2.1 asks that they ship with it); they belong in the project, not
# on the board.
BOARD_EXCLUDE_DIRS = ("tools_payload", "third_party")
# What a running node writes next to its firmware: its Reticulum identity
# and paths, the SD stand-in, Python caches. Never firmware -- a stray
# identity here, copied to every new Stump, would give them all the same one.
RUNTIME_STATE_DIRS = ("rns", "sd", "__pycache__")


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


def configure_plugins(fw_dir, show_advanced=False, extra=()):
    """Runs the wizard prompts each discovered plugin declares.

    Returns {CONFIG_KEY: value} to be written into config.py.

    Only non-advanced prompts are asked by default. Plugin authors mark
    the settings with working defaults as advanced precisely so a
    technician isn't walked through six questions to install one thing;
    those stay changeable later, either by editing config.py or from
    whatever in-app surface the plugin provides.
    """
    plugins = discover_plugins(fw_dir) + list(extra)
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


# ---------------------------------------------------------------------------
# Plugins and packs (docs/PLUGINS.md, docs/PACKS.md)
# ---------------------------------------------------------------------------
#
# A plugin is a folder with install.py (and, for the Provisioner, a
# plugin.json: title, wizard prompts, the files to upload and a hash of
# each). A pack is the download: pack.json plus one or more plugin
# folders, and optionally tools/ and home/ for the SD card, a theme and
# a bot persona. EXPECTED_FILE_HASHES covers the core only; each plugin
# carries its own hashes, and the built-in plugins' plugin.json files are
# in the core manifest, so the chain of trust for those is unbroken.

PACKS_DIR = Path(__file__).resolve().parent / "packs"
PACK_CACHE_DIR = Path.home() / ".provisioner" / "packs"

# Patterns a plugin should never need: anything that holds the node's one
# event loop. A warning, not a refusal -- a technician decides.
_LINT_PATTERNS = (
    ("time.sleep(", "time.sleep() stops the whole node -- use await asyncio.sleep()"),
    ("utime.sleep(", "utime.sleep() stops the whole node -- use await asyncio.sleep()"),
    ("sleep_ms(", "sleep_ms() stops the whole node -- use await asyncio.sleep_ms()"),
    ("socket.socket(", "a raw socket can block the node -- the core owns networking"),
    ("input(", "input() waits for a keyboard the node doesn't have"),
    ("urequests", "urequests blocks, and visitors' Wi-Fi has no internet anyway"),
    ("machine.reset(", "resets the node"),
)


def _sha16(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def firmware_plugin_api(fw_dir):
    """The plugin API version a firmware folder offers (plugins.py
    API_VERSION), or 0 if it predates the plugin API."""
    import re
    try:
        m = re.search(r"^API_VERSION\s*=\s*(\d+)", (Path(fw_dir) / "plugins.py").read_text(), re.M)
        return int(m.group(1)) if m else 0
    except OSError:
        return 0


def _plugin_spec(plugin_dir):
    try:
        with open(Path(plugin_dir) / "plugin.json") as f:
            return json.load(f)
    except Exception:
        return {}


def plugin_files(plugin_dir, spec=None):
    """Every file of a plugin, as paths relative to the plugin folder's
    PARENT ("tavern/install.py"): its plugin.json "files" list if it has
    one, else everything in the folder. Includes plugin.json and READMEs;
    _belongs_on_board() decides what goes to the board."""
    d = Path(plugin_dir)
    spec = _plugin_spec(d) if spec is None else spec
    listed = spec.get("files")
    if listed:
        return [f for f in listed if (d.parent / f).is_file()]
    return sorted(f.relative_to(d.parent).as_posix() for f in d.rglob("*")
                  if f.is_file() and "__pycache__" not in f.parts
                  and not any(f.name.endswith(s) for s in MANIFEST_EXCLUDE_SUFFIXES))


def verify_plugin(plugin_dir, spec=None):
    """(problems, unhashed): files whose hash doesn't match plugin.json's
    "hashes", or that are missing; and files with no hash at all."""
    d = Path(plugin_dir)
    spec = _plugin_spec(d) if spec is None else spec
    hashes = spec.get("hashes") or {}
    problems, unhashed = [], []
    for rel in plugin_files(d, spec):
        if rel.endswith("plugin.json"):
            continue
        want = hashes.get(rel)
        if want is None:
            unhashed.append(rel)
        elif _sha16(d.parent / rel) != want:
            problems.append((rel, "changed since it was hashed"))
    for rel in hashes:
        if not (d.parent / rel).is_file():
            problems.append((rel, "missing"))
    return problems, unhashed


def hash_plugin(plugin_dir):
    """Writes a hash of each of the plugin's files into its plugin.json
    (for plugin authors: run after every change, before publishing)."""
    d = Path(plugin_dir)
    spec = _plugin_spec(d)
    if not spec:
        spec = {"plugin": d.name, "title": d.name, "version": "0.1"}
    spec["hashes"] = {rel: _sha16(d.parent / rel) for rel in plugin_files(d, spec)
                      if not rel.endswith("plugin.json")}
    with open(d / "plugin.json", "w") as f:
        f.write(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    return spec["hashes"]


def lint_plugin(plugin_dir):
    """[(file, line_no, warning)] for calls that would hold the node."""
    out = []
    for f in sorted(Path(plugin_dir).rglob("*.py")):
        try:
            lines = f.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            code = line.split("#", 1)[0]
            for pat, why in _LINT_PATTERNS:
                if pat in code:
                    out.append((f.relative_to(Path(plugin_dir).parent).as_posix(), n, why))
    return out


def _pack_files(pack_dir, spec):
    """Files in a pack's tools/ and home/ folders (relative to the pack)."""
    d = Path(pack_dir)
    out = []
    for sub in ("tools", "home"):
        if spec.get(sub, True) is False:
            continue
        root = d / sub
        if root.is_dir():
            out += sorted(f.relative_to(d).as_posix() for f in root.iterdir() if f.is_file()
                          and not f.name.startswith("."))
    return out


def hash_pack(pack_dir):
    """Hashes every plugin in a pack, then the pack's tools/ and home/
    files into pack.json."""
    d = Path(pack_dir)
    with open(d / "pack.json") as f:
        spec = json.load(f)
    for name in spec.get("plugins", []):
        hash_plugin(d / name)
    spec["hashes"] = {rel: _sha16(d / rel) for rel in _pack_files(d, spec)}
    spec["plugin_hashes"] = {name: _sha16(d / name / "plugin.json") for name in spec.get("plugins", [])}
    with open(d / "pack.json", "w") as f:
        f.write(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    return spec


def _safe_extract(zip_path, dest):
    """Extracts a pack zip, refusing absolute paths and '..'."""
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            p = Path(info.filename)
            if p.is_absolute() or ".." in p.parts:
                raise ValueError("unsafe path in zip: %s" % info.filename)
        zf.extractall(dest)


def load_pack(path):
    """A pack folder or .zip -> {"dir", "spec", "source"}, or None."""
    p = Path(path).expanduser()
    if p.is_file() and p.suffix.lower() == ".zip":
        dest = PACK_CACHE_DIR / p.stem
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        try:
            _safe_extract(p, dest)
        except Exception as e:
            print(f"  (skipping {p.name}: {e})")
            return None
        found = list(dest.glob("pack.json")) + list(dest.glob("*/pack.json"))
        if not found:
            print(f"  (skipping {p.name}: no pack.json inside)")
            return None
        p_dir, source = found[0].parent, p
    elif (p / "pack.json").is_file():
        p_dir, source = p, p
    else:
        return None
    try:
        with open(p_dir / "pack.json") as f:
            spec = json.load(f)
    except Exception as e:
        print(f"  (skipping {p_dir.name}: pack.json won't parse -- {e})")
        return None
    for key in ("name", "version", "author", "license"):
        if not spec.get(key):
            print(f"  (skipping {p_dir.name}: pack.json has no \"{key}\")")
            return None
    return {"dir": p_dir, "spec": spec, "source": source}


def discover_packs(extra_roots=()):
    """Every pack in packs/ next to this Provisioner (folders and zips),
    plus any extra folders given."""
    found, seen = [], set()
    for root in [PACKS_DIR] + [Path(r) for r in extra_roots]:
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            pack = load_pack(p)
            if pack is None:
                continue
            key = (pack["spec"]["name"], pack["spec"]["version"])
            if key in seen:
                continue        # a folder and its own zip: list once
            seen.add(key)
            found.append(pack)
    return found


def check_pack(pack, fw_api):
    """(refusal_or_None, warnings) for a pack against this firmware."""
    spec, d = pack["spec"], pack["dir"]
    warnings = []
    need = int(spec.get("stump_api", 1))
    if need > fw_api:
        return ("needs plugin API %d; this firmware has %s" % (need, fw_api or "none (too old)")), []
    for name in spec.get("plugins", []):
        pd = d / name
        if not (pd / "install.py").is_file():
            return ("plugin folder %s/ has no install.py" % name), []
        if name in ("urns", "lib", "peripherals", "web", "docs", "third_party", "tools_payload"):
            return ("a plugin can't be called %s" % name), []
        problems, unhashed = verify_plugin(pd)
        for rel, why in problems:
            warnings.append("%s: %s" % (rel, why))
        if unhashed:
            warnings.append("%d file(s) in %s/ have no hash (the author hasn't run --hash-pack)" % (len(unhashed), name))
        want = (spec.get("plugin_hashes") or {}).get(name)
        if want and _sha16(pd / "plugin.json") != want:
            warnings.append("%s/plugin.json changed since the pack was hashed" % name)
        for f, n, why in lint_plugin(pd):
            warnings.append("%s line %d: %s" % (f, n, why))
    hashes = spec.get("hashes") or {}
    for rel in _pack_files(d, spec):
        if rel in hashes and _sha16(d / rel) != hashes[rel]:
            warnings.append("%s: changed since it was hashed" % rel)
    return None, warnings


def describe_pack(pack, fw_api, files_listed=True):
    """Prints what a technician should see before installing a pack.
    files_listed=False (the wizard) says whether the files match the
    pack's hashes instead of listing each one (--list-packs does)."""
    spec, d = pack["spec"], pack["dir"]
    print(f"  {bold(spec['name'])} {spec['version']}  —  by {spec['author']}, {spec['license']} licence")
    if spec.get("description"):
        for line in _wrap(spec["description"], 68):
            print(f"    {line}")
    if spec.get("credits"):
        for i, line in enumerate(_wrap("Credits: " + spec["credits"], 68)):
            print(f"    {line}")
    bits = []
    if spec.get("plugins"):
        bits.append("plugins: " + ", ".join(spec["plugins"]))
    if spec.get("theme"):
        bits.append("theme: " + spec["theme"].get("name", "?"))
    if spec.get("persona"):
        bits.append("bot persona")
    extra = _pack_files(d, spec)
    if extra:
        bits.append("%d file(s) for the SD card" % len(extra))
    if spec.get("ram_kb"):
        bits.append("~%s KB RAM" % spec["ram_kb"])
    if bits:
        print("    " + " · ".join(bits))
    refusal, warnings = check_pack(pack, fw_api)
    files = []
    for name in spec.get("plugins", []):
        files += [f for f in plugin_files(d / name) if _belongs_on_board(f)]
    if files_listed:
        for rel in files:
            print(f"      {rel}  {_sha16(d / rel)}")
        for rel in extra:
            print(f"      {rel}  {_sha16(d / rel)}  (SD card)")
    elif not any("changed since" in w for w in warnings):
        print("    " + green(_glyphs()["done"]) + " its %d file(s) match the pack's hashes"
              " (--list-packs lists them)" % (len(files) + len(extra)))
    if refusal:
        print("    " + yellow("CAN'T INSTALL: " + refusal))
    for w in warnings:
        print("    " + yellow("NOTE: " + w))
    return refusal, warnings


def choose_packs(fw_dir, packs=None):
    """Lists the packs found and asks which to install. Returns the
    chosen ones (possibly none)."""
    packs = discover_packs() if packs is None else packs
    if not packs:
        return []
    fw_api = firmware_plugin_api(fw_dir)
    banner("PACKS")
    print("A pack adds plugins (extra chat commands and pages), and sometimes a")
    print("theme, a bot persona and files for the SD card. A pack is code with")
    print("full access to the node: install only packs from people you trust.\n")
    chosen = []
    for pack in packs:
        refusal, warnings = describe_pack(pack, fw_api, files_listed=False)
        if refusal:
            print()
            continue
        if ask_yes_no("    Install %s?" % pack["spec"]["name"], False):
            if warnings and not ask_yes_no("    It has the notes above. Install anyway?", False):
                print()
                continue
            chosen.append(pack)
        print()
    return chosen


def _pack_plugin_specs(packs):
    out = []
    for pack in packs or []:
        for name in pack["spec"].get("plugins", []):
            out.append((name, _plugin_spec(pack["dir"] / name)))
    return out


def _sd_push(port, src, dest, timeout=None):
    """One file onto the node's SD card (mount and copy in one session)."""
    mount = "import fserv\ntry:\n fserv.mount_sd()\nexcept Exception as e:\n print('mount failed:', e)\n"
    if _node_has_same_file(port, mount, dest.lstrip(":"), src):
        return True, "unchanged"
    size = Path(src).stat().st_size
    ok, out = run(["mpremote", "connect", port, "exec", mount, "fs", "cp", str(src), dest],
                  timeout=timeout or (120 + size // 800))
    return ok, out


def _merge_board_themes(port, themes):
    """Adds pack themes to the board's themes.json (read by theme.py)."""
    ok, out = run(["mpremote", "connect", port, "fs", "cat", ":themes.json"], timeout=20)
    current = {}
    if ok:
        try:
            start = out.index("{")
            current = json.loads(out[start:out.rindex("}") + 1])
        except Exception:
            current = {}
    for t in themes:
        name = t.get("name")
        if name:
            current[name] = {"label": t.get("label", name), "colors": t.get("colors", {})}
    tmp = Path(tempfile.mkdtemp()) / "themes.json"
    tmp.write_text(json.dumps(current, ensure_ascii=False))
    ok, out = run(["mpremote", "connect", port, "fs", "cp", str(tmp), ":themes.json"], timeout=30)
    return ok


def upload_plugin_folder(port, plugin_dir):
    """Copies one plugin's board files (plugin.json "files", minus
    plugin.json and READMEs) to the node. True if all arrived intact."""
    d = Path(plugin_dir)
    files = [f for f in plugin_files(d) if _belongs_on_board(f)]
    dirs = sorted({str(Path(f).parent) for f in files}, key=lambda s: s.count("/"))
    for sub in dirs:
        run(["mpremote", "connect", port, "fs", "mkdir", f":{sub}"], timeout=15)
    ok_all = True
    for rel in files:
        print(f"    {rel} ...", end=" ", flush=True)
        ok, out = run(["mpremote", "connect", port, "fs", "cp", str(d.parent / rel), f":{rel}"],
                      timeout=_cp_timeout(d.parent / rel))
        print("ok" if ok else "FAILED: " + out.strip()[-70:])
        ok_all = ok_all and ok
    sizes = _get_device_file_sizes(port)
    if sizes is not None:
        problems = _diff_against_device(d.parent, files, sizes)
        for rel, why in problems:
            print(f"    {rel}: {why}")
        ok_all = ok_all and not problems
    return ok_all


def install_packs(port, packs):
    """Puts the chosen packs on the node: their plugins on its flash,
    tools/ and home/ on its SD card, their themes into themes.json."""
    if not packs:
        return True
    banner("INSTALLING PACKS")
    ok_all, themes = True, []
    for pack in packs:
        spec, d = pack["spec"], pack["dir"]
        print(f"  {spec['name']} {spec['version']}")
        for name in spec.get("plugins", []):
            ok_all = upload_plugin_folder(port, d / name) and ok_all
        # A pack's tools/ become its own section of the Tools page,
        # tools/<pack name>/ (tools/<folder>/ for files the pack keeps in
        # a folder of tools/); home/ goes to home/ as it is.
        made = set()
        for rel in _pack_files(d, spec):
            sub, fname = rel.split("/", 1)
            if sub == "tools":
                if "/" in fname:
                    sec, fname = fname.split("/", 1)
                    fname = fname.replace("/", "_")
                else:
                    sec = spec["name"]
                sub = "tools/" + sec
            size = (d / rel).stat().st_size
            if size > APP_USB_MAX:
                # ~1 KB/s over USB: a 30 MB installer would take hours.
                print(f"    {rel} ({size // 1000000} MB): too big for USB -- copy it to the card's")
                print(f"      {sub}/ folder with a card reader")
                continue
            print(f"    {rel} -> SD card {sub}/ ...", end=" ", flush=True)
            if sub not in made:
                run(["mpremote", "connect", port, "exec",
                     "import fserv\nfserv.mount_sd()\nimport os\n"
                     "for d in %r:\n try:\n  os.mkdir(d)\n except OSError:\n  pass\n"
                     % (["/sd/tools", "/sd/" + sub] if sub.startswith("tools/") else ["/sd/" + sub],)],
                    timeout=25)
                made.add(sub)
            ok, out = _sd_push(port, d / rel, f":/sd/{sub}/{fname}")
            print("ok" if ok else "FAILED: " + str(out).strip()[-70:])
            ok_all = ok_all and ok
        if spec.get("theme"):
            themes.append(spec["theme"])
    if themes:
        print("  themes:", ", ".join(t.get("name", "?") for t in themes), "...",
              "ok" if _merge_board_themes(port, themes) else "FAILED")
    print("\n  Restart the node to start them (power-cycle, or Ctrl+D in mpremote).")
    return ok_all


def remove_plugin(port, name):
    """Deletes a plugin folder from the node (its data on the SD card,
    /sd/plugins/<name>/, stays)."""
    if name in ("urns", "lib", "peripherals", "web") or "/" in name or not name:
        print("That isn't a plugin.")
        return False
    code = ("import os\n"
            "def rm(p):\n"
            " try:\n"
            "  for n in os.listdir(p):\n"
            "   rm(p + '/' + n)\n"
            "  os.rmdir(p)\n"
            " except OSError:\n"
            "  os.remove(p)\n"
            "try:\n"
            " os.stat(%r + '/install.py')\n"
            " rm(%r)\n"
            " print('REMOVED')\n"
            "except OSError:\n"
            " print('NOPLUGIN')\n") % (name, name)
    ok, out = run(["mpremote", "connect", port, "exec", code], timeout=60)
    if "REMOVED" in out:
        print(f"Removed {name}. Its saved data, if any, stays in /sd/plugins/{name}/.")
        return True
    print(f"No plugin called {name} on the node." if "NOPLUGIN" in out else out.strip())
    return False


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

# ── The terminal layout ──────────────────────────────────────────────
# Every run follows a ROUTE: numbered sections shown up front on a card
# (what each does, what it needs), then each section opens with a header
# and a breadcrumb of where you are, and the run ends on a summary of
# what happened and what to remember. Inside a section, banner() is a
# sub-heading; outside one, a heading of its own.

def _width():
    try:
        cols = shutil.get_terminal_size((80, 24)).columns
    except Exception:
        cols = 80
    return max(50, min(76, cols - 2))


def _glyphs():
    if _fancy():
        return {"tl": "╭", "tr": "╮", "bl": "╰", "br": "╯", "h": "─", "v": "│", "H": "━",
                "done": "✓", "fail": "✗", "skip": "–", "now": "●", "todo": "○", "sep": "  "}
    return {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|", "H": "=",
            "done": "+", "fail": "X", "skip": "-", "now": "*", "todo": "o", "sep": "  "}


def _visible_len(text):
    import re
    return len(re.sub(r"\033\[[0-9;]*m", "", text))


def box(title, lines):
    """A rounded box with a title, the lines wrapped to the width."""
    g, w = _glyphs(), _width()
    inner = w - 4
    head = g["h"] + " " + title + " "
    print("\n" + g["tl"] + bold(head) + g["h"] * max(0, w - 2 - len(head)) + g["tr"])
    for line in lines:
        lead = line[:len(line) - len(line.lstrip())]
        parts = [line]
        if _visible_len(line) > inner and "\033" not in line:
            wrapped = _wrap(line, inner - len(lead) - 2)
            parts = [lead + wrapped[0]] + [lead + "  " + w for w in wrapped[1:]]
        for part in parts:
            print(g["v"] + " " + part + " " * max(0, inner - _visible_len(part)) + " " + g["v"])
    print(g["bl"] + g["h"] * (w - 2) + g["br"])


class _Indented:
    """sys.stdout while a route runs: everything printed inside a section
    is indented under its header, so each section reads as one block."""

    def __init__(self, real):
        self.real, self.on, self.bol = real, False, True

    def write(self, text):
        if self.on and text:
            out = []
            for part in text.splitlines(True):
                if self.bol and part != "\n":
                    out.append("  ")
                out.append(part)
                self.bol = part.endswith("\n")
            self.real.write("".join(out))
        else:
            self.real.write(text)
            if text:
                self.bol = text.endswith("\n")
        return len(text)

    def __getattr__(self, name):
        return getattr(self.real, name)


def _indent(on):
    if not isinstance(sys.stdout, _Indented):
        sys.stdout = _Indented(sys.stdout)
    sys.stdout.on = on


def _in(prompt):
    """input(), indented like the rest of the section. (A terminal prints
    the prompt itself, past sys.stdout, so the indent is added here.)"""
    out = sys.stdout
    if isinstance(out, _Indented) and out.on and out.bol:
        try:
            direct = out.real.isatty() and sys.stdin.isatty()
        except Exception:
            direct = False
        if direct:
            prompt = "  " + prompt
    return input(prompt)


TIMINGS_FILE = Path.home() / ".provisioner" / "timings.json"

# First guesses at how long each section takes, in seconds, until this
# computer has timed its own (TIMINGS_FILE): the route card shows the
# median of the last runs here once there are some.
ESTIMATES = {
    "cam": {"Check": 20, "Board": 20, "Questions": 300, "Flash": 180, "Firmware": 900,
            "Install": 300, "Verify": 120},          # ~30 min in all, as timed in the field
    "heltec": {"Check": 20, "Board": 20, "Flash": 180, "Configure": 120, "Install": 60, "Verify": 60},
    "heltec_transport": {"Check": 20, "Board": 20, "Flash": 240, "Verify": 60},
    "pc": {"Folder": 15, "Python": 90, "Firmware": 20, "Configure": 300, "Packs": 30,
           "Chain": 30, "Start": 60},
}


def _load_timings():
    try:
        return json.loads(TIMINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_timing(key, step, seconds):
    t = _load_timings()
    runs = t.setdefault(key, {}).setdefault(step, [])
    runs.append(round(seconds))
    del runs[:-5]                             # the last five runs
    try:
        TIMINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TIMINGS_FILE.write_text(json.dumps(t, indent=1))
    except OSError:
        pass


def _span(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return "%d s" % max(1, seconds)
    if seconds < 3600:
        return "%d min" % round(seconds / 60.0)
    return "%d h %02d" % (seconds // 3600, (seconds % 3600) // 60)


def _sections(nums):
    """[1, 2, 3, 5] -> "sections 1-3 and 5"."""
    runs, start = [], None
    for i, n in enumerate(nums):
        if start is None:
            start = n
        if i + 1 == len(nums) or nums[i + 1] != n + 1:
            runs.append(str(start) if start == n else "%d-%d" % (start, n))
            start = None
    if len(runs) == 1:
        return ("section " if "-" not in runs[0] else "sections ") + runs[0]
    return "sections " + ", ".join(runs[:-1]) + " and " + runs[-1]


def keep_awake():
    """Keeps this computer from going to sleep until the Provisioner ends
    (a sleeping computer cuts the USB copy halfway). True if it could."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["caffeinate", "-im", "-w", str(os.getpid())],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if sys.platform.startswith("win"):
            import ctypes
            ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            return True
    except Exception:
        pass
    return False


class Route:
    """The numbered sections of one run (see the comment above)."""
    current = None

    def __init__(self, title, steps, needs=(), subtitle="", key=None, why=()):
        self.title, self.subtitle, self.key = title, subtitle, key
        self.why = list(why)                  # lines saying where the time goes
        # [(name, what)] or [(name, who, what)], who: "you" (it asks you
        # things) or "auto" (it runs by itself).
        self.who = {}
        flat = []
        for st in steps:
            if len(st) == 3:
                self.who[st[0]] = st[1]
                flat.append((st[0], st[2]))
            else:
                flat.append(st)
        steps = flat
        self.steps = list(steps)              # [(name, what)]
        self.needs = list(needs)
        self.state = ["todo"] * len(self.steps)
        self.notes = []                       # [(label, value)] for the summary
        self.index = -1
        self.t0 = [None] * len(self.steps)
        self.took = [None] * len(self.steps)
        self.started = time.time()

    def expected(self, name):
        """(seconds, measured?) for a section: this computer's median of
        its last runs, else the first guess."""
        runs = _load_timings().get(self.key or "", {}).get(name) or []
        if runs:
            return sorted(runs)[len(runs) // 2], True
        return (ESTIMATES.get(self.key or "", {}).get(name), False)

    def show(self):
        g = _glyphs()
        lines = [self.subtitle, ""] if self.subtitle else []
        width = max(len(n) for n, _ in self.steps)
        whow = 6 if self.who else 0
        room = _width() - 4 - (width + 6) - whow - 9
        total, measured, you_s, auto_s = 0, False, 0, 0
        for i, (name, what) in enumerate(self.steps, 1):
            secs, real = self.expected(name)
            total += secs or 0
            measured = measured or real
            if self.who.get(name) == "you":
                you_s += secs or 0
            elif self.who:
                auto_s += secs or 0
            when = ("~" + _span(secs)) if secs else ""
            who = {"you": "you", "auto": "auto"}.get(self.who.get(name), "") if self.who else ""
            parts = _wrap(what, room) or [""]
            lines.append("%2d  %s  %s%s %s" % (i, bold(name.ljust(width)), who.ljust(whow),
                                              parts[0].ljust(room), when.rjust(8)))
            lines += [" " * (width + 6 + whow) + p for p in parts[1:]]
        if total:
            lines += ["", "About %s in all (%s)." % (_span(total), "as timed on this computer" if measured
                                                    else "a first guess, until this computer has timed a run")]
            if self.who:
                yours = [i for i, (n, _) in enumerate(self.steps, 1) if self.who.get(n) == "you"]
                lines.append("you  = it asks you things: %s, about %s (as long as you take)"
                             % (_sections(yours), _span(you_s)))
                lines.append("auto = it runs by itself: about %s%s" % (
                    _span(auto_s), ", you can step away" if auto_s >= 300 else ""))
        for para in self.why:
            lines += [""] + _wrap(para, _width() - 4)
        if self.needs:
            lines += ["", "You'll need:"] + ["  " + g["now"] + " " + n for n in self.needs]
        _indent(False)
        box(self.title, lines)
        Route.current = self

    def _crumbs(self):
        g = _glyphs()
        marks = {"done": green(g["done"]), "fail": red(g["fail"]), "skip": g["skip"],
                 "now": cyan(g["now"]), "todo": g["todo"]}
        parts = []
        for (name, _), st in zip(self.steps, self.state):
            label = bold(name) if st == "now" else name
            parts.append(marks[st] + " " + label)
        line, out = "", []
        for part in parts:
            add = (g["sep"] if line else "") + part
            if line and _visible_len(line + add) > _width() - 2:
                out.append(line)
                line = part
            else:
                line += add
        return out + [line]

    def start(self, name):
        """Opens the section called `name`."""
        g = _glyphs()
        i = [n for n, _ in self.steps].index(name)
        if 0 <= self.index < len(self.state) and self.state[self.index] == "now":
            self.state[self.index] = "done"
        self.index = i
        self.state[i] = "now"
        self.t0[i] = time.time()
        head = "%s %d/%d  %s " % (g["H"] * 3, i + 1, len(self.steps), name.upper())
        _indent(False)
        print("\n\n" + bold(head + g["H"] * max(0, _width() - len(head))))
        for line in self._crumbs():
            print("  " + line)
        print("  " + self.steps[i][1])
        _indent(True)

    def end(self, state="done", why=""):
        """Closes the current section: done, fail or skip."""
        g = _glyphs()
        if self.index < 0:
            return
        self.state[self.index] = state
        name = self.steps[self.index][0]
        took = None
        if self.t0[self.index]:
            took = self.took[self.index] = time.time() - self.t0[self.index]
            if state == "done" and self.key:
                _save_timing(self.key, name, took)
        mark = {"done": green(g["done"]), "fail": red(g["fail"]), "skip": g["skip"]}[state]
        word = {"done": "done", "fail": "didn't work", "skip": "skipped"}[state]
        print("\n%s %s: %s%s%s" % (mark, name, word, (" in " + _span(took)) if took and state == "done" else "",
                                   (" -- " + why) if why else ""))

    def skip_rest(self, why=""):
        for i in range(len(self.state)):
            if self.state[i] in ("todo", "now"):
                self.state[i] = "skip"

    def note(self, label, value):
        self.notes.append((label, value))

    def summary(self, closing=""):
        g = _glyphs()
        if 0 <= self.index < len(self.state) and self.state[self.index] == "now":
            self.state[self.index] = "done"
        marks = {"done": green(g["done"]), "fail": red(g["fail"]), "skip": g["skip"], "todo": g["skip"]}
        words = {"done": "done", "fail": red("didn't work"), "skip": "skipped", "todo": "not reached"}
        if 0 <= self.index < len(self.state) and self.t0[self.index] and self.took[self.index] is None:
            self.took[self.index] = time.time() - self.t0[self.index]
        width = max(len(n) for n, _ in self.steps)
        lines = ["%s  %s  %s%s" % (marks.get(st, "?"), name.ljust(width), words.get(st, st),
                                   ("  (%s)" % _span(t)) if t and st in ("done", "fail") else "")
                 for (name, _), st, t in zip(self.steps, self.state, self.took)]
        lines += ["", "Took %s in all." % _span(time.time() - self.started)]
        if self.notes:
            lw = max(len(l) for l, _ in self.notes)
            room = _width() - 4 - lw - 2
            lines.append("")
            for label, value in self.notes:
                value = str(value)
                parts = [value] if "\033" in value or len(value) <= room else \
                    [value[k:k + room] for k in range(0, len(value), room)]
                lines.append("%s  %s" % (bold(label.ljust(lw)), parts[0]))
                lines += [" " * (lw + 2) + p for p in parts[1:]]
        if closing:
            lines += ["", closing]
        _indent(False)
        box(self.title + " -- summary", lines)
        Route.current = None
        return all(st != "fail" for st in self.state)


def banner(text):
    """A heading: a sub-heading inside a route's section, else a heading."""
    g = _glyphs()
    if Route.current is not None and Route.current.index >= 0:
        head, sep, rest = text.partition(" — ")
        if head.isupper():
            head = head.capitalize()
        print("\n" + cyan(g["h"] * 2 + " " + head + sep + rest + " " + g["h"] * 2))
        return
    head = g["H"] * 3 + " " + text + " "
    print("\n" + bold(head + g["H"] * max(0, _width() - len(head))))


def subhead(text):
    """A topic inside a section (the config questions)."""
    g = _glyphs()
    print("\n" + cyan(g["h"] * 2 + " " + text + " " + g["h"] * 2))


# Color/bold terminal output for exactly the things a technician needs
# to walk away remembering -- IP addresses, the certification verdict,
# the manual-check instruction below -- not decoration everywhere.
# Added on request, directly prompted by a real mixup this session:
# an IP address technicians needed to reference again later (after a
# router reboot reassigned it) got lost in a wall of plain text the
# same way any of the surrounding print() output would. Gated on
# isatty() -- these are raw ANSI escape codes, meaningless and
# actively ugly if this output is ever piped to a file or a log
# rather than read directly in a terminal, so they're only emitted
# when there's an actual terminal on the other end to render them.
def _tty():
    return sys.stdout.isatty()


def _ansi(text, code):
    return f"\033[{code}m{text}\033[0m" if _tty() else text


def bold(text):
    return _ansi(text, "1")


def green(text):
    return _ansi(text, "1;32")


def yellow(text):
    return _ansi(text, "1;33")


def red(text):
    return _ansi(text, "1;31")


def cyan(text):
    return _ansi(text, "1;36")


def ask(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    val = _in(f"{prompt}{suffix}: ").strip()
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
        raw = _in(f"{prompt} [{hint}]: ").strip().lower()
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
        raw = _in(f"Choose 1-{len(options)}: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("  invalid choice, try again")


# ── Wi-Fi networks around this computer ──────────────────────────────
# The wizard offers the networks in range instead of making you type a
# name (and get one letter wrong). Each operating system lists them its
# own way; when the list can't be had (no Wi-Fi, permissions), the
# question is simply asked as before.

WIFI_SCAN = {}          # ssid -> {"signal": 0-100, "open": bool}, from the last scan


def _wifi_pct(dbm):
    """dBm -> 0-100, as the other systems report it."""
    return max(0, min(100, 2 * (int(dbm) + 100)))


def parse_netsh(text):
    """Windows: `netsh wlan show networks mode=bssid` (English or French)."""
    import re
    nets, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^SSID \d+\s*:\s*(.*)$", line)
        if m:
            cur = {"ssid": m.group(1).strip(), "signal": 0, "open": False}
            nets.append(cur)
            continue
        if cur is None or ":" not in line:
            continue
        key, val = [x.strip() for x in line.split(":", 1)]
        k = key.lower()
        if k.startswith("auth"):
            cur["open"] = val.lower() in ("open", "ouvrir", "ouvert", "abierta")
        elif k.startswith("signal") or k.startswith("señal"):
            m = re.search(r"(\d+)\s*%", val)
            if m:
                cur["signal"] = max(cur["signal"], int(m.group(1)))
    return nets


def parse_nmcli(text):
    """Linux: `nmcli -t -f SSID,SIGNAL,SECURITY device wifi list`
    (colons inside a name are written \\:)."""
    import re
    nets = []
    for line in text.splitlines():
        parts = [x.replace("\\:", ":").replace("\\\\", "\\") for x in re.split(r"(?<!\\):", line)]
        if len(parts) < 3:
            continue
        try:
            sig = int(parts[1])
        except ValueError:
            sig = 0
        nets.append({"ssid": parts[0], "signal": sig, "open": parts[2].strip() in ("", "--")})
    return nets


def parse_system_profiler(text):
    """macOS: `system_profiler SPAirPortDataType -json`."""
    import json
    import re
    nets = []
    try:
        data = json.loads(text)
    except ValueError:
        return nets
    for top in data.get("SPAirPortDataType", []):
        for iface in top.get("spairport_airport_interfaces", []):
            found = list(iface.get("spairport_airport_other_local_wireless_networks", []))
            cur = iface.get("spairport_current_network_information")
            if isinstance(cur, dict):
                found.append(cur)
            for n in found:
                m = re.match(r"\s*(-?\d+)", str(n.get("spairport_signal_noise", "")))
                sec = str(n.get("spairport_security_mode", "")).lower()
                nets.append({"ssid": n.get("_name", ""),
                             "signal": _wifi_pct(m.group(1)) if m else 0,
                             "open": sec.endswith("_none") or sec in ("", "none")})
    return nets


def parse_mac_ports(text):
    """`networksetup -listallhardwareports`: the Wi-Fi device (en0...)."""
    port = None
    for line in text.splitlines():
        if line.startswith("Hardware Port:"):
            port = line.split(":", 1)[1].strip()
        elif line.startswith("Device:") and port in ("Wi-Fi", "AirPort"):
            return line.split(":", 1)[1].strip()
    return None


def parse_mac_preferred(text):
    """`networksetup -listpreferredwirelessnetworks en0`: the names, in order."""
    return [l.strip() for l in text.splitlines()[1:] if l.startswith(("\t", " ")) and l.strip()]


def mac_saved_networks(run_cmd=None):
    """The Wi-Fi networks this Mac has joined before. macOS lets the
    Terminal list those without Location access (it hides the names of the
    networks around unless the Terminal has it, and never asks)."""
    import subprocess

    def out(cmd):
        if run_cmd:
            return run_cmd(cmd)
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
    dev = parse_mac_ports(out(["networksetup", "-listallhardwareports"])) or "en0"
    return parse_mac_preferred(out(["networksetup", "-listpreferredwirelessnetworks", dev]))


def scan_wifi():
    """The Wi-Fi networks this computer hears, strongest first:
    [{"ssid", "signal", "open"}]. ([], why) when it can't tell."""
    import subprocess
    if sys.platform == "darwin":
        cmd, parse = ["system_profiler", "SPAirPortDataType", "-json"], parse_system_profiler
    elif sys.platform.startswith("win"):
        cmd, parse = ["netsh", "wlan", "show", "networks", "mode=bssid"], parse_netsh
    else:
        cmd, parse = ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"], parse_nmcli
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30)
        try:
            out = r.stdout.decode("utf-8")
        except UnicodeDecodeError:          # Windows console tools write the OEM code page
            out = r.stdout.decode("oem" if sys.platform.startswith("win") else "latin-1", "replace")
    except (OSError, subprocess.SubprocessError) as e:
        return [], "couldn't list Wi-Fi networks (%s)" % e
    best = {}
    hidden = 0
    for n in parse(out):
        name = (n.get("ssid") or "").strip()
        if not name or name.lower() in ("<redacted>", "<hidden>"):
            hidden += bool(name)
            continue
        if name not in best or n["signal"] > best[name]["signal"]:
            best[name] = n
    nets = sorted(best.values(), key=lambda n: -n["signal"])
    if not nets and sys.platform == "darwin":
        saved = mac_saved_networks()
        if saved:
            nets = [{"ssid": n, "signal": -1, "open": False, "saved": True} for n in saved]
            for n in nets:
                WIFI_SCAN.setdefault(n["ssid"], n)
            return nets, ("macOS hides the networks around from the Terminal, and doesn't ask: "
                          "these are the ones this Mac has joined before")
    if not nets:
        if hidden:
            return [], ("this system hides Wi-Fi names from the Terminal (recent macOS does): "
                        "type the name as your Wi-Fi menu shows it")
        if sys.platform.startswith("win") and "location" in out.lower():
            return [], ("Windows lists Wi-Fi networks only with Location on "
                        "(Settings > Privacy & security > Location)")
        return [], "no Wi-Fi networks heard from this computer"
    for n in nets:
        WIFI_SCAN[n["ssid"]] = n
    return nets, ""


def _fancy():
    return "utf" in (getattr(sys.stdout, "encoding", "") or "").lower()


def _bars(signal):
    n = max(1, min(4, (signal + 24) // 25))
    return ("▂▄▆█"[:n] if _fancy() else "|" * n).ljust(4)


def ask_wifi(prompt, default="", scan=None):
    """Asks for a Wi-Fi name, offering the networks in range. Typing a
    name (or a blank for the default) still works at the same prompt."""
    scan = scan or scan_wifi
    while True:
        print("%s -- looking for Wi-Fi networks around this computer..." % prompt)
        nets, why = scan()
        if not nets:
            print("  (%s)" % why)
            return ask("%s, typed in" % prompt, default)
        nets = nets[:20]
        if why:
            print("  (%s)" % why)
        for i, n in enumerate(nets, 1):
            if n.get("saved"):
                print("  %2d) %s" % (i, n["ssid"]))
                continue
            print("  %2d) %s %s%s" % (i, _bars(n["signal"]), n["ssid"], "" if n["open"] else ("  🔒" if _fancy() else "  (password)")))
        print("   r) look again     or type a name that isn't listed")
        raw = _in("Choose 1-%d%s: " % (len(nets), " [%s]" % default if default else "")).strip()
        if raw.lower() == "r":
            continue
        if not raw:
            if default:
                return default
            print("  pick a number, or type the name")
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(nets):
            return nets[int(raw) - 1]["ssid"]
        return raw


def wifi_is_open(ssid):
    """True when the last scan saw this network without a password."""
    return bool(WIFI_SCAN.get(ssid, {}).get("open"))


def check_cert(cert_path, key_path, host=None):
    """Checks a certificate and key on this computer before they go near a
    board. Returns (info, problems, warnings): info = {"host", "not_after"}."""
    import ssl, time as _time
    problems, warnings = [], []
    try:
        pem = Path(cert_path).read_text()
        key = Path(key_path).read_text()
    except OSError as e:
        return None, ["can't read the files: %s" % e], []
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_path, key_path)       # also proves the key matches
    except ssl.SSLError as e:
        return None, ["the key doesn't match the certificate, or a file isn't PEM: %s" % e], []
    try:
        d = ssl._ssl._test_decode_cert(cert_path)       # first certificate in the file
    except Exception as e:
        return None, ["can't read the certificate: %s" % e], []
    names = [v for k, v in d.get("subjectAltName", ()) if k == "DNS"]
    not_after = int(ssl.cert_time_to_seconds(d["notAfter"]))
    days = (not_after - int(_time.time())) // 86400
    if host is None:
        exact = [n for n in names if not n.startswith("*.")]
        if not exact:
            problems.append("the certificate only has wildcard names (%s): add --host stump.example.app"
                            % ", ".join(names))
        else:
            host = exact[0]
    elif not any(n == host or (n.startswith("*.") and host.endswith(n[1:]) and host.count(".") == n.count("."))
                 for n in names):
        problems.append("%s isn't one of the certificate's names (%s)" % (host, ", ".join(names)))
    if days < 0:
        problems.append("the certificate expired %d days ago" % -days)
    elif days < 21:
        warnings.append("the certificate expires in %d days -- renew soon" % days)
    if pem.count("BEGIN CERTIFICATE") < 2:
        warnings.append("only one certificate in the file: use fullchain.pem (with the intermediate), "
                        "not cert.pem -- phones often reject a chain without it")
    if "BEGIN EC PRIVATE KEY" not in key and "EC" not in key.split("\n")[0]:
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            from cryptography.hazmat.primitives.asymmetric import ec
            if not isinstance(load_pem_private_key(key.encode(), None), ec.EllipticCurvePrivateKey):
                warnings.append("RSA key: ECDSA (certbot --key-type ecdsa) makes each HTTPS connection "
                                "much faster on the ESP32")
        except Exception:
            pass
    return {"host": host, "not_after": not_after}, problems, warnings


def install_cert(port, cert_path, key_path, host=None):
    """--install-cert: checks the certificate here, then copies it to the
    board's /tls/ (internal flash). The node uses it from its next boot."""
    print(bold("\nInstalling HTTPS certificate"))
    info, problems, warnings = check_cert(cert_path, key_path, host)
    for w in warnings:
        print(yellow("  warning: " + w))
    if problems:
        for p in problems:
            print("  " + p)
        print("Nothing was copied to the board.")
        return False
    import time as _time
    print("  host: %s | valid until %s (%d days)" % (
        info["host"], _time.strftime("%Y-%m-%d", _time.gmtime(info["not_after"])),
        (info["not_after"] - int(_time.time())) // 86400))
    with tempfile.TemporaryDirectory() as tmp:
        info_path = Path(tmp) / "info.json"
        info_path.write_text(json.dumps(info))
        run(["mpremote", "connect", port, "fs", "mkdir", ":/tls"], timeout=15)
        for src, dst in ((cert_path, ":/tls/fullchain.pem"), (key_path, ":/tls/privkey.pem"),
                         (str(info_path), ":/tls/info.json")):
            ok, out = run(["mpremote", "connect", port, "fs", "cp", str(src), dst], timeout=60)
            if not ok:
                print("  FAILED copying %s: %s" % (src, out))
                return False
    print(green("  Installed. Reboot the node; its boot log should say \"[web] HTTPS on 443 for %s\"." % info["host"]))
    return True


# Before every command to a board over USB: its watchdog (keepalive.py,
# 1.0.0-beta.26+) given 2 hours. A Stump stopped at the console disarms its
# own, but a long session (the Tools copy: up to half an hour) must not
# depend on that -- a reset mid-copy is "unexpected read during raw paste".
# Left at the console afterwards, the board starts the Stump by itself
# when the 2 hours are up. Harmless on a board without one (or where 2
# hours is too long: caught).
QUIET_WATCHDOG = ("try:\n import machine\n machine.WDT(timeout=7200000).feed()\n"
                  "except Exception:\n pass")


def _with_quiet_watchdog(cmd):
    if (len(cmd) > 3 and cmd[0] == "mpremote" and cmd[1] == "connect"
            and cmd[3] not in ("reset", "soft-reset", "repl", "bootloader")):
        return cmd[:3] + ["exec", QUIET_WATCHDOG] + cmd[3:]
    return cmd


def run(cmd, **kwargs):
    """Run a subprocess, always returning (ok, stdout+stderr) instead of raising."""
    cmd = _with_quiet_watchdog(cmd)
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
                    if name.startswith(("ttyUSB", "ttyACM", "cu.usb", "cu.SLAB", "cu.wchusbserial")):
                        dev = f"/dev/{name}"
                        # No USB ids without pyserial: the port name still tells a
                        # UART bridge (a Heltec's CP2102) from native USB (a CAM).
                        candidates.append({"device": dev, "description": "(pyserial not installed — no VID/PID info)",
                                           "guess": _guess_board(None, None, dev.replace("SLAB_USBtoUART", "usbserial"), "")})
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


def choose_board(role=None):
    """Picks the board's port. With the role already known (the start
    menu), returns the port alone; without, (port, board_type)."""
    ports = scan_boards()
    if not ports:
        manual = ask("No boards auto-detected. Enter a serial port path manually (blank to abort)")
        if not manual:
            return None if role else (None, None)
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

    if role:
        named = {"heltec": "Heltec", "heltec_transport": "Heltec", "cam": "CAM"}[role]
        if guess and guess != "unknown board" and named.lower() not in guess.lower():
            print(yellow("  That doesn't look like a %s. Flashing the wrong board is destructive." % named))
            if not ask_yes_no("  Use this port anyway?", False):
                return None
        return chosen_device

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
MANIFEST_EXCLUDE = {"boot_fail_count", "plugins_state.json", "plugins_loading"}
MANIFEST_EXCLUDE_SUFFIXES = (".pyc", ".bak", ".orig", ".swp", ".DS_Store")


def manifest_candidates(fw_dir):
    """Every file that legitimately belongs in the manifest.

    Use this instead of a bare rglob when regenerating hashes, so
    runtime state and stray local files can't be blessed as firmware."""
    out = []
    for f in sorted(Path(fw_dir).rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(fw_dir).as_posix()
        if rel in MANIFEST_EXCLUDE:
            continue
        if rel.split("/")[0] in RUNTIME_STATE_DIRS:
            continue                        # a node's own state (identity!), never firmware
        # A plugin folder's files are hashed in its own plugin.json; the
        # core manifest carries that plugin.json, which vouches for them.
        top = rel.split("/")[0]
        if "/" in rel and (Path(fw_dir) / top / "plugin.json").is_file() and rel != top + "/plugin.json":
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
    # The built-in plugins: each file against its plugin.json's hash (and
    # that plugin.json against the core manifest, just above).
    for name, spec in discover_plugins(resolved):
        problems, unhashed = verify_plugin(Path(resolved) / name, spec)
        stale += [rel for rel, _ in problems] + unhashed
    return stale


def _cp_timeout(path):
    """Seconds to allow one `mpremote fs cp`. A flat 60 s was too short for
    web/codec2.wasm (210 KB): copying to the board runs at roughly
    2-3 KB/s over USB, and the copy stopped at 146 KB. Allow 60 s plus a
    second per KB, which covers a board running at half that speed."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return 60
    return 60 + size // 1000


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
    # The built-in plugins (fservbot, stumpid, and any plugin folder in
    # the firmware tree): uploaded from their own plugin.json file lists.
    for name, spec in discover_plugins(resolved):
        present += [f for f in plugin_files(resolved / name, spec) if _belongs_on_board(f)]

    # An update keeps the node's own settings. config.py on the board is
    # what the config wizard wrote for THIS node (its name, Wi-Fi,
    # passwords, theme...); the one in the firmware folder is someone
    # else's, and copying it over silently changed nodes (a theme, for
    # one, was the visible sign). Older configs are fine: the firmware
    # reads every newer setting with a default. --replace-config copies
    # the folder's config.py anyway; a freshly flashed board has none,
    # so it gets one.
    if "config.py" in present and "--replace-config" not in sys.argv:
        state = _board_config_state(port)
        if state in ("yes", "unknown"):
            present.remove("config.py")
            print("  Keeping this node's own config.py (its settings stay as they are).")
            if state == "yes":
                try:
                    saved = _backup_board_config(port)
                except Exception:
                    saved = None
                if saved:
                    print("  A copy of it is saved on this computer: %s" % saved)
            print("  To replace it with the one in the firmware folder: --replace-config,")
            print("  or run the wizard and answer yes to the config wizard.")
        elif state == "lost":
            # A Stump ran here, yet its settings are gone: never replaced
            # silently with this folder's -- that turned a node's chain and
            # pages off without a word.
            folder_cfg = (resolved / "config.py").read_text(encoding="utf-8", errors="replace")
            backups = sorted(CONFIG_BACKUPS.glob("config-*.py")) if CONFIG_BACKUPS.exists() else []
            print(yellow("  This board ran a Stump, but its config.py is missing."))
            if backups:
                last = backups[-1]
                print("  The last copy saved from a board: %s" % last)
                print("    (%s)" % _config_summary(last.read_text(encoding="utf-8", errors="replace")))
                if ask_yes_no("  Put that copy back on the board?", True):
                    present.remove("config.py")
                    ok, out = run(["mpremote", "connect", port, "fs", "cp", str(last), ":/config.py"], timeout=30)
                    print("    " + ("restored" if ok else "FAILED: " + out.strip()))
            if "config.py" in present:
                print("  This folder's config.py: %s" % _config_summary(folder_cfg))
                if not ask_yes_no("  Use this folder's config.py for this board?", False):
                    present.remove("config.py")
                    print("  Not copied. Set it up with the wizard (python3 provisioner.py) and its config questions.")

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
        size_kb = f.stat().st_size // 1024 if f.exists() else 0
        if size_kb >= 50:
            # The codec takes a minute or two: say so, or it looks hung.
            print(f"  uploading {fname} ({size_kb} KB, may take 1-2 minutes) ...")
        else:
            print(f"  uploading {fname} ...")
        ok, out = run(["mpremote", "connect", port, "fs", "cp", str(f), f":{fname}"], timeout=_cp_timeout(f))
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
                run(["mpremote", "connect", port, "fs", "cp", str(f), f":{fname}"], timeout=_cp_timeout(f))
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


CONFIG_BACKUPS = Path.home() / ".provisioner" / "config_backups"


def _board_config_state(port):
    """"yes" (the board has /config.py), "fresh" (none, and nothing else
    says a Stump ran there), "lost" (none, but a Stump ran there: its
    identity, its start record...), or "unknown" (couldn't tell)."""
    ok, out = run(["mpremote", "connect", port, "exec",
                   "import os\ndef has(p):\n try:\n  os.stat(p)\n  return 1\n except OSError:\n  return 0\n"
                   "print('CONFIG:%d TRACES:%d' % (has('/config.py'), has('/rns') + has('/boot_state.json')"
                   " + has('/boot_fail_count') + has('/plugins_state.json')))\n"], timeout=25)
    import re as _re
    m = _re.search(r"CONFIG:(\d) TRACES:(\d+)", out or "") if ok else None
    if not m:
        return "unknown"
    if m.group(1) == "1":
        return "yes"
    return "lost" if int(m.group(2)) else "fresh"


def _board_has_config(port):
    """True if the board already has a config.py (a node set up before),
    or if that can't be told -- keeping a config is the safe mistake."""
    return _board_config_state(port) != "fresh"


def _backup_board_config(port):
    """A copy of the board's config.py on this computer, before anything
    is uploaded (~/.provisioner/config_backups, the newest 30 kept).
    Returns its path, or None."""
    CONFIG_BACKUPS.mkdir(parents=True, exist_ok=True)
    dest = CONFIG_BACKUPS / ("config-%s.py" % time.strftime("%Y%m%d-%H%M%S"))
    ok, _ = run(["mpremote", "connect", port, "fs", "cp", ":/config.py", str(dest)], timeout=30)
    if not ok or not dest.exists() or dest.stat().st_size == 0:
        try:
            dest.unlink()
        except OSError:
            pass
        return None
    old = sorted(CONFIG_BACKUPS.glob("config-*.py"))
    for f in old[:-30]:
        try:
            f.unlink()
        except OSError:
            pass
    return dest


def _config_summary(text):
    """A line saying what a config.py sets: name, chain, features."""
    import re as _re
    def val(k):
        m = _re.search(r"(?m)^%s\s*=\s*(.+?)\s*(#.*)?$" % k, text)
        return m.group(1) if m else "?"
    return "name %s, chain %s, features %s" % (val("NODE_NAME"), "on" if val("CHAIN_ENABLED") == "True" else "off",
                                               val("FEATURES"))


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
            rel = f.relative_to(source_dir)
            # third_party/ (the Codec 2 sources, ~3 MB) stays in the
            # downloadable project; on the node it only made this copy
            # take ~11 minutes over USB. Caches never belong here.
            if rel.parts and rel.parts[0] in ("third_party",) or "__pycache__" in rel.parts \
                    or rel.parts[:2] == ("tools_payload", "apps"):
                continue
            if f.is_file():
                zf.write(f, arcname=str(Path("final_firmware") / rel))


def _node_has_same_file(port, mount, node_path, local_path):
    """True if the node already holds a byte-identical copy (SHA-256
    computed on the board, read in chunks)."""
    import hashlib
    want = hashlib.sha256(Path(local_path).read_bytes()).hexdigest()
    code = (mount + "import hashlib, binascii\n"
            "try:\n"
            " h = hashlib.sha256()\n"
            " with open(%r, 'rb') as f:\n"
            "  while True:\n"
            "   b = f.read(4096)\n"
            "   if not b: break\n"
            "   h.update(b)\n"
            " print('SHA:' + binascii.hexlify(h.digest()).decode())\n"
            "except OSError:\n"
            " print('SHA:none')\n") % node_path
    ok, out = run(["mpremote", "connect", port, "exec", code], timeout=120)
    if not ok:
        return False
    for line in out.splitlines():
        if line.startswith("SHA:"):
            return line[4:].strip() == want
    return False


APP_USB_MAX = 1000000   # app files larger than this go on the card by card reader


SKIP_TOOLS = False      # set by the CAM route's questions (or --skip-tools)


def install_tools_on_node(port):
    """Copies the technician tools (served on the node's /tools page) to
    its SD card. The slowest part of an upload -- roughly 1 MB at ~1 KB/s
    the first time -- so it says so up front, skips files already on the
    card, can be skipped entirely with --skip-tools, and Ctrl+C stops it
    cleanly instead of with a traceback: the app is already installed by
    the time this runs."""
    if "--skip-tools" in sys.argv or SKIP_TOOLS:
        print("\n  Technician tools: skipped. The node works without them;")
        print("  only its /tools download page is affected.")
        print("  Its sections' READMEs (a few KB) are brought up to date all the same:")
        refresh_tool_readmes(port)
        return
    here = Path(__file__).resolve().parent
    total = 0
    for f in [here / "provisioner.py", here / "README.md", here / "LICENSE"] + \
             [f for f in (here / "final_firmware" / "tools_payload").rglob("*") if f.is_file()]:
        if f.is_file() and not (f.parent.name == "apps" and f.stat().st_size > APP_USB_MAX):
            total += f.stat().st_size
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            z = Path(tmpdir) / "z.zip"
            _build_firmware_zip(z, here / "final_firmware")
            total += z.stat().st_size
    except Exception:
        pass
    print("\n  Technician tools for the node's /tools page: about %d KB to copy to its SD card."
          % (total // 1024))
    print("  Copies to the card are slow (~1 KB/s): up to %d minutes the first time; files"
          % max(1, round(total / 1000 / 60)))
    print("  already there are skipped. The app is installed already -- Ctrl+C here is safe,")
    print("  and --skip-tools skips this step next time.")
    try:
        return _install_tools_on_node(port)
    except KeyboardInterrupt:
        print("\n\n  Stopped. The app is installed and the node works normally; only the")
        print("  technician tools on its SD card are incomplete. Re-run --upload-app later")
        print("  to finish them (files already copied are skipped).")
        return


def _tool_readmes(here):
    """(local README, section folder on the card) for every Tools page
    section this Provisioner knows: the built-in ones and each pack's."""
    out = []
    payload = here / "final_firmware" / "tools_payload"
    for src, sec in (("provisioner", "provisioner"), ("flasher", "flasher"), ("apps", "firefly")):
        out += [(rd, sec) for rd in sorted((payload / src).glob("README*.md"))]
    for pack in discover_packs():
        tools = pack["dir"] / "tools"
        if pack["dir"].is_dir() and tools.is_dir():
            out += [(rd, pack["spec"]["name"]) for rd in sorted(tools.glob("README*.md"))]
    return out


def refresh_tool_readmes(port):
    """Brings the Tools page's READMEs (each section's title, summary and
    Read me, in every language) up to date on the card -- only for the
    sections already there, so it never adds a section of its own. Small
    and quick: run even with --skip-tools, so a translation doesn't wait
    for the slow tools copy. Unchanged ones are skipped."""
    here = Path(__file__).resolve().parent
    readmes = _tool_readmes(here)
    if not readmes:
        return
    ok, out = run(["mpremote", "connect", port, "exec",
                   "import fserv, os\nfserv.mount_sd()\n"
                   "try:\n print('DIRS:' + ','.join(os.listdir('/sd/tools')))\n"
                   "except OSError:\n print('DIRS:')\n"], timeout=30)
    present = set()
    for line in (out or "").splitlines():
        if line.startswith("DIRS:"):
            present = set(x for x in line[5:].strip().split(",") if x)
    done, failed = [], []
    for rd, sec in readmes:
        if sec not in present:
            continue
        ok, why = _sd_push(port, rd, ":/sd/tools/%s/%s" % (sec, rd.name))
        (done if ok else failed).append(sec + "/" + rd.name)
    if done:
        print("    READMEs up to date: " + ", ".join(done))
    for f in failed:
        print("    Could not copy " + f)


def _install_tools_on_node(port):
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

    print("  Copying:")
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

    # The Tools page has a section per folder of tools/, each with its
    # own README.md. Mounting the card also moves tools left loose by
    # older Provisioners into their folders (fserv.migrate_tools), so
    # they're found in place below rather than copied again.
    for d in (":/sd/tools", ":/sd/fw", ":/sd/tools/provisioner", ":/sd/tools/flasher",
              ":/sd/tools/firefly"):
        run(["mpremote", "connect", port, "exec", MOUNT, "fs", "mkdir", d], timeout=25)

    installed, failed, by_card = [], [], []

    def push(src, dest, label, timeout=None):
        """Copies one file to the SD card, saying what it's doing. Copies to
        the card run at roughly 1 KB/s over USB, so a large file takes
        minutes: each one prints its name and size first and its result
        after, and one already on the card byte for byte is skipped."""
        if not Path(src).is_file():
            return
        size = Path(src).stat().st_size
        print("    %s (%d KB) ..." % (label, max(1, size // 1024)), end=" ", flush=True)
        if _node_has_same_file(port, MOUNT, dest.lstrip(":"), src):
            print("unchanged, skipped")
            installed.append(label)
            return
        if timeout is None:
            timeout = 120 + size // 800          # allows down to ~0.8 KB/s
        t0 = time.time()
        # exec + fs cp in ONE invocation so the mount is still live when
        # the copy runs.
        ok, out = run(["mpremote", "connect", port, "exec", MOUNT,
                       "fs", "cp", str(src), dest], timeout=timeout)
        took = int(time.time() - t0)
        if ok:
            print("ok (%dm%02ds)" % (took // 60, took % 60))
            installed.append(label)
        else:
            # run()'s timeout message embeds the whole mpremote command
            # (MOUNT is multi-line), so its "last line" is a fragment of
            # the command, not an error: report timeouts in plain words.
            if out.startswith("command timed out"):
                reason = "timed out after %ds" % timeout
            elif out.strip():
                reason = out.strip().splitlines()[-1][:70]
            else:
                reason = "no output"
            print("FAILED (%s)" % reason)
            failed.append((label, reason))

    if me.is_file():
        push(me, ":/sd/tools/provisioner/provisioner.py", "provisioner.py")

    # Same reasoning as provisioner.py itself: the node hands back
    # exactly the reference doc it was provisioned with, rather than a
    # technician needing to remember to keep a separate copy on hand.
    # README.md sits right next to provisioner.py in every download,
    # same as final_firmware/ does -- best-effort like everything else
    # here, a missing README.md still lets the rest of this function
    # proceed normally.
    readme = here / "README.md"
    if readme.is_file():
        push(readme, ":/sd/tools/provisioner/Stump-README.md", "Stump-README.md")

    # Same reasoning again: whoever ends up with a copy of this build
    # off the node gets the actual license it ships under, not just the
    # code. Named LICENSE (no extension) to match the standard,
    # tool-recognized convention (GitHub, package managers, and license
    # scanners all look for this exact name) rather than LICENSE.md or
    # LICENSE.txt.
    license_file = here / "LICENSE"
    if license_file.is_file():
        push(license_file, ":/sd/tools/provisioner/LICENSE", "LICENSE")

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
                push(zip_path, ":/sd/tools/provisioner/Stump_Beta_A.zip", "Stump_Beta_A.zip")
        except Exception as e:
            failed.append(("Stump_Beta_A.zip", str(e)[:70]))

    payload = here / "final_firmware" / "tools_payload"
    if not payload.is_dir():
        payload = here / "tools_payload"
    if payload.is_dir():
        # Each section's README.md (what the Tools page shows about it).
        # Each in English (README.md) and, when there is one, the visitor's
        # language (README.fr.md, README.es.md).
        for src, sec in (("provisioner", "provisioner"), ("flasher", "flasher"), ("apps", "firefly")):
            for rd in sorted((payload / src).glob("README*.md")):
                push(rd, ":/sd/tools/%s/%s" % (sec, rd.name), sec + "/" + rd.name)
        fl = payload / "flasher"
        push(fl / "esptool-bundle.js", ":/sd/tools/flasher/esptool-bundle.js", "esptool-bundle.js")
        push(fl / "esptool-js-LICENSE.txt", ":/sd/tools/flasher/esptool-js-LICENSE.txt", "licence")
        push(fl / "catalog.json", ":/sd/fw/catalog.json", "catalog.json")
        img_dir = payload / "images"
        if img_dir.is_dir():
            for img in sorted(img_dir.glob("*.bin")):
                push(img, ":/sd/fw/" + img.name, img.name)
        # FireFly apps for the About page and /tools, found there by name
        # (FireFly-Android-<version>.apk, FireFly-RK3326-<version>.zip).
        # Small ones go over USB; big ones never do -- the Android APK is
        # ~50 MB, about 14 hours at USB speed, seconds with a card reader.
        apps_dir = payload / "apps"
        if apps_dir.is_dir():
            for app in sorted(apps_dir.iterdir()):
                if not app.is_file() or (app.name.startswith("README") and app.name.endswith(".md")):
                    continue
                if app.stat().st_size > APP_USB_MAX:
                    print("    %s (%d MB): too big to copy over USB -- put it on the SD card"
                          % (app.name, app.stat().st_size // 1000000))
                    print("      with a card reader, in tools/firefly/ on the card.")
                    by_card.append("tools/firefly/" + app.name)
                    continue
                push(app, ":/sd/tools/firefly/" + app.name, app.name)
    else:
        print("  (no tools_payload folder found next to the firmware --")
        print("   the browser flasher won't be available on this node)")
    # Home-page branding (docs/HOME_BRANDING.md): a "home" folder next to
    # this provisioner goes to home/ on the card, like the tools. The node
    # creates that folder on the card when it mounts it (fserv.mount_sd).
    brand = here / "home"
    if brand.is_dir():
        for f in sorted(brand.iterdir()):
            if not f.is_file():
                continue
            if f.stat().st_size > APP_USB_MAX:
                print("    home/%s (%d MB): too big to copy over USB -- put it in home/ on the SD card"
                      % (f.name, f.stat().st_size // 1000000))
                print("      with a card reader.")
                by_card.append("home/" + f.name)
                continue
            push(f, ":/sd/home/" + f.name, "home/" + f.name)

    # The packs' sections' READMEs too (the built-in ones were pushed above).
    refresh_tool_readmes(port)
    if installed:
        print("  Installed: " + ", ".join(installed))
        print("  Tools at   http://<node>/tools")
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
    if by_card:
        # Each with the folder it belongs in on the card.
        print("  Copy with a card reader, onto the SD card at: " + ", ".join(by_card))
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
    # Three roles, three sensible defaults -- the old binary check here
    # (cam vs. everything-else-is-firefly) silently gave a standalone
    # transport relay the wrong default name ("unnamed-firefly"), since
    # it isn't one. Caught by a review, not by anyone hitting it in the
    # field, but worth fixing before someone did.
    default_names = {
        "cam": "unnamed-stump",
        "pc": "unnamed-stump",
        "heltec_transport": "unnamed-repeater",
    }
    subhead("Name")
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
        subhead("Radio")
        print("Radio parameters (defaults match the deployed mesh):")
        radio = dict(DEFAULT_RADIO)
        radio["frequency"] = int(ask("Frequency (Hz)", radio["frequency"]))
        radio["bandwidth"] = int(ask("Bandwidth (Hz)", radio["bandwidth"]))
        radio["txpower"] = int(ask("TX power", radio["txpower"]))
        radio["spreadingfactor"] = int(ask("Spreading factor", radio["spreadingfactor"]))
        radio["codingrate"] = int(ask("Coding rate", radio["codingrate"]))
        profile["radio"] = radio
        return profile, None

    if board_type == "heltec":
        subhead("Radio")
        print("Radio parameters (defaults match the deployed mesh):")
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
        subhead("Wi-Fi: how the CAM reaches it")
        print("WiFi Remote (Station mode) — this is what lets the CAM reach this")
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
                ssid = ask_wifi("  The CAM's hotspot name (its Node name, or custom SSID)", "")
                psk = ""
                default_ip = "192.168.4.2"
            else:
                ssid = ask_wifi("  WiFi network for the Heltec to join", "")
                psk = "" if wifi_is_open(ssid) else ask("  WiFi password", "")
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

    elif board_type == "pc":
        # Stump on a computer (docs/PC.md): the computer's own Wi-Fi is the
        # operating system's to set up, so nothing to join and no hotspot
        # name to broadcast -- only what visitors are told.
        profile["wifi_ssid"], profile["wifi_pass"] = "", ""
        subhead("Visitors' Wi-Fi")
        print("Visitors join this computer's hotspot (Windows: Mobile Hotspot; macOS:")
        print("Internet Sharing), or a router the computer is on, and open its address.")
        profile["pc_hotspot_name"] = ask("The Wi-Fi name visitors join (shown on the About page)", node_name)
        profile["ssid_name"] = profile["pc_hotspot_name"]
        profile["ssid_include_ip"] = False
        subhead("Storage")
        profile["pc_storage_mb"] = _pc_ask_storage(_PC_FOLDER[0], _pc_storage_in_folder(_PC_FOLDER[0]))
    if board_type == "cam":
        subhead("Wi-Fi")
        print("This build's config.py has the CAM join an EXISTING WiFi network")
        print("(for internet/LXMF reachability) -- it's not creating its own hotspot")
        print("with this name. The local walk-up AP is a separate, fixed 'Stump'")
        print("hotspot that comes up automatically, not something configured here.")
        wifi_ssid = ask_wifi("WiFi network to join", "")
        wifi_pass = "" if wifi_is_open(wifi_ssid) else ask("WiFi password", "")
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

    if board_type in ("cam", "pc"):
        # ---- Chain of Stumps over Wi-Fi (docs/CHAIN.md) ----
        subhead("Chain")
        print("Stumps can be chained over Wi-Fi -- e.g. one per apartment, each")
        print("joining the Wi-Fi of the one before it -- so files move between them")
        print("over Wi-Fi and never over LoRa.")
        chain_cfg = None
        if ask_yes_no("Is this Stump part of a chain?", False):
            first = ask_choice("Its place in the chain", [
                "The first Stump (the others join it)",
                "Joins another Stump's Wi-Fi",
            ]).startswith("The first")
            chain_cfg = {"enabled": True, "uplink_ssid": "", "uplink_pass": ""}
            if board_type == "pc":
                # A computer: the OS joins Wi-Fi; Stump needs addresses only.
                chain_cfg.update(subnet=4, channel=0)
                if first:
                    print("  The other Stumps join this computer's hotspot (or the router it's on).")
                    import secrets as _secrets
                    print("  A chain secret keeps visitors out of the chain. Every Stump in the")
                    print("  chain needs the same one: note it down.")
                    chain_cfg["secret"] = ask("  Chain secret", _secrets.token_hex(8)) or ""
                else:
                    same = ask_choice("  How does this computer reach that Stump?", [
                        "On the Wi-Fi it's on now (home, office): the Stump joined it too",
                        "By joining the Stump's own Wi-Fi",
                    ]).startswith("On the Wi-Fi")
                    if same:
                        print("  This computer stays on its Wi-Fi (and its internet). The Stump takes chain")
                        print("  links there when it's the first Stump and has a chain secret.")
                        chain_cfg["uplink_ssid"] = "(this computer's Wi-Fi)"
                        default_ip = ""
                        mine = _route_ip("192.0.2.1") or ""
                        if mine.count(".") == 3 and not mine.startswith("127."):
                            print("  Looking for Stumps on this network...")
                            found = find_stumps(mine.rsplit(".", 1)[0] + ".", skip=(mine,))
                            for ip, node in found:
                                print("    %s  %s" % (ip, node.get("name") or "?"))
                            if found:
                                default_ip = found[0][0]
                            else:
                                print("  None found: its address is the \"On the Wi-Fi\" one in its setup")
                                print("  summary (or --get-ip PORT with it on USB).")
                    else:
                        print("  Join the other Stump's Wi-Fi from this computer's Wi-Fi settings.")
                        chain_cfg["uplink_ssid"] = ask_wifi("  Wi-Fi name of the Stump this one joins",
                                                            "LaBuche-Stump.web.app")
                        print("  Its address is the \"Router\" in this computer's Wi-Fi details once it's")
                        print("  joined (a CAM: 192.168.4.1) -- not this computer's own \"IP address\".")
                        default_ip = "192.168.4.1"
                    while True:
                        chain_cfg["parent_ip"] = ask("  That Stump's address", default_ip)
                        if not chain_cfg["parent_ip"]:
                            continue
                        if _route_ip(chain_cfg["parent_ip"]) != chain_cfg["parent_ip"]:
                            break
                        print(yellow("  That's this computer's own address. The Stump it joins is the Router"
                                     " (%s.1 on this network)." % chain_cfg["parent_ip"].rsplit(".", 1)[0]))
                    chain_cfg["secret"] = ask("  Chain secret (the same as on the first Stump)", "") or ""
            elif first:
                print("  The whole chain shares one Wi-Fi channel, set here. Pick the quietest:")
                print("  bench/wifi_bench.py lists the channels in use around a CAM (1, 6 and 11")
                print("  don't overlap).")
                while True:
                    try:
                        chain_cfg["channel"] = int(ask("  Wi-Fi channel (1-13)", "11"))
                        if 1 <= chain_cfg["channel"] <= 13:
                            break
                    except ValueError:
                        pass
                    print("    A number from 1 to 13.")
                chain_cfg["subnet"] = 4
                import secrets as _secrets
                suggested = _secrets.token_hex(8)
                print("  A chain secret keeps visitors on any Stump's Wi-Fi out of the chain.")
                print("  Every Stump in the chain needs the same one: note it down.")
                chain_cfg["secret"] = ask("  Chain secret", suggested) or ""
            else:
                chain_cfg["uplink_ssid"] = ask_wifi("  Wi-Fi name of the Stump this one joins", "LaBuche-Stump.web.app")
                chain_cfg["uplink_pass"] = "" if wifi_is_open(chain_cfg["uplink_ssid"]) else \
                    (ask("  Its Wi-Fi password (blank if open)", "") or "")
                print("  Each Stump's own Wi-Fi uses its own address range, 192.168.<n>.1:")
                print("  the first Stump is 4; give every other one a different number.")
                while True:
                    try:
                        chain_cfg["subnet"] = int(ask("  This Stump's number (5-254)", "5"))
                        if 5 <= chain_cfg["subnet"] <= 254:
                            break
                    except ValueError:
                        pass
                    print("    A number from 5 to 254.")
                chain_cfg["secret"] = ask("  Chain secret (the same as on the first Stump)", "") or ""
                chain_cfg["channel"] = 0
                print("  Once it's up, check the link from a computer on its Wi-Fi:")
                print("    python3 provisioner.py --check-chain 192.168.%d.1" % chain_cfg["subnet"])
            if not chain_cfg["secret"]:
                print("  " + yellow("No secret: anyone on a Stump's Wi-Fi could join the chain."))
        profile["chain"] = chain_cfg

        subhead("The Heltec (LoRa radio)")
        has_heltec = True
        if board_type == "pc":
            link = ask_choice("The Heltec (LoRa radio)", [
                "Plugged into this computer by USB",
                "Over Bluetooth (paired with this computer, no cable)",
                "Over Wi-Fi (the Heltec joins this computer's network)",
                "None (this Stump reaches the LoRa mesh through the chain, or not at all)",
            ])
            has_heltec = not link.startswith("None")
            profile["pc_heltec"] = ("usb" if link.startswith("Plugged") else "ble" if link.startswith("Over Bluetooth")
                                    else "wifi" if has_heltec else "none")
            if profile["pc_heltec"] == "usb":
                port = ask("  Its USB port ('auto' finds it)", "auto")
                profile["pc_heltec_port"] = port or "auto"
            elif profile["pc_heltec"] == "ble":
                _print_ble_pairing()
                profile["pc_heltec_ble"] = _ble_name(ask("  Its Bluetooth name, e.g. RNode 3B87 ('auto': the first RNode found)",
                                                         "auto"))
            if profile["pc_heltec"] in ("usb", "ble"):
                radio, src = mesh_radio_settings((chain_cfg or {}).get("parent_ip"))
                print("  Radio: %s (%s)" % (_radio_text(radio), "the same as the Stump at %s" % src if src
                                             else "the Provisioner's defaults: they must match the mesh's"))
                profile["pc_radio"] = radio
        elif chain_cfg:
            has_heltec = ask_yes_no("Does this Stump have its own Heltec (LoRa radio)?",
                                    not chain_cfg["uplink_ssid"])
        profile["heltec_enabled"] = has_heltec
        if board_type == "pc" and profile["pc_heltec"] != "wifi":
            # USB or none: no Wi-Fi bridge entry to fill in.
            profile["heltec_host"], profile["heltec_port"] = "192.168.4.2", 7633
        elif not has_heltec:
            profile["heltec_host"], profile["heltec_port"] = "192.168.4.2", 7633
            print("  No Heltec: this Stump reaches the LoRa mesh through the chain.")

        # Same explicit pairing question as the Heltec's own wizard branch
        # above, and for the identical reason: this CAM's own hotspot is a
        # real, joinable network the Heltec can connect to directly
        # instead of a router, but its address (192.168.4.1) is a
        # different range from this prompt's old default -- asking
        # outright, rather than letting the two boards' provisioning runs
        # silently assume different networks, is the actual fix.
        if board_type == "pc" and profile.get("pc_heltec") == "wifi":
            print("  Set the Heltec to join this computer's network with a fixed address:")
            print("  rnodeconf <port> -w STATION --ip <address> (see docs/PC.md).")
            profile["heltec_host"] = ask("  The Heltec's address", "192.168.137.2")
            profile["heltec_port"] = 7633
        elif has_heltec and board_type != "pc":
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

        # ---- Packs ----
        # Asked before the persona and the theme: a pack can bring both.
        try:
            fw = _remembered_path("stump_app_dir") or STUMP_APP_DIR
            packs = choose_packs(fw)
        except Exception as e:
            print(f"  (pack scan skipped: {e})")
            packs = []
        profile["packs"] = [str(p["source"]) for p in packs]
        persona = {}
        for p in packs:
            persona = p["spec"].get("persona") or persona
        pack_themes = [p["spec"]["theme"] for p in packs if p["spec"].get("theme", {}).get("name")]

        # ---- Local greeter name ----
        subhead("The bot")
        print("The node's bot has a name: it's who answers '!' commands in the")
        print("chat (and the Concierge, a hidden page at /concierge). Cosmetic,")
        print("and separate from the node name above, which is what mesh peers see.")
        profile["bot_name"] = ask("  Bot's name", persona.get("bot_name") or "BarKeep")

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
            greeting = ask("  Auto-reply text (max 200 chars)", persona.get("mesh_greeting") or "")
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
            profile["plugin_config"] = configure_plugins(fw, extra=_pack_plugin_specs(packs))
        except Exception as e:
            print(f"  (plugin scan skipped: {e})")
            profile["plugin_config"] = {}

        # ---- Credit economy ----
        subhead("File sharing")
        print("File sharing can run as a credit economy (bring something to take")
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

        # ---- Features ----
        # Which visitor features this node offers. One that's off is gone:
        # no tile, no link, and its addresses answer "not offered here".
        # Turning chat off also turns off chat over the mesh.
        subhead("Features")
        print("Which features should this node offer? (A feature that's off is")
        print("removed entirely: no tile, no link, its pages answer 'not offered'.)")
        picked = []
        for key, label in (
            ("chat", "Chat -- rooms and DMs, on WiFi and over the mesh"),
            ("billboard", "Billboard -- the bulletin board"),
            ("files", "File sharing -- upload and download"),
            ("about", "About page -- where this came from, how to connect"),
        ):
            if ask_yes_no("  Offer " + label + "?", True):
                picked.append(key)
        if not picked:
            print("  With nothing enabled, visitors see only the logo and title.")
            if not ask_yes_no("  Keep it that way?", False):
                picked = ["chat", "billboard", "files", "about"]
                print("  OK -- all four features enabled.")
        profile["features"] = picked

        # ---- Propagation node ----
        subhead("Propagation node")
        print("Run an LXMF propagation node? It holds messages for people who")
        print("are offline (FireFly, Sideband) and hands them over when they're")
        print("back. Needs the SD card. Each stored message costs ~30 s of")
        print("background work on this board. Can be switched later in /admin.")
        profile["propagation_node"] = ask_yes_no("  Run a propagation node?", False)

        # ---- Look ----
        # Site-wide: every page and the chat use it, for every visitor.
        # A browser can still pick its own from /admin; that only
        # changes that one browser.
        subhead("Look")
        print("Pick the node's look. It applies to every page and the chat, for")
        print("everyone. (Anyone with the admin password can still switch their")
        print("own browser from /admin -- that changes nothing for other visitors.)")
        looks = [
            "Amber -- warm dark, the original look",
            "Phosphor -- green terminal on near-black",
            "OLED -- true black with blue accents",
            "Paper -- light, for bright daylight",
        ]
        for t in pack_themes:
            label = t.get("label", t["name"])
            if isinstance(label, dict):
                label = label.get("en") or next(iter(label.values()), t["name"])
            looks.append("%s -- %s (from a pack)" % (t["name"], label))
        look = ask_choice("Theme:", looks)
        profile["theme"] = look.split(" ", 1)[0].lower()

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
    # customized config.py from the one in the resolved firmware folder
    # (the template), saving it beside the node's profile in
    # ~/.provisioner/profiles/, and pushing that one file. Never written
    # back into the firmware folder: it's part of the git checkout, and a
    # changed config.py there made every later `git pull` refuse to run --
    # silently leaving the old Provisioner and firmware in place.
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
            theme=profile.get("theme"),
            features=profile.get("features"),
            propagation_node=profile.get("propagation_node"),
            chain=profile.get("chain") or ({"enabled": False} if "chain" in profile else None),
            heltec_enabled=profile.get("heltec_enabled"),
        )
    except Exception as e:
        print(f"FAILED to generate config.py: {e}")
        return False

    CONFIG_OUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in profile["node_name"]) or "node"
    local_config = CONFIG_OUT_DIR / (safe + ".config.py")
    local_config.write_text(new_content)
    print(f"config.py for this node saved at: {local_config}")

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
                         ssid_name=_UNSET, theme=None, features=None, propagation_node=None,
                         chain=None, heltec_enabled=None):
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
    saw_theme = False
    saw_features = False
    saw_pn = False
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
        if propagation_node is not None and stripped.split("=", 1)[0].strip() == "PROPAGATION_NODE":
            out.append("PROPAGATION_NODE = %r\n" % bool(propagation_node))
            saw_pn = True
            continue
        if features is not None and stripped.split("=", 1)[0].strip() == "FEATURES":
            out.append("FEATURES = %r\n" % list(features))
            saw_features = True
            continue
        if theme is not None and stripped.split("=", 1)[0].strip() == "THEME":
            out.append("THEME = %r\n" % theme)
            saw_theme = True
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
        if in_heltec_block and heltec_enabled is not None and '"enabled"' in line:
            indent = line[:len(line) - len(line.lstrip())]
            out.append("%s\"enabled\": %r,\n" % (indent, bool(heltec_enabled)))
            continue
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

    if chain is not None:
        # CHAIN_* (chain.py): replaced in place, or appended as a block.
        want = {
            "CHAIN_ENABLED": bool(chain.get("enabled")),
            "CHAIN_UPLINK_SSID": chain.get("uplink_ssid", ""),
            "CHAIN_UPLINK_PASS": chain.get("uplink_pass", ""),
            "CHAIN_SUBNET": int(chain.get("subnet", 4)),
            "CHAIN_CHANNEL": int(chain.get("channel", 0) or 0),
            "CHAIN_SECRET": chain.get("secret", ""),
        }
        rebuilt = []
        for line in out:
            key = line.split("=", 1)[0].strip()
            if key in want and "=" in line and not line.lstrip().startswith("#"):
                rebuilt.append("%s = %r\n" % (key, want.pop(key)))
            else:
                rebuilt.append(line)
        out = rebuilt
        if want:
            out.append("\n# ---- Chain: Stumps linked over Wi-Fi, docs/CHAIN.md (added by the Provisioner) ----\n")
            for k in ("CHAIN_ENABLED", "CHAIN_UPLINK_SSID", "CHAIN_UPLINK_PASS", "CHAIN_SUBNET",
                      "CHAIN_CHANNEL", "CHAIN_SECRET"):
                if k in want:
                    out.append("%s = %r\n" % (k, want[k]))
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
    if propagation_node is not None and not saw_pn:
        out.append("\n# ---- LXMF propagation node: store-and-forward (added by the Provisioner) ----\n")
        out.append("PROPAGATION_NODE = %r\n" % bool(propagation_node))
    if features is not None and not saw_features:
        out.append("\n# ---- Features offered: any of chat, billboard, files, about (added by the Provisioner) ----\n")
        out.append("FEATURES = %r\n" % list(features))
    if theme is not None and not saw_theme:
        out.append("\n# ---- Site theme: amber, phosphor, oled or paper (added by the Provisioner) ----\n")
        out.append("THEME = %r\n" % theme)

    return "".join(out)


def _print_discovered_addrs(addrs, wifi_ssid=None):
    """Prints the board's discovered LAN/AP addresses -- shared by
    --get-ip and the wizard's own post-config address lookup, so both
    render this the same way rather than drifting into two different
    formats for the same data. Bold and colored deliberately: this is
    exactly the information a technician needs to walk away
    remembering, and a real, reported mixup this session (an IP that
    changed after a router reboot, needed again later) showed how
    easily that gets lost in a wall of otherwise-identical plain text.
    """
    if addrs.get("sta"):
        label = f" (from '{wifi_ssid}')" if wifi_ssid else ""
        print(bold(f"LAN IP{label}: ") + bold(green(addrs["sta"])))
        print("  -- reachable from anyone else on that same network:")
        print("     " + cyan(f"http://{addrs['sta']}/billboard"))
    if addrs.get("ap"):
        print(bold("AP IP (node's own hotspot): ") + bold(green(addrs["ap"])))
        print("  -- connect a phone to the node's own 'Stump' Wi-Fi network,")
        print("     then browse to: " + cyan(f"http://{addrs['ap']}/billboard"))


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


def erase_flash(port, board=None):
    """Erases a board's whole flash with esptool -- the CAM (ESP32-S3) or
    the Heltec (also an ESP32-S3) -- to start it over cleanly: a board
    whose firmware got corrupted, or one going to another use. Asks which
    board and for a yes first; on a CAM, its config.py is copied to this
    computer before (if it can be read). The CAM's SD card isn't touched.
    True if erased."""
    banner("ERASE A BOARD'S FLASH — %s" % port)
    if board is None:
        board = ask_choice("Which board is on %s?" % port, ["A Stump CAM", "A Heltec"])
        board = "cam" if board and board.startswith("A Stump") else "heltec"
    if board == "cam":
        print("  Everything on the CAM's flash goes: MicroPython, the Stump, its identity")
        print("  (a new one is made: it's a new node to the mesh), its settings, the chat")
        print("  and billboard kept on flash. Its SD card -- files, tools -- isn't touched.")
        print("  Afterwards: run the Provisioner, \"A Stump CAM\", to set it up again.")
    else:
        print("  Everything on the Heltec's flash goes: the RNode firmware, its radio")
        print("  settings, and its Bluetooth pairing (forget it on the computer/phone too).")
        print("  Afterwards: run the Provisioner, \"A Heltec...\", to install it again.")
    if not ask_yes_no("  Erase %s now?" % port, False):
        print("  Nothing erased.")
        return False
    if board == "cam":
        try:
            saved = _backup_board_config(port)
        except Exception:
            saved = None
        if saved:
            print("  Its config.py, copied to this computer first: %s" % saved)
    print("  Erasing (about 30 s)...")
    ok, out = run(["esptool", "--port", port, "erase-flash"], timeout=180)
    if not ok and ("Failed to connect" in out or "No serial data received" in out or "Timed out" in out):
        new_port = _bootloader_recovery(port)
        if new_port:
            port = new_port
            print("  Retrying on %s ..." % port)
            ok, out = run(["esptool", "--port", port, "erase-flash"], timeout=180)
    if ok:
        print("  " + green("Erased.") + " Unplug it and plug it back in, then set it up with the Provisioner.")
        return True
    print("  " + red("Not erased:") + " " + "\n    ".join(out.strip().splitlines()[-6:]))
    print("  If it won't connect: a data USB cable (not power-only), and hold BOOT while")
    print("  plugging it in (download mode).")
    return False


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
    print("[1/3] Serial bridge test...")
    if board_type in ("heltec", "heltec_transport"):
        print("  SKIPPED — this board runs standalone Reticulum/RNode-family")
        print("  firmware, not MicroPython; mpremote has nothing to connect to")
        print("  here. See the radio test below.")
        results["serial_bridge"] = None
    else:
        ok, out = run(["mpremote", "connect", port, "exec", "print('provisioner-ping')"], timeout=15)
        results["serial_bridge"] = ok
        print("  OK — board responded over serial." if ok else f"  FAILED: {out.strip()}")
        if ok and board_type == "cam":
            rel_ok, rel_text, _ = release_check(port, _remembered_path("stump_app_dir") or STUMP_APP_DIR)
            results["release"] = rel_ok
            print(("  Release: " if rel_ok else "  FAILED release: ") + rel_text)

    # 2. SD card storage -- meaningful ONLY for the CAM. Neither Heltec
    # role has an SD card at all, confirmed directly: this is also the
    # test that, on a failure, offers to call wipe_sd_card() -- which
    # now refuses outright for any non-CAM board_type on its own, but
    # skipping the whole test here means a heltec_transport board never
    # even reaches a false "card looks unusable" prompt in the first
    # place, rather than relying on the wipe function's own refusal as
    # the only line of defence.
    print("[2/3] SD card storage test...")
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
    print("[3/3] Radio TX/RX test...")
    if board_type == "cam":
        print("  SKIPPED — the CAM has no radio of its own; it reaches the mesh")
        print("  through the Heltec Bridge (confirm that connection manually below).")
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

    # Heltec Bridge reachability and CAM web server reachability used to
    # be automated steps 4 and 5 here. Both removed on request after
    # repeatedly producing false failures that had nothing to do with
    # whether the board actually worked: a timeout that only meant this
    # laptop wasn't on the CAM's own hotspot at the moment the check
    # ran, a boot sequence that takes close to a minute so an immediate
    # check failed even on a healthy board, and a router reboot that
    # silently reassigned the LAN IP being probed. Each fix made the
    # automation more correct but not simpler, and every one of them
    # was a real, reported confusion a human glancing at a loaded page
    # would have resolved in seconds. A person checking "does the page
    # load" is faster and more reliable here than a laptop-side probe
    # that depends on network conditions the check itself can't see or
    # control -- this replaces both with exactly that instruction
    # rather than automating around their failure modes further.
    if board_type == "cam":
        banner("CONFIRM CONNECTIVITY MANUALLY")
        print("Power-cycle the board, then check it yourself -- this is the one")
        print("piece of this suite a person confirms faster and more reliably")
        print("than an automated probe from a laptop that may or may not be on")
        print("the right network at the exact moment it happens to run:")
        print()
        print(bold("  LAN IP:") + " join the same network the board is on, then browse to")
        print("    its address -- find it with:")
        print("    " + cyan(f"python3 provisioner.py --get-ip {port}"))
        print(bold("  AP IP:") + "  join the board's own hotspot, then browse to")
        print("    " + cyan("http://192.168.4.1/"))
        print()
        print("Either one loading the BarKeep page confirms the board is up and")
        print("serving requests correctly.")

    banner("DIAGNOSTIC SUMMARY")
    for k, v in results.items():
        label = "SKIPPED" if v is None else ("PASS" if v else "FAIL")
        print(f"  {k:<15} {label}")
    all_relevant_passed = all(v for v in results.values() if v is not None)
    if all_relevant_passed:
        print("\n" + bold(green("CERTIFIED FOR FIELD DEPLOYMENT")))
    else:
        print("\n" + bold(yellow("NOT CERTIFIED")) + " — resolve failures above before deploying.")
    return results


# ---------------------------------------------------------------------------
# Full guided flow
# ---------------------------------------------------------------------------

def _cam_after_board(r, port, app_dir):
    """A CAM, once its port is known: every question first (section 3),
    then the long, unattended part (4-7) with nothing more to ask."""
    # ── Questions
    r.start("Questions")
    print("Everything is asked now, so the rest can run while you're away.")
    flash = ask_yes_no(f"Flash MicroPython onto {port}? (erases the board: yes for a new or "
                       "broken board, no to keep it and only update)", True)
    upload = bool(app_dir) and (flash or ask_yes_no(
        "Upload the Stump files? (keeps the board's settings)", True))
    global SKIP_TOOLS
    SKIP_TOOLS = upload and not ask_yes_no(
        "Copy the Tools page's files to its SD card too? They're the slow part: ~20-25 min "
        "the first time and after each update (the firmware bundle and the Provisioner change). "
        "No: the node works the same; only its /tools downloads stay as they were", True)
    profile = None
    if ask_yes_no("Answer the setup questions? (no: the board keeps its settings)", True):
        profile, _ = config_wizard("cam")
    verify = ask_yes_no("Run the diagnostic suite at the end?", True)
    r.end()
    left = sum((r.expected(n)[0] or 0) for n, _ in r.steps[r.index + 1:])
    awake = keep_awake()
    box("Over to the Provisioner", [
        "Sections %d-%d run by themselves now: about %s." % (r.index + 2, len(r.steps), _span(left)),
        "Leave the USB cable in." + (" This computer won't go to sleep until the end."
                                     if awake else " Keep this computer awake (no sleep, lid open)."),
        "You can step away: the summary at the end says how it went."])

    # ── Flash
    r.start("Flash")
    flashed = False
    if flash:
        flashed, flashed_port = flash_cam_micropython(port)
        if flashed and flashed_port and flashed_port != port:
            # Bootloader recovery can land the board on another port.
            print(f"\nThe board moved to {flashed_port} during flashing: using that from here.")
            port = flashed_port
            r.note("Board", port)
        r.end("done" if flashed else "fail")
    else:
        r.end("skip", "the board keeps what it has")

    # ── Firmware
    r.start("Firmware")
    if not app_dir:
        r.end("skip", "no firmware folder (section 1)")
    elif upload and (flashed or not flash):
        if flashed:
            # esptool resets the board right after writing; the first
            # mpremote attempt would race that reboot.
            print("Waiting for the board to finish booting after the flash...")
            time.sleep(5)
        ok = upload_stump_app(port, app_dir=app_dir)
        r.end("done" if ok else "fail",
              "" if ok else "retry with: python3 provisioner.py --upload-app %s" % port)
    elif flash and not flashed:
        r.end("skip", "the flash didn't work")
    else:
        r.end("skip", "the board keeps its Stump files")

    # ── Install
    r.start("Install")
    if profile is None:
        r.end("skip", "nothing new to install")
    else:
        ok = push_config_to_board(port, "cam", profile)
        if profile.get("packs"):
            ok = install_packs(port, [p for p in (load_pack(x) for x in profile["packs"]) if p]) and ok
        banner("Finding the node's addresses")
        print("Restarting the board so it boots with the new settings...")
        run(["mpremote", "connect", port, "reset"], timeout=15)
        print("Waiting for it to boot (Wi-Fi, clock, Reticulum)...")
        time.sleep(12)
        addrs = get_stump_ip(port)
        if addrs and (addrs.get("sta") or addrs.get("ap")):
            print()
            _print_discovered_addrs(addrs, wifi_ssid=profile.get("wifi_ssid"))
            if addrs.get("sta"):
                r.note("On the Wi-Fi", "http://%s/" % addrs["sta"])
            if addrs.get("ap"):
                r.note("Its own Wi-Fi", "http://%s/" % addrs["ap"])
        else:
            print("No address yet: that's normal right after an upload. Power-cycle the")
            print("board, wait a few seconds, then: python3 provisioner.py --get-ip %s" % port)
        chain = profile.get("chain") or {}
        if chain.get("enabled"):
            r.note("Chain", "first Stump" if not chain.get("uplink_ssid") else
                   "joins '%s'" % chain["uplink_ssid"])
            if chain.get("secret"):
                r.note("Chain secret", chain["secret"])
            if not chain.get("uplink_ssid") and chain.get("secret") and profile.get("wifi_ssid"):
                r.note("Computers join", "on its own Wi-Fi at %s, or on '%s' at %s" % (
                    (addrs or {}).get("ap") or "192.168.4.1", profile["wifi_ssid"],
                    (addrs or {}).get("sta") or "its address there (--get-ip)"))
        r.end("done" if ok else "fail")

    # ── Verify
    r.start("Verify")
    rel_ok, rel_text, rel = release_check(port, app_dir)
    print(("" if rel_ok else red(_glyphs()["fail"] + " ")) + "Release: " + rel_text)
    r.note("Release", (rel + " " + _glyphs()["done"]) if rel_ok else red(rel_text))
    if not rel_ok and not verify:
        r.end("fail", rel_text)
    elif verify:
        results = diagnostics(port, "cam")
        passed = all(v for v in results.values() if v is not None)
        r.end("done" if passed else "fail", "certified" if passed else "NOT certified: see the summary above")
    else:
        r.end("skip")
    if not r.summary("Re-check any time: python3 provisioner.py --diag %s" % port):
        print("Fix what's marked " + red(_glyphs()["fail"]) + ", then run that section again"
              " (python3 provisioner.py --help lists them one by one).")


# What the Provisioner sets up, and the route each one takes.
ROLES = [
    ("cam", "A Stump CAM", "the ESP32-S3-CAM: pages, chat, files, mesh"),
    ("heltec", "A Heltec for a CAM", "the RNode radio a CAM reaches over Wi-Fi"),
    ("heltec_transport", "A Heltec relay", "a standalone LoRa repeater, no CAM"),
    ("pc", "A Stump on this computer", "Windows, macOS or Linux, with a Heltec"),
    ("fix", "Check or fix something", "diagnostics, chain, address, packs, card"),
]


def route_for(role):
    if role == "cam":
        return Route("Setting up a Stump CAM", key="cam", steps=[
            ("Check", "you", "the tools on this computer, and the Stump firmware folder"),
            ("Board", "you", "pick the CAM's USB port"),
            ("Questions", "you", "all of them now: flash or not, name, Wi-Fi, chain, the Heltec, "
                                 "packs, bot, features, look"),
            ("Flash", "auto", "MicroPython onto the board (erases it)"),
            ("Firmware", "auto", "the Stump files (~5 min), then the Tools page's files onto its "
                                 "SD card (~20 min, if you say so in section 3)"),
            ("Install", "auto", "the settings and packs; restart; find its address"),
            ("Verify", "auto", "the diagnostic suite"),
        ], needs=["the CAM on USB, with a data cable (not a charge-only one)",
                  "the name and password of the Wi-Fi it joins, if any",
                  "for a chain: the chain secret, and the Stump it joins",
                  "a laptop: its lid open (a closed laptop sleeps anyway)"],
            subtitle="Nothing touches the board before you've answered everything.",
            why=["Why so long: the board takes files over USB slowly, about 1 KB/s once they go "
                 "to its SD card. The Tools page's files are most of the wait: the firmware "
                 "bundle (~900 KB) and the Provisioner, which a technician can download from the "
                 "node. They change with every update, so they're copied again after each one; "
                 "section 3 asks whether to. Everything else already on the card is skipped.",
                 "Leave the cable in. This computer is kept awake until the end."])
    if role == "heltec":
        return Route("Setting up a Heltec for a CAM", key="heltec", steps=[
            ("Check", "you", "the tools on this computer"),
            ("Board", "you", "pick the Heltec's USB port"),
            ("Flash", "you", "the RNode firmware (erases the board); rnodeconf asks its own questions"),
            ("Configure", "you", "radio settings, access code, its Wi-Fi (how the CAM reaches it)"),
            ("Install", "you", "the settings onto the Heltec (rnodeconf may ask again)"),
            ("Verify", "auto", "the diagnostic suite"),
        ], needs=["the Heltec V3 on USB, with a data cable",
                  "the radio settings of your mesh (the defaults match the deployed one)",
                  "the Wi-Fi it joins: usually the CAM's own, and a fixed address in it"],
            subtitle="Nothing touches the board before section 3, and each section asks first.")
    if role == "heltec_transport":
        return Route("Setting up a Heltec relay", key="heltec_transport", steps=[
            ("Check", "you", "the tools on this computer"),
            ("Board", "you", "pick the Heltec's USB port"),
            ("Flash", "you", "radio settings, then the standalone Reticulum firmware (erases the "
                             "board); rnodeconf asks its own questions"),
            ("Verify", "auto", "the diagnostic suite"),
        ], needs=["the Heltec V3 on USB, with a data cable",
                  "the radio settings of your mesh (the defaults match the deployed one)"])
    raise ValueError(role)


def start_menu(version=None):
    """What are you setting up? Returns a role from ROLES, or None.
    version: release_lines() for the top of the box."""
    g = _glyphs()
    lines = (version or []) + ([""] if version else []) + [
             "Sets up Stump hardware step by step: it shows the route first,",
             "then goes through it one numbered section at a time, asking",
             "before anything touches a board.", "", "What are you setting up?", ""]
    width = max(len(t) for _, t, _ in ROLES)
    for i, (_, title, what) in enumerate(ROLES, 1):
        lines.append("%d) %s  %s" % (i, bold(title.ljust(width)), what))
    box("The Provisioner", lines)
    while True:
        raw = input("Choose 1-%d (blank to quit): " % len(ROLES)).strip()
        if not raw:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(ROLES):
            return ROLES[int(raw) - 1][0]
        print("  a number from 1 to %d" % len(ROLES))


def fix_menu():
    """Check or fix something: the one-off tools, without the flags."""
    actions = [
        ("Run the diagnostic suite on a board", "diag"),
        ("Check a chain of Stumps is linked", "chain"),
        ("Find a CAM's addresses", "ip"),
        ("Re-upload the Stump files to a CAM (keeps its settings)", "upload"),
        ("Add a pack to a CAM", "pack"),
        ("Update the Tools page on a CAM's card", "tools"),
        ("Erase a CAM's SD card", "wipe"),
        ("Erase a board's flash (a CAM or a Heltec): start it over", "erase"),
        ("Check the tools on this computer", "toolcheck"),
    ]
    box("Check or fix something", ["%d) %s" % (i, a) for i, (a, _) in enumerate(actions, 1)])
    raw = input("Choose 1-%d (blank to go back): " % len(actions)).strip()
    if not raw.isdigit() or not 1 <= int(raw) <= len(actions):
        return
    what = actions[int(raw) - 1][1]
    if what == "toolcheck":
        check_tools()
        return
    if what == "chain":
        target = ask("A Stump's address (blank: the Stump on this computer)", "")
        check_chain(target or None)
        return
    ports = scan_boards()
    port = ask_choice("Which port?", [p["device"] for p in ports]) if ports else ask("The board's port")
    if not port:
        return
    if what == "diag":
        diagnostics(port)
    elif what == "ip":
        addrs = get_stump_ip(port)
        if addrs:
            _print_discovered_addrs(addrs)
        else:
            print("  No address yet: reset the board, wait a few seconds, and try again.")
    elif what == "upload":
        upload_stump_app(port)
    elif what == "pack":
        packs = discover_packs()
        chosen = choose_packs(_remembered_path("stump_app_dir") or STUMP_APP_DIR, packs) if packs else []
        if chosen:
            install_packs(port, chosen)
        elif not packs:
            print("  No packs found. Put pack folders or .zip files in %s" % PACKS_DIR)
    elif what == "tools":
        install_tools_on_node(port)
    elif what == "wipe":
        if ask_yes_no("This erases the card. Is this port a CAM?", False):
            wipe_sd_card(port, board_type="cam")
    elif what == "erase":
        erase_flash(port)


def _ask_radio():
    print("  Radio parameters (the defaults match the deployed mesh):")
    radio = dict(DEFAULT_RADIO)
    radio["frequency"] = int(ask("  Frequency (Hz)", radio["frequency"]))
    radio["bandwidth"] = int(ask("  Bandwidth (Hz)", radio["bandwidth"]))
    radio["txpower"] = int(ask("  TX power", radio["txpower"]))
    radio["spreadingfactor"] = int(ask("  Spreading factor", radio["spreadingfactor"]))
    radio["codingrate"] = int(ask("  Coding rate", radio["codingrate"]))
    return radio


def checkout_warning():
    """A git checkout whose final_firmware/config.py was changed (older
    Provisioners wrote the node's settings there) makes `git pull` refuse
    to update -- and the old Provisioner then runs as if nothing happened.
    Says so, with the fix. Silent outside a git checkout."""
    here = Path(__file__).resolve().parent
    if not (here / ".git").exists():
        return False
    try:
        r = subprocess.run(["git", "-C", str(here), "status", "--porcelain", "final_firmware/config.py"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    if not r.stdout.strip():
        return False
    print(yellow("Note: final_firmware/config.py has local changes, so `git pull` can't update this"))
    print(yellow("folder (it stops with \"Your local changes ... would be overwritten\")."))
    print("Your nodes keep their own settings; to update, put the file back and pull:")
    print("    git checkout -- final_firmware/config.py && git pull")
    print("(Newer Provisioners keep each node's settings in ~/.provisioner/profiles/ instead.)\n")
    return True


def interactive_wizard():
    checkout_warning()
    role = start_menu(release_lines())
    if role is None:
        print("Nothing done.")
        return
    if role == "pc":
        setup_pc()
        return
    if role == "fix":
        fix_menu()
        return
    r = route_for(role)
    r.show()
    if not ask_yes_no("Start?", True):
        print("Nothing done.")
        return

    # ── Check
    r.start("Check")
    tools_ok = check_tools()
    resolved_app_dir = None
    if role == "cam":
        banner("The Stump firmware folder")
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
            rel, here_rel = _folder_release(resolved_app_dir), _folder_release()
            print(f"Using firmware source: {resolved_app_dir} (Stump {rel or '?'})")
            if here_rel and rel != here_rel:
                print(yellow(f"  That's not this download's firmware ({here_rel}): a folder remembered from"
                             " an earlier run. To install this one, answer the folder question with"
                             f" {STUMP_APP_DIR}"))
        else:
            print("No firmware folder found: MicroPython will still flash, but section 4")
            print("(Firmware) will be skipped unless this is fixed.")
    if not tools_ok and not ask_yes_no("\n  Some tools are missing. Carry on anyway?", False):
        r.end("fail", "tools missing")
        r.summary("Install the missing tools (above), then run the Provisioner again.")
        return
    r.end("done" if tools_ok else "fail", "" if tools_ok else "some tools missing; carried on")

    # ── Board
    r.start("Board")
    port = choose_board(role)
    if not port:
        r.end("fail", "no board selected")
        r.summary("Plug the board in with a data cable, then run the Provisioner again.")
        return
    r.note("Board", port)
    r.end()

    if role == "cam":
        _cam_after_board(r, port, resolved_app_dir)
        return

    # ── Flash
    r.start("Flash")
    flashed = False
    if role == "heltec_transport":
        if ask_yes_no(f"Flash the standalone Reticulum firmware onto {port} now?", True):
            flashed = flash_heltec_standalone_reticulum(port, _ask_radio())
            r.end("done" if flashed else "fail")
        else:
            r.end("skip")
    elif ask_yes_no(f"Flash firmware onto {port} now? (erases the board)", True):
        if role == "heltec":
            flashed = bool(flash_heltec_rnode(port))
        else:
            flashed, flashed_port = flash_cam_micropython(port)
            if flashed and flashed_port and flashed_port != port:
                # Bootloader recovery can land the board on another port.
                print(f"\nThe board moved to {flashed_port} during flashing: using that from here.")
                port = flashed_port
                r.note("Board", port)
        r.end("done" if flashed else "fail")
    else:
        r.end("skip", "the board keeps what it has")

    # ── Firmware (CAM)
    if role == "cam":
        r.start("Firmware")
        if not resolved_app_dir:
            r.end("skip", "no firmware folder (section 1)")
        elif flashed or ask_yes_no("Upload the Stump files now? (keeps the board's settings)", False):
            if flashed:
                # esptool resets the board right after writing; the first
                # mpremote attempt would race that reboot.
                print("Waiting for the board to finish booting after the flash...")
                time.sleep(5)
            ok = upload_stump_app(port, app_dir=resolved_app_dir)
            r.end("done" if ok else "fail",
                  "" if ok else "retry with: python3 provisioner.py --upload-app %s" % port)
        else:
            r.end("skip", "the board keeps its Stump files")

    # ── Configure, Install
    profile = None
    if role in ("cam", "heltec"):
        r.start("Configure")
        if ask_yes_no("Answer the setup questions now?", True):
            profile, _ = config_wizard(role)
            r.end()
        else:
            r.end("skip", "the board keeps its settings")
        r.start("Install")
        if profile is None:
            r.end("skip", "nothing new to install")
        else:
            ok = push_config_to_board(port, role, profile)
            if role == "cam" and profile.get("packs"):
                ok = install_packs(port, [p for p in (load_pack(x) for x in profile["packs"]) if p]) and ok
            if role == "cam":
                banner("Finding the node's addresses")
                print("Restarting the board so it boots with the new settings...")
                run(["mpremote", "connect", port, "reset"], timeout=15)
                print("Waiting for it to boot (Wi-Fi, clock, Reticulum)...")
                time.sleep(12)
                addrs = get_stump_ip(port)
                if addrs and (addrs.get("sta") or addrs.get("ap")):
                    print()
                    _print_discovered_addrs(addrs, wifi_ssid=profile.get("wifi_ssid"))
                    if addrs.get("sta"):
                        r.note("On the Wi-Fi", "http://%s/" % addrs["sta"])
                    if addrs.get("ap"):
                        r.note("Its own Wi-Fi", "http://%s/" % addrs["ap"])
                else:
                    print("No address yet: that's normal right after an upload. Power-cycle the")
                    print("board, wait a few seconds, then: python3 provisioner.py --get-ip %s" % port)
                chain = profile.get("chain") or {}
                if chain.get("enabled"):
                    r.note("Chain", "first Stump" if not chain.get("uplink_ssid") else
                           "joins '%s'" % chain["uplink_ssid"])
                    if chain.get("secret"):
                        r.note("Chain secret", chain["secret"])
            hw = profile.get("heltec_wifi") or {}
            if role == "heltec" and hw.get("ip"):
                r.note("Heltec address", "%s (enter it as the CAM's Heltec address)" % hw["ip"])
            r.end("done" if ok else "fail")

    # ── Verify
    r.start("Verify")
    if ask_yes_no("Run the diagnostic suite now?", True):
        results = diagnostics(port, role)
        passed = all(v for v in results.values() if v is not None)
        r.end("done" if passed else "fail", "certified" if passed else "NOT certified: see the summary above")
    else:
        r.end("skip")
    ok = r.summary("Re-check any time: python3 provisioner.py --diag %s" % port)
    if not ok:
        print("Fix what's marked " + red(_glyphs()["fail"]) + ", then run that section again"
              " (python3 provisioner.py --help lists them one by one).")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


# ==========================================================================
# Stump on a computer (docs/PC.md): python3 provisioner.py --pc [FOLDER]
# ==========================================================================
# The computer plays the CAM's role. Its Stump folder plays the board's
# flash: the same firmware files --upload-app puts on a CAM are copied there
# (with config.py kept on updates), plus pc/stump_pc.py and pc/pc_compat/,
# which make the firmware run on regular Python. The folder has its own
# Python environment (venv) with the two packages Stump needs.

PC_SOURCE = Path(__file__).resolve().parent / "pc"
PC_PACKAGES = ("cryptography", "pyserial")
PC_BLE_PACKAGE = "bleak"          # only for a Heltec over Bluetooth


def _print_ble_pairing():
    print("  Pairing is done from the Stump's /admin page (Radio -> Bluetooth), no terminal:")
    print("  not paired yet? Choose USB here, pair from /admin, then switch to Bluetooth there.")


def _pc_ensure_bleak(folder):
    """bleak in the folder's Python, for a Heltec over Bluetooth. True if there."""
    py = _pc_python(folder)
    if subprocess.run([str(py), "-c", "import bleak"], capture_output=True).returncode == 0:
        return True
    print("  Installing bleak (Bluetooth) into this Stump's Python (needs internet, this once) ...")
    r = subprocess.run([str(py), "-m", "pip", "install", "--quiet", "--upgrade", PC_BLE_PACKAGE])
    if r.returncode != 0:
        print("  " + red("pip couldn't install bleak.") + " Connect to the internet and run --pc again.")
        return False
    return True
PC_SERVICE_LABEL = "app.stump.node"


def pc_default_folder():
    return Path.home() / "Stump"


def _pc_python(folder, windowless=False):
    v = Path(folder) / "venv"
    if os.name == "nt":
        return v / "Scripts" / ("pythonw.exe" if windowless else "python.exe")
    return v / "bin" / "python"


def _pc_ensure_venv(folder):
    """The folder's own Python with Stump's packages. Returns its path, or
    None (with the reason printed)."""
    py = _pc_python(folder)
    if not py.exists():
        print("  Making this Stump's own Python environment (venv) ...")
        r = subprocess.run([sys.executable, "-m", "venv", str(Path(folder) / "venv")],
                           capture_output=True, text=True)
        if r.returncode != 0 or not py.exists():
            print("  " + red("Couldn't make it: ") + (r.stderr or r.stdout).strip()[-300:])
            print("  (On Linux: sudo apt install python3-venv)")
            return None
    check = subprocess.run([str(py), "-c", "import cryptography, serial"], capture_output=True)
    if check.returncode != 0:
        print("  Installing %s into it (needs internet, this once) ..." % " and ".join(PC_PACKAGES))
        r = subprocess.run([str(py), "-m", "pip", "install", "--quiet", "--upgrade"] + list(PC_PACKAGES))
        if r.returncode != 0:
            print("  " + red("pip couldn't install them.") + " Connect to the internet and run --pc again.")
            return None
    return py


def _board_files(fw):
    """What --upload-app would put on a CAM: the core and the built-in
    plugins, as paths relative to the firmware folder."""
    files = [f for f in STUMP_APP_FILES if (fw / f).is_file()]
    for name, spec in discover_plugins(fw):
        files += [f for f in plugin_files(fw / name, spec) if _belongs_on_board(f)]
    return files


def _copy_if_changed(src, dst):
    """Copies src to dst unless dst is already identical. True if copied."""
    import shutil
    dst = Path(dst)
    try:
        if dst.is_file() and dst.stat().st_size == Path(src).stat().st_size and \
                dst.read_bytes() == Path(src).read_bytes():
            return False
    except OSError:
        pass
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return True


def _pc_copy_firmware(fw, folder):
    """The firmware and the computer runtime into the Stump folder. Keeps
    the folder's own config.py. Returns how many files changed."""
    folder = Path(folder)
    changed = 0
    for rel in _board_files(fw):
        if rel.endswith(".mpy"):
            continue                        # the ESP32's compiled modules
        if rel == "config.py" and (folder / "config.py").exists():
            continue                        # this Stump's own settings stay
        changed += _copy_if_changed(fw / rel, folder / rel)
    changed += _copy_if_changed(PC_SOURCE / "stump_pc.py", folder / "stump_pc.py")
    for f in sorted((PC_SOURCE / "pc_compat").glob("*.py")):
        changed += _copy_if_changed(f, folder / "pc_compat" / f.name)
    return changed


def _pc_install_packs(folder, packs):
    """Packs into a Stump folder: their plugins next to the firmware, their
    tools and home files in its sd/ folder, their themes in themes.json --
    what install_packs() does on a CAM, as local copies."""
    folder = Path(folder)
    themes = {}
    tf = folder / "themes.json"
    if tf.exists():
        try:
            themes = json.loads(tf.read_text(encoding="utf-8"))
        except ValueError:
            themes = {}
    for pack in packs:
        spec, d = pack["spec"], pack["dir"]
        print(f"  {spec['name']} {spec['version']}")
        for name in spec.get("plugins", []):
            for rel in plugin_files(d / name):
                if _belongs_on_board(rel):
                    _copy_if_changed(d / rel, folder / rel)
        for rel in _pack_files(d, spec):
            sub, fname = rel.split("/", 1)
            dest = folder / "sd" / (("tools/" + spec["name"]) if sub == "tools" else sub) / fname
            _copy_if_changed(d / rel, dest)
        t = spec.get("theme")
        if t and t.get("name"):
            themes[t["name"]] = {"label": t.get("label", t["name"]), "colors": t.get("colors", {})}
    if themes:
        tf.write_text(json.dumps(themes, ensure_ascii=False), encoding="utf-8")


def _pc_install_tools(fw, folder):
    """The Tools page's sections in the Stump folder's sd/tools/: local
    copies, so nothing here is slow (no 1 MB limit either)."""
    folder = Path(folder)
    here = Path(__file__).resolve().parent
    tools = folder / "sd" / "tools"
    payload = fw / "tools_payload"
    for src, dst in ((here / "provisioner.py", "provisioner/provisioner.py"),
                     (here / "README.md", "provisioner/Stump-README.md"),
                     (here / "LICENSE", "provisioner/LICENSE"),
                     (payload / "flasher" / "esptool-bundle.js", "flasher/esptool-bundle.js"),
                     (payload / "flasher" / "esptool-js-LICENSE.txt", "flasher/esptool-js-LICENSE.txt")):
        if src.is_file():
            _copy_if_changed(src, tools / dst)
    for src, sec in (("provisioner", "provisioner"), ("flasher", "flasher"), ("apps", "firefly")):
        for f in sorted((payload / src).glob("*")):
            if f.is_file() and (f.name.startswith("README") or src == "apps"):
                _copy_if_changed(f, tools / sec / f.name)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            z = Path(tmp) / "Stump_Beta_A.zip"
            _build_firmware_zip(z, fw)
            _copy_if_changed(z, tools / "provisioner" / "Stump_Beta_A.zip")
    except Exception as e:
        print("  (firmware zip for the Tools page skipped: %s)" % e)
    cat = payload / "flasher" / "catalog.json"
    if cat.is_file():
        _copy_if_changed(cat, folder / "sd" / "fw" / "catalog.json")


def _pc_config_block(profile):
    """What a computer Stump adds to config.py (docs/PC.md)."""
    out = ["\n# ---- Stump on a computer (docs/PC.md; added by the Provisioner) ----\n",
           "PC_HOTSPOT_IP = 'auto'      # the address visitors reach this computer on\n",
           "PC_HTTP_PORT = 80           # 8080 is used if 80 is taken\n",
           "PC_STORAGE_MB = %d%s# the \"card\": the sd/ folder (shared files fill it to 75%%)\n"
           % (int(profile.get("pc_storage_mb") or PC_STORAGE_DEFAULT_MB),
              " " * max(1, 8 - len(str(int(profile.get("pc_storage_mb") or PC_STORAGE_DEFAULT_MB))))),
           "PC_HOTSPOT_NAME = %r\n" % profile.get("pc_hotspot_name", ""),
           "CONFIG['time_sync'] = {'enabled': False}   # the computer keeps its own time\n"]
    chain = profile.get("chain") or {}
    if chain.get("parent_ip"):
        out.append("PC_CHAIN_PARENT_IP = %r   # the Stump this one joins\n" % chain["parent_ip"])
    heltec = profile.get("pc_heltec", "none")
    if heltec == "ble":
        out.append("# The Heltec over Bluetooth ('auto': the first paired RNode); radio settings: /admin -> Radio.\n")
        out.append("CONFIG['interfaces'] = [i for i in CONFIG['interfaces'] if i.get('type') != 'WiFiSerialInterface'] + [\n")
        radio = profile.get("pc_radio") or {}
        out.append("    {'type': 'RNodeBLEInterface', 'name': 'Heltec (Bluetooth)', 'enabled': True,\n")
        out.append("     'target': %r%s},\n]\n" % (profile.get("pc_heltec_ble", "auto"),
                                                 "".join(", %r: %r" % kv for kv in sorted(radio.items()))))
    elif heltec == "usb":
        out.append("# The Heltec on USB ('auto' finds it); radio settings: /admin -> Radio.\n")
        out.append("CONFIG['interfaces'] = [i for i in CONFIG['interfaces'] if i.get('type') != 'WiFiSerialInterface'] + [\n")
        radio = profile.get("pc_radio") or {}
        out.append("    {'type': 'RNodeSerialInterface', 'name': 'Heltec (USB)', 'enabled': True,\n")
        out.append("     'port': %r%s},\n]\n" % (profile.get("pc_heltec_port", "auto"),
                                               "".join(", %r: %r" % kv for kv in sorted(radio.items()))))
    elif heltec == "none":
        out.append("CONFIG['interfaces'] = [i for i in CONFIG['interfaces'] if i.get('type') != 'WiFiSerialInterface']\n")
    return "".join(out)


PC_STORAGE_DEFAULT_MB = 2048
PC_STORAGE_MIN_MB = 256
_PC_FOLDER = [None]          # the folder being set up (the storage question looks at its disk)


def _disk_free_bytes(folder):
    """Bytes free on the disk the folder is (or will be) on."""
    import shutil
    p = Path(folder or Path.home()).expanduser()
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(str(p)).free
    except OSError:
        return 0


def _gb(n_bytes):
    return "%.1f GB" % (n_bytes / 1024 ** 3) if n_bytes >= 1024 ** 3 else "%d MB" % (n_bytes // 1024 ** 2)


def _pc_storage_default(free_bytes):
    """2048 MB, or less on a nearly full disk: a quarter of what's free,
    in steps of 256 MB."""
    quarter = int(free_bytes // 4 // (1024 ** 2)) // 256 * 256
    return max(PC_STORAGE_MIN_MB, min(PC_STORAGE_DEFAULT_MB, quarter))


def _pc_storage_in_folder(folder):
    """PC_STORAGE_MB in the folder's config.py (its questions asked again:
    the answer it had), or None."""
    try:
        m = re.search(r"(?m)^PC_STORAGE_MB\s*=\s*(\d+)", (Path(folder) / "config.py").read_text(encoding="utf-8"))
        return int(m.group(1)) if m else None
    except (OSError, TypeError):
        return None


def _pc_storage_text(cfg):
    mb = int(cfg.get("PC_STORAGE_MB") or PC_STORAGE_DEFAULT_MB)
    return "%d MB of storage" % mb


def _pc_ask_storage(folder, now=None):
    """The size of a computer Stump's "card" (the sd/ folder), in MB."""
    free = _disk_free_bytes(folder)
    used = _dir_bytes(Path(folder) / "sd") if folder else 0
    most = max(PC_STORAGE_MIN_MB, (free + used) // 1024 ** 2)
    print("What visitors share is kept in the Stump's sd/ folder, its \"card\". Shared files fill it")
    print("to 75%, then the oldest make room. 2048 MB holds about 1.5 GB of files (documents, photos,")
    print("apps); for videos or offline maps, more (8192 MB: about 6 GB).")
    print("This disk has %s free%s." % (_gb(free), ("; the Stump's files take %s" % _gb(used)) if used else ""))
    default = now or _pc_storage_default(free)
    while True:
        typed = ask("Storage for this Stump, in MB", str(default))
        try:
            mb = int(str(typed).strip().lower().replace("mb", "").strip())
        except ValueError:
            print("  " + yellow("A number of MB, e.g. 2048."))
            continue
        if mb < PC_STORAGE_MIN_MB:
            print("  " + yellow("At least %d MB." % PC_STORAGE_MIN_MB))
        elif mb > most:
            print("  " + yellow("This disk can give it %d MB at most." % most))
        else:
            return mb


def _dir_bytes(path):
    total = 0
    for dirpath, _, filenames in os.walk(str(path)):
        for n in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, n))
            except OSError:
                pass
    return total


def _set_config_value(folder, key, value, comment=""):
    """KEY = value in the folder's config.py, in place of its line (or at
    the end), everything else as it was."""
    path = Path(folder) / "config.py"
    lines = path.read_text(encoding="utf-8").split("\n")
    new = "%s = %r%s" % (key, value, ("    # " + comment) if comment else "")
    pat = re.compile(r"^%s\s*=(?!=)" % re.escape(key))
    for i, line in enumerate(lines):
        if pat.match(line):
            lines[i] = new
            break
    else:
        if lines and lines[-1] == "":
            lines.insert(len(lines) - 1, new)
        else:
            lines.append(new)
    tmp = path.with_suffix(".py.tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _pc_change_settings(r, folder, cfg):
    """An update keeps every setting. What to change, if anything: just
    the storage, or all the questions again. True for all the questions."""
    while True:
        a = (ask("Change something? s: the storage (now %d MB) · a: all the questions again · Enter: nothing"
                 % int(cfg.get("PC_STORAGE_MB") or PC_STORAGE_DEFAULT_MB)) or "").strip().lower()
        if a in ("", "n", "no", "nothing"):
            return False
        if a in ("a", "all", "y", "yes"):
            return True
        if a in ("s", "storage"):
            mb = _pc_ask_storage(folder, int(cfg.get("PC_STORAGE_MB") or PC_STORAGE_DEFAULT_MB))
            _set_config_value(folder, "PC_STORAGE_MB", mb, "the \"card\": the sd/ folder (shared files fill it to 75%)")
            cfg["PC_STORAGE_MB"] = mb
            print("  " + green("Storage: %d MB" % mb) + ", the rest kept. It applies when the Stump starts again"
                  " (at the end of this update, if it starts at login; or /admin -> Restart).")
            r.note("Storage", "%d MB" % mb)
            r.end("done", "storage set to %d MB, the rest kept" % mb)
            return False
        print("  " + yellow("'s', 'a', or Enter."))


def _pc_configure_existing(r, folder):
    """The Configure section of an update: keeps the settings, but says
    what they are, notices a Heltec plugged in by USB since (and offers
    to use it, keeping the rest), and lets them be changed. True if the
    wizard's questions should run again."""
    reconfigure = False
    cfg = _read_folder_config(folder) or {}
    heltec = _pc_heltec_in_config(cfg)
    print("This Stump's settings: " + _pc_settings_summary(cfg))
    usb = [p for p in list_serial_ports() if any(m in p["guess"] for m in _HELTEC_MARKERS)]
    if heltec == "ble" and usb:
        # Plugged in to charge: fine. Never offered as USB -- opening its port
        # restarts the Heltec, which drops its Bluetooth link.
        print("A Heltec is plugged in by USB (%s): this Stump uses it over Bluetooth, so it leaves the"
              " USB port alone (charging is fine)." % usb[0]["device"])
        reconfigure = _pc_change_settings(r, folder, cfg)
    elif heltec != "usb" and usb:
        print(yellow("A Heltec is plugged in by USB (%s), but this Stump doesn't use one." % usb[0]["device"]))
        if ask_yes_no("Use it for this Stump (the other settings stay as they are)?", True):
            radio, src = mesh_radio_settings(cfg.get("PC_CHAIN_PARENT_IP"))
            print("  Radio: %s (%s)" % (_radio_text(radio), "the same as the Stump at %s" % src if src
                                         else "the Provisioner's defaults: they must match the mesh's"))
            _pc_add_usb_heltec(folder, radio)
            print("  It needs the RNode firmware: a Heltec set up by the Provisioner has it.")
            r.note("Heltec", "on USB (%s)" % usb[0]["device"])
            r.end("done", "the Heltec on USB added, the rest kept")
            heltec = "usb"
        else:
            reconfigure = _pc_change_settings(r, folder, cfg)
    elif heltec == "usb" and not usb:
        print(yellow("This Stump uses a Heltec on USB, and none is plugged in now: it will wait for it"
                     " (and connect by itself when it's plugged in)."))
        if ask_yes_no("Use a Heltec over Bluetooth instead (the other settings stay as they are)?", False):
            _print_ble_pairing()
            target = _ble_name(ask("  Its Bluetooth name, e.g. RNode 3B87 ('auto': the first RNode found)", "auto"))
            radio, src = mesh_radio_settings(cfg.get("PC_CHAIN_PARENT_IP"))
            print("  Radio: %s (%s)" % (_radio_text(radio), "the same as the Stump at %s" % src if src
                                         else "the Provisioner's defaults: they must match the mesh's"))
            _pc_add_heltec(folder, radio, "ble", target)
            r.note("Heltec", "over Bluetooth (%s)" % target)
            r.end("done", "the Heltec over Bluetooth, the rest kept")
            heltec = "ble"
        else:
            reconfigure = _pc_change_settings(r, folder, cfg)
    else:
        reconfigure = _pc_change_settings(r, folder, cfg)
    if r.state[r.index] == "now" and not reconfigure:
        r.end("skip", "config.py kept")
    return reconfigure


def _pc_heltec_in_config(cfg):
    """'usb', 'ble', 'wifi' or 'none': the Heltec a computer Stump's config.py uses."""
    kinds = [i.get("type") for i in (cfg.get("CONFIG") or {}).get("interfaces", []) if i.get("enabled", True)]
    if "RNodeSerialInterface" in kinds:
        return "usb"
    if "RNodeBLEInterface" in kinds:
        return "ble"
    return "wifi" if "WiFiSerialInterface" in kinds else "none"


def _pc_settings_summary(cfg):
    heltec = {"usb": "a Heltec on USB", "ble": "a Heltec over Bluetooth", "wifi": "a Heltec over Wi-Fi",
              "none": "no Heltec"}[_pc_heltec_in_config(cfg)]
    parts = [str(cfg.get("NODE_NAME") or "?"), heltec, _pc_storage_text(cfg)]
    if cfg.get("CHAIN_ENABLED"):
        parent = cfg.get("PC_CHAIN_PARENT_IP")
        parts.append("joins the Stump at %s" % parent if parent else "first Stump of a chain")
    return " · ".join(parts)


def mesh_radio_settings(parent_ip=None, get=None):
    """The radio settings the mesh uses: those of the Stump this one joins
    (its /node.json, firmware 1.0.0-beta.4+), else the Provisioner's
    defaults. (settings, where they came from)."""
    if parent_ip:
        try:
            r = (get or _get_json)("http://%s/node.json" % parent_ip, timeout=3).get("radio")
            if r and all(k in r for k in ("frequency", "bandwidth", "sf", "cr")):
                return ({"frequency": r["frequency"], "bandwidth": r["bandwidth"],
                         "spreadingfactor": r["sf"], "codingrate": r["cr"]}, parent_ip)
        except Exception:
            pass
    return ({k: DEFAULT_RADIO[k] for k in ("frequency", "bandwidth", "spreadingfactor", "codingrate")}, None)


def _radio_text(r):
    return "%.3f MHz, %g kHz, SF%d, CR4/%d" % (r["frequency"] / 1e6, r["bandwidth"] / 1000,
                                               r["spreadingfactor"], r["codingrate"])


def _pc_add_usb_heltec(folder, radio=None):
    _pc_add_heltec(folder, radio, "usb")


def _ble_name(typed):
    """What was typed for the Heltec's Bluetooth name, as it advertises:
    "EA69" (what its screen shows) -> "RNode EA69"; blank -> "auto"."""
    t = (typed or "").strip()
    if not t or t.lower() == "auto":
        return "auto"
    if re.fullmatch(r"[0-9A-Fa-f]{4}", t):
        return "RNode " + t.upper()
    m = re.fullmatch(r"(?i)rnode[\s_-]*([0-9a-f]{4})", t)
    return "RNode " + m.group(1).upper() if m else t


def _pc_add_heltec(folder, radio=None, kind="usb", target="auto"):
    """Switches an existing computer Stump to a Heltec on USB (kind "usb")
    or over Bluetooth ("ble", target: its name or "auto"), keeping every
    other setting: appended to config.py, so it overrides the radio lines
    above it and nothing else. radio: the mesh's settings (every node of a
    mesh needs the same frequency, bandwidth, SF and CR)."""
    extra = "".join(", %r: %r" % (k, v) for k, v in sorted((radio or {}).items()))
    if kind == "ble":
        head, entry = ("over Bluetooth", "{'type': 'RNodeBLEInterface', 'name': 'Heltec (Bluetooth)', 'enabled': True, "
                       "'target': %r%s}" % (target, extra))
    else:
        head, entry = ("on USB", "{'type': 'RNodeSerialInterface', 'name': 'Heltec (USB)', 'enabled': True, "
                       "'port': 'auto'%s}" % extra)
    block = ("\n# ---- The Heltec %s (added by the Provisioner) ----\n"
             "CONFIG['interfaces'] = [i for i in CONFIG['interfaces']\n"
             "                        if i.get('type') not in ('WiFiSerialInterface', 'RNodeSerialInterface',"
             " 'RNodeBLEInterface')] + [\n"
             "    %s,\n]\n" % (head, entry))
    with open(Path(folder) / "config.py", "a", encoding="utf-8") as f:
        f.write(block)


def _pc_write_config(fw, folder, profile):
    gen = _generate_config_py(
        fw / "config.py", profile["node_name"], profile["wifi_ssid"], profile["wifi_pass"],
        profile["heltec_host"], profile["heltec_port"],
        credits_enabled=profile.get("credits_enabled"),
        credit_weights=profile.get("credit_weights"),
        bot_name=profile.get("bot_name"),
        mesh_greeting=profile.get("mesh_greeting"),
        plugin_config=profile.get("plugin_config"),
        ssid_include_ip=profile.get("ssid_include_ip"),
        ssid_name=profile.get("ssid_name", _UNSET),
        theme=profile.get("theme"),
        features=profile.get("features"),
        propagation_node=profile.get("propagation_node"),
        chain=profile.get("chain") or {"enabled": False},
        heltec_enabled=profile.get("heltec_enabled"),
    )
    (Path(folder) / "config.py").write_text(gen + _pc_config_block(profile), encoding="utf-8")


def _pc_launchers(folder):
    """Double-clickable files in the Stump folder: Start (the login service
    if there is one, else the Stump on its own, then the page opens) and
    Stop (asks the Stump to turn off -- the radio off first -- else stops
    the service). The same names stump_host.START_FILE/STOP_FILE gives
    /admin. Returns (start, stop) paths."""
    folder = Path(folder).resolve()
    py, launcher = _pc_python(folder), folder / "stump_pc.py"
    py_bg = _pc_python(folder, windowless=True)
    if sys.platform == "darwin":
        names = ("Start Stump.command", "Stop Stump.command")
        label = PC_SERVICE_LABEL
        start = """#!/bin/bash
# Starts this Stump (made by the Provisioner): double-click it.
PLIST="$HOME/Library/LaunchAgents/%(label)s.plist"
if [ -f "$PLIST" ]; then
  launchctl kickstart "gui/$(id -u)/%(label)s" 2>/dev/null || launchctl load -w "$PLIST"
else
  nohup "%(py)s" "%(launcher)s" --service >/dev/null 2>&1 &
fi
echo "Stump is starting..."
sleep 5
open "http://localhost/"
""" % dict(label=label, py=py, launcher=launcher)
        stop = """#!/bin/bash
# Turns this Stump off (made by the Provisioner): double-click it.
# The Stump switches the radio off first; it starts again with "Start Stump"
# or at the next login.
for port in 80 8080; do
  if curl -s -m 3 -X POST -d "" "http://127.0.0.1:$port/admin/poweroff" >/dev/null; then
    echo "Stump is turning off."; exit 0
  fi
done
launchctl kill TERM "gui/$(id -u)/%(label)s" 2>/dev/null || pkill -TERM -f "%(launcher)s"
echo "Stump stopped."
""" % dict(label=label, launcher=launcher)
    elif os.name == "nt":
        names = ("Start Stump.bat", "Stop Stump.bat")
        start = ("@echo off\r\nrem Starts this Stump (made by the Provisioner): double-click it.\r\n"
                 "schtasks /Run /TN Stump >nul 2>&1 || start \"\" \"%s\" \"%s\" --service\r\n"
                 "echo Stump is starting...\r\ntimeout /t 5 >nul\r\nstart \"\" http://localhost/\r\n"
                 % (py_bg, launcher))
        stop = ("@echo off\r\nrem Turns this Stump off (made by the Provisioner): double-click it.\r\n"
                "rem The Stump switches the radio off first; \"Start Stump\" starts it again.\r\n"
                "curl -s -m 3 -X POST -d \"\" http://127.0.0.1/admin/poweroff >nul 2>&1 && goto done\r\n"
                "curl -s -m 3 -X POST -d \"\" http://127.0.0.1:8080/admin/poweroff >nul 2>&1 && goto done\r\n"
                "schtasks /End /TN Stump >nul 2>&1\r\n:done\r\necho Stump is turning off.\r\n"
                "timeout /t 3 >nul\r\n")
    else:
        names = ("start-stump.sh", "stop-stump.sh")
        start = """#!/bin/sh
# Starts this Stump (made by the Provisioner).
systemctl --user start stump 2>/dev/null || nohup "%s" "%s" --service >/dev/null 2>&1 &
echo "Stump is starting..."
sleep 5
xdg-open "http://localhost/" >/dev/null 2>&1 || true
""" % (py, launcher)
        stop = """#!/bin/sh
# Turns this Stump off (made by the Provisioner); the radio off first.
for port in 80 8080; do
  if curl -s -m 3 -X POST -d "" "http://127.0.0.1:$port/admin/poweroff" >/dev/null; then
    echo "Stump is turning off."; exit 0
  fi
done
systemctl --user stop stump 2>/dev/null || pkill -TERM -f "%s"
echo "Stump stopped."
""" % launcher
    paths = []
    for name, text in zip(names, (start, stop)):
        p = folder / name
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        if os.name != "nt":
            os.chmod(p, 0o755)
        paths.append(p)
    return tuple(paths)


def _pc_autostart(folder):
    """Starts this Stump at login: a LaunchAgent on macOS, a scheduled task
    on Windows, a systemd user service on Linux. True if set up."""
    folder = Path(folder).resolve()
    launcher = folder / "stump_pc.py"
    if sys.platform == "darwin":
        agents = Path.home() / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        plist = agents / (PC_SERVICE_LABEL + ".plist")
        plist.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>%s</string>
  <key>ProgramArguments</key><array><string>%s</string><string>%s</string><string>--service</string></array>
  <key>WorkingDirectory</key><string>%s</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
</dict></plist>
""" % (PC_SERVICE_LABEL, _pc_python(folder), launcher, folder))
        # Unloaded first: an update restarts the running Stump on its new files.
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        r = subprocess.run(["launchctl", "load", "-w", str(plist)], capture_output=True, text=True)
        if r.returncode != 0:
            print("  " + red("launchctl: ") + (r.stderr or r.stdout).strip())
            return False
        print("  Starts at login (%s); running now. Log: %s" % (plist, folder / "stump.log"))
        print("  Remove it from login: launchctl unload -w %s" % plist)
        return True
    if os.name == "nt":
        cmd = '"%s" "%s" --service' % (_pc_python(folder, windowless=True), launcher)
        r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/RL", "LIMITED",
                            "/TN", "Stump", "/TR", cmd], capture_output=True, text=True)
        if r.returncode != 0:
            print("  " + red("schtasks: ") + (r.stderr or r.stdout).strip())
            return False
        # Restarted, so an update's new files are the ones running.
        subprocess.run(["schtasks", "/End", "/TN", "Stump"], capture_output=True)
        subprocess.run(["schtasks", "/Run", "/TN", "Stump"], capture_output=True)
        print("  Starts at login (Task Scheduler: Stump); running now. Log: %s" % (folder / "stump.log"))
        print('  Stop it: schtasks /End /TN Stump    Remove: schtasks /Delete /TN Stump /F')
        return True
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / "stump.service").write_text(
        "[Unit]\nDescription=Stump\n\n[Service]\nWorkingDirectory=%s\nExecStart=%s %s --service\n"
        "Restart=on-failure\n\n[Install]\nWantedBy=default.target\n" % (folder, _pc_python(folder), launcher))
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    r = subprocess.run(["systemctl", "--user", "enable", "stump"], capture_output=True, text=True)
    if r.returncode == 0:
        r = subprocess.run(["systemctl", "--user", "restart", "stump"], capture_output=True, text=True)
    if r.returncode != 0:
        print("  " + red("systemctl: ") + (r.stderr or r.stdout).strip())
        return False
    print("  Starts at login (systemd --user: stump); running now. Log: %s" % (folder / "stump.log"))
    return True


# ── Is the chain linked? (docs/CHAIN.md, docs/PC.md) ─────────────────
# Run by the --pc wizard when this computer joins another Stump, and on
# demand with --check-chain. Each step that fails says what to do.

CHAIN_PORT = 4242
CHAIN_HELLO_MAGIC = b"STUMPCHAIN1"


def _get_json(url, timeout=4):
    """GET a Stump's JSON, straight to it: never through a proxy set in
    the system settings or the environment (a Stump is always on the
    local network, and a proxy -- often a VPN or filter app -- can't
    reach it, which showed as "Connection refused" for a Stump that was
    right there)."""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _why_unreachable(e):
    """'refused', 'timeout' or 'other' for a failed _get_json."""
    import socket
    reason = getattr(e, "reason", e)
    if isinstance(reason, ConnectionRefusedError):
        return "refused"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "timeout"
    if getattr(reason, "errno", None) in (65, 113) or "No route to host" in str(reason):
        return "noroute"
    return "other"


MAC_LOCAL_NETWORK = "x-apple.systempreferences:com.apple.preference.security?Privacy_LocalNetwork"


def _route_ip(dest):
    """This computer's address on the way to `dest` (sends nothing)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((dest, CHAIN_PORT))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def chain_hello(parent_ip, secret, my_ip, timeout=3.0):
    """Says hello to the Stump at parent_ip as a chain member would, with
    the chain secret. True if it let us in, False if it didn't answer."""
    import hashlib, hmac, socket
    tag = b""
    if secret:
        tag = hmac.new(secret.encode(), (my_ip + ">" + parent_ip).encode(), hashlib.sha256).digest()[:16]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind((my_ip, 0))              # any port: the answer comes back to it
        s.settimeout(0.5)
        end = time.time() + timeout
        while time.time() < end:
            s.sendto(CHAIN_HELLO_MAGIC + tag, (parent_ip, CHAIN_PORT))
            try:
                data, addr = s.recvfrom(256)
                if addr[0] == parent_ip and data.startswith(CHAIN_HELLO_MAGIC):
                    return True
            except socket.timeout:
                pass
        return False
    finally:
        s.close()


def chain_preflight(parent_ip, secret, ssid="", hello=chain_hello, get=_get_json, route=_route_ip, wait=60):
    """Before the Stump on this computer starts: can it join the Stump at
    parent_ip? A list of (ok, what, fix); stops at the first failure,
    since the later steps depend on it. A Stump whose Wi-Fi is up but
    whose web server refuses is usually still starting (a CAM takes about
    a minute after power-on): that's retried for up to `wait` seconds."""
    out = []
    where = "'%s'" % ssid if ssid and not ssid.startswith("(") else \
        "the network that Stump is on" if ssid else "that Stump's Wi-Fi"
    prefix = parent_ip.rsplit(".", 1)[0] + "."
    mine = route(parent_ip)
    if not mine or not mine.startswith(prefix):
        out.append((False, "This computer is on %s's network" % parent_ip,
                    "It isn't (its address is %s). Join %s from this computer's Wi-Fi menu, "
                    "then check again. Joined it and still see this? The computer went back to its "
                    "usual Wi-Fi, since a Stump's has no internet: turn off Auto-Join for the usual "
                    "one while you set up (macOS: System Settings > Wi-Fi > its ... button)."
                    % (mine or "unknown", where)))
        return out
    if mine == parent_ip:
        out.append((False, "%s is another Stump" % parent_ip,
                    "%s is this computer's own address, so it was checking itself. The Stump it "
                    "joins is the network's Router (%s1 for a CAM; see this computer's Wi-Fi "
                    "details). Run python3 provisioner.py --pc --reconfigure and enter that, or set "
                    "PC_CHAIN_PARENT_IP in the Stump folder's config.py." % (parent_ip, prefix)))
        return out
    out.append((True, "This computer is on %s's network (as %s)" % (parent_ip, mine), ""))
    try:
        node = get("http://%s/node.json" % parent_ip)
        who = "%s, Stump %s" % (node.get("name") or "?", node.get("version") or "?")
    except Exception:
        node, who = None, None
    chain, err, deadline, told = None, None, time.time() + wait, False
    while True:
        try:
            chain = get("http://%s/chain.json" % parent_ip)
            if node is None:
                try:
                    node = get("http://%s/node.json" % parent_ip)
                    who = "%s, Stump %s" % (node.get("name") or "?", node.get("version") or "?")
                except Exception:
                    pass
            err = None
            break
        except Exception as e:
            err = e
        if node is not None or _why_unreachable(err) != "refused" or time.time() >= deadline:
            break
        if not told:
            print("  Its web server isn't answering yet (a CAM takes about a minute after power-on):"
                  " trying again for up to %d s..." % wait)
            told = True
        time.sleep(3)
    if err is not None:
        e, kind = err, _why_unreachable(err)
        if node is None and kind == "noroute" and sys.platform == "darwin":
            out.append((False, "A Stump answers at http://%s/" % parent_ip,
                        "No route to host: most often nothing is at %s now -- on a shared Wi-Fi a "
                        "Stump's address can change when it restarts (python3 provisioner.py --get-ip "
                        "PORT tells it, with the Stump on USB). Or macOS is keeping Python off the "
                        "local network, even if a browser opens the page: System Settings > Privacy & "
                        "Security > Local Network, turn on Python (open \"%s\" goes there)."
                        % (parent_ip, MAC_LOCAL_NETWORK)))
        elif node is None and kind == "timeout" and not parent_ip.endswith(".1"):
            out.append((False, "A Stump answers at http://%s/" % parent_ip,
                        "No answer from %s (%s). Is the Stump on, and still at that address? On a "
                        "shared Wi-Fi its address can change when it restarts: with it on USB, "
                        "python3 provisioner.py --get-ip PORT tells it; then run --pc --reconfigure "
                        "with the new one." % (parent_ip, e)))
        elif node is None and kind == "refused":
            on_its_own = parent_ip.endswith(".1")
            out.append((False, "A Stump answers at http://%s/" % parent_ip,
                        ("Its Wi-Fi is up (it gave this computer %s), but " % mine if on_its_own else
                         "Something is at %s, but " % parent_ip)
                        + "its web server refused the "
                        "connection, for %d s. Its program has most likely stopped: power-cycle it and "
                        "check again in a minute; if it does it again, its boot log says why "
                        "(python3 provisioner.py --diag PORT, or a serial monitor). A phone "
                        "opening http://%s/ shows the same." % (wait, parent_ip)))
        elif node is None:
            out.append((False, "A Stump answers at http://%s/" % parent_ip,
                        "Nothing answers (%s). Is that Stump on, and is %s its address? A CAM's is "
                        "192.168.<its CHAIN_SUBNET>.1: 192.168.4.1 unless changed. Opening "
                        "http://%s/ in a browser shows it." % (e, parent_ip, parent_ip)))
        else:
            out.append((False, "%s knows about chains" % who,
                        "Its firmware is older than the chain: update it (provisioner.py --upload-app PORT)."))
        return out
    out.append((True, "A Stump answers at %s%s" % (parent_ip, " (%s)" % who if who else ""), ""))
    if not chain.get("enabled"):
        out.append((False, "That Stump is in a chain",
                    "It isn't. On it, set CHAIN_ENABLED = True and a CHAIN_SECRET in its config.py "
                    "(or run its wizard: chain > 'The first Stump'), then restart it."))
        return out
    out.append((True, "That Stump is in a chain (%s)" % chain.get("role"), ""))
    if chain.get("ap_ip") and parent_ip != chain["ap_ip"] and chain.get("lan_ip") != parent_ip:
        # Reached on a shared network (home Wi-Fi), not on its own Wi-Fi.
        why = ("it's not the first Stump" if chain.get("role") != "root" else
               "it has no chain secret" if not chain.get("secret") else
               "its firmware is older than 1.0.0-beta.4, or CHAIN_LAN = False in its config.py")
        out.append((False, "That Stump takes chain links on this network",
                    "It takes them on its own Wi-Fi only (%s): %s. Either join its Wi-Fi and use "
                    "%s, or fix that (update it: provisioner.py, Check or fix > Re-upload the Stump "
                    "files)." % (chain["ap_ip"], why, chain["ap_ip"])))
        return out
    if chain.get("secret") and not secret:
        out.append((False, "The same chain secret on both",
                    "That Stump has a chain secret and this one has none. Run --pc --reconfigure "
                    "and enter its secret (CHAIN_SECRET in its config.py)."))
        return out
    if secret and not chain.get("secret"):
        out.append((False, "The same chain secret on both",
                    "That Stump has no chain secret and this one does. Give both the same one."))
        return out
    if not hello(parent_ip, secret, mine):
        out.append((False, "That Stump lets this computer into the chain",
                    "It didn't answer the hello. Most often the chain secrets differ (its log then "
                    "says 'refused (wrong chain secret)'): copy CHAIN_SECRET exactly from its "
                    "config.py. If they match, this computer's firewall may be blocking the answer: "
                    "allow Python (macOS: System Settings > Network > Firewall)."))
        return out
    out.append((True, "That Stump lets this computer into the chain (the secret matches)", ""))
    return out


def _print_checks(checks):
    for ok, what, fix in checks:
        print("  %s %s" % (green("✓") if ok else red("✗"), what))
        if fix:
            print("      → " + fix)
    return all(ok for ok, _, _ in checks)


def _read_folder_config(folder):
    import runpy
    sys.path.insert(0, str(Path(folder)))     # config.py imports from its folder
    try:
        return runpy.run_path(str(Path(folder) / "config.py"))
    except Exception as e:
        print(red("  Can't read %s: %s" % (Path(folder) / "config.py", e)))
        return None
    finally:
        sys.path.remove(str(Path(folder)))


def chain_live(folder, parent_ip, wait=60, get=_get_json):
    """Once the Stump on this computer runs: has the Stump above answered
    it, and does it list it below? A list of (ok, what, fix)."""
    cfg = _read_folder_config(folder) or {}
    port = int(cfg.get("PC_HTTP_PORT", 80) or 80)
    status, url = None, None
    end = time.time() + wait
    print("  Waiting for the link (up to %d s)..." % wait)
    while time.time() < end:
        for p in dict.fromkeys((port, 8080)):
            u = "http://127.0.0.1:%d/chain.json" % p
            try:
                status, url = get(u), u
                break
            except Exception:
                pass
        up = (status or {}).get("uplink") or {}
        if up.get("parent_heard_s") is not None:
            break
        time.sleep(3)
    if status is None:
        return [(False, "The Stump on this computer is running",
                 "It doesn't answer. Start it (%s %s) and check again with "
                 "--check-chain; if it stops, the end of stump.log says why."
                 % (_pc_python(folder), Path(folder) / "stump_pc.py"))]
    out = [(True, "The Stump on this computer is running", "")]
    up = status.get("uplink") or {}
    if status.get("role") != "member":
        out.append((False, "It's set to join another Stump",
                    "Its config.py makes it the first Stump (CHAIN_UPLINK_SSID is empty): run --pc --reconfigure."))
        return out
    if up.get("parent_heard_s") is None:
        out.append((False, "The Stump above answers it",
                    "No answer yet from %s. Check that its config.py's CHAIN_SECRET matches, and look "
                    "for '[chain]' lines in stump.log." % (up.get("parent") or parent_ip)))
        return out
    out.append((True, "The Stump above answers it (last %d s ago)" % up["parent_heard_s"], ""))
    try:
        below = get("http://%s/chain.json" % parent_ip).get("below", [])
        mine = up.get("ip")
        out.append((mine in below, "%s lists this computer below it" % parent_ip,
                    "" if mine in below else "Not yet (it lists %s): check again in a minute." % (below or "nobody")))
    except Exception:
        pass
    return out


def check_remote_chain(ip, get=_get_json):
    """From a computer on a Stump's Wi-Fi: that Stump's place in its chain."""
    try:
        st = get("http://%s/chain.json" % ip)
    except Exception as e:
        return [(False, "A Stump answers at http://%s/" % ip,
                 "Nothing answers (%s): join that Stump's Wi-Fi first, and check its address." % e)]
    out = [(True, "A Stump answers at %s" % ip, "")]
    if not st.get("enabled"):
        out.append((False, "It's in a chain", "It isn't: CHAIN_ENABLED = True in its config.py, then restart it."))
        return out
    if st.get("role") == "root":
        below = st.get("below") or []
        out.append((True, "It's the first Stump; below it: %s" % (", ".join(below) or "nobody yet"), ""))
        return out
    up = st.get("uplink") or {}
    if not up.get("ip"):
        out.append((False, "It joined the Wi-Fi of the Stump above ('%s')" % up.get("ssid"),
                    "Not yet. Check CHAIN_UPLINK_SSID (exactly that Stump's Wi-Fi name), "
                    "CHAIN_UPLINK_PASS, and that the Stump above is on and in range."))
        return out
    out.append((True, "It joined '%s' as %s" % (up.get("ssid"), up.get("ip")), ""))
    if up.get("parent_heard_s") is None:
        out.append((False, "The Stump above (%s) answers it" % up.get("parent"),
                    "No answer: the chain secrets differ, or the Stump above isn't in a chain. "
                    "Give both the same CHAIN_SECRET and CHAIN_ENABLED = True."))
        return out
    out.append((True, "The Stump above (%s) answers it (last %d s ago)" % (up.get("parent"), up["parent_heard_s"]), ""))
    return out


def find_stumps(prefix, get=_get_json, skip=(), workers=64):
    """Stumps on this network (prefix "192.168.0."): asks each address's
    /node.json at once, briefly. [(ip, node)] for those in a chain that
    take links there: a CAM, first of its chain, with a secret, on the
    Wi-Fi it joined (chain.lan_ip), or a computer that is the first Stump.
    A few seconds for a whole /24."""
    from concurrent.futures import ThreadPoolExecutor

    def ask(ip):
        try:
            node = get("http://%s/node.json" % ip, timeout=1.5)
        except Exception:
            return None
        chain = node.get("chain") if isinstance(node, dict) else None
        if isinstance(chain, dict) and chain.get("enabled") and (
                chain.get("lan_ip") == ip or (chain.get("role") == "root" and chain.get("ap_ip") == ip)):
            return ip, node                # a CAM on the shared Wi-Fi, or a computer Stump
        return None
    ips = [prefix + str(n) for n in range(1, 255) if prefix + str(n) not in skip]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [x for x in pool.map(ask, ips) if x]


def _set_parent_ip(folder, ip):
    """A new address for the Stump this one joins: appended to config.py,
    overriding the line above it, nothing else."""
    with open(Path(folder) / "config.py", "a", encoding="utf-8") as f:
        f.write("\nPC_CHAIN_PARENT_IP = %r   # the Stump this one joins (its address changed)\n" % ip)


def _follow_moved_parent(folder, parent, checks, find=None):
    """When the Stump this one joins doesn't answer on a shared Wi-Fi, it
    has most likely got a new address from the router: looks for it on the
    network and offers it. The new address, or None."""
    if not checks or checks[-1][0] or not checks[-1][1].startswith("A Stump answers") or parent.endswith(".1"):
        return None
    prefix = parent.rsplit(".", 1)[0] + "."
    print("  Looking for Stumps on %s0/24 (its address may have changed)..." % prefix)
    found = (find or find_stumps)(prefix, skip=(_route_ip(parent),))
    if not found:
        print("  None found taking chain links on this network.")
        return None
    for ip, node in found:
        print("    %s  %s" % (ip, node.get("name") or "?"))
    ip, node = found[0] if len(found) == 1 else (None, None)
    if ip is None:
        pick = ask("  Which one does this Stump join (its address)", found[0][0])
        ip = pick.strip()
    if not ask_yes_no("  Join %s at %s from now on?" % (node.get("name") if node else "the Stump", ip), True):
        return None
    _set_parent_ip(folder, ip)
    return ip


def check_chain(target=None):
    """--check-chain [FOLDER | ADDRESS]."""
    import re
    banner("STUMP CHAIN CHECK")
    if target and re.match(r"^\d+\.\d+\.\d+\.\d+$", target):
        return _print_checks(check_remote_chain(target))
    folder = Path(target).expanduser() if target else pc_default_folder()
    cfg = _read_folder_config(folder)
    if cfg is None:
        print("  No Stump on this computer at %s. For a Stump elsewhere: --check-chain ITS.ADDRESS" % folder)
        return False
    if not cfg.get("CHAIN_ENABLED"):
        print("  The Stump in %s isn't in a chain (CHAIN_ENABLED is off): --pc --reconfigure." % folder)
        return False
    parent = cfg.get("PC_CHAIN_PARENT_IP")
    if not cfg.get("CHAIN_UPLINK_SSID") or not parent:
        print("  It's the first Stump of its chain. Who joined it:")
        try:
            st = _get_json("http://127.0.0.1:%d/chain.json" % int(cfg.get("PC_HTTP_PORT", 80) or 80))
            print("    " + (", ".join(st.get("below") or []) or "nobody yet"))
            return True
        except Exception:
            print(red("    It isn't running: start it, and check again."))
            return False
    checks = chain_preflight(parent, cfg.get("CHAIN_SECRET", ""), cfg.get("CHAIN_UPLINK_SSID", ""))
    ok = _print_checks(checks)
    moved = None if ok else _follow_moved_parent(folder, parent, checks)
    if moved:
        parent = moved
        print("  Restart this Stump so it joins the new address (python3 provisioner.py --pc).")
        ok = _print_checks(chain_preflight(parent, cfg.get("CHAIN_SECRET", ""), cfg.get("CHAIN_UPLINK_SSID", "")))
    if ok:
        ok = _print_checks(chain_live(folder, parent, wait=30))
    print("\n  " + (green("The chain is linked.") if ok else "Fix what's marked ✗, then: python3 provisioner.py --check-chain"))
    return ok


def setup_pc(folder=None, reconfigure=False):
    """Sets up (or updates) a Stump on this computer. True if it's ready."""
    fw = STUMP_APP_DIR
    if not (fw / "example_node.py").is_file() or not (PC_SOURCE / "stump_pc.py").is_file():
        print(red("Run this from the Stump download: final_firmware/ and pc/ must be next to provisioner.py."))
        return False
    r = Route("A Stump on this computer", key="pc", steps=[
        ("Folder", "where this Stump lives (its firmware, settings and files)"),
        ("Python", "the folder's own Python, with cryptography and pyserial (internet, first time)"),
        ("Firmware", "the Stump files, copied or brought up to date"),
        ("Configure", "questions: visitors' Wi-Fi, chain, the Heltec, packs, bot, features, look"),
        ("Packs", "the packs and the Tools page"),
        ("Chain", "if it joins another Stump: checks the link, says what to fix"),
        ("Start", "now, and whenever you log in (if you want)"),
    ], needs=["Python 3.8 or newer (this one: %s)" % sys.version.split()[0],
              "a Heltec on USB, Bluetooth or Wi-Fi, or none (a chain can bring LoRa)",
              "to join a CAM: this computer on the CAM's Wi-Fi, and the chain secret"],
        subtitle="Updating? The same route: settings, identity and files are kept.")
    r.show()

    r.start("Folder")
    if folder is None:
        folder = ask("Folder for this Stump", str(pc_default_folder()))
    folder = Path(folder).expanduser().resolve()
    _PC_FOLDER[0] = folder
    first = not (folder / "config.py").exists()
    folder.mkdir(parents=True, exist_ok=True)
    print("%s (%s)" % (folder, "new" if first else "updating: its settings are kept"))
    r.note("Folder", str(folder))
    r.end()

    r.start("Python")
    py = _pc_ensure_venv(folder)
    if py is None:
        r.end("fail", "see above")
        r.summary("Fix the Python environment (above), then run --pc again.")
        return False
    r.end()

    r.start("Firmware")
    n = _pc_copy_firmware(fw, folder)
    rel = _folder_release(folder) or _folder_release(folder / "final_firmware")
    if rel:
        r.note("Release", rel)
    r.end("done", "%d file(s) updated, Stump %s" % (n, rel or "?"))

    r.start("Configure")
    profile = None
    if not (first or reconfigure):
        reconfigure = _pc_configure_existing(r, folder)
    if first or reconfigure:
        profile, _ = config_wizard("pc")
        _pc_write_config(fw, folder, profile)
        r.end("done", "config.py written")

    if _pc_heltec_in_config(_read_folder_config(folder) or {}) in ("usb", "ble"):
        # Bluetooth support ready, so /admin can switch the Heltec to it.
        _pc_ensure_bleak(folder)

    r.start("Packs")
    packs = []
    if profile and profile.get("packs"):
        packs = [p for p in (load_pack(x) for x in profile["packs"]) if p]
    elif not first:
        found = discover_packs()
        packs = [p for p in found if all((folder / name / "install.py").exists()
                                         for name in p["spec"].get("plugins", []))]
        if packs:
            print("Packs on this Stump, brought up to date: " + ", ".join(p["spec"]["name"] for p in packs))
    if packs:
        _pc_install_packs(folder, packs)
    _pc_install_tools(fw, folder)
    print("Tools page: ready")
    r.end()

    cfg = _read_folder_config(folder) or {}
    parent = cfg.get("PC_CHAIN_PARENT_IP")
    joins = bool(cfg.get("CHAIN_ENABLED") and cfg.get("CHAIN_UPLINK_SSID") and parent)
    secret = cfg.get("CHAIN_SECRET", "") or ""
    r.start("Chain")
    if not cfg.get("CHAIN_ENABLED"):
        r.end("skip", "not in a chain")
    elif not joins:
        r.note("Chain", "first Stump: the others join it")
        if secret:
            r.note("Chain secret", secret)
        r.end("skip", "it's the first Stump: nothing to join")
    else:
        print("Can this computer join %s?" % parent)
        while True:
            checks = chain_preflight(parent, secret, cfg.get("CHAIN_UPLINK_SSID", ""))
            if _print_checks(checks):
                break
            moved = _follow_moved_parent(folder, parent, checks)
            if moved:
                parent = moved
                print("\nCan this computer join %s?" % parent)
                continue
            if not ask_yes_no("Fix it, then check again? (no: carry on, check later with --check-chain)", True):
                break
            print()
        if all(ok for ok, _, _ in checks):
            r.note("Chain", "joins %s" % parent)
            r.end("done", "ready to join")
        if r.state[r.index] == "now":
            r.end("fail", "fix what's marked, then: python3 provisioner.py --check-chain")

    r.start("Start")
    started = False
    run_cmd = "%s %s" % (_pc_python(folder), folder / "stump_pc.py")
    try:
        start_file, stop_file = _pc_launchers(folder)
        print("  To turn it off and on: /admin -> \"Turn off this Stump\", or double-click")
        print("  \"%s\" / \"%s\" in %s" % (start_file.name, stop_file.name, folder))
        r.note("On / off", "/admin, or %s / %s" % (start_file.name, stop_file.name))
    except OSError as e:
        print("  " + yellow("Couldn't write the Start/Stop files: %s" % e))
    if ask_yes_no("Start this Stump automatically when you log in?", True):
        started = _pc_autostart(folder)
        r.note("Log", str(folder / "stump.log"))
    else:
        print("\nStart it yourself with:")
        print("  " + run_cmd)
        r.note("Start it", run_cmd)
    if joins and started:
        print("\nChecking the link now that it runs:")
        if _print_checks(chain_live(folder, parent)):
            r.note("Chain", green("linked to %s" % parent))
        else:
            r.state[[n for n, _ in r.steps].index("Chain")] = "fail"
            r.note("Chain", red("not linked yet: python3 provisioner.py --check-chain"))
    r.note("Open", "http://localhost/ on this computer (8080 if 80 was taken)")
    r.end("done", "running, and at every login" if started else "start it with the command below")
    r.summary("More: docs/PC.md (the hotspot, the Heltec, chaining with CAM Stumps).")
    return True

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
    elif "--install-cert" in args:
        idx = args.index("--install-cert")
        if len(args) < idx + 4:
            print("Usage: provisioner.py --install-cert PORT fullchain.pem privkey.pem [--host stump.example.app]")
            sys.exit(1)
        host = args[args.index("--host") + 1] if "--host" in args and args.index("--host") + 1 < len(args) else None
        sys.exit(0 if install_cert(args[idx + 1], args[idx + 2], args[idx + 3], host) else 1)
    elif "--check-chain" in args:
        idx = args.index("--check-chain")
        target = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("--") else None
        sys.exit(0 if check_chain(target) else 1)
    elif "--pc" in args:
        # Stump on this computer (docs/PC.md): set up or update its folder.
        idx = args.index("--pc")
        folder = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("--") else None
        ok = setup_pc(folder, reconfigure="--reconfigure" in args)
        sys.exit(0 if ok else 1)
    elif "--erase-flash" in args:
        idx = args.index("--erase-flash")
        if idx + 1 >= len(args):
            print("Usage: provisioner.py --erase-flash PORT   (a CAM or a Heltec: everything on its flash)")
            sys.exit(1)
        sys.exit(0 if erase_flash(args[idx + 1]) else 1)
    elif "--upload-app" in args:
        idx = args.index("--upload-app")
        if idx + 1 >= len(args):
            print("Usage: provisioner.py --upload-app PORT [--replace-config]")
            sys.exit(1)
        port = args[idx + 1]
        if upload_stump_app(port):
            packs = discover_packs()
            if packs and ask_yes_no("\n%d pack(s) found next to the Provisioner. Install any?" % len(packs), False):
                fw = _remembered_path("stump_app_dir") or STUMP_APP_DIR
                chosen = choose_packs(fw, packs)
                if chosen:
                    print("  (Their settings keep their defaults: run the full wizard to set them.)")
                    install_packs(port, chosen)
    elif "--list-packs" in args:
        fw = _remembered_path("stump_app_dir") or STUMP_APP_DIR
        packs = discover_packs(args[args.index("--list-packs") + 1:])
        if not packs:
            print("No packs found. Put pack folders or .zip files in %s" % PACKS_DIR)
        for pack in packs:
            describe_pack(pack, firmware_plugin_api(fw))
            print()
    elif "--install-pack" in args:
        idx = args.index("--install-pack")
        if len(args) < idx + 3:
            print("Usage: provisioner.py --install-pack PORT PACK [PACK ...]   (a folder or .zip)")
            sys.exit(1)
        port = args[idx + 1]
        fw = _remembered_path("stump_app_dir") or STUMP_APP_DIR
        packs = [p for p in (load_pack(x) for x in args[idx + 2:]) if p]
        if not packs:
            print("No pack there (a pack is a folder or .zip with a pack.json).")
            sys.exit(1)
        chosen = choose_packs(fw, packs)
        sys.exit(0 if (chosen and install_packs(port, chosen)) else 1)
    elif "--remove-plugin" in args:
        idx = args.index("--remove-plugin")
        if len(args) < idx + 3:
            print("Usage: provisioner.py --remove-plugin PORT NAME")
            sys.exit(1)
        sys.exit(0 if remove_plugin(args[idx + 1], args[idx + 2]) else 1)
    elif "--hash-plugin" in args or "--hash-pack" in args:
        flag = "--hash-plugin" if "--hash-plugin" in args else "--hash-pack"
        idx = args.index(flag)
        if idx + 1 >= len(args):
            print("Usage: provisioner.py %s FOLDER" % flag)
            sys.exit(1)
        target = Path(args[idx + 1])
        if flag == "--hash-plugin":
            for rel, h in hash_plugin(target).items():
                print("  %s  %s" % (h, rel))
        else:
            spec = hash_pack(target)
            print("  hashed %s %s: %d plugin(s), %d SD file(s)" % (
                spec["name"], spec["version"], len(spec.get("plugins", [])), len(spec.get("hashes", {}))))
        lint = [w for name in ([target.name] if flag == "--hash-plugin" else
                              json.load(open(target / "pack.json")).get("plugins", []))
                for w in lint_plugin(target if flag == "--hash-plugin" else target / name)]
        for f, n, why in lint:
            print("  NOTE %s line %d: %s" % (f, n, why))
    elif "--version" in args:
        idx = args.index("--version")
        for line in release_lines(checkout_status(fetch=True)):
            print(line)
        if idx + 1 < len(args):
            ok, text, _ = release_check(args[idx + 1])
            print(("CAM: " if ok else red("CAM: ")) + text)
            sys.exit(0 if ok else 1)
    elif "--get-ip" in args:
        idx = args.index("--get-ip")
        if idx + 1 >= len(args):
            print("Usage: provisioner.py --get-ip PORT")
            sys.exit(1)
        port = args[idx + 1]
        addrs = get_stump_ip(port)
        if addrs and (addrs.get("sta") or addrs.get("ap")):
            _print_discovered_addrs(addrs)
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
