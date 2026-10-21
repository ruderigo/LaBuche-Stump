# Benchmarks for chaining Stumps over Wi-Fi

Two measurements decide whether big files can travel Stump to Stump over
Wi-Fi (leaving LoRa alone) fast enough:

| | Needs | Measures |
|---|---|---|
| `relay_bench.py` | one CAM | How many file packets per second a Stump in the middle of a chain can forward (its CPU work), what encryption costs at the two ends, and MicroPython's socket overhead |
| `profile_bench.py` | one CAM | Where the forwarding time goes: each step, garbage collection, and the parts of link encryption |
| `tls_probe.py` | one CAM and a laptop | Whether the CAM can serve HTTPS, and how long each handshake takes and what it costs in memory (docs/HTTPS_SETUP.md, step 0) |
| `wifi_bench.py` + `wifi_bench_host.py` | one CAM and a laptop | Real Wi-Fi speed from where the CAM sits (TCP and 483-byte UDP packets, both ways, with losses), and which channels are crowded around it |

From the repository folder, with the CAM plugged in:

```
mpremote connect PORT run bench/relay_bench.py      # ~1 minute
mpremote connect PORT reset

mpremote connect PORT run bench/wifi_bench.py       # prints its address, then waits
python3 bench/wifi_bench_host.py <that address>     # on the laptop, second terminal
mpremote connect PORT reset
```

`wifi_bench.py` joins the Wi-Fi in the board's `config.py`; set
`USE_AP = True` at its top to have it open its own network
(`StumpBench`) for the laptop to join instead. Run the Wi-Fi test with
the laptop in the same room, then a wall away, then a floor away.

Running either stops the node until the reset.

**Reading the result:** a file crossing the chain goes no faster than
the slowest of (a) the relay ceiling from `relay_bench.py` and (b) the
UDP figure from `wifi_bench.py` — and somewhat slower than that, because
Reticulum waits for acknowledgements between batches of packets.
