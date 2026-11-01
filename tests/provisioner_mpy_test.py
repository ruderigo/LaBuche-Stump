# The Provisioner putting modules on a board compiled (.mpy), against a
# simulated board: compiled when the board's MicroPython and mpy-cross
# agree, source otherwise; boot.py, main.py and config.py always source;
# plugins compiled too; the .py a compiled module replaces removed from
# the board; the size check against what was actually sent.
# Needs mpy-cross 1.28 (pip install mpy-cross==1.28.0.post2, or
# MPY_CROSS=/path/to/mpy-cross).
import os, re, sys, tempfile, shutil
from pathlib import Path
W = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(W))
import builtins
builtins.input = lambda *a: ""
import provisioner as P

FAIL = []


def check(n, c, i=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else "  " + repr(i)[:600]))
    if not c:
        FAIL.append(n)


if os.environ.get("MPY_CROSS"):
    P._MPY.update(cmd=[os.environ["MPY_CROSS"]])
    P._MPY["ver"] = re.search(r"mpy v(\d+\.\d+)", __import__("subprocess").run(
        [os.environ["MPY_CROSS"], "--version"], capture_output=True, text=True).stdout).group(1)
cmd, ver = P._mpy_cross_cmd()
if not cmd:
    print("skip: no mpy-cross here (pip install mpy-cross==1.28.0.post2)")
    print("FAILURES", [])
    sys.exit(0)

fw = W / "final_firmware"
calls = []
BOARD = {"mpy": "6.3", "config": "CONFIG:1 TRACES:1"}


def fake_run(c, timeout=None, **kw):
    calls.append(c)
    s = " ".join(str(x) for x in c)
    if "exec" in c and "_mpy" in s:
        return True, "MPY:" + BOARD["mpy"] if BOARD["mpy"] else ""
    if "exec" in c and "TRACES" in s:
        return True, BOARD["config"]
    if "exec" in c and "def walk" in s:
        sizes = {}
        for k in calls:
            if "cp" in k and str(k[-1]).startswith(":") and not str(k[-1]).startswith(":/sd"):
                sizes[k[-1][1:]] = Path(k[-2]).stat().st_size
        return True, repr(sizes)
    return True, ""


P.run = fake_run
P.resolve_path = lambda *a, **k: fw
P.ask_yes_no = lambda *a, **k: True
P.install_tools_on_node = lambda port: None
P.CONFIG_BACKUPS = Path(tempfile.mkdtemp()) / "backups"


def dests():
    return [c[-1] for c in calls if "cp" in c]


def removed():
    for c in calls:
        if "exec" in c and "os.remove" in str(c[-1]):
            return str(c[-1])
    return ""


# 1. The board loads what mpy-cross makes: compiled.
calls.clear(); P._MPY.pop("said", None)
ok = P.upload_stump_app("/dev/fake")
d = dests()
check("modules go compiled", ":rrc.mpy" in d and ":barkeep.mpy" in d and ":urns/lxmf.mpy" in d
      and ":rrc.py" not in d, [x for x in d if "rrc" in x])
check("main.py stays source", ":main.py" in d and ":main.mpy" not in d, [x for x in d if "main" in x])
check("precompiled native modules (lib/) go as they are", ":lib/bz2_fast_xtensawin.mpy" in d, [x for x in d if "lib/" in x])
check("the built-in plugins too", ":stumpid/install.mpy" in d and ":fservbot/core.mpy" in d, [x for x in d if "stumpid" in x])
check("other files as they are", any(x.startswith(":web/") for x in d), [x for x in d if "web" in x])
check("the size check passes against what was sent", ok)
r = removed()
check("the .py each compiled module replaces is removed from the board",
      "'rrc.py'" in r and "'stumpid/install.py'" in r and "'boot.py'" not in r, r[:300])
sent = [(Path(c[-2]), fw / (c[-1][1:-4] + ".py")) for c in calls if "cp" in c and str(c[-1]).endswith(".mpy")]
sent = [(m, s) for m, s in sent if s.is_file()]
mp = sum(m.stat().st_size for m, s in sent)
src = sum(s.stat().st_size for m, s in sent)
check("smaller than the source (well under 60%)", mp < 0.6 * src, (mp, src))
print("  %d KB of source went as %d KB" % (src // 1024, mp // 1024))

# 2. A fresh board: config.py goes, as source.
calls.clear(); BOARD["config"] = "CONFIG:0 TRACES:0"
P.upload_stump_app("/dev/fake")
check("a fresh board's config.py goes as source", ":config.py" in dests() and ":config.mpy" not in dests())
BOARD["config"] = "CONFIG:1 TRACES:1"

# 3. An older MicroPython on the board: source, and stale .mpy removed.
calls.clear(); BOARD["mpy"] = "6.2"; P._MPY.pop("said", None)
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()) as said:
    ok = P.upload_stump_app("/dev/fake")
d = dests()
check("an older MicroPython on the board: source", ":rrc.py" in d and ":rrc.mpy" not in d and ok, [x for x in d if "rrc" in x])
check("... and it says why", "loads .mpy 6.2" in said.getvalue(), said.getvalue()[:300])
check("... and a .mpy left from before is removed", "'rrc.mpy'" in removed(), removed()[:200])

# 4. Can't tell the board's version: source (the safe way).
calls.clear(); BOARD["mpy"] = None
P.upload_stump_app("/dev/fake")
check("board version unknown: source", ":rrc.py" in dests() and ":rrc.mpy" not in dests())
BOARD["mpy"] = "6.3"

# 5. A pack's plugin, compiled too.
calls.clear()
ok = P.upload_plugin_folder("/dev/fake", W / "packs" / "tavern-pack" / "tavern")
check("a pack's plugin goes compiled", ":tavern/install.mpy" in dests() and ok, dests())

# 6. A module that doesn't compile goes as source (and says so).
bad = Path(tempfile.mkdtemp()) / "brokenplug"
bad.mkdir()
(bad / "install.py").write_text("def setup(stump):\n    return (\n")
(bad / "plugin.json").write_text('{"name": "brokenplug", "files": ["brokenplug/install.py"]}')
calls.clear()
with contextlib.redirect_stdout(io.StringIO()) as said:
    P.upload_plugin_folder("/dev/fake", bad)
check("a module that doesn't compile: sent as source, and said",
      ":brokenplug/install.py" in dests() and "didn't compile" in said.getvalue(), (dests(), said.getvalue()[-300:]))
shutil.rmtree(bad.parent, ignore_errors=True)

# 7. Removing a compiled plugin: recognised as a plugin.
calls.clear()
P.remove_plugin("/dev/fake", "tavern")
check("--remove-plugin knows a compiled plugin", any("install.mpy" in str(c[-1]) for c in calls if "exec" in c))

print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
