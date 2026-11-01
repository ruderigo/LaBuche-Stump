#!/usr/bin/env python3
"""Builds Arborist: one HTML page that makes a Freenove ESP32-S3-CAM
into a Stump, updates it, changes its settings and watches it start, from
Chrome or Edge, saved on the computer and opened from there -- nothing to
install, no certificate.

    python3 scripts/build_arborist.py dist/ [--micropython FILE.bin]

Writes dist/Arborist-<release>.html, holding:

  esptool-js        the flashing code (Apache-2.0; its licence in the page)
  MicroPython 1.28  the ESP32_GENERIC_S3 SPIRAM_OCT image (MIT), the one the
                    Provisioner flashes: --micropython, or downloaded from
                    micropython.org and checked against MICROPYTHON_SHA256
  the Stump         the board's files from final_firmware/ (the plugins too),
                    modules compiled to .mpy for MicroPython 1.28 when
                    mpy-cross 1.28 is here (pip install mpy-cross==1.28.0.post2)
  a config.py       template: final_firmware/config.py with every name,
                    password and Wi-Fi taken out (a fresh Stump's values)

Packed as one gzip, in base64, inside the page; each file's SHA-256 is
checked on the board after it's copied.
"""
import base64, gzip, hashlib, io, json, os, re, subprocess, sys, tempfile, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FW = ROOT / "final_firmware"
HERE = Path(__file__).resolve().parent / "arborist"
sys.path.insert(0, str(ROOT))
import provisioner as P  # noqa: E402

MICROPYTHON_URL = P.CAM_FIRMWARE_LATEST_URL
MICROPYTHON_VERSION = "1.28.0"
MICROPYTHON_MPY = "6.3"          # the .mpy version MicroPython 1.28 loads
# The image as micropython.org served it to CI (1 758 064 bytes, 2026-10-25);
# the build stops on any other. STUMP_MICROPYTHON_SHA256 overrides it
# (another image: "" turns the check off and says the hash).
MICROPYTHON_SHA256 = os.environ.get("STUMP_MICROPYTHON_SHA256",
                                    "67c19ae123d84152019b57526ed5291dd0a2b4edd87655c5f76b46c9a62ff5dd") or None

# The template: config.py with these set to a fresh, unnamed Stump's values
# (the page then writes the form's answers into it, as for a Stump being
# changed: settingsFrom/writeSettings in arborist/repl.js). Every other
# line is config.py's own.
ASKED = ("NODE_NAME", "WIFI_SSID", "WIFI_PASS", "AUTH_ADMIN_PASSWORD", "FSERVBOT_OP_PASSWORD")
HELTEC_FRESH = {"HELTEC_ENABLED": False, "HELTEC_HOST": "192.168.4.2", "HELTEC_PORT": 7633}
FRESH = {
    "SSID_NAME": None, "SSID_INCLUDE_IP": False, "BOT_NAME": "BarKeep", "MESH_GREETING": "",
    "AUTH_MODE": "open", "CREDITS_ENABLED": False, "FEATURES": ["chat", "billboard", "files"], "THEME": "paper",
    "CHAIN_ENABLED": False, "CHAIN_UPLINK_SSID": "", "CHAIN_UPLINK_PASS": "", "CHAIN_SECRET": "",
    "CHAIN_SUBNET": 4, "CHAIN_CHANNEL": 11, "CHAIN_LAN": True,
}
HELTEC = ("HELTEC_ENABLED", "HELTEC_HOST", "HELTEC_PORT")


def release():
    r = P._folder_release(FW)
    if not r:
        raise SystemExit("no STUMP_RELEASE in final_firmware/example_node.py")
    return r


