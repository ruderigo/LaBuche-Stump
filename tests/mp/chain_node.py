# One Stump in a simulated three-Stump chain on loopback addresses
# (127.0.N.x stand in for each Stump's Wi-Fi subnet). Started three times
# by tests/run.py:   A (root: hub only)   B (uplink to A, hub)   C (uplink to B),
# plus E, an intruder on B's Wi-Fi with the wrong chain secret.
# C opens a link to a destination on A, through B, and sends a file;
# A checks it arrives intact. Prints DONE/FAIL lines for run.py.
import sys, os, time
sys.path.insert(0, '.')
role, work = sys.argv[2], sys.argv[3]
SECRET = "chain-test-secret"

from urns.log import LOG_NONE, LOG_NOTICE, set_loglevel
set_loglevel(LOG_NONE)
from urns import Reticulum
from urns.transport import Transport
from urns.destination import Destination
from urns.identity import Identity
import uasyncio as asyncio

ifaces = {
    "A": [{"type": "ChainHubInterface", "listen_ip": "127.0.4.1", "listen_port": 4242, "secret": SECRET}],
    "B": [{"type": "ChainUplinkInterface", "listen_ip": "127.0.5.2", "listen_port": 4242,
           "forward_ip": "127.0.4.1", "forward_port": 4242, "secret": SECRET},
          {"type": "ChainHubInterface", "listen_ip": "127.0.5.1", "listen_port": 4242, "secret": SECRET}],
    "C": [{"type": "ChainUplinkInterface", "listen_ip": "127.0.6.2", "listen_port": 4242,
           "forward_ip": "127.0.5.1", "forward_port": 4242, "secret": SECRET}],
    # An intruder on B's Wi-Fi with the wrong chain secret: must not get in.
    "E": [{"type": "ChainUplinkInterface", "listen_ip": "127.0.7.2", "listen_port": 4242,
           "forward_ip": "127.0.5.1", "forward_port": 4242, "secret": "not-the-secret"}],
}[role]
store = work + "/" + role
try:
    os.mkdir(store)
except OSError:
    pass
rns = Reticulum(config_path=store + "/config.json", loglevel=LOG_NONE)
rns.config = {"enable_transport": role == "B", "interfaces": ifaces}
rns.setup_interfaces()
print(role, "up:", ", ".join(str(i) for i in rns.interfaces))

FILE = bytes((i * 7 + 3) & 0xFF for i in range(16000))
state = {"done": False}


def out(msg):
    print(role, msg)


async def role_A():
    ident = Identity()
    dest = Destination(ident, Destination.IN, Destination.SINGLE, "stumptest", "sink")
    open(work + "/sink", "w").write(dest.hexhash)

    def on_link(link):
        out("link from C established")
        def done(res):
            ok = getattr(res, "data", None) == FILE
            out(("DONE file arrived intact, %d bytes" % len(res.data)) if ok else "FAIL file differs")
            state["done"] = True
        link.set_resource_concluded_callback(done)
    dest.set_link_established_callback(on_link)
    t0 = time.time()
    while not state["done"] and time.time() - t0 < 150:
        dest.announce()
        await asyncio.sleep(4)
    if not state["done"]:
        out("FAIL nothing arrived")


async def role_B():
    t0 = time.time()
    # A PC on B's Wi-Fi joins with scripts/chain_hello.py (run by tests/run.py).
    while time.time() - t0 < 160:
        try:
            os.stat(work + "/finished")
            break
        except OSError:
            pass
        await asyncio.sleep(1)
    hub = [i for i in rns.interfaces if i.__class__.__name__ == "ChainHubInterface"][0]
    out("DONE relayed %d link packets; children: %s" % (Transport.relayed_links, ", ".join(sorted(hub.peers))))


async def role_C():
    from urns.link import OutgoingLink
    from urns.resource import Resource
    t0 = time.time()
    h = None
    while time.time() - t0 < 140:
        try:
            h = bytes.fromhex(open(work + "/sink").read())
        except OSError:
            await asyncio.sleep(1)
            continue
        if Transport.has_path(h) and Identity.recall(h):
            break
        Transport.request_path(h)
        await asyncio.sleep(2)
    if not h or not Identity.recall(h):
        out("FAIL no path to A")
        return
    from urns import const
    out("path to A: %d hops" % Transport.path_table[h][const.IDX_PT_HOPS])
    dest = Destination(Identity.recall(h), Destination.OUT, Destination.SINGLE, "stumptest", "sink")
    sent = {}

    def on_est(link):
        out("link to A established")
        sent["t"] = time.time()
        r = Resource(link, FILE)
        def concluded(res):
            if getattr(res, "is_initiator", False):
                out("DONE sent %d bytes in %.1f s" % (len(FILE), time.time() - sent["t"]))
                state["done"] = True
        link.set_resource_concluded_callback(concluded)
    OutgoingLink(dest, established_callback=on_est)
    while not state["done"] and time.time() - t0 < 150:
        await asyncio.sleep(1)
    if not state["done"]:
        out("FAIL transfer didn't finish")


async def role_E():
    await asyncio.sleep(30)
    out("DONE intruder stopped")


async def main():
    asyncio.create_task(rns.run())
    await asyncio.sleep(1)
    await {"A": role_A, "B": role_B, "C": role_C, "E": role_E}[role]()
    if role in ("A", "C"):
        open(work + "/finished-" + role, "w").write("1")
        try:
            os.stat(work + "/finished-A"); os.stat(work + "/finished-C")
            open(work + "/finished", "w").write("1")
        except OSError:
            pass

asyncio.run(main())
