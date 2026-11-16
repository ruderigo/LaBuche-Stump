# Meter pack

A tester's counter. It tells you how much the Stump sends people's
apps over LXMF, and how many of those messages fit **one LoRa packet**,
so you can measure a kit on one firmware, update it, and measure again.
It's the same pack on any firmware since 1.0.0-beta.53: before
1.0.0-beta.78 text goes as it is; from 1.0.0-beta.78 it goes packed to
the apps that read it (CLIENT_QUICKSTART, *Packed text*).

| You write | You get |
|---|---|
| `/meter` | The numbers since the Stump started, or since the last reset |
| `/meter 3` (or `1`, `2`) | A fixed test text of that many lines, sent to you |
| `/meter last` | What your last test took |
| `/meter room` | An estimate from the latest lines of the room you're in |
| `/meter reset` | Counters back to zero (for everyone) |

Slash only: nothing of it shows in the room.

**Where you ask from matters.** The counters are of messages the Stump
sends an app over LXMF (LoRa, or any other Reticulum route). The Wi-Fi
page isn't LXMF, so a Stump whose people are all on the Wi-Fi counts
**0 messages**, and says why. From the Wi-Fi page you still get:

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
meter · 1.0.0-beta.78 · packed text: on, 2 address(es) read it
to apps in 42 min: 57 messages, 9120 B of text → 5630 B sent (62%), 49 packed
one packet: 55 of 57 (as plain text: 46)
```

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
Those you time yourself, with the same `/meter 3` each time. It doesn't
count what Stumps send each other (`#lora-mesh` between Stumps, the
chain), nor voice notes. With stamps on (`LXMF_STAMPS`), a message
carries 18 to 34 bytes more than the one-packet rule here assumes.

## Good to know

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
