# Runs a fixed chat script through rrc.handle_input with fservbot and
# stumpid loaded; prints every reply and room line. Run from a firmware tree.
import sys
class _M: pass
_m = _M(); _m.aes = lambda *a, **k: None
sys.modules['ucryptolib'] = _m; sys.modules['cryptolib'] = _m
sys.path.insert(0, '.')
import config
config.AUTH_ADMIN_PASSWORD = 'pw'; config.FSERVBOT_OP_PASSWORD = 'op'
import rrc, i18n
NEW = True
if NEW:
    import plugins
    print('loaded', plugins.load_all())
else:
    for n in ('fservbot', 'stumpid'):
        m = __import__(n + '.install', None, None, ('activate',)); m.activate()
A, B = '192.168.4.50', '192.168.4.51'
i18n.set_lang(B, 'en')
script = [
 (A, '/help'), (B, '/help'), (A, '/nick alice'), (B, '/nick bob'), (A, 'hello all'),
 (A, '/whoami'), (A, '/auth'), (A, '/auth abc'), (A, '/admin'), (A, '/admin nope mode'),
 (A, '/admin pw mode'), (A, '/admin pw room vip minted'), (B, '/join vip'),
 (A, '/admin pw zzz'), (A, '/invite bob'), (A, '/fshelp'), (A, '/fslist'),
 (A, '!files'), (A, '!list'), (A, '!fs'), (B, '/msg alice hi there'), (A, '/me waves'),
 (A, '/roll'), (A, '!roll 2d6'), (A, '/rooms'), (A, '/names'), (A, '/nope'), (B, '/fsset op broadcast 1'),
]
last = 0
for cid, line in script:
    r, nr = rrc.handle_input(cid, rrc.get_user(cid)['room'], line)
    print('>>', cid[-2:], line); print('  replies', r, nr)
    if nr: rrc.touch_user(cid, room=nr)
    for room in rrc.room_names():
        for m in rrc.since(room, 0):
            if m['id'] > last:
                print('  #%s <%s> %s' % (room, m['nick'], m['body']))
    last = max([m['id'] for room in rrc.room_names() for m in rrc.since(room, 0)] + [last])
