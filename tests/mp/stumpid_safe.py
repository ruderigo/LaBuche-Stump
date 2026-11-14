# stumpid can't be left switched off (1.0.0-beta.73). On the kit: the board
# reset while stumpid was starting (Arborist resets it, or stops it for a
# prompt, each time it connects), safe mode switched stumpid off, and then
# /auth and /whoami were "unknown command" and /admin -- where it's turned
# back on -- refused every password, stumpid being what checks it.
import sys, os
class _M: pass
_m = _M(); _m.aes = lambda *a, **k: None
sys.modules['ucryptolib'] = _m; sys.modules['cryptolib'] = _m
sys.path.insert(0, '.')
import json
import config
config.AUTH_ADMIN_PASSWORD = 'Zebra-77-secret'
config.FSERVBOT_OP_PASSWORD = 'op'
FAIL = []


def check(name, cond, info=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  ' + repr(info)[:400]))
    if not cond:
        FAIL.append(name)


# The board as the kit's WROVER was: stumpid starting when it last stopped,
# and switched off by an earlier firmware's safe mode.
with open('plugins_state.json', 'w') as f:
    f.write(json.dumps({"off": ["stumpid"], "auto_off": {"stumpid": "the node restarted while it was starting"}}))
with open('plugins_loading', 'w') as f:
    f.write(json.dumps({"plugin": "stumpid"}))
import plugins, rrc, i18n
running = plugins.load_all()
check("stumpid starting when the board last stopped: it starts anyway", "stumpid" in running, running)
st = plugins._load_state()
check("... switched off by an older firmware: back on, and saved so", "stumpid" not in st["off"] and "stumpid" not in st["auto_off"], st)
check("... the other built-in one too", "fservbot" in running, running)

import barkeep
CID = "w1"
i18n.set_lang(CID, "en")

# -- /admin's password without stumpid ------------------------------------------
plugins._plugins["stumpid"]["state"] = "failed"
plugins._plugins["stumpid"]["error"] = "MemoryError: memory allocation failed"
check("(stumpid not running: no identity service)", plugins.service_lookup("identity") is None)
check("/admin's password without stumpid: the one in config.py works", barkeep._check_admin_pw_now("Zebra-77-secret"))
check("... a wrong one doesn't", not barkeep._check_admin_pw_now("wrong"))
check("... nor a blank one", not barkeep._check_admin_pw_now(""))
saved = config.AUTH_ADMIN_PASSWORD
config.AUTH_ADMIN_PASSWORD = ""
check("... no admin password set: fservbot's operator password, as stumpid does", barkeep._check_admin_pw_now("op"))
config.FSERVBOT_OP_PASSWORD = ""
check("... none set at all: nothing gets in", not barkeep._check_admin_pw_now("") and not barkeep._check_admin_pw_now("op"))
config.AUTH_ADMIN_PASSWORD, config.FSERVBOT_OP_PASSWORD = saved, "op"

# -- its commands say why, not "unknown command" --------------------------------
for cmd in ("/auth", "/whoami", "/admin x", "/invite Bob"):
    lines, _ = rrc.handle_input(CID, "main", cmd)
    out = " ".join(lines)
    check("%s with stumpid down: says identity checks are unavailable, and why" % cmd.split()[0],
          "stumpid isn't running" in out and "MemoryError" in out and "unknown command" not in out, out)
lines, _ = rrc.handle_input(CID, "main", "/nosuchthing")
check("a command that really doesn't exist: still unknown", "unknown command" in " ".join(lines), lines)
plugins._plugins["stumpid"]["state"] = "on"
plugins._plugins["stumpid"]["error"] = ""
lines, _ = rrc.handle_input(CID, "main", "/auth")
check("stumpid running: /auth answers as ever", lines and lines[0].startswith("AUTH-CHALLENGE"), lines)

# -- a failed start tried again once the node is up --------------------------------
plugins._unregister("stumpid")
plugins._plugins["stumpid"]["state"] = "failed"
plugins._plugins["stumpid"]["error"] = "OSError: 5"
again = plugins.retry_core()
check("a built-in plugin that failed to start: tried again once the node is up", again == ["stumpid"] and plugins._active("stumpid"), again)
lines, _ = rrc.handle_input(CID, "main", "/auth")
check("... and /auth works", lines and lines[0].startswith("AUTH-CHALLENGE"), lines)
check("... its gate there once, not twice", len([g for g in plugins._gates if g[2] == "stumpid"]) == 1, plugins._gates)
check("nothing failed: nothing tried again", plugins.retry_core() == [])

# -- stopped on purpose while starting: no safe mode next time -----------------
real = plugins.load_one


def stop(name):
    if name == "fservbot":
        raise KeyboardInterrupt
    return real(name)


plugins.load_one = stop
try:
    plugins.load_all()
    check("(Ctrl+C reached the loader)", False)
except KeyboardInterrupt:
    pass
plugins.load_one = real
check("stopped on purpose (Ctrl+C) while a plugin was starting: no safe mode at the next start",
      plugins._read_marker() is None, plugins._read_marker())

print("FAILURES", FAIL)
