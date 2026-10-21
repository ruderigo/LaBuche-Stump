import sys, os, time
class _M: pass
_m = _M(); _m.aes = lambda *a, **k: None
sys.modules['ucryptolib'] = _m; sys.modules['cryptolib'] = _m
sys.path.insert(0, '.')
import config
config.AUTH_ADMIN_PASSWORD = 'pw'; config.FSERVBOT_OP_PASSWORD = 'op'; config.TAVERN_VOTE_MINUTES = 1
SD = os.getcwd() + '/sdp'
os.system('rm -rf %s && mkdir -p %s' % (SD, SD))
import fserv; fserv.SD_MOUNT = SD; fserv.sd_ok = True
import plugins, rrc, i18n
print(plugins.load_all())
FAIL = []
def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c: FAIL.append(n)
A, B = '192.168.4.50', '192.168.4.51'
for c, n in ((A, 'alice'), (B, 'bob')):
    i18n.set_lang(c, 'en'); rrc.touch_user(c, nick=n, room='main')
def say(cid, t):
    plugins._cmd_use.clear()
    import tavern.install as T; T._flood.clear()
    r, nr = rrc.handle_input(cid, 'main', t); return r
def last(n=1): return [(m['nick'], m['body']) for m in rrc.since('main', 0)][-n:]
r = say(A, '!roll 3d6'); check('roll posts', last()[0][0] == 'Tavernier' and 'alice rolls 3d6' in last()[0][1], last())
check('roll bad', say(A, '!roll 99d6')[0].startswith('usage'))
say(A, '!roll 20'); check('roll d20 shorthand', 'rolls 1d20' in last()[0][1], last())
say(A, '!8ball will it rain?'); check('8ball', last()[0][1].startswith('🎱 alice'), last())
say(A, '!slap bob'); check('slap', 'trout' in last()[0][1], last())
check('slap usage', say(A, '!slap')[0].startswith('usage'))
say(B, 'hi everyone')
check('seen', say(A, '!seen bob')[0].startswith('bob last spoke in #main'), say(A, '!seen bob'))
check('seen never', 'never' in say(A, '!seen zed')[0])
check('seen self', say(A, '!seen alice') == ["you're right here"])
check('quote empty', 'no quotes' in say(A, '!quote')[0])
check('quote add', say(A, '!quote add To be or not to be') == ['quote #1 saved'])
say(B, '/quote add second one')
say(A, '!quote 2'); check('quote n', last()[0][1] == '📜 #2: second one — bob', last())
check('quote find', say(A, '!quote find be')[0].startswith('📜 #1'))
check('vote none', 'no poll' in say(A, '!vote')[0])
say(A, '!vote Pizza tonight? | yes | no | maybe'); check('vote open', last()[0][1].startswith('🗳 alice asks: Pizza tonight?'), last())
check('vote already', 'already' in say(B, '!vote x | a | b')[0])
check('vote cast', say(B, '!vote 1') == ['vote counted: yes'])
check('vote by name', say(A, '!vote no') == ['vote counted: no'])
check('vote change', say(B, '!vote 3') == ['vote changed: maybe'])
check('vote bad', 'from 1 to 3' in say(B, '!vote 9')[0])
check('vote status', 'yes 0 · 2) no 1 · 3) maybe 1' in say(B, '!vote')[0], say(B, '!vote'))
check('vote close not yours', 'only alice' in say(B, '!vote close')[0])
import tavern.install as T
T._state['polls']['main']['ends'] = 0
T._tick(); check('timer closes poll (node language)', last()[0][1].startswith('🗳 Résultat : Pizza tonight?') and 'main' not in T._state['polls'], last())
check('saved file', os.listdir(SD + '/plugins/tavern') == ['tavern.json'])
# flood
T._flood.clear(); plugins._cmd_use.clear()
rs = [rrc.handle_input(A, 'main', 'spam %d' % i)[0] for i in range(8)]
check('flood guard', rs[:6] == [[]] * 6 and rs[6] and rs[6][0].startswith('⧗'), rs)
# rnfs
CID = 'bagaibqabciqkq6rth6jhr2vkh7c2ip7p6bsmdxmslbia47rjbmwpcz6wwu6veya'
T._flood.clear()
say(B, 'grab this: rnfs:' + CID + ' great read')
check('spotter hint', last()[0][0] == 'Tavernier' or 'rnfs get ' + CID in last()[0][1], last())
import rnfs.install as R
check('spotted', R._state['spotted'][0][0] == CID, R._state['spotted'])
say(B, 'and bafkreigqte4wbglswc74766w445b67scla7kd2wvrqtp6t7a2ofw32znny too')
check('hint rate limited', 'rnfs get bafkrei' not in last()[0][1] and len(R._state['spotted']) == 2, last())
check('not a cid', R.find_links('bonjour bagel http://x rnfs:name/zz') == [])
check('name link', R.find_links('see rnfs:name/' + 'a' * 32 + '/index.html') == [('name/' + 'a' * 32 + '/index.html', None)])
check('shelf empty', 'empty' in say(A, '!shelf')[0])
check('shelf bad', "isn't" in say(A, '!shelf add nonsense title')[0])
say(A, '!shelf add ' + CID + ' The Manifesto'); check('shelf add', 'shelved #1: The Manifesto' in last()[0][1], last())
check('shelf dup', 'already' in say(B, '!shelf add rnfs:' + CID)[0])
check('shelf list', say(B, '!shelf')[1] == '#1 The Manifesto — rnfs get ' + CID, say(B, '!shelf'))
check('shelf n', say(B, '!shelf 1') == ['#1 The Manifesto — rnfs get ' + CID])
check('shelf rm not yours', 'only alice' in say(B, '!shelf rm 1')[0])
check('shelf rm', say(A, '/shelf rm 1') == ['removed: The Manifesto'])
check('help lists', any('!roll 2d6' in l for l in rrc.help_lines('en')) and any('!shelf' in l for l in rrc.help_lines('en')))
print(rrc.help_lines('en', mesh=True)[-2])
# mesh user
import rrc_mesh
M = 'ef' * 16
rrc_mesh._peers[M] = {'nick': 'meshy', 'room': 'main', 'home_room': 'main'}
rrc.touch_user(M, nick='~meshy', room='main'); i18n.set_lang(M, 'en')
say(A, '!shelf add ' + CID + ' Again')
r = rrc.handle_input(M, 'main', '!shelf')[0]
check('mesh shelf one line', len(r) == 1 and 'on the shelf' in r[0], r)
print('FAILURES', FAIL)
