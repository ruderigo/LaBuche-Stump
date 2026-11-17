# A LoRa client (a FireFly, as far as the Stumps can tell): an LXMF
# person on a simulated Heltec, built on the Stump's own urns. Used by
# pc_lora_client_chain_test.py. Run in a folder with the firmware:
#   python3 lora_client.py FOLDER PORT TARGET_LXMF TARGET_KEY_B64URL
# Reads commands on stdin, one per line ("announce", "path", "send <text>");
# prints what it gets as JSON lines ("IN {...}").
import os, sys, json, time, base64, threading
HERE, PORT, TARGET, KEY = sys.argv[1:5]
os.chdir(HERE)
sys.path[:0] = [os.path.join(HERE, "pc_compat"), HERE]
import stump_host
stump_host.install(HERE)
import uasyncio as asyncio
from urns.reticulum import Reticulum
from urns.identity import Identity
from urns.transport import Transport

rns = Reticulum()
rns.config = {"enable_transport": False, "interfaces": [
    {"type": "RNodeSerialInterface", "name": "Heltec (USB)", "enabled": True, "port": PORT}]}
rns.setup_interfaces()
from urns.lxmf import LXMRouter
router = LXMRouter(identity=rns.identity)
dest = router.register_delivery_identity(rns.identity, display_name="Fly")
print("ME " + dest.hexhash, flush=True)
target = bytes.fromhex(TARGET)
pub = base64.urlsafe_b64decode(KEY + "=" * (-len(KEY) % 4))
Identity.remember(b"\x00" * 32, target, pub)          # from the Stump's QR code, as FireFly does


def got(m):
    print("IN " + json.dumps({"from": m.source_hash.hex(), "title": m.title_as_string() or "",
                              "text": m.content_as_string() or "",
                              "ok": bool(getattr(m, "signature_validated", False))}), flush=True)


router.register_delivery_callback(got)
cmds = []


def read_stdin():
    for line in sys.stdin:
        cmds.append(line.rstrip("\n"))


threading.Thread(target=read_stdin, daemon=True).start()


async def drive():
    while True:
        await asyncio.sleep(0.2)
        while cmds:
            c = cmds.pop(0)
            try:
                if c == "announce":
                    router.announce()
                elif c == "path":
                    Transport.request_path(target)
                elif c.startswith("send "):
                    router.send_message(target, c[5:])
                print("DID " + c.split(" ")[0] + " path=" + str(Transport.has_path(target)), flush=True)
            except Exception as e:
                print("ERR " + c + ": " + repr(e), flush=True)


async def main():
    asyncio.create_task(drive())
    await rns.run()

asyncio.run(main())