def config_template():
    """config.py with the asked settings empty, the fresh defaults set,
    and the Heltec bridge off.
    Fails if a setting is missing, or if any password or name of the
    folder's own config.py is still in it."""
    src = (FW / "config.py").read_text(encoding="utf-8")
    out, seen, block = [], set(), False
    for line in src.splitlines(keepends=True):
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.startswith((" ", "\t", "#")) and key in ASKED:
            out.append("%s = ''\n" % key); seen.add(key); continue
        if "=" in line and not line.startswith((" ", "\t", "#")) and key in FRESH:
            out.append("%s = %r\n" % (key, FRESH[key])); seen.add(key); continue
        if "Heltec Bridge" in line:
            block = True
        if block:
            ind = line[:len(line) - len(line.lstrip())]
            for k, h in (('"enabled"', "HELTEC_ENABLED"), ('"target_host"', "HELTEC_HOST"), ('"target_port"', "HELTEC_PORT")):
                if line.lstrip().startswith(k):
                    line = "%s%s: %r,\n" % (ind, k, HELTEC_FRESH[h]); seen.add(h)
                    if h == "HELTEC_PORT":
                        block = False
        out.append(line)
    text = "".join(out)
    for key in list(ASKED) + list(FRESH):
        if key not in seen:                   # a setting config.py doesn't have yet: added
            text += ("%s = ''\n" % key) if key in ASKED else ("%s = %r\n" % (key, FRESH[key]))
    missing = [h for h in HELTEC if h not in seen]
    if missing:
        raise SystemExit("config.py: no Heltec Bridge %s to fill in" % ", ".join(missing))
    # Nothing of the folder's own node may be left: its passwords, names, Wi-Fi.
    ns = {}
    exec(compile(re.sub(r"^from lora_boards import.*$", "LORA_BOARDS = {}", src, flags=re.M), "config.py", "exec"), ns)
    for key in list(ASKED) + list(FRESH):
        v = ns.get(key)
        if isinstance(v, str) and v and v != FRESH.get(key) and ("'%s'" % v in text or '"%s"' % v in text
                                                                 or ("PASS" in key and v in text)):
            raise SystemExit("the template still has %s's value from final_firmware/config.py" % key)
    compile(text, "config.py", "exec")      # still Python
    return text


def board_files():
    """[(path on the board, local file)]: what the Provisioner uploads,
    the plugins' files too, config.py aside (the page writes it)."""
    rels = [f for f in P.STUMP_APP_FILES if (FW / f).is_file() and f != "config.py"]
    for name, spec in P.discover_plugins(FW):
        rels += [f for f in P.plugin_files(FW / name, spec) if P._belongs_on_board(f)]
    return sorted(set(rels))


def compiled(rels, tmp):
    """[(path on the board, bytes)]: modules as .mpy when mpy-cross makes
    MicroPython 1.28's version, as source otherwise."""
    cmd, ver = P._mpy_cross_cmd()
    use = bool(cmd) and ver == MICROPYTHON_MPY
    print("  modules: %s" % ("compiled (.mpy %s)" % ver if use else
                             "as source (mpy-cross %s here, MicroPython 1.28 needs %s)" % (ver, MICROPYTHON_MPY) if cmd
                             else "as source (no mpy-cross: pip install mpy-cross==1.28.0.post2)"))
    out = []
    for r in rels:
        src = FW / r
        if use and r.endswith(".py") and r.split("/")[-1] not in P.MPY_SOURCE_ONLY:
            dest = Path(tmp) / (r[:-3] + ".mpy")
            dest.parent.mkdir(parents=True, exist_ok=True)
            p = subprocess.run(cmd + ["-o", str(dest), "-s", r, str(src)], capture_output=True, text=True)
            if p.returncode != 0:
                raise SystemExit("%s didn't compile: %s" % (r, p.stderr.strip()[-300:]))
            out.append((r[:-3] + ".mpy", dest.read_bytes()))
        else:
            out.append((r, src.read_bytes()))
    return out, (ver if use else None)


