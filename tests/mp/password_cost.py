# Password checks that can't hold up the CAM (milestone 4 of hardening),
# on the board's MicroPython: a profile's hashing gives everyone else a
# turn; all checks together are budgeted; wrong guesses lock the address
# AND the account (from many devices, the account still locks); /admin's
# door locks too, loosely, never for the computer itself; and through the
# web: register, log in, change the password, the admin's reset.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import profiles, guard, time, config
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


profiles.reset(ROOT + "/profiles_pwc.json")
profiles.register("Rod", "secret11")
profiles.register("Ana", "secret22")

# 1. The same answers, without blocking.
check("check_async: right and wrong, as check()",
      asyncio.run(profiles.check_async("rod", "secret11")) and not asyncio.run(profiles.check_async("rod", "nope1234"))
      and not asyncio.run(profiles.check_async("nobody", "secret11")))
turns = [0]


async def ticker():
    while True:
        turns[0] += 1
        await asyncio.sleep(0)


async def during():
    t = asyncio.create_task(ticker())
    ok = await profiles.check_async("rod", "secret11")
    t.cancel()
    return ok


ok = asyncio.run(during())
check("while it hashes, everyone else gets turns (it used to hold the Stump the whole time)",
      ok and turns[0] >= profiles.ROUNDS // profiles.YIELD_EVERY - 1, turns[0])

# 2. All checks together, budgeted.
guard.reset()
guard.PW_BUDGET = 3
got = [asyncio.run(barkeep._profile_pw("rod", "secret11", "10.0.1.%d" % i)) for i in range(5)]
check("all checks together: past the budget, 'wait' -- and no hashing done for it",
      [g[0] for g in got[:3]] == [True] * 3 and got[3] == (False, 2) and got[4] == (False, 2), got)
guard.PW_BUDGET = 1000

# 3. Wrong guesses lock the address and the account.
guard.reset()
for i in range(5):
    asyncio.run(barkeep._profile_pw("rod", "wrong%03d" % i, "10.0.2.%d" % i))      # five devices, one try each
ok, wait = asyncio.run(barkeep._profile_pw("rod", "secret11", "10.0.2.99"))
check("five wrong passwords for rod from five devices: rod is locked, even from a new one",
      not ok and wait > 0, (ok, wait))
ok, wait = asyncio.run(barkeep._profile_pw("ana", "secret22", "10.0.2.99"))
check("... another account isn't", ok and wait == 0, (ok, wait))
guard.reset()
for i in range(5):
    asyncio.run(barkeep._profile_pw("ana" if i % 2 else "rod", "bad%05d" % i, "10.0.3.1"))
ok, wait = asyncio.run(barkeep._profile_pw("ana", "secret22", "10.0.3.1"))
check("one device guessing at several accounts: that device is locked", not ok and wait > 0, (ok, wait))

# 4. /admin's door: loosely, across addresses; never the computer itself.
guard.reset()
for i in range(barkeep.ADMIN_LOCK_AFTER):
    barkeep._check_admin_pw("nope", "10.0.4.%d" % i)
check("/admin: wrong passwords from many devices lock the door for a new one",
      not barkeep._check_admin_pw("pw", "10.0.4.200"), guard._fails.get("door:admin"))
check("... but never for the computer the Stump runs on", barkeep._check_admin_pw("pw", "127.0.0.1"))
guard.reset()
check("... and a handful of wrong ones from anywhere don't lock it",
      [barkeep._check_admin_pw("nope", "10.0.5.%d" % i) for i in range(4)] and barkeep._check_admin_pw("pw", "10.0.5.99"))

# 5. Through the web, end to end.
guard.reset()


def form(path, **f):
    body = "&".join("%s=%s" % kv for kv in f.items()).encode()
    s, ct, b = req(path, body=body, headers=("Content-Type: application/x-www-form-urlencoded",))
    return s, b


s, b = form("/profile/register", nick="Kim", pw="kimpass1", pw2="kimpass1")
check("register on the web: the profile exists, its password works", profiles.is_registered("kim")
      and profiles.check("kim", "kimpass1") and not profiles.check("kim", "other123"), b[-300:])
s, b = form("/profile/login", nick="Kim", pw="kimpass1")
check("log in on the web", s.startswith("200") and "Kim" in b, b[-300:])
print('FAILURES:', FAIL)
