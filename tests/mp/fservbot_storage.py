import sys, os
class _M: pass
_m = _M(); _m.aes = lambda *a, **k: None
sys.modules['ucryptolib'] = _m; sys.modules['cryptolib'] = _m
sys.path.insert(0, '.')
import config
config.AUTH_ADMIN_PASSWORD = 'pw'; config.FSERVBOT_OP_PASSWORD = 'op'
SD = os.getcwd() + '/sdm'
os.system('rm -rf %s && mkdir -p %s' % (SD, SD))
open(SD + '/fservbot.json', 'w').write('{"custom": {"!hello": "hi from saved state"}, "disabled": [], "opts": {}}')
import fserv; fserv.SD_MOUNT = SD; fserv.sd_ok = True
import fservbot.core as fc, stumpid.core as sc
fc.STORAGE_FILE = SD + '/fservbot.json'; sc.STORAGE_FILE = SD + '/stumpid.json'
import billboard; billboard._sd_available = lambda: True
import plugins, rrc, i18n
print(plugins.load_all())
print('fserv file', fc.STORAGE_FILE, os.listdir(SD), os.listdir(SD + '/plugins/fservbot'))
print('stumpid file', sc.STORAGE_FILE)
A = '192.168.4.50'; i18n.set_lang(A, 'en')
def say(t):
    r, nr = rrc.handle_input(A, 'main', t); print('>>', t, r)
say('!hello'); say('/fsset op broadcast 5')
import fservbot.install as fi
print('notice before talk:', fi._notice(), [m['body'][:40] for m in rrc.since('main', 0)][-1])
say('just chatting')
n0 = len(rrc.since('main', 0)); fi._notice(); n1 = len(rrc.since('main', 0)); fi._notice(); n2 = len(rrc.since('main', 0))
print('notice posts', n1 - n0, n2 - n1, rrc.since('main', 0)[-1]['nick'], rrc.since('main', 0)[-1]['body'][:50])
print('saved at', os.listdir(SD + '/plugins/fservbot'))
