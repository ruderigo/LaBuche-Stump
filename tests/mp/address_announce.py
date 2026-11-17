# This Stump's address on its own page (the home page's card, a QR code,
# the long and the short address), and announcing from /admin (the
# timer, live; "Announce now", spaced for the radio's airtime).
import sys
exec(open(sys.argv[1] + '/harness.py').read())
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


import config
config.AUTH_ADMIN_PASSWORD = 'pw'
import rrc, i18n, qr, announcer, settings
import binascii, hashlib
settings.CONFIG_FILE = "config.py"        # (the test's own folder, never the machine's root)
i18n.set_lang('192.168.4.50', 'en')

ADDR = "9b5c47448ab12cd3ef0011223344aa55"

# ── the address page ─────────────────────────────────────────────────
rrc.NODE = {}
s, ct, b = req('/address')
check("before the Stump has its address: the page says it's coming", s.startswith("200") and "still starting" in b, b[:300])
rrc.NODE = {"name": "LaBuche", "lxmf": ADDR}
s, ct, b = req('/')
check("the home page has the card", "href='/address'" in b and "Its address" in b, b[-800:])
s, ct, b = req('/address')
check("the page: the QR code, the long address to copy, the short one",
      s.startswith("200") and "<svg" in b and "value='" + ADDR + "'" in b and "&lt;9b5c4744&gt;" in b and "LaBuche" in b, b[:600])
m = qr.matrix(ADDR)
digest = binascii.hexlify(hashlib.sha256(b''.join(bytes(r) for r in m)).digest()).decode()[:16]
# (made by this encoder, checked against Project Nayuki's reference and
# decoded by zxing-cpp when it was written: 1.0.0-beta.51)
check("the QR code: version 3 (29 modules), the known-good modules", len(m) == 29 and digest == "c5d8af5c5f976e86", (len(m), digest))
check("... made once, then kept", barkeep._QR.get(ADDR) is not None and barkeep._address_qr(ADDR) is barkeep._QR[ADDR])
i18n.set_lang('192.168.4.50', 'fr')
s, ct, b = req('/address')
check("... in the visitor's language", "L'adresse de cette Bûche" in b and "Ajouter une adresse" in b)
i18n.set_lang('192.168.4.50', 'en')

# The QR code's link (asked by the FireFly team): the address, that it's a
# Stump and its name, and its public key, which must give the address.
from urns.identity import Identity
from urns.destination import Destination
import webtext
ident = Identity()
real = Destination.hash(ident, "lxmf", "delivery")
real_hex = binascii.hexlify(real).decode()
key = webtext.b64url(ident.get_public_key())
check("the key, base64url: 86 characters, no padding, URL-safe", len(key) == 86 and "=" not in key and "+" not in key
      and "/" not in key, key)
rrc.NODE = {"name": "B\u00fbche 3", "lxmf": real_hex, "key": key}
rrc.NODE_INFO = {"release": "1.0.0-beta.79"}
link = barkeep.address_link(rrc.NODE)
check("the link: lxmf://<address>?stump=<name, encoded>&key=<key>&r=<release>",
      link == "lxmf://" + real_hex + "?stump=B%C3%BBche%203&key=" + key + "&r=1.0.0-beta.79", link)
rrc.NODE_INFO = {}
check("... a Stump that doesn't know its release: no r", barkeep.address_link(rrc.NODE).endswith("&key=" + key))
rrc.NODE_INFO = {"release": "1.0.0-beta.79"}
pad = key + "=" * (-len(key) % 4)
pub = binascii.a2b_base64(pad.replace("-", "+").replace("_", "/"))
nh = hashlib.sha256(b"lxmf.delivery").digest()[:10]
ih = hashlib.sha256(pub).digest()[:16]
check("... its key gives its address (what an app checks: a faked code can't claim another's)",
      len(pub) == 64 and hashlib.sha256(nh + ih).digest()[:16] == real)
s, ct, b = req('/address')
check("the page's QR code is the link's; the plain address still there to copy",
      barkeep._QR.get(link) is not None and barkeep._QR[link] in b and "value='" + real_hex + "'" in b)
