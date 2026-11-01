# Registered profiles (profiles.py) on the board's MicroPython: register,
# the password checked (never kept as typed), credits counted per Stump,
# two Stumps' copies merged both ways without losing anything, a new
# password and a deletion carried across, the same nick registered on two
# Stumps before they synced, sessions; and the chat: a registered nick is
# its owner's, and logged in, the nick is the profile's.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import profiles as P
import rrc
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


P.reset("profiles_a.json")
rec, why = P.register("Rod", "secret1", credits=3)
check("registered", why is None and rec["nick"] == "Rod")
check("the password isn't kept as typed", "secret1" not in open("profiles_a.json").read())
check("right password / wrong one", P.check("rod", "secret1") and not P.check("rod", "secret2"))
check("refused: taken, bad nick, short password",
      [P.register(*a)[1] for a in (("ROD", "abcdefg"), ("a b", "abcdefg"), ("guest-1", "abcdefg"), ("Ana", "123"))]
      == ["taken", "nick", "nick", "password"])
P.add_credits("rod", -1)
check("credits: earned and spent here", P.balance("rod") == 2)
a_recs, a_seq = P.changed_since(0, 10)

P.reset("profiles_b.json")
P.register("Ana", "secret2")
check("B takes A's profiles", P.merge(a_recs) == 1 and P.balance("rod") == 2 and P.check("rod", "secret1"))
P.add_credits("rod", 5)
b_recs, _ = P.changed_since(0, 10)

P.reset("profiles_a.json")
P.add_credits("rod", -1)                 # spent on A meanwhile
check("A takes B's: both Stumps' credits counted, nothing lost",
      P.merge(b_recs) == 2 and P.balance("rod") == 6 and P.is_registered("ana"), P.balance("rod"))
check("the same again: nothing changes", P.merge(b_recs) == 0)
P.set_password("rod", "newpass9")
a2, _ = P.changed_since(a_seq, 10)
P.reset("profiles_b.json")
P.merge(a2)
check("a new password reaches the other Stump", P.check("rod", "newpass9") and not P.check("rod", "secret1"))
tok = P.start_session("ana")
check("a session", P.session_nick(tok) == "Ana")
P.delete("ana")
check("deleted: gone, its sessions ended", not P.is_registered("ana") and P.session_nick(tok) is None)
d, _ = P.changed_since(0, 10)
P.reset("profiles_a.json")
P.merge(d)
check("the deletion reaches the other Stump", not P.is_registered("ana"))

P.reset("profiles_c.json")
P.register("Zed", "aaaaaa1")
c, _ = P.changed_since(0, 10)
P.reset("profiles_d.json")
P.register("Zed", "bbbbbb1")
P.merge(c)
names = sorted(r["nick"].split("-")[0] + ("-x" if "-" in r["nick"] else "") for r in P.all_profiles())
check("one nick registered on two Stumps before they synced: one keeps it, the other is renamed",
      names == ["Zed", "Zed-x"], [r["nick"] for r in P.all_profiles()])
check("... both passwords still work", P.check(P.all_profiles()[0]["nick"], "aaaaaa1") or P.check(P.all_profiles()[0]["nick"], "bbbbbb1"))

P.reset("profiles_e.json")
P.register("Rod", "secret1")
rrc.touch_user("192.168.4.70", nick="walker")
check("chat: a registered nick isn't free to others", rrc.nick_taken("rod", "192.168.4.70")
      and not rrc.nick_taken("rod", "p:rod"))
r, _ = rrc.handle_input("192.168.4.70", "main", "/nick Rod")
check("... /nick to it is refused", rrc.get_user("192.168.4.70")["nick"] == "walker", r)
rrc.touch_user("p:rod", nick="Rod")
r, _ = rrc.handle_input("p:rod", "main", "/nick bob")
check("logged in: the nick is the profile's", rrc.get_user("p:rod")["nick"] == "Rod", r)
rrc.touch_user("192.168.4.71", nick="Ana")
P.register("Ana", "secret3")
check("a profile arriving for a nick someone uses: they go back to a guest name",
      rrc.release_registered_nicks() == 1 and rrc.get_user("192.168.4.71")["nick"].startswith("guest-"))
bad = {"nick": "<b>x</b>", "id": "ab", "salt": "00", "hash": "00", "pv": 1, "by": "z", "c": {}, "del": 0}
check("a record from another Stump with a nick that isn't one: ignored", P.merge([bad]) == 0 and not P.is_registered("<b>x</b>"))
# The person on the computer itself, with a browser visitor ID (or logged
# into a profile): still "this computer" for #lora-mesh and /lora invite.
import guard
guard.net("v0123456789abcdef0123", "127.0.0.1")
guard.net("p:rod", "127.0.0.1")
guard.net("vfedcba9876543210fedc", "192.168.4.80")
rrc.LORA_LOCKED[0] = True
check("#lora-mesh: the computer's own browser, by visitor ID or profile, is let in",
      rrc.lora_allows("v0123456789abcdef0123") and rrc.lora_allows("p:rod") and rrc.lora_allows("127.0.0.1"))
check("... a visitor from elsewhere isn't (unless invited)", not rrc.lora_allows("vfedcba9876543210fedc"))
rrc.LORA_LOCKED[0] = False
print('FAILURES:', FAIL)