def micropython(path):
    if path:
        data = Path(path).read_bytes()
    else:
        print("  downloading %s" % MICROPYTHON_URL)
        with urllib.request.urlopen(MICROPYTHON_URL, timeout=120) as r:
            data = r.read()
    h = hashlib.sha256(data).hexdigest()
    if data[:1] != b"\xe9" or len(data) < 500000:
        raise SystemExit("that isn't an ESP32 firmware image (%d bytes)" % len(data))
    if MICROPYTHON_SHA256 and h != MICROPYTHON_SHA256:
        raise SystemExit("MicroPython image: SHA-256 %s, expected %s" % (h, MICROPYTHON_SHA256))
    if not MICROPYTHON_SHA256:
        print("  MicroPython image SHA-256 %s (not pinned yet: STUMP_MICROPYTHON_SHA256)" % h)
        if os.environ.get("GITHUB_ACTIONS"):       # shown on the run's page, and readable from the API
            print("::notice title=MicroPython image SHA-256::%s %d bytes" % (h, len(data)))
    return data, h


def pack(entries, header):
    """One blob: 4 bytes (little-endian) header length, the JSON header,
    then each entry's bytes in order; gzipped. header["files"] gets each
    entry's [name, offset, length, sha256]."""
    body, files, off = io.BytesIO(), [], 0
    for name, data in entries:
        files.append([name, off, len(data), hashlib.sha256(data).hexdigest()])
        body.write(data)
        off += len(data)
    header = dict(header, files=files)
    h = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    raw = len(h).to_bytes(4, "little") + h + body.getvalue()
    return gzip.compress(raw, compresslevel=9, mtime=0)


def build(out_dir, mp_path=None):
    rel = release()
    print("Arborist, %s" % rel)
    with tempfile.TemporaryDirectory() as tmp:
        app, mpy = compiled(board_files(), tmp)
    mp, mp_sha = micropython(mp_path)
    tpl = config_template()
    entries = [("micropython.bin", mp)] + [("app/" + p, d) for p, d in app]
    blob = pack(entries, {"release": rel, "micropython": MICROPYTHON_VERSION, "micropython_sha256": mp_sha,
                          "mpy": mpy, "config_template": tpl})
    esptool = (FW / "tools_payload" / "flasher" / "esptool-bundle.js").read_text(encoding="utf-8")
    m = re.search(r"export\{([^}]*)\}\s*;?\s*$", esptool)
    if not m:
        raise SystemExit("esptool-bundle.js: no export list at its end")
    names = ",".join("%s:%s" % (b.strip(), a.strip()) for a, b in (x.split(" as ") for x in m.group(1).split(",")))
    esptool = esptool[:m.start()] + "window.esptool={%s};" % names
    lic = (FW / "tools_payload" / "flasher" / "esptool-js-LICENSE.txt").read_text(encoding="utf-8")
    page = (HERE / "page.html").read_text(encoding="utf-8")
    ssid = re.search(r'^DEFAULT_SSID = "([^"]*)"', (FW / "captive_portal.py").read_text(encoding="utf-8"), re.M).group(1)
    for k, v in (("@@RELEASE@@", rel), ("@@SSID@@", ssid), ("@@MICROPYTHON@@", MICROPYTHON_VERSION),
                 ("/*@@REPL@@*/", (HERE / "repl.js").read_text(encoding="utf-8")),
                 ("/*@@ESPTOOL@@*/", esptool.replace("</script", "<\\/script")),
                 ("@@ESPTOOL_LICENSE@@", lic.replace("&", "&amp;").replace("<", "&lt;")),
                 ("@@PAYLOAD@@", base64.b64encode(blob).decode())):
        if k not in page:
            raise SystemExit("page.html has no %s" % k)
        page = page.replace(k, v)
    out = Path(out_dir) / ("Arborist-%s.html" % re.sub(r"[^A-Za-z0-9._-]", "_", rel))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    size = sum(len(d) for _, d in app)
    print("  %d files (%d KB), MicroPython %d KB -> %s (%d KB)" % (len(app), size // 1024, len(mp) // 1024, out, out.stat().st_size // 1024))
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    mp = None
    if "--micropython" in a:
        i = a.index("--micropython")
        mp = a[i + 1]
        del a[i:i + 2]
    if len(a) != 1:
        raise SystemExit(__doc__)
    build(a[0], mp)
