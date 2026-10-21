# Tavern pack

The reference pack for the Stump plugin API: everything a community
pack can do, in one place. Text only, so mesh users get all of it.

| Command | What it does |
|---|---|
| `!roll 2d6` | Throw dice (1–10 dice, 2–1000 sides); the result goes to the room |
| `!8ball <question>` | The magic 8-ball answers, in the room |
| `!slap <nick>` | The classic trout |
| `!seen <nick>` | When someone last spoke, and in which room |
| `!quote add <text>` / `!quote` / `!quote 12` / `!quote find <word>` | The tavern's hall of fame |
| `!vote <question> \| yes \| no` | Opens a poll (2–6 options) in this room; `!vote 1` to vote, `!vote` for the count, `!vote close` (whoever opened it). It closes by itself after 30 minutes and posts the result |
| *(flood guard)* | More than 6 room lines in 10 seconds: told to slow down, lines dropped for 20 seconds |

Every command also works as `/roll`, `/vote` … (answered without the
line being posted). The page at `/p/tavern/` (a card on the home page)
shows open polls, recent results and the quotes; `/p/tavern/votes.json`
is the same as data.

The pack also brings a **Tavern** theme (warm wood) and a persona: the
wizard offers "Tavernier" as the bot's name and a welcome line for
mesh visitors. Both can be changed there.

Settings (`config.py`, all optional): `TAVERN_NICK` (default
`Tavernier`), `TAVERN_FLOOD_LINES` (`6`; `0` turns the guard off),
`TAVERN_VOTE_MINUTES` (`30`).

Data: `/sd/plugins/tavern/tavern.json`. Without an SD card, quotes and
polls last until the next restart.

## Install

Put this folder (or `tavern-pack-1.0.zip`) in `packs/` next to
`provisioner.py` and run the wizard, or:

```
python3 provisioner.py --install-pack PORT packs/tavern-pack
```

then restart the node.
