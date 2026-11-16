# The Meter pack: a tester's counter of what the Stump sends people's
# apps (bytes of text, bytes as sent, messages that fit one LoRa packet),
# the same on a firmware with packed text (1.0.0-beta.78) and one
# without, so a kit is measured before and after. It only counts.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import config
config.AUTH_ADMIN_PASSWORD = 'pw'
import plugins, rrc, rrc_mesh, i18n
from urns import lxmf
try:
    from urns import ztext
except ImportError:
    ztext = None            # a firmware before packed text
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


plugins.load_all()
check("the pack starts (plugin API 2)", plugins._plugins.get('meter', {}).get('state') == 'on', plugins._plugins.get('meter'))
import meter.install as M
HAS = hasattr(lxmf, "PACKED_TEXT")          # this firmware packs text (1.0.0-beta.78 on)
print("firmware with packed text:", HAS)
rrc.NODE_INFO = {"version": "x", "release": "1.0.0-test"}

A = 'cd' * 16           # an app that reads packed text (FireFly, build 74)
B = 'ef' * 16           # one that doesn't (Sideband)
W = 'v' + 'w' * 20      # someone on the Wi-Fi
for h in (A, B, W):
    i18n.set_lang(h, 'en')
for h in (A, B):
    rrc.touch_user(h, nick='t' + h[:2], room='lora-mesh')
    rrc_mesh._peers[h] = {"nick": 't' + h[:2], "room": "lora-mesh", "stump": False}


class OldRouter:        # a firmware before packed text: nothing to ask
    pass


def cmd(h, line, room='lora-mesh'):
    plugins._cmd_use.clear()        # (the core's own pace: one plugin command per person per 1.5 s)
    replies, _ = rrc.handle_input(h, room, line)
    return replies


def deliver(h, lines, title=""):
    """As rrc_mesh sends a reply: one message, through the mesh_out hooks."""
    text = "\n".join(lines)
    out = plugins.mesh_out(h, text, title)
    check("the message goes out unchanged", out == (text, title), out)
    return text


# 1. A firmware without packed text (beta.73): everything counted as it is.
lxmf.ROUTER[0] = OldRouter()
r = cmd(A, '/meter')
check("/meter: three lines", len(r) == 3, r)
check("... the release, and that this firmware doesn't pack", "1.0.0-test" in r[0] and "not in this firmware" in r[0], r[0])
check("... nothing counted yet", " 0 messages" in r[1] and "one packet: 0 of 0" in r[2], r)
# A board starts in the year 2000 and jumps to today when it learns the
# time: the minutes are counted, not read off the clock (a kit answered
# "14134732 min").
M._minute(); M._minute(); M._minute()
r = cmd(A, '/meter')
check("three minutes counted: 3 min, whatever the clock says", "in 3 min:" in r[1] and "since" not in M._n, r[1])
check("... each fits a mesh line", all(len(l) <= plugins.MESH_LINE_CHARS for l in r), [len(l) for l in r])
deliver(A, r)
t3 = cmd(A, '/meter 3')
check("/meter 3: the three test lines, numbered", len(t3) == 3 and [l[:10] for l in t3] == ["meter 1/3:", "meter 2/3:", "meter 3/3:"], t3)
check("... whole (none cut at the mesh's 200 characters)", all(l.endswith(".") and len(l) < 200 for l in t3), [len(l) for l in t3])
text3 = deliver(A, t3)
n3 = len(text3.encode())
r = cmd(A, '/meter last')
check("/meter last on the old firmware: sent as it is, over a link",
      ("3 line(s), %d B of text → %d B sent (100%%), over a link" % (n3, n3)) in r[0], r)