n20 = len(qr.matrix(barkeep.address_link({"name": "N" * 20, "lxmf": real_hex, "key": key})))
check("... a 20-character name still fits (version 9 for plain letters, its release too)", n20 == 17 + 4 * 9, n20)
check("no key: the plain address", barkeep.address_link({"lxmf": real_hex}) == real_hex)

# The second code, for LXMF apps that don't know Stumps: Columba's
# lxma://<address>:<public key, hex> (1.0.0-beta.53).
lx = barkeep.lxma_link(rrc.NODE)
check("the lxma link: lxma://<address>:<the same key, 128 lowercase hex>",
      lx == "lxma://" + real_hex + ":" + binascii.hexlify(pub).decode() and lx == lx.lower() and len(lx) == 7 + 32 + 1 + 128, lx)
check("... none without a key, or with a key that isn't 64 bytes",
      barkeep.lxma_link({"lxmf": real_hex}) == "" and barkeep.lxma_link({"lxmf": real_hex, "key": "AAAA"}) == "")
s, ct, b = req('/address')
check("the page has both codes and the switch (no script)",
      barkeep._QR.get(link) in b and barkeep._QR.get(lx) in b and "id='qk-lx'" in b and "Columba</label>" in b
      and "<script" not in b.split("class='panel addr'")[1].split("</div>")[0], b[:300])
check("... FireFly's shown first", "id='qk-ff' class='qk' checked" in b)
check("... both codes kept, made once", len(barkeep._QR) == 2)
rrc.NODE = {"name": "LaBuche", "lxmf": ADDR}

# ── announcing: the timer and "Announce now" ────────────────────────
s, ct, b = admin_page()
check("/admin: the Announcing group, every 2 min, the button",
      "Announcing" in b and "every 2 min" in b and "name='announce_min'" in b and "value='2'" in b
      and "formaction='/admin/announce'" in b, b[-2000:] if "Announcing" not in b else "")
form = {"node_name": "LaBuche", "bot_name": "BarKeep", "greeting": "", "economy": "free", "auth_mode": "open",
        "f_chat": "1", "f_billboard": "1", "f_files": "1", "f_about": "1", "librarian": "1"}
ch, bad = settings.parse_form(dict(form, announce_min="5"))
check("the timer in minutes: 5 -> 300 s", bad is None and ch.get("REANNOUNCE_INTERVAL") == 300, (ch, bad))
for v in ("0", "1441", "soon"):
    check("timer %r refused" % v, settings.parse_form(dict(form, announce_min=v))[1] == "announce_min")
check("a form without the field leaves it alone", "REANNOUNCE_INTERVAL" not in settings.parse_form(form)[0])
cfg = open("config.py").read()
saved, later = settings.apply({"REANNOUNCE_INTERVAL": ch["REANNOUNCE_INTERVAL"]})
check("changed: at once (no restart), kept in config.py", saved and "REANNOUNCE_INTERVAL" not in later
      and announcer.every() == 300 and "REANNOUNCE_INTERVAL = 300" in open("config.py").read(), (saved, later))
open("config.py", "w").write(cfg)
check("bounds: 1 min to a day", announcer.set_every(5) == 60 and announcer.set_every(10 ** 7) == 86400
      and announcer.set_every(120) == 120)

# The button. Not announced yet: the loop (example_node) takes the request.
tok = login()
body = b"admin_pass=pw"
s, ct, b = req('/admin/announce', body=body)
check("Announce now: asked for, said so", s.startswith("200") and "Announcing: its address" in b, b[:300])
check("... the loop takes it, once", announcer.take() and not announcer.take())
s, ct, b = req('/admin/announce', body=body)
check("asked again at once: refused, says how long (the radio's airtime)", "s more before the next" in b, b[:300])
announcer.tick(announcer.NOW_GAP)
s, ct, b = req('/admin/announce', body=body)
check("... after the gap: accepted", "Announcing: its address" in b and announcer.take(), b[:300])
announcer.done()
announcer.tick(65)
s, ct, b = admin_page()
check("/admin says when it last went out and when the next is", "Last announced 65 s ago; the next in 55 s" in b,
      [l for l in b.split("<p") if "nnounc" in l][:4])
s, ct, b = req('/admin/announce', body=b"admin_pass=wrong")
check("without the password: refused", s.startswith("403"))
print('FAILURES:', FAIL)
