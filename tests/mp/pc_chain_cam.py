# A CAM Stump joining a Stump on a computer (docs/PC.md) as its chain
# uplink: the real MicroPython chain and LXMF code, on loopback. The
# computer Stump (stump_pc.py) is the first Stump, its hotspot on
# 127.0.4.1. This CAM says hello with the chain secret, finds the
# computer's LXMF address, and sends it a chat line. tests/run.py checks
# the line reaches the computer's chat room over HTTP.
import sys, time
sys.path.insert(0, '.')
work, pc_lxmf = sys.argv[2], sys.argv[3]
from urns.log import LOG_NONE, set_loglevel
set_loglevel(LOG_NONE)
from urns import Reticulum
from urns.transport import Transport
from urns.identity import Identity
import uasyncio as asyncio
from node_common import setup_node

rns = Reticulum(config_path=work + "/cam/config.json", loglevel=LOG_NONE)
rns.config = {"enable_transport": True, "interfaces": [
    {"type": "ChainUplinkInterface", "listen_ip": "127.0.5.2", "listen_port": 4242,
     "forward_ip": "127.0.4.1", "forward_port": 4242, "secret": "pc-chain-secret"}]}
dest, router = setup_node(rns, "camtest")
rns.setup_interfaces()
target = bytes.fromhex(pc_lxmf)


async def main():
    up = rns.interfaces[0]
    t0 = time.time()
    while not up.parent_seen and time.time() - t0 < 30:
        await asyncio.sleep(0.5)
    print("CAM", "uplink answered" if up.parent_seen else "FAIL no answer to hello")
    router.announce()
    t0 = time.time()
    while Identity.recall(target) is None and time.time() - t0 < 60:
        Transport.request_path(target)
        await asyncio.sleep(3)
    if Identity.recall(target) is None:
        print("CAM FAIL computer's LXMF address never known")
        return
    print("CAM path to the computer known")
    router.send_message(target, "hello from the CAM")
    print("CAM sent")
    await asyncio.sleep(25)


async def both():
    asyncio.create_task(main())
    await rns.run()

try:
    asyncio.run(asyncio.wait_for(both(), 100))
except Exception as e:
    print("CAM end:", type(e).__name__)
