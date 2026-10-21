import sys
exec(open(sys.argv[1] + '/harness.py').read())
import plugins, rrc, i18n
print(plugins.load_all())
A = '192.168.4.50'; i18n.set_lang(A, 'en'); rrc.touch_user(A, nick='alice', room='main')
for t in ('!quote add <script>alert(1)</script>', '!vote Best <b>drink</b>? | tea | coffee', '!vote 2', '!shelf add bagaibqabciqkq6rth6jhr2vkh7c2ip7p6bsmdxmslbia47rjbmwpcz6wwu6veya Manifesto & co'):
    plugins._cmd_use.clear(); print(t, rrc.handle_input(A, 'main', t)[0])
s, ct, b = req('/p/tavern/')
print(s, '<script>alert' not in b, '&lt;script&gt;' in b, 'coffee' in b, 'Hall of fame' in b or 'livre' in b)
s, ct, b = req('/p/tavern/votes.json'); print(s, ct, b[:200])
s, ct, b = req('/p/rnfs/'); print(s, 'Manifesto &amp; co' in b, 'rnfs get bagaib' in b, "href='/tools'" in b)
s, ct, b = req('/p/rnfs/shelf.json'); print(ct, b[:150])
s, ct, b = req('/'); i = b.find('tiles home'); print('cards:', "href='/p/tavern/'" in b, "href='/p/rnfs/'" in b, 'La taverne' in b, 'Étagère rnfs' in b)
s, ct, b = req('/plugins.json'); print(b)
