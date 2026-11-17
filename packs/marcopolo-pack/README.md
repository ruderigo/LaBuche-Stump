# Marco Polo pack

A tester's call and answer between Stumps. One Stump says **marco** in
a shared room, every other Stump with the pack answers **polo**, and the
caller times each round trip and counts the calls that never came
back. It's the same pack on any firmware since 1.0.0-beta.53, so you
time a setup, update it, and time it again.

Where the Meter pack counts bytes, this one measures what they cost:
**time** and **lost lines**, on the real path a room line takes (the
radio, or the chain).

| You write | You get |
|---|---|
| `/marco start` | Ten short calls, 30 s apart |
| `/marco start 10 3 30` | Ten calls, size 3 (the long line), 30 s apart |
| `/marco` | Where the run is, and the times for each Stump that answered |
| `/marco stop` | The run ends |

Slash only. Sizes: `0` is the bare call (15 bytes), `1` 125 bytes, `2`
247, `3` 363 (a room line holds 400 characters). From 1 to 30 calls,
15 to 600 s apart, one run at a time.

## A test, start to finish

Install the pack on **both** Stumps (the caller and at least one that
answers) and restart them. On the caller, in any room:

```
/marco start 10 3 30
```

In `#lora-mesh` you then see, on every Stump:

```
<Marco@LaBuche2> marco 1 a1b2 The ferry was late again this morning, ... /1
<Marco@macOS> polo 1 a1b2
<Marco@LaBuche2> marco 2 a1b2 The ferry was late again this morning, ... /2
```

Five minutes later (ten calls), then two more for the last answers:

```
/marco
marco · #lora-mesh · run a1b2: 10 of 10 calls sent, 363 B (size 3), every 30 s · finished · answered 0 from elsewhere
macOS: 9 polos of 10, 1 lost · round trip 6.0 / 8.4 / 14.0 s (min / average / max)
```

(Those figures are only an example.) Write them down, update the
Stumps, and run the same line again. Do one run with size 0 and one
with size 3: the short call shows the path itself, the long one what a
long line costs on it.

## What the numbers are

- **round trip**: from the call being written on this Stump to its
  polo arriving back here. The call's way out carries the size you
  chose; the polo is always short. Each Stump looks at its room once a
  second, so a time is good to about two seconds, not less: fine over
  the radio, coarse over Wi-Fi.
- **lost**: calls with no polo from that Stump after 120 s. A polo
  that comes in later still counts, and takes the call off the lost.
- A long call can cross the radio in several pieces (a line is cut at
  200 bytes). The polo is sent when the **last** piece is in, so the
  time is the whole line's. With packed text (1.0.0-beta.82) the same
  line takes fewer pieces.
- **answered from elsewhere**: how many calls from other Stumps this
  one has answered.

It doesn't tell the radio from the chain: it times whatever path the
room's lines take between the two Stumps. Use a room that crosses the
link you want to measure (`#lora-mesh` by default).

## Good to know

- **Nothing is on the air until someone writes `/marco start`.**
  Installed and idle, the pack only answers calls.
- Its lines are written as a person's, so they travel to the other
  Stumps and to the apps in the room: that's the path being timed.
  (A plugin's lines normally stay on their own Stump.) Everyone in the
  room sees them: keep it to a test room.
- It paces itself: 15 s between calls at least, 30 calls a run, one
  run at a time, 20 answers a minute whoever calls. Ten long calls is
  about 4 KB over the radio, plus the answers.
- Anyone in a room of the Stump can start or stop a run.
- Nothing is saved: a run and its times are gone when the Stump
  restarts. No SD card needed.
- `MARCOPOLO_ROOM = "wifi-mesh"` in config.py times another room;
  `MARCOPOLO_NICK` changes the name. Both Stumps must use the same
  room.

Install it like the other packs (`PACKS.md`), on each Stump that
takes part.
