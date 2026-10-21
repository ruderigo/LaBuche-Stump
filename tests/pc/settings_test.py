# /admin's Stump settings (settings.py): the Provisioner's answers changed
# in place -- config.py's lines rewritten (the rest of the file, a dict
# over several lines included, untouched), bad values refused, the running
# Stump changed at once where it can (credits, weights, access mode,
# features), the rest flagged for a restart. Nothing else is touched (the
# identity, the history). Run: python3 tests/pc/settings_test.py
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, "pc", "pc_compat"), os.path.join(ROOT, "final_firmware")]
import stump_host
stump_host.install(os.path.join(ROOT, "final_firmware"))

FAIL = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:500]))
    if not cond:
        FAIL.append(name)
        if os.environ.get("GITHUB_ACTIONS"):
            print("::error::" + (name + ": " + repr(info)[:1500]).replace("\n", "%0A"))


import settings as S

CFG = '''# a Stump's config.py
NODE_NAME = 'LaBuche'
BOT_NAME = "Concierge"   # the bot
MESH_GREETING = 'Bonjour = hi'
CONFIG = {
    "interfaces": [
        {"type": "X", "name": "a = b"},
    ],
}
CREDITS_ENABLED = False
CREDIT_WEIGHTS = {
    'video': 3, 'music': 2,
    'document': 1, 'other': 1}
FEATURES = ['chat', 'billboard', 'files']
AUTH_MODE = 'open'
THEME = 'paper'
'''
# (The computer's files map "/" to the Stump folder, as on the board.)
STUMP_PATH = "/settings_test_config.py"
path = os.path.join(ROOT, "final_firmware", "settings_test_config.py")
open(path, "w").write(CFG)
ok = S.write_config({"BOT_NAME": "Barkeep", "CREDIT_WEIGHTS": {"video": 5, "music": 2, "document": 1, "other": 0},
                     "FEATURES": ["chat"], "SENSOR_HUB": ""}, STUMP_PATH)
out = open(path).read()
ns = {}
exec(out, ns)
check("written: the changed lines, as Python config.py can load", ok and ns["BOT_NAME"] == "Barkeep"
      and ns["CREDIT_WEIGHTS"] == {"video": 5, "music": 2, "document": 1, "other": 0} and ns["FEATURES"] == ["chat"], out)
check("a dict over several lines replaced whole (no leftover lines)", "'document': 1, 'other': 1}" not in out, out)
check("the rest untouched: other settings, the CONFIG block, comments", ns["NODE_NAME"] == "LaBuche"
      and ns["MESH_GREETING"] == "Bonjour = hi" and ns["CONFIG"]["interfaces"][0]["name"] == "a = b"
      and ns["THEME"] == "paper" and out.startswith("# a Stump's config.py"), out)
check("a setting not there yet: added", ns.get("SENSOR_HUB") == "", out)
check("no temporary file left behind", not os.path.exists(path + ".tmp"))

# The /admin form.
base = {"node_name": "LaBuche", "bot_name": "Concierge", "greeting": "Hi", "economy": "free",
        "auth_mode": "hybrid", "f_chat": "1", "f_files": "1"}
ch, bad = S.parse_form(base)
check("form: read", bad is None and ch["CREDITS_ENABLED"] is False and ch["FEATURES"] == ["chat", "files"]
      and ch["NODE_NAME"] == "LaBuche" and (ch.get("AUTH_MODE") in (None, "hybrid")), (ch, bad))
ch, bad = S.parse_form(dict(base, economy="custom", w_video="4", w_music="2", w_document="1", w_other="0"))
check("form: a custom economy", ch["CREDITS_ENABLED"] and ch["CREDIT_WEIGHTS"] == {"video": 4, "music": 2,
                                                                                   "document": 1, "other": 0})
for field, value, why in (("node_name", "", "an empty name"), ("node_name", "x" * 21, "a name too long for the Wi-Fi"),
                          ("bot_name", " ", "an empty bot name"), ("greeting", "x" * 201, "an auto-reply over 200"),
                          ("economy", "lottery", "an unknown economy")):
    check("form: %s refused" % why, S.parse_form(dict(base, **{field: value}))[1] == field)
check("form: a negative weight refused", S.parse_form(dict(base, economy="custom", w_video="-1", w_music="1",
                                                           w_document="1", w_other="1"))[1] == "w_video")
