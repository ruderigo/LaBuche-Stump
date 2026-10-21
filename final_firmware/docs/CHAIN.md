# Chaining Stumps over Wi-Fi

Stumps can be linked to each other over Wi-Fi: one per apartment, say,
each joining the Wi-Fi of the one before it. Messages, announces and
files then cross the chain over Wi-Fi, and **big transfers never touch
LoRa**. Only the Stumps that have a Heltec use the radio at all, and the
rest reach the LoRa mesh through the chain.

```
 [ Stump 4 ] <-- Wi-Fi -- [ Stump 5 ] <-- Wi-Fi -- [ Stump 6 ]
  192.168.4.1              192.168.5.1              192.168.6.1
  (first; Heltec)           ^ phones, a PC            ^ phones, a PC
```

## How it works

- Every Stump serves its own Wi-Fi to visitors, as always, but on its
  **own address range**: `192.168.<n>.1`, a different `n` on each Stump.
- Every Stump except the first joins the Wi-Fi of the Stump before it,
  its **uplink**, as a Wi-Fi client, alongside serving its own.
- Over those links the Stumps speak **Reticulum**, and each one relays
  for the others. The ESP32 can't route ordinary internet traffic
  between its two Wi-Fi sides, but it doesn't need to: Reticulum is all
  the chain carries.
- **One channel.** An ESP32 serves its own Wi-Fi on the channel of the
  network it joins, so the whole chain uses the first Stump's channel.
  Pick the quietest: `bench/wifi_bench.py` lists the channels in use
  around a CAM (1, 6 and 11 don't overlap).
- **`#wifi-mesh`** is the chain's chat room, on every Stump of it: the
  other Stumps join it as soon as they're heard over the chain, lines
  from their people show as `<nick@thatstump>`, and people on different
  Stumps can DM each other (`/msg rod@LaBuche ...`). `#lora-mesh` is
  for LoRa phones and handhelds; the Stumps share it too (lines from
  another Stump's `#lora-mesh` show there as `<nick@thatstump>`), over
  the radio when the Stump has one online, even with Wi-Fi there.
  `#wifi-mesh` always goes over Wi-Fi; `#main` stays on its Stump.
- **The chain secret** keeps the chain to Stumps (and PCs) that know it.
  Each one says hello to the Stump above it with a code made from the
  secret, and a Stump only sends to, and accepts from, those that have.
  Visitors on the Wi-Fi can't join it. Use the same secret on every
  Stump.

## Big transfers stay off LoRa

A Stump with a Heltec relays at most **48 KB of file data per link every
10 minutes** onto the radio. That's enough for a voice note or a
propagation-node sync (16 KB each); a big file is cut off there.
Between chained Stumps (and PCs) there is no such limit. It applies to
transfers the Stump *relays*; its own transfers (voice notes, the
propagation node) were already limited to 16 KB.

## Speed

Measured on a CAM with `bench/` (October 2026): forwarding takes about
1.1 ms per 483-byte packet, sending and receiving it another 1.3 ms, so
one Stump in the middle of a chain relays up to roughly **190 KB/s**.
Its Wi-Fi was measured at 420–500 KB/s. Expect something like 120–170
KB/s for a real file across the chain, about half a gigabyte an hour.
More Stumps in the chain add a little delay, not a slowdown: each one
forwards while the next sends. Walls and floors slow the Wi-Fi itself:
measure where the Stumps will actually sit.

The ESP32 itself only takes files up to 16 KB. Big files travel between
**PCs** at each end (rnfs, Sideband, MeshChat, NomadNet), and the
Stumps carry them.

## Setting it up

In the Provisioner wizard, answer **yes** to "Is this Stump part of a
chain?":

- **The first Stump:** pick the channel; note the chain secret it
  suggests.
- **Every other Stump:** the Wi-Fi name of the Stump it joins (the
  wizard lists the networks in range: pick it by number), its own
  number (5, 6, 7… different on each), and the same chain secret. Then
  say whether it has its own Heltec. Without one, it reaches the LoRa
  mesh through the chain.

Or in `config.py`:

```python
CHAIN_ENABLED = True
CHAIN_UPLINK_SSID = "Stump 3B"   # the Stump this one joins; "" on the first
CHAIN_UPLINK_PASS = ""
CHAIN_SUBNET = 5                 # 4 on the first Stump, then 5, 6, ...
CHAIN_CHANNEL = 11               # read on the first Stump only
CHAIN_SECRET = "the same on every Stump"
```

If a Stump starts before the one it joins, it keeps trying, and links up
when the other appears. If the Stump above it restarts, it rejoins by
itself.

**A tree, not a line.** A new Stump joins whichever Stump is closest;
any Stump takes several below it (its Wi-Fi holds 10 devices, phones
included). A Stump on a computer further down only joins: it has one
Wi-Fi radio.

**When the Stump above goes away** (off, moved, out of range) for a
minute, a CAM looks for another Stump of the same chain in range and
joins the strongest: it tries the open networks by signal and asks
each one's `/chain.json` -- the same chain (`chain`, made from the
secret without giving it away), still reaching the first Stump (`path`),
and not one of its own below it (that would make a circle). Nothing
fits: it goes back to trying the Stump it was set up with, and looks
again every 3 minutes. At its next restart it tries that one first.
`/chain.json` → `fallback` names the Stump it joined instead.

### A PC on the chain

A PC on a Stump's Wi-Fi joins the chain with Reticulum's ordinary UDP
interface pointed at that Stump. In `~/.reticulum/config`:

```
[[Stump chain]]
  type = UDPInterface
  enabled = yes
  listen_ip = 0.0.0.0
  listen_port = 4242
  forward_ip = 192.168.5.1
  forward_port = 4242
```

With a chain secret, also run, and leave running:

```
python3 scripts/chain_hello.py 192.168.5.1 <chain secret>
```

It prints `in the chain` once the Stump has let it in.

**On a shared network instead.** The first Stump of a chain, with a
chain secret, also takes chain links on the Wi-Fi it joined itself
(`WIFI_SSID`, e.g. home): a PC on that network points at the Stump's
address there (`192.168.0.161`, say) instead of joining its Wi-Fi, and
keeps its internet. Only with a secret, since anyone on that network
could reach it; `CHAIN_LAN = False` turns it off. `GET /chain.json`
shows the address as `lan_ip`.

## Checking it

- **From a computer:** `python3 provisioner.py --check-chain 192.168.<n>.1`,
  on that Stump's Wi-Fi: whether it joined the Stump above, whether that
  one answers, and what to fix if not. For a Stump on the computer
  itself: `--check-chain` alone.
- **Boot log:** `[chain] member on 192.168.5.1`, `[chain] uplink:
  192.168.4.23 via 192.168.4.1 (signal -52 dBm)`.
- **`/admin` → Stump chain:** the Stump above (and when it last
  answered), and who's linked below.
- **`GET /chain.json`:**
  `{"enabled", "role": "root"|"member", "ap_ip", "lan_ip", "channel",
  "secret", "uplink": {"ssid", "ip", "parent", "parent_heard_s"},
  "below": [...]}` (`lan_ip`: see *On a shared network instead*).

## Limits

- **A tree, not a mesh.** Each Stump has one uplink. If a Stump in the
  middle goes down, the ones beyond it are cut off until it's back.
- **Shared air.** All the links share one channel, alongside the
  building's own Wi-Fi.
- **Client slots.** Every Stump, PC and phone joined to a Stump's Wi-Fi
  uses one of its client slots. The ESP32 handles only a handful at
  once.
