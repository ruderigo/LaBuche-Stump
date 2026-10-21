import sys, os
class _M: pass
_m = _M(); _m.aes = lambda *a, **k: None
sys.modules['ucryptolib'] = _m; sys.modules['cryptolib'] = _m
sys.path.insert(0, '.')
import plugins
mode = sys.argv[2]
if mode == 'reset':
    for f in ('plugins_state.json', 'plugins_loading'):
        try: os.remove(f)
        except OSError: pass
    print('reset'); sys.exit()
if mode == 'culprit': plugins._write_marker({'plugin': 'dice'})
if mode == 'running': plugins._write_marker({'plugin': None})
print('RUNNING', plugins.load_all())
print('SAFE', plugins.safe_mode())
print('OFF', plugins._load_state())
import uasyncio as asyncio
plugins.STABLE_AFTER_S = 0
asyncio.run(plugins.stable_loop())
print('MARKER', plugins._read_marker())
