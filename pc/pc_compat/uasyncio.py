# MicroPython's uasyncio, on the computer's asyncio (Stump on a computer:
# docs/PC.md). The firmware's streams use awrite()/aclose() and
# sleep_ms(); everything else is the same API.
import asyncio as _a
from asyncio import *          # noqa: F401,F403


async def sleep_ms(ms):
    await _a.sleep(ms / 1000)


async def _awrite(self, data, *args):
    self.write(data if isinstance(data, (bytes, bytearray, memoryview)) else str(data).encode())
    await self.drain()


async def _aclose(self):
    try:
        self.close()
        await self.wait_closed()
    except Exception:
        pass


_a.StreamWriter.awrite = _awrite
_a.StreamWriter.aclose = _aclose
