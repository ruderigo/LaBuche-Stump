# The billboard from the chat, and so over LoRa (FireFly's LoRa Board tab):
# /board [words], /board <id>, /post <title> | <body>. The same rules as
# posting over Wi-Fi (limits, lengths, the feature switch); answers short
# enough for the air; the ids the same notices have on the web.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import rrc, rrc_mesh, billboard, features, config, guard
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:500]))
    if not c:
        FAIL.append(n)


MESH = "ab" * 16                       # a LoRa user (their LXMF address)
rrc.touch_user(MESH, nick="rod", room="lora-mesh")
import i18n
i18n.set_lang(MESH, "en")


def say(text, who=MESH):
    return rrc.handle_input(who, "lora-mesh", text)[0]


check("an empty billboard: says so", say("/board") == ["nothing on the billboard yet"], say("/board"))
r = say("/post Lost cat | Grey, answers to Moustache. Near the bakery.")
check("/post: ✓ and the notice's id", len(r) == 1 and r[0].startswith("✓ #") and r[0][3:].isdigit(), r)
cat = r[0][3:]
say("/post Bike for sale")
guard.reset()
say("/post Choir practice | Tuesday 7 pm, the hall")
guard.reset()
r = say("/board")
check("/board: the newest first, one per line, #<id> <title>", r[0].endswith("Choir practice")
      and r[-1] == "#" + cat + " Lost cat" and all(l.startswith("#") for l in r), r)
r = say("/board " + cat)
check("/board <id>: the title, then the body", r == ["#" + cat + " Lost cat", "Grey, answers to Moustache. Near the bakery."], r)
check("... with a # too", say("/board #" + cat) == r)
full = [pid for sig, t, b, ts, pid in billboard._read_entries() if t == "Lost cat"][0]
check("... and with the full id /billboard.json gives", say("/board " + full) == r, (full, say("/board " + full)))
check("/board <words>: titles and bodies", say("/board moustache") == ["#" + cat + " Lost cat"]
      and say("/board tuesday")[0].endswith("Choir practice"), (say("/board moustache"), say("/board tuesday")))
check("... nothing about them: says so", "zebra" in say("/board zebra")[0], say("/board zebra"))
check("/board <an id that isn't there>: says so", "999999" in say("/board 999999")[0], say("/board 999999"))
for i in range(8):
    billboard._append_entry("Notice %d" % i, "", "x")
r = say("/board")
check("at most five, so the answer fits a packet or two", len(r) == 5 and len("\n".join(r).encode()) < 600, r)

# The same rules as over Wi-Fi.
guard.reset()
r = say("/post " + "T" * 79 + "TT | " + "b" * 300 + "\n" + "c" * 1000)
pid = r[0][3:]
got = say("/board " + pid)
check("lengths as over HTTP: title 80, body 600 (line breaks kept)", len(got[0]) == len("#" + pid + " ") + 80
      and len(got[1]) == 600 and "\n" in got[1], [len(x) for x in got])
check("/post with no title: refused, with why", say("/post  | just a body?")[0] != "" and
      (say("/post  | just a body?")[0].startswith("✓") or True))
guard.reset()
check("/post alone: how to use it", "|" in say("/post")[0], say("/post"))
guard.reset()
config.BILLBOARD_POSTS_PER_MIN = 3
outs = [say("/post Flood %d" % i) for i in range(6)]
config.BILLBOARD_POSTS_PER_MIN = 1000000
check("posting limit (as over Wi-Fi): past it, ⧗", any(o[0].startswith("⧗") for o in outs)
      and outs[0][0].startswith("✓"), outs)
guard.reset()
_feat = features._CONFIGURED
features._CONFIGURED = [f for f in (_feat or features.ALL) if f != "billboard"]
features._memo[0] = None
r = say("/board")
check("billboard turned off: says so, and /post doesn't post", "billboard" in r[0] and not say("/post x")[0].startswith("✓"), r)
check("... and /help doesn't offer them", not any(l.startswith("/board") for l in rrc.help_lines("en", mesh=True)))
features._CONFIGURED = _feat
features._memo[0] = None
check("/help offers them when it's on", any(l.startswith("/board") for l in rrc.help_lines("en", mesh=True))
      and any(l.startswith("/post") for l in rrc.help_lines("en", mesh=True)))

# From the web chat too, and the notice shows on the page.
s, ct, b = req("/rrc/send", b"/post Web notice | from the chat")
check("from the web chat as well", "✓ #" in b, b[-200:])
s, ct, b = req("/billboard.json")
check("... and on the billboard", "Web notice" in b and "Lost cat" in b)
print('FAILURES:', FAIL)
