# Bavard pack

A bot that never stops talking, to test a Stump's guardrails for days.
**A test tool**: it starts paused, and talks only once `BAVARD_ON = "on"`
is set (the wizard asks) or someone presses *Resume* in `/admin` →
Bavard (until the next restart).

**Alone**, Bavard posts a line of numbers in `#bavard`, at a pace that
swings from near-instant to a minute and a half of silence:

```
[2026-11-09 05:12:03.417Z] #000123 4821-9055-1770-2246
```

Date and time in UTC, to the millisecond (a `~` in front means the
node's clock hasn't been set by the network yet), the line's number, and
16 digits that are a scramble of that number: never the same twice for
this bot, and no lexicon or table to carry.

**When it finds another Bavard on another Stump**, the two play
Molière in the original French, réplique by réplique:

```
[2026-11-09 05:14:41.032Z] Le Misanthrope I, 1 · PHILINTE — Qu'est-ce donc ? Qu'avez-vous ?
[2026-11-09 05:14:41.288Z] Le Misanthrope I, 1 · ALCESTE — Laissez-moi, je vous prie.
```

Each bot takes half the characters (they alternate in the order they
first speak), posts its own lines on its own Stump and passes the turn
to the other over a Reticulum link. The twelve plays of the collection
come round in a loop, so a duet doesn't run out: a simulated 72 hours at
the default pace played about 21,000 répliques, the collection twice
over, with the bots' memory flat.

## How two Bavards find each other

Every Bavard is a Reticulum destination, `bavard.bot`, and announces
itself every 5 minutes. A Bavard that hears another one calls it if its
own hash is the smaller of the two (the other waits, and calls itself
after 3 minutes if it hasn't been called). The two say hello with where
they are in the plays (the one further along wins), then pass the turn
back and forth with a few bytes each time: the text never crosses, each
reads it from its own card.

Bot lines aren't relayed to the other Stumps of a chain (`rrc.post`
with `bot=True`), so each Stump's `#bavard` shows its own bot's
characters; the full play, both sides, is in the transcripts.

If the partner goes quiet for 5 minutes, the bot goes back to numbers;
when they hear each other again, the play picks up where it stopped.
One partner at a time (`BAVARD_PARTNER` pins one), and **one duet at a
time** among all the Bavards that hear each other (1.2):

- Each Bavard's announce says what it's doing (free, in a duet with whom,
  or holding a place in a play), and it announces again when that changes.
- A free Bavard that knows of a duet stays out of it: no call, no answer.
  If two duets formed at once, the one with the smaller pair of addresses
  stays and the other breaks up.
- When a duet breaks (a bot leaves, is paused, or its link goes silent),
  the bot that stays holds its place in the play for 10 minutes. The
  smallest free Bavard calls it and they go on from that line. Everyone
  works out the same pair, so nobody races.
- Each turn passed is acknowledged, and passed again every 8 s until it is,
  so a turn lost while a Stump is busy costs seconds, not the old 90 s.
- While the room is shared with other Stumps (`/setchan … -wifi` or
  `-lora`) and other Bavards are about, the ones not in the duet post
  nothing there: only the duet shows. A Bavard that hears no other says
  its numbers there as when alone (1.3: it went quiet, as if broken).
  Set the room `-local` for a load test with every bot's numbers.

A Bavard that calls
proves its Stump's identity on the link, and the one called checks that
the destination it claims is that identity's, and the pinned partner's
when there is one: anyone else is refused.

## What it measures

Bavard never works around a guardrail: every line goes through BarKeep's
plugin posting budget (`stump.post()`, 6 room lines in any rolling
minute), with `wait=False` so a line over the budget is refused there and
then instead of waiting in BarKeep's queue. A refused line is counted,
logged as `DROP`, and in a duet tried again two seconds later, so no
réplique is lost. Then:

- **`/p/bavard/`** (a card on the home page) and **`/p/bavard/stats.json`**:
  lines tried, posted and refused by the budget, the longest silence,
  bots heard, the partner, links made and lost, the link's round trip,
  where the play is, errors.
- **`/sd/plugins/bavard/log-YYYYMMDD.txt`**: every line tried, `OK` or
  `DROP`, with its time, and the link events (`--`). Seven days kept, 16
  MB a day at most.
- **`!bavard`**: the same in one line.
- **`/admin` → Bavard**: pause, leave the duet, start Molière again.

Paces (`BAVARD_PACE`): `variable` (default: 40 % near-instant, 30 %
within 3 s, 20 % within 15 s, 10 % up to 90 s), `quick`, `burst`
(always near-instant: hammers the budget), `polite` (10.5–15 s, under the
budget).

