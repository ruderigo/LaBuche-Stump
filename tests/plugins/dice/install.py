VERSION = "0.1"
import uasyncio as asyncio
TICKS = []
SEEN = {}
def setup(stump):
    stump.nick = "Dé"
    stump.command("roll", roll, help="!roll 2d6 -- throw some dice")
    stump.command("many", many, help={"en": "/many -- five lines", "fr": "/many -- cinq lignes"}, mesh_lines=2)
    stump.command("boom", boom)
    stump.command("hi", lambda ctx, arg: ctx.t("hello", who=ctx.nick))
    stump.command("secret", lambda ctx, arg: "pw=" + arg, trigger=False, help="/secret <pw>")
    stump.on_message(seen, priority=60)
    stump.gate(flood, priority=30)
    stump.on_result(shout, priority=70)
    stump.every(2, tick)
    stump.every(1, atick)
    stump.page("", index, nav="Dice", sub="Throw some dice")
    stump.page("/p/dice/api", api, raw=True)
    stump.page("form", form)
    stump.page("bad", lambda req: 1 / 0)
    stump.page("go", lambda req: (303, "/p/dice/", "text/html"))
    stump.provide("dice", {"sides": 6})
    for bad in (lambda: stump.command("join", roll), lambda: stump.command("auth", roll),
                lambda: stump.gate(flood, 5), lambda: stump.page("/p/other/x", index)):
        try:
            bad(); TICKS.append("NOT REFUSED")
        except ValueError as e:
            TICKS.append("refused: %s" % e)
    TICKS.append("setting=%s" % stump.setting("SIDES", 6))
    TICKS.append("storage=%s" % stump.storage())
def roll(ctx, arg):
    ctx.post("%s rolls %s: %d" % (ctx.nick, arg or "1d6", 4))
    ctx.reply("(only you see this)")
def many(ctx, arg):
    return ["line %d %s" % (i, "x" * 250 if i == 0 else "") for i in range(5)]
def boom(ctx, arg):
    raise RuntimeError("kaboom")
def seen(ctx):
    SEEN[ctx.nick.lower()] = ctx.room
    if "dice?" in ctx.text:
        ctx.reply("try !roll")
def flood(ctx):
    if ctx.text == "BLOCKME":
        return ["⧗ — blocked by flood"]
    if ctx.text == "DROPME":
        return []
    return None
def shout(ctx, replies, new_room):
    if ctx.text == "/names":
        return [r.upper() for r in replies], new_room
def tick():
    TICKS.append("tick")
async def atick():
    await asyncio.sleep(0)
    raise ValueError("async fail")
def index(req):
    return "<h1>Dice</h1><p>lang=%s</p>" % req.lang
async def api(req):
    await asyncio.sleep(0)
    return (200, '{"sides": 6}', "application/json")
def form(req):
    return "<p>got %s</p>" % req.form.get("n", "-")
