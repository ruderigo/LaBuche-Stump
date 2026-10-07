"""
stump_tls -- named so, not "tls": recent MicroPython has a built-in "tls"
module (the layer under "ssl"), and built-ins win over files.

HTTPS for the Stump's own Wi-Fi, so browsers treat the chat as a secure
page -- which is what lets them open the microphone for voice notes
(browsers only allow live microphone access on HTTPS or localhost).

How it fits together:
  - A real certificate for a hostname you own (e.g. stump.example.app),
    from Let's Encrypt, installed with:
        python3 provisioner.py --install-cert PORT fullchain.pem privkey.pem
    (see docs/HTTPS_SETUP.md). It lives on internal flash, so it works
    without an SD card.
  - On the Stump's own Wi-Fi the captive-portal DNS answers EVERY name
    with the Stump's address, so https://stump.example.app reaches the
    Stump, and the certificate is valid for that name -- no warning,
    even with no internet (phones already trust the issuing CA).
  - Visitors who open a page over plain HTTP on the Stump's Wi-Fi get a
    tiny page that asks their browser to fetch https://<hostname>/tls-ok;
    only if that succeeds (certificate valid by the PHONE's clock) does it
    move to HTTPS. Expired certificate, wrong clock, no answer: they stay
    on HTTP, with no warning. Visitors on the router's LAN never try:
    there the hostname resolves to the public internet, not this node.

With no certificate installed, or a MicroPython without TLS server
support, nothing changes: the node serves plain HTTP as before.
"""

import ujson as json

TLS_DIR = "/tls"
CERT_FILE = TLS_DIR + "/fullchain.pem"
KEY_FILE = TLS_DIR + "/privkey.pem"
INFO_FILE = TLS_DIR + "/info.json"      # {"host", "not_after"} written at install
AP_PREFIX = "192.168.4."                # the Stump's own Wi-Fi

_state = {"active": False, "reason": "no certificate installed", "host": None, "not_after": None}


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def context():
    """An SSLContext for port 443, or None (and the reason in status())."""
    try:
        info = json.loads(_read(INFO_FILE))
        cert, key = _read(CERT_FILE), _read(KEY_FILE)
    except (OSError, ValueError):
        _state.update(active=False, reason="no certificate installed")
        return None
    _state.update(host=info.get("host"), not_after=info.get("not_after"))
    try:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
    except (ImportError, AttributeError) as e:
        _state.update(active=False, reason="this MicroPython can't serve TLS (%s)" % e)
        return None
    except Exception as e:
        _state.update(active=False, reason="certificate or key rejected: %s" % e)
        return None
    _state.update(active=True, reason="")
    return ctx


def mark_failed(reason):
    _state.update(active=False, reason=reason)


def status():
    return dict(_state)


def https_redirect(peer_ip, path):
    """The https:// URL a plain-HTTP visitor's browser should TRY, or None. Only for
    visitors on the Stump's own Wi-Fi, where the hostname resolves here."""
    if not _state["active"] or not _state["host"]:
        return None
    if not (peer_ip or "").startswith(AP_PREFIX):
        return None
    return "https://" + _state["host"] + (path if path.startswith("/") else "/")


def days_left(now_unix):
    """Days until the certificate expires (negative once expired), or None."""
    na = _state.get("not_after")
    if not na:
        return None
    return (int(na) - int(now_unix)) // 86400