## What it does on the node

For whoever reviews or installs it:

- **Posts** only in its own room, and only with `stump.post()` (plugin
  API 4), which BarKeep paces like every plugin's lines. It never calls
  `rrc.post()` itself.
- **Network**: one Reticulum destination, `bavard.bot`, announced every
  5 minutes; at most one link, to another Bavard, carrying JSON packets
  under 200 bytes. No sockets, no HTTP. The turn-passing packets aren't
  budgeted by the node: at the `burst` pace across LoRa, a pair of bots
  exchanges a packet or two per line posted. Watch the airtime on a
  radio-only pair.
- **Files**: reads the plays in `/sd/tools/bavard/`; writes only in
  `/sd/plugins/bavard/` (`state.json`, and the transcript: about 1 MB a
  day at the default pace, 7 days kept, 16 MB a day at most). Nothing
  without an SD card.
- **Timers**: one every second (an `async def` that yields, so it never
  holds the loop), and three slow ones. Errors are caught, counted on
  the page and logged, so the timer is never switched off.
- **RAM**: flat over a simulated 72 hours (see below); a play's line
  offsets and the last 1,000 transcript lines are what it holds.
- Doesn't modify core files, bind ports or start its own tasks.

## Testing

`tests/bavard_duet.py` drives two bots through the firmware's real
`plugins.py`, on a fake clock and a fake Reticulum: numbers well formed
and unique; the duet in order with nothing lost or repeated; each
character always the same bot; on to the next play; a silent link back
to numbers, and the play resumed. With the MicroPython Unix port
(`scripts/build_micropython.sh` in SandBox_Stump_On):

```
MICROPYTHON=~/micropython-f32/micropython python3 tests/run.py
```

A 72-hour run of the same harness with the full corpus at the default
pace played about 21,600 répliques with no errors and flat memory.

## Settings

All optional, in `config.py` (the wizard asks `BAVARD_ON`, the rest under "advanced"):

| | Default | |
|---|---|---|
| `BAVARD_ON` | `off` | `on`: talks from the start. Off, it waits for *Resume* in `/admin` |
| `BAVARD_ROOM` | `bavard` | The room it talks in (made if missing). `lora-mesh` puts every line on the radio: careful |
| `BAVARD_PACE` | `variable` | `variable`, `quick`, `burst`, `polite` |
| `BAVARD_ANNOUNCE` | `300` | Seconds between announces |
| `BAVARD_PARTNER` | | Only pair with this Bavard (its `me` in `stats.json`) |
| `BAVARD_LOG` | `on` | `off`: no transcript on the card |
| `BAVARD_NICK` | `Bavard` | The name it posts as |
| `BAVARD_CORPUS` | `/sd/tools/bavard` | Where the plays are |

## The plays

Molière's plays are in the public domain, and so is the text used here:
Project Gutenberg's French *Œuvres complètes* (Philarète Chasles's
edition, tomes [1](https://www.gutenberg.org/ebooks/40086),
[2](https://www.gutenberg.org/ebooks/43535),
[3](https://www.gutenberg.org/ebooks/50173) and
[4](https://www.gutenberg.org/ebooks/57270)). Free for any use; the code
is MIT.

The twelve: Le Misanthrope, Le Tartuffe, L'Avare, L'École des femmes,
Les Précieuses ridicules, Dom Juan, George Dandin, L'École des maris,
Amphitryon, Monsieur de Pourceaugnac, L'Amour médecin, Sganarelle.
Stage directions, footnotes and the editor's notices are left out; a
long speech is cut into several lines of the same character; verse
breaks show as ` / `.

They go on the SD card, in `tools/bavard/` (so they're also on the Tools
page). Without them, or when the two bots' corpora differ, the bots play
the first scene of Le Misanthrope, built into the plugin, in a loop.

`build_corpus.py` makes them (it downloads the four tomes once, into
`gutenberg/`):

```
python3 build_corpus.py --list   # the plays it finds in the tomes
python3 build_corpus.py          # the twelve, into tools/
python3 build_corpus.py --all    # every play in the four tomes (22)
python3 ../../provisioner.py --hash-pack .
```

`tests/build_corpus_test.py` checks it on a tome laid out like
Gutenberg's (prose and verse, notes, stage directions).

## Install

Like the other packs (`PACKS.md`):

```
python3 provisioner.py --install-pack PORT packs/bavard-pack
```

then restart the node. It needs firmware 1.0.0-beta.61 or later (plugin
API 4), an SD card for the plays and the transcript, and a second Stump
with Bavard for the duet.