check("... the text really is too long for one packet as it is", n3 > M.ONE_PACKET)
r = cmd(A, '/meter 2')
check("a second test at once: asked to wait", len(r) == 1 and "One test at a time" in r[0], r)
M._asked.clear()
t1 = cmd(A, '/meter 1')
deliver(A, t1)
r = cmd(A, '/meter last')
check("one line: one packet either way", "1 line(s)" in r[0] and "(100%), one packet" in r[0], r)
r = cmd(A, '/meter')
check("the totals: 3 messages, none packed", " 3 messages" in r[1] and "(100%), 0 packed" in r[1], r[1])
check("... 2 of them in one packet, as plain text the same", "one packet: 2 of 3 (as plain text: 2)" in r[2], r[2])
r = cmd(B, '/meter last')
check("someone who ran no test: told how", "No test yet" in r[0], r)
r = cmd(A, '/meter nonsense')
check("anything else: how to use it", r and r[0].startswith("/meter ·"), r)
check("!meter is not a command (nothing of it in the room)", not any('meter' in (m.get('body') or '') and m.get('kind') != 'system'
      for m in rrc.since('lora-mesh', 0) if (m.get('body') or '').startswith('/')), rrc.since('lora-mesh', 0)[-3:])
r = cmd(A, '/meter reset')
check("/meter reset", "zero" in r[0] and M._n["msgs"] == 0 and not M._tests, (r, M._n))

# 2. Whatever the router is or does, counting never breaks a message.
class Broken:
    packs = {}

    def packs_for(self, d):
        raise RuntimeError("boom")

    def packed_size(self, d, t):
        raise RuntimeError("boom")


lxmf.ROUTER[0] = Broken()
check("a router that fails: the message still goes, counted as it is",
      plugins.mesh_out(A, "hello there", "") == ("hello there", "") and M._n["msgs"] == 1 and M._n["sent"] == 11, M._n)
lxmf.ROUTER[0] = None
check("no router at all: the same", plugins.mesh_out(A, "hello there", "") == ("hello there", "") and M._n["msgs"] == 2)
check("not text, no title, an address that isn't one: the same",
      plugins.mesh_out("zz", b"\x00\xff", None) == (b"\x00\xff", None) and plugins.mesh_out(A, "", "") == ("", "")
      and M._n["msgs"] == 4, M._n)
for i in range(M.MAX_TESTERS + 30):
    M._asked.clear()
    plugins.mesh_out("%032x" % i, "meter 1/1: x", "")
check("only so many testers remembered", len(M._tests) == M.MAX_TESTERS, len(M._tests))
check("the hook answered None every time (nothing rewritten)", M._out(A, "meter 1/1: x", "#lora-mesh") is None)
check("the one-packet rule is LXMF's: 295 bytes of content, the title counted",
      M._one_packet(0, 254) and M._one_packet(0, 295) and not M._one_packet(0, 297)
      and M._one_packet(10, 285) and not M._one_packet(10, 287), "")
cmd(A, '/meter reset')
M._asked.clear()

# 3. A firmware with packed text: the same test, and what it saved.
if HAS:
    from urns import umsgpack
    from urns.identity import Identity
    rt = lxmf.LXMRouter(identity=Identity())
    rt.register_delivery_identity(rt.identity, display_name="Stump")
    rt.handle_announce(bytes.fromhex(A), umsgpack.packb([b"Rod", None, [], "stump:firefly-android/0.6.2.b.78", {"z": 1}]))
    rt.handle_announce(bytes.fromhex(B), umsgpack.packb([b"Alice", None, [0]]))
    r = cmd(A, '/meter')
    check("packed text on, one address reads it", "packed text: on, 1 address(es) read it" in r[0], r[0])
    text3 = deliver(A, cmd(A, '/meter 3'))
    z3 = len(ztext.pack(text3.encode()))
    r = cmd(A, '/meter last')
    check("the same test to an app that reads packed text: %d B → %d B, one packet" % (n3, z3),
          ("%d B of text → %d B sent" % (n3, z3)) in r[0] and r[0].endswith("one packet") and z3 < 0.7 * n3, r)
    M._asked.clear()
    deliver(B, cmd(B, '/meter 3'))
    r = cmd(B, '/meter last')
    check("... to one that doesn't: as it is, over a link", ("→ %d B sent (100%%), over a link" % n3) in r[0], r)
    r = cmd(A, '/meter')
    check("the totals tell them apart: 1 packed of 2", " 2 messages" in r[1] and ", 1 packed" in r[1], r[1])
    check("... one packet where plain text had none", "one packet: 1 of 2 (as plain text: 0)" in r[2], r[2])
    lxmf.PACKED_TEXT[0] = False
    r = cmd(A, '/meter')
    check("packed text switched off in config: said so", "packed text: off" in r[0], r[0])
    M._asked.clear()
    deliver(A, cmd(A, '/meter 3'))
    r = cmd(A, '/meter last')
    check("... and counted as it is", "(100%), over a link" in r[0], r)
    lxmf.PACKED_TEXT[0] = True

