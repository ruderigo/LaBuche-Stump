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
  - Only the voice recorder (/rrc/rec, a small window the chat opens)
    and /admin use HTTPS; the chat and the other pages stay on plain HTTP,
    where its 2-second polls cost no handshake. Before either, the
    visitor's browser fetches https://<hostname>/tls-ok; only if that
    succeeds (certificate valid by the PHONE's clock) is HTTPS used.
    Expired certificate, wrong clock, no answer: plain HTTP and the
    phone's recorder app, with no warning. Visitors on the router's LAN
    never try: there the hostname resolves to the public internet.

With no certificate installed, or a MicroPython without TLS server
support, nothing changes: the node serves plain HTTP as before.
"""

import ujson as json

TLS_DIR = "/tls"
CERT_FILE = TLS_DIR + "/fullchain.pem"
KEY_FILE = TLS_DIR + "/privkey.pem"
INFO_FILE = TLS_DIR + "/info.json"      # {"host", "not_after"} written at install
AP_PREFIX = "192.168.4."                # the Stump's own Wi-Fi (chain.py may move it)


def _ap_prefix():
    try:
        import chain
        if chain.settings():
            return chain.ap_prefix()
    except Exception:
        pass
    return AP_PREFIX

# "code" names the reason for /admin, which says it in the reader's
# language (i18n admin_tls_why_<code>); "detail" is the technical part.
_state = {"active": False, "reason": "no certificate installed", "code": "no_cert", "detail": "",
          "host": None, "not_after": None}


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def context():
    """An SSLContext for port 443, or None (and the reason in status())."""
    try:
        info = json.loads(_read(INFO_FILE))
        cert, key = _read(CERT_FILE), _read(KEY_FILE)
    except (OSError, ValueError):
        _state.update(active=False, reason="no certificate installed", code="no_cert", detail="")
        return None
    _state.update(host=info.get("host"), not_after=info.get("not_after"))
    try:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
    except (ImportError, AttributeError) as e:
        _state.update(active=False, reason="this MicroPython can't serve TLS (%s)" % e, code="no_tls", detail=str(e))
        return None
    except Exception as e:
        _state.update(active=False, reason="certificate or key rejected: %s" % e, code="rejected", detail=str(e))
        return None
    _state.update(active=True, reason="", code="", detail="")
    return ctx


def mark_failed(reason, code="failed", detail=""):
    _state.update(active=False, reason=reason, code=code, detail=detail or reason)


def status():
    return dict(_state)


def https_redirect(peer_ip, path):
    """The https:// URL a plain-HTTP visitor's browser should TRY, or None. Only for
    visitors on the Stump's own Wi-Fi, where the hostname resolves here."""
    if not _state["active"] or not _state["host"]:
        return None
    if not (peer_ip or "").startswith(_ap_prefix()):
        return None
    return "https://" + _state["host"] + (path if path.startswith("/") else "/")


def days_left(now_unix):
    """Days until the certificate expires (negative once expired), or None."""
    na = _state.get("not_after")
    if not na:
        return None
    return (int(na) - int(now_unix)) // 86400
