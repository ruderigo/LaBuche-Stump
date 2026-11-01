# HTTPS only where it's needed (review H-01): with a certificate, the chat
# stays on plain HTTP (its 2-second polls were each a TLS handshake), /admin
# moves to HTTPS (a password is typed there), and the microphone opens in
# a small HTTPS window, /rrc/rec, that hands the recording back to the chat.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


import stump_tls as tls, i18n
i18n.set_lang('192.168.4.50', 'en')

# No certificate: nothing changes
s, ct, b = req('/rrc')
check("no certificate: chat page, no recorder window", s.startswith('200') and 'var REC_URL=""' in b, b[:200])

tls._state.update(active=True, host='stump.test')
s, ct, b = req('/rrc')
check("chat stays on plain HTTP", s.startswith('200') and '<title>RRC' in b, s)
check("chat knows the HTTPS recorder", 'var REC_URL="https://stump.test/rrc/rec"' in b)
check("chat checks the certificate before using it", "recOrigin+'/tls-ok'" in b)
s, ct, b = req('/')
check("home stays on plain HTTP (no upgrade page)", s.startswith('200') and "tls-ok" not in b)
s, ct, b = req('/billboard')
check("billboard stays on plain HTTP", s.startswith('200') and "tls-ok" not in b)
s, ct, b = req('/admin')
check("/admin tries HTTPS first (a password is typed there)", "https://stump.test/tls-ok" in b
      and "https://stump.test/admin" in b, b[:300])
s, ct, b = req('/admin?plain=1')
check("/admin falls back to HTTP when the phone refuses the certificate", "admin_pass" in b)
s, ct, b = req('/rrc', headers=('Host: stump.test',), secure=True)
check("chat opened over HTTPS goes back to HTTP", s.startswith('302') and header('Location') == 'http://stump.test/rrc',
      (s, header('Location')))
s, ct, b = req('/rrc/rec?o=http%3A%2F%2F192.168.4.1', headers=('Host: stump.test',), secure=True)
check("recorder page over HTTPS", s.startswith('200') and "id='rec'" in b and 'getUserMedia' in b, s)
check("recorder hands audio back only to this Stump's chat",
      '"http://stump.test"' in b and '"http://192.168.4.1"' in b and "postMessage({stumpVoice:buf}, back" in b)
check("recorder in the visitor's language", "Tap ♪, speak, tap ■" in b)
s, ct, b = req('/rrc/poll')
check("polls answer on plain HTTP (no upgrade)", s.startswith('200') and ct.startswith('application/json'), (s, ct))
print('FAILURES:', FAIL)
