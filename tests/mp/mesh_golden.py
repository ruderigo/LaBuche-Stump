import sys
class _M: pass
_m = _M(); _m.aes = lambda *a, **k: None
sys.modules['ucryptolib'] = _m; sys.modules['cryptolib'] = _m
sys.path.insert(0, '.')
import config
config.AUTH_ADMIN_PASSWORD = 'pw'; config.FSERVBOT_OP_PASSWORD = 'op'
import rrc, i18n, rrc_mesh
if True:
    import plugins; plugins.load_all()
else:
    for n in ('fservbot', 'stumpid'):
        m = __import__(n + '.install', None, None, ('activate',)); m.activate()
A = '192.168.4.50'; i18n.set_lang(A, 'en')
def say(cid, line):
    r, nr = rrc.handle_input(cid, rrc.get_user(cid)['room'], line); print('>> web', line, '->', r, nr)
say(A, '/admin pw room vip minted'); say(A, '/admin pw room lxmf hybrid')
M = 'cd' * 16
peer = rrc_mesh._get_peer(M, 'Rod')
print('peer', peer['room'], peer.get('home_room'))
for line in ('/help', '/rooms', '/join vip', '#vip hi', 'hello', '/auth', '/whoami', '/fslist', '!list', '/names', '/part', '/msg nobody x', '/admin pw mode'):
    r, nr = rrc_mesh._handle(M, peer, line)
    if nr: peer['room'] = nr
    print('>> mesh', line, '->', [x if not x.startswith('AUTH-CH') else 'AUTH-CHALLENGE <n>' for x in r], nr)
print('suffix', repr(rrc_mesh._tier_suffix('vip')), repr(rrc_mesh._tier_refusal(M, 'vip', 'en')))