check("form: no features at all is allowed (the Provisioner allows it too)",
      S.parse_form({k: v for k, v in base.items() if not k.startswith("f_")})[0]["FEATURES"] == [])

# Applied to a running Stump (its config.py in the Stump folder, "/").
S.CONFIG_FILE = STUMP_PATH
import fserv, features
fserv.CREDITS_ENABLED, fserv.CLASS_WEIGHTS = False, {"video": 3, "music": 2, "document": 1, "other": 1}
features._CONFIGURED = ["chat", "billboard", "files"]
import config
config.NODE_NAME, config.BOT_NAME, config.MESH_GREETING = "LaBuche", "Barkeep", ""
cur = S.current()
form = {"node_name": cur["NODE_NAME"], "bot_name": cur["BOT_NAME"], "greeting": cur["MESH_GREETING"],
        "economy": "standard", "auth_mode": "open", "f_chat": "1", "f_billboard": "1", "f_files": "1"}
saved, later = S.apply(S.parse_form(form)[0])
check("the economy turned on: at once, nothing waits for a restart", saved and fserv.CREDITS_ENABLED and later == [],
      (saved, later, fserv.CREDITS_ENABLED))
saved, later = S.apply(S.parse_form(dict(form, f_billboard=""))[0])
check("a feature off: the pages follow at once", saved and not features.enabled("billboard") and later == [], later)
saved, later = S.apply(S.parse_form(dict(form, f_billboard="", bot_name="Sam"))[0])
check("the bot's name: kept, applied at the next restart (said so)", saved and later == ["BOT_NAME"]
      and "BOT_NAME = 'Sam'" in open(path).read(), (later, open(path).read()))
saved, later = S.apply(S.parse_form(dict(form, f_billboard="", f_chat="", bot_name="Sam"))[0])
check("chat off: the pages at once, chat over the mesh at the next restart", later == ["FEATURES"], later)
saved, later = S.apply(S.parse_form(dict(form, f_billboard="", f_chat="", bot_name="Sam"))[0])
check("saved again with nothing changed: nothing written, nothing waits", saved and later == [], later)
import barkeep
saved, later = S.apply(S.parse_form(dict(form, f_billboard="", f_chat="", bot_name="Sam"))[0])
check("the librarian's firefly: off at once (no bubble on the pages), kept in config.py",
      saved and later == [] and not S.librarian_on() and barkeep._ask_drawer("en") == ""
      and "LIBRARIAN = False" in open(path).read(), (later, open(path).read()))
saved, later = S.apply(S.parse_form(dict(form, f_billboard="", f_chat="", bot_name="Sam", librarian="1"))[0])
check("... and back on", S.librarian_on() and "id='ask'" in barkeep._ask_drawer("en"))
check("economy names: free / standard / custom", [S.economy({"CREDITS_ENABLED": e, "CREDIT_WEIGHTS": w}) for e, w in
      ((False, S.STANDARD_WEIGHTS), (True, S.STANDARD_WEIGHTS), (True, dict(S.STANDARD_WEIGHTS, video=9)))]
      == ["free", "standard", "custom"])
# A computer's "card" (PC_STORAGE_MB): in the card, changed at once.
st = S.storage()
check("storage: the size, what's in sd/, the disk's free space", st and st["mb"] == 2048 and st["disk_free"] > 0
      and st["max_mb"] >= 2048, st)
html = barkeep._render_admin_setup_section("en", "pw")
check("the card has a Storage group (a computer)", "name='storage_mb'" in html and "value='2048'" in html
      and "Storage" in html and "free" in html, html[-1500:])
mb = min(4096, st["max_mb"])
saved, later = S.apply(S.parse_form(dict(form, storage_mb=str(mb)))[0])
check("storage changed: at once, kept in config.py", saved and "PC_STORAGE_MB" not in later and config.PC_STORAGE_MB == mb
      and "PC_STORAGE_MB = %d" % mb in open(path).read() and S.current()["PC_STORAGE_MB"] == mb, (later, saved))
check("... the card is that size now", os.statvfs("/sd")[2] <= mb * S.MB)
for v in ("100", "lots", str(st["max_mb"] + 10 ** 6)):
    check("storage %r refused" % v, S.parse_form(dict(form, storage_mb=v))[1] == "storage_mb")
check("a form without the storage field (the CAM's) leaves it alone", "PC_STORAGE_MB" not in S.parse_form(form)[0])
os.remove(path)
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
