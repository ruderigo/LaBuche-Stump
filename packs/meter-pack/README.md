# Meter pack

A tester's counter. It tells you how much the Stump sends people's
apps over LXMF, and how many of those messages fit **one LoRa packet**,
so you can measure a kit on one firmware, update it, and measure again.
It's the same pack on any firmware since 1.0.0-beta.53: before
1.0.0-beta.82 text goes as it is; from 1.0.0-beta.82 it goes packed to
the apps that read it (CLIENT_QUICKSTART, *Packed text*).

| You write | You get |
|---|---|
| `/meter` | The numbers since the Stump started, or since the last reset |
| `/meter info` | The firmware, whether it packs text, and for how long it has counted |
| `/meter 3` (or `1`, `2`) | A fixed test text of that many lines, sent to you |
| `/meter last` | What your last test took |
| `/meter room` | An estimate from the latest lines of the room you're in |
| `/meter ann` | The announces the Stump sent, their bytes, and how many of those bytes are Reticulum 1.6.1 stamps (1.4; firmware 1.0.0-beta.86 and later) |
| `/meter reset` | Counters back to zero (for everyone) |

Slash only: nothing of it shows in the room.

**What is counted.** What this Stump sends over LXMF:

- **to apps**: messages to a person's app (FireFly, Sideband...), over
  LoRa or any other Reticulum route;
- **from apps** (1.3): the room lines people write from an app. On a
  firmware and an app that both pack, their size as they arrived;
- **to other Stumps** (1.2): a room's lines going to the next Stump
  and the `#lora-mesh` radio broadcast, **by radio** apart from **by
  the chain** (1.3): bytes only cost on the first.

What the **Wi-Fi page** shows is not LXMF and isn't counted. So with
everyone on Wi-Fi pages, *to apps* stays at 0; a line someone writes
in a shared room (`#lora-mesh`, `#wifi-mesh`) is counted *to other
Stumps* on the Stump it was written on, when it leaves for the others.
Each Stump counts only what it sends itself: ask each one.

From the Wi-Fi page you also get estimates:

- `/meter room`: the room's latest lines (up to 20), each measured as
  it would go to an app. On a firmware with packed text: their size as
  text and packed. On an older one: their size as text, to compare
  after the update.
- `/meter 3` then `/meter last`: the same test, worked out instead of
  sent, and marked as an estimate.

## A test, start to finish

From FireFly (or Sideband), over LoRa, on the firmware you have now:

```
/meter reset
/meter 3
/meter last
```

```
meter 1/3: The ferry was late again this morning, so we walked up to ...
meter 2/3: Le vieux poêle chauffe encore très bien, mais il faudra ...
meter 3/3: Mi hermana dice que el puente sigue cerrado y que ...

your last test: 3 line(s), 403 B of text → 403 B sent (100%), over a link (several packets)
```

Note how long the three lines take to arrive. Update the Stump and the
app, wait for the app's announce to be heard (a minute or two, or send
any message first), and do the same again:

```
your last test: 3 line(s), 403 B of text → 212 B sent (53%), one packet
```

`/meter 3` is the one that shows the difference: 403 bytes is too long
for one packet as it is, and fits once packed. `/meter 1` (133 bytes)
is one packet either way.

Then use the Stump normally for a while, and ask for the totals (the
figures here are only an example):

```
to apps: 57 messages, 9120 B of text → 5630 B sent (62%), 49 packed; in one packet: 55 (as plain text: 46)
from apps: 12 lines, 2310 B of text → 1420 B received (61%); in one packet: 12 (as plain text: 9)
to other Stumps: by radio 31 messages, 6040 B of text → 3810 B sent (63%); by the chain 640 messages, 108100 B → 108100 B
```

Over the mesh those are the three lines; on the Wi-Fi page the
`/meter info` line comes first.

## What the numbers are

- **B of text**: the message's text as written (UTF-8 bytes).
- **B sent**: its bytes as the LXMF content that goes out. Equal to
  the text on a firmware without packed text, to an app that doesn't
  read it, or when packing wouldn't make it shorter.
- **one packet**: the message fits a single LXMF packet (295 bytes of
  content, the title included). Past that it goes over a link: a
  handshake, then several packets. *As plain text* is how many would
  have fit without packing.
- **address(es) read it**: how many addresses this Stump has heard say
  they read packed text. That includes other Stumps, so it can be more
  than the apps you see.

What it doesn't measure: airtime, time to deliver, or lost packets.
Those you time yourself, with the same `/meter 3` each time. Voice
notes and beacons aren't counted. *To other Stumps* includes
everything one Stump sends another as a message: room lines, and also
the lists of names and profiles they keep in step. With stamps on (`LXMF_STAMPS`), a message
carries 18 to 34 bytes more than the one-packet rule here assumes.

## Good to know

- 1.5: **to apps, by radio apart**: how many of the messages to apps
  left by the Stump's radio, and their bytes as sent (the airtime). A
  message counts as by radio when the app's path comes in on the radio,
  or when there's no path yet and a radio is on; a path on the Wi-Fi or
  TCP costs no airtime and isn't counted there.
- 1.5: a dash instead of "100 %" when nothing was counted; the
  chain's line gets its share too; the line from apps says its bytes
  are **estimated** (each line packed again here, not read off the
  radio: a long repeated line packs to a few percent).
- 1.4: `/meter ann`, the announces and their stamps.
- 1.3: lines **from apps**; **radio** apart from **chain**; the state
  line moved to `/meter info`. To time what the bytes cost, see the
  Marco Polo pack.
- 1.2: what goes **to other Stumps** is counted too. For that the pack
  puts a counter in front of the Stump's own send function
  (`rrc_mesh._send_item`), in memory: it counts, then calls the real
  one with the same message. No file of the Stump is changed, and
  switching the plugin off stops the counting. On a firmware where
  that function isn't there, the line reads "not counted on this
  firmware".
- 1.1: the minutes are counted by the pack (1.0 read the clock, and a
  board's jumps from the year 2000 to today: "in 14134732 min"); the
  Wi-Fi page is told why it reads zero, and gets `/meter room` and an
  estimated test.

- It **changes nothing that is sent**: it looks at each message and
  lets it through as it is. With the Simple pack on, it counts the
  reworded text, which is what goes out.
- On a firmware with packed text it works out each message's packed
  size a second time to count it, which takes a board a little longer
  per message (not measured yet). Switch the plugin off in `/admin` →
  **Plugins** when you're done measuring (no restart).
- One test per person every 20 seconds; the Stump's own limit on plugin
  commands applies too.
- Nothing is saved: the counters start again with the Stump. No SD
  card needed.

Install it like the other packs (`PACKS.md`): put `meter-pack` in
`packs/` next to `provisioner.py`, then
`python3 provisioner.py --install-pack PORT packs/meter-pack`, and
restart the node.