# 4. Everywhere else.
# On the Wi-Fi page nothing goes over LXMF: said so, and estimates instead.
cmd(A, '/meter reset')
M._asked.clear()
r = cmd(W, '/meter', room='main')
check("on the Wi-Fi: the same three lines, and why they may read zero",
      len(r) == 4 and r[0].startswith("meter ·") and "only messages sent to apps over LoRa/LXMF are counted" in r[3], r)
tw = cmd(W, '/meter 3', room='main')
check("... /meter 3 there: the same test lines", tw == t3, tw)
r = cmd(W, '/meter last', room='main')
want = len(ztext.pack(text3.encode())) if HAS else n3
check("... /meter last: worked out, and said to be an estimate",
      ("3 line(s), %d B of text → %d B sent" % (n3, want)) in r[0] and r[0].endswith("(an estimate: asked from the Wi-Fi)"), r)
check("... %s" % ("one packet, packed" if HAS else "over a link on a firmware that doesn't pack"),
      ("one packet" in r[0]) == HAS, r)
check("... not counted as a message sent", M._n["msgs"] == 0, M._n)
r = cmd(W, '/meter room', room='lobby')
check("/meter room where nothing was said", "No lines in this room yet" in r[0], r)
said = ["Paix, discoureuse! allons, sortons d'ici! Messieurs, je vous supplie de consulter la bonne manière.",
        "J'ai un cheval merveilleux, et c'est un animal infatigable.",
        "Mon cheval a fait tout cela aujourd'hui; et de plus j'ai été à Ruel voir un malade."]
for i, l in enumerate(said):
    rrc.post('main', 'Bavard-%d' % i, l)
rrc.post('main', '*', 'someone joined', kind='system')
r = cmd(W, '/meter room', room='main')
size = sum(len(("<Bavard-%d> %s" % (i, l)).encode()) for i, l in enumerate(said))
if HAS:
    packed = sum(len(ztext.pack(("<Bavard-%d> %s" % (i, l)).encode())) for i, l in enumerate(said))
    check("/meter room: the room's own lines, as text and packed (%d → %d B)" % (size, packed),
          ("last 3 lines: %d B of text → %d B packed" % (size, packed)) in r[0] and packed < 0.75 * size, r)
else:
    check("/meter room on a firmware that doesn't pack: the text's size, and to ask again after the update",
          ("last 3 lines: %d B of text; this firmware doesn't pack" % size) in r[0], r)
for i in range(60):
    rrc.post('main', 'Bavard-1', 'line %d of a busy room, about as long as people write them when they chat' % i)
r = cmd(A, '/meter room', room='main')
import re as _re
n = int(_re.search(r"last (\d+) lines", r[0]).group(1))
check("... a busy room: only its latest lines (%d), one mesh line" % n, 1 <= n <= M.ROOM_LINES and len(r) == 1
      and len(r[0]) <= plugins.MESH_LINE_CHARS, r)
i18n.set_lang(A, 'fr')
r = cmd(A, '/meter')
check("in the person's language", "texte compacté" in r[0] and "un seul paquet" in r[2], r)
i18n.set_lang(A, 'es')
check("... Spanish too", "texto compactado" in cmd(A, '/meter')[0])
i18n.set_lang(A, 'en')
check("/help lists it", any("/meter" in l for l in plugins.help_lines('en')), plugins.help_lines('en'))
plugins.set_enabled('meter', False)
n = M._n["msgs"]
plugins.mesh_out(A, "after it was switched off", "")
check("switched off in /admin: counts nothing", M._n["msgs"] == n)

print("FAILURES:", FAIL)
