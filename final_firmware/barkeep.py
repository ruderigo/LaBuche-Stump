# Project Stump -- BarKeep (chat interface + site router)
# Place at: firmware/barkeep.py
#
# Same routing/logic as before -- this pass only changes presentation.
# All command processing, billboard/fserv reuse, and download handling
# are unchanged from the tested version.
#
# DESIGN NOTE: no external fonts or CDN assets anywhere in this file.
# Anyone connected to Stump's own AP has no route to the wider internet
# at all -- a Google Fonts link, for instance, would just silently fail
# for exactly the people this is built for. Every style below is a
# system font stack or plain CSS, nothing fetched.

import os
import ujson as json
import uasyncio as asyncio
import billboard
import fserv
import rrc
import rrc_ui

# Per-node greeter name. Falls back so this module still imports on its
# own (tests, or a config.py from a build that predates the setting).
try:
    from config import BOT_NAME
except ImportError:
    BOT_NAME = "BarKeep"

BARKEEP_ART = (
    "    )  (\n"
    "   (    )\n"
    " .--------.\n"
    "/  o  o  o \\\n"
    "|          |\n"
    " \\________/\n"
    "  |  |  |  |\n"
)

STYLE = """
:root{
  --bg:#1b1512; --panel:#2a2119; --ember:#d97a3a; --ember-bright:#f0a050;
  --text:#ecdfc8; --muted:#9c8d76; --border:#493c2e;
}
*{box-sizing:border-box;}
body{
  background:var(--bg); color:var(--text);
  font-family:Georgia,'Iowan Old Style','Palatino Linotype',serif;
  max-width:600px; margin:0 auto; padding:28px 18px 40px; line-height:1.55;
}
h1{
  font-family:ui-monospace,'Cascadia Code','SF Mono','Courier New',monospace;
  color:var(--ember); font-size:1.35rem; letter-spacing:.02em; margin:0 0 4px;
  animation:glow 5s ease-in-out infinite;
}
@keyframes glow{
  0%,100%{text-shadow:0 0 6px rgba(217,122,58,.30);}
  50%{text-shadow:0 0 15px rgba(217,122,58,.65);}
}
@media (prefers-reduced-motion:reduce){ h1{animation:none;} }
.sub{color:var(--muted); margin-top:0;}
pre{
  color:var(--ember); font-family:ui-monospace,monospace;
  font-size:.82rem; line-height:1.15; margin:0 0 10px;
}
.panel{
  background:var(--panel); border:1px solid var(--border);
  border-radius:8px; padding:12px 14px; margin:12px 0;
  overflow-wrap:break-word; word-break:break-word;
}
li{overflow-wrap:break-word; word-break:break-word;}
#log{max-height:220px; overflow-y:auto;}
#log p{margin:0 0 8px;}
a{color:var(--ember-bright);}
.row{display:flex; gap:8px; flex-wrap:wrap; margin:10px 0;}
.row input{flex:1; min-width:0;}
input,button{
  font-family:inherit; font-size:1rem; padding:10px 12px;
  border-radius:6px; border:1px solid var(--border);
}
input{background:var(--panel); color:var(--text);}
input:focus,button:focus{outline:2px solid var(--ember); outline-offset:1px;}
button{
  background:var(--ember); color:var(--bg); font-weight:bold;
  border:none; cursor:pointer;
}
button:hover{background:var(--ember-bright);}
ul{list-style:none; padding:0; margin:0;}
li{padding:7px 0; border-bottom:1px solid var(--border);}
li:last-child{border-bottom:none;}
small{color:var(--muted);}

/* Two destination tiles. Grid so they stay equal width and stack
   sensibly on a narrow phone, which is the common case here. */
.tiles{display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:14px 0;}
.tile{
  display:flex; flex-direction:column; align-items:center; gap:4px;
  background:var(--panel); border:1px solid var(--border); border-radius:8px;
  padding:16px 10px; color:var(--ember); text-decoration:none;
  transition:border-color .15s, color .15s;
}
.tile:hover,.tile:focus{border-color:var(--ember); color:var(--ember-bright); outline:none;}
.tile span{font-weight:bold; font-size:.95rem;}
.tile small{color:var(--muted); text-align:center; line-height:1.25;}
@media (max-width:380px){ .tiles{grid-template-columns:1fr;} }
"""


def _esc_name(s):
    """Escapes the operator-set greeter name.

    It lands in HTML and, in the chat script, inside a JS string literal
    -- so an apostrophe (entirely plausible in a name like O'Malley)
    would otherwise break the page. Escaping the quote characters covers
    both cases, since the surrounding JS uses single quotes."""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))


# Two destinations, as tiles rather than the text links they used to be.
#
# Inline SVG on purpose. Anyone connected to the Stump's own AP has no
# route to the wider internet, so an icon font or a CDN sprite sheet
# would silently fail to load for precisely the people this is built
# for -- the same reasoning the stylesheet already follows. These are a
# few hundred bytes and always work.
_ICON_CHAT = (
    "<svg viewBox='0 0 24 24' width='30' height='30' fill='none' "
    "stroke='currentColor' stroke-width='1.6' stroke-linecap='round' "
    "stroke-linejoin='round'>"
    "<path d='M21 11.5a8.4 8.4 0 0 1-9 8.4 9.9 9.9 0 0 1-4.2-.9L3 21l1.9-4.4"
    "A8.4 8.4 0 0 1 12 3.1a8.4 8.4 0 0 1 9 8.4z'/>"
    "<path d='M8.5 10.5h7M8.5 14h4.5'/></svg>"
)
_ICON_BOARD = (
    "<svg viewBox='0 0 24 24' width='30' height='30' fill='none' "
    "stroke='currentColor' stroke-width='1.6' stroke-linecap='round' "
    "stroke-linejoin='round'>"
    "<rect x='3' y='4' width='18' height='15' rx='1.5'/>"
    "<path d='M3 8h18M12 19v2M8 21h8'/>"
    "<path d='M6.5 11.5h5M6.5 14.5h8'/></svg>"
)

NAV_TILES = (
    "<div class='tiles'>"
    "<a class='tile' href='/rrc'>" + _ICON_CHAT +
    "<span>Chat</span><small>talk to whoever's here</small></a>"
    "<a class='tile' href='/billboard'>" + _ICON_BOARD +
    "<span>Billboard</span><small>notices &amp; messages</small></a>"
    "</div>"
)


def _page(body):
    return ("<!DOCTYPE html><html><head><meta name='viewport' "
             "content='width=device-width, initial-scale=1'>"
             "<style>" + STYLE + "</style></head><body>" + body + "</body></html>")


def _url_encode(s):
    """Minimal percent-encoding for filenames used in href query strings.
    MicroPython has no urllib.parse.quote built in. Without this, a
    filename containing a space, '&', or '#' breaks the resulting link
    outright -- a real bug, not a style nit, and one that upload (below)
    would have exposed immediately once reachable."""
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    out = ""
    for ch in s:
        if ch in safe:
            out += ch
        else:
            out += "%%%02X" % ord(ch)
    return out


def _clean_filename(name):
    """Same defensive filtering fserv.py's own upload handler already
    applies -- no path traversal, no quote-breaking."""
    name = name.replace("/", "_").replace("..", "_").replace("'", "").replace('"', "")
    return name.strip()


def _process_command(text, identifier):
    cmd = text.strip()
    lower = cmd.lower()

    if lower in ("menu", "help", ""):
        # 'balance' is only listed when there's actually a tab to check.
        balance_line = (
            "&nbsp;&nbsp;<b>balance</b> &mdash; what you've got saved up<br>"
            if fserv.CREDITS_ENABLED else ""
        )
        return (
            "Here's what I've got:<br>"
            "&nbsp;&nbsp;<b>menu</b> &mdash; this list<br>"
            "&nbsp;&nbsp;<b>chat</b> &mdash; talk to whoever else is here<br>"
            "&nbsp;&nbsp;<b>billboard</b> &mdash; the notice board<br>"
            "&nbsp;&nbsp;<b>files</b> &mdash; what's on the shelf<br>"
            "&nbsp;&nbsp;<b>get &lt;name&gt;</b> &mdash; take something home<br>"
            + balance_line +
            "&nbsp;&nbsp;<b>upload</b> &mdash; how to bring something of your own"
        )

    if lower in ("upload", "how to upload", "how do i upload", "share", "share a file"):
        # Don't promise credit that isn't being given.
        if fserv.CREDITS_ENABLED:
            return ("See that box below, under the chat? Pick a file there and hit "
                     "Upload &mdash; bring something, and I'll credit you for it.")
        return ("See that box below, under the chat? Pick a file there and hit "
                 "Upload &mdash; anything you leave is there for the next person.")

    if lower in ("chat", "rrc", "irc", "talk"):
        return "Everyone's in here &mdash; <a href='/rrc'>open RRC</a>."

    if lower == "billboard":
        return "Right through there &mdash; <a href='/billboard'>the billboard</a>."

    if lower == "files":
        if not fserv.sd_ok:
            return "Shelf's empty right now &mdash; no card in the slot."
        names = fserv._list_files()
        if not names:
            return "Nothing on the shelf yet. Bring something, if you've got it."
        lines = []
        for n in names:
            cls = fserv.guess_class(n)
            safe_name = billboard._esc(n)
            # Class is always useful; the cost label only means something
            # when credits are actually being charged. Showing "1 credit"
            # in free mode would be a straightforwardly false statement.
            if fserv.CREDITS_ENABLED:
                tag = " <small>(" + cls + ", " + str(fserv.credit_cost(n)) + " credit)</small>"
            else:
                tag = " <small>(" + cls + ")</small>"
            lines.append(
                "&nbsp;&nbsp;<a href='/download?f=" + _url_encode(n) + "'>" + safe_name +
                "</a>" + tag
            )
        return "On the shelf:<br>" + "<br>".join(lines)

    if lower.startswith("get "):
        fname = _clean_filename(cmd[4:])
        if not fname:
            return "Get what, exactly?"
        return ("Here you go &mdash; <a href='/download?f=" + _url_encode(fname) + "'>" +
                 billboard._esc(fname) + "</a>. Click to take it.")

    if lower == "balance":
        if not fserv.CREDITS_ENABLED:
            return "No tab to keep here &mdash; everything's free. Take what you need."
        bal = fserv.credit_balance(identifier)
        unit = "credit" if bal == 1 else "credits"
        return "You've got " + str(bal) + " " + unit + " with me."

    return "Don't know that one. Try <b>menu</b>."


def _render_chat_page():
    return (
        "<pre>" + BARKEEP_ART + "</pre>"
        "<h1>Stump</h1>"
        "<p class='sub'>Pull up a log. I'm " + _esc_name(BOT_NAME) + ".</p>"
        "<div class='panel' id='log'>"
        "<p><b>" + _esc_name(BOT_NAME) + ":</b> Evening. Type <b>menu</b> to see what's around.</p>"
        "</div>"
        "<div class='row'>"
        "<input id='in' placeholder='Say something...' autofocus>"
        "<button onclick='sendMsg()'>Send</button>"
        "</div>"
        + NAV_TILES +
        "<div class='panel'>"
        "<p class='sub' style='margin-top:0;'>Bring something?</p>"
        "<input type='file' id='upfile'>"
        "<div class='row'>"
        "<input id='uphash' placeholder='Awaiting-slot hash (optional)'>"
        "<button onclick='doUpload()'>Upload</button>"
        "</div>"
        "<p id='upstatus'><small></small></p>"
        "</div>"
        "<script>"
        "function doUpload(){"
        "  var file=document.getElementById('upfile').files[0];"
        "  if(!file){return;}"
        "  var hash=document.getElementById('uphash').value;"
        "  var status=document.getElementById('upstatus');"
        "  status.innerHTML='<small>Sending...</small>';"
        "  fetch('/upload',{method:'POST',headers:{'X-Filename':file.name,'X-Hash':hash},body:file})"
        "    .then(function(r){return r.text();})"
        "    .then(function(t){status.innerHTML='<small>'+t+'</small>';});"
        "}"
        "function sendMsg(){"
        "  var input=document.getElementById('in');"
        "  var text=input.value;"
        "  if(!text)return;"
        "  var log=document.getElementById('log');"
        "  log.innerHTML+='<p><b>You:</b> '+text.replace(/</g,'&lt;')+'</p>';"
        "  input.value='';"
        "  fetch('/chat',{method:'POST',body:text})"
        "    .then(function(r){return r.text();})"
        "    .then(function(t){"
        "      log.innerHTML+='<p><b>"+_esc_name(BOT_NAME)+":</b> '+t+'</p>';"
        "      log.scrollTop=log.scrollHeight;"
        "      input.focus();"
        "    });"
        "}"
        "document.getElementById('in').addEventListener('keydown',function(e){"
        "  if(e.key==='Enter'){sendMsg();}"
        "});"
        "</script>"
    )


# Same escape hatches as the chat page. On a kiosk every page needs to
# reach every other in one touch -- going Billboard -> Home -> Chat is
# two deliberate taps on a wall panel, and the old page couldn't reach
# chat at all.
_ICON_HOME = (
    "<svg viewBox='0 0 24 24' width='24' height='24' fill='none' "
    "stroke='currentColor' stroke-width='1.6' stroke-linecap='round' "
    "stroke-linejoin='round'><path d='M3 10.5 12 3l9 7.5'/>"
    "<path d='M5.5 9.5V20h13V9.5'/></svg>"
)

KIOSK_NAV = (
    "<div class='tiles'>"
    "<a class='tile' href='/'>" + _ICON_HOME + "<span>Home</span></a>"
    "<a class='tile' href='/rrc'>"
    + _ICON_CHAT.replace("width='30' height='30'", "width='24' height='24'")
    + "<span>Chat</span></a>"
    "</div>"
)


def _render_billboard_page():
    return billboard._render_page() + KIOSK_NAV


async def _send(writer, status, body, content_type="text/html"):
    """Headers and body are written separately, and str bodies encoded,
    rather than concatenated -- 'str' + bytes is a hard TypeError in
    MicroPython (verified against the real interpreter), so the old
    concatenating version failed outright on every binary response.
    fserv.py's own _send already did it this way; this had diverged."""
    # Connection: close matters here. The server closes after every
    # response, but HTTP/1.1 defaults to keep-alive -- so without this
    # the browser holds the socket open expecting to reuse it, then has
    # to discover it's dead and re-issue the request on a new one. That
    # shows up as pages that feel sluggish or hang on the first click.
    resp = ("HTTP/1.1 " + status + "\r\nContent-Type: " + content_type +
             "\r\nContent-Length: " + str(len(body)) +
             "\r\nConnection: close\r\n\r\n")
    await writer.awrite(resp)
    if isinstance(body, str):
        body = body.encode()
    await writer.awrite(body)


MIME_TYPES = {
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav",
    "flac": "audio/flac", "ogg": "audio/ogg",
    "mp4": "video/mp4", "mkv": "video/x-matroska", "mov": "video/quicktime",
    "avi": "video/x-msvideo", "webm": "video/webm",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
    "pdf": "application/pdf", "txt": "text/plain", "md": "text/plain",
    "zip": "application/zip", "epub": "application/epub+zip",
}


def _mime_for(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return MIME_TYPES.get(ext, "application/octet-stream")


def _disposition_name(filename):
    """Makes a filename safe to sit inside a quoted HTTP header value.

    A quote or backslash would terminate or escape the quoted string and
    corrupt the header; CR/LF would split it into forged extra headers.
    Anything problematic becomes '_' rather than being dropped, so the
    name still resembles the original."""
    out = []
    for ch in filename:
        out.append("_" if (ch in '"\\' or ord(ch) < 32 or ord(ch) == 127) else ch)
    return "".join(out)


async def _send_file(writer, fpath, size, filename, chunk=16384):
    """Streams a file from disk in chunks instead of reading it into RAM.

    Content-Disposition is what actually gives the saved file its real
    name. Without it the browser names the download after the last URL
    path segment -- "download" -- with no extension, so every file
    arrived unreadable until the user renamed it by hand.

    16KB chunks, not 2KB: a 10MB transfer at 2KB is over 5000
    read/write/await cycles, and every await hands control to the DNS
    poller and the bridge loop before coming back. The per-cycle
    overhead, not the bytes, dominated transfer time. 16KB is still tiny
    against available PSRAM and nowhere near large enough to fragment
    the heap, but cuts the cycle count eightfold.
    """
    await writer.awrite(
        "HTTP/1.1 200 OK\r\nContent-Type: " + _mime_for(filename) + "\r\n"
        "Content-Disposition: attachment; filename=\"" + _disposition_name(filename) + "\"\r\n"
        "Content-Length: " + str(size) + "\r\nConnection: close\r\n\r\n"
    )
    with open(fpath, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            await writer.awrite(buf)


def _query(path, key):
    """Pulls one value out of a query string. MicroPython has no
    urllib.parse, and the existing ad-hoc split('f=') pattern elsewhere
    in this file only works when there is exactly one parameter -- RRC
    polls with two (room and since), so it needs to actually parse."""
    if "?" not in path:
        return None
    qs = path.split("?", 1)[1]
    for pair in qs.split("&"):
        k, _, v = pair.partition("=")
        if k == key:
            return billboard._url_decode(v)
    return None


MAX_HEADERS = 40
# Chat commands and billboard entries are tiny (a line of text, and
# billboard caps entries at 200 chars anyway). Anything claiming to be
# bigger is a broken or hostile client, not a real post.
MAX_SMALL_BODY = 8192


async def _read_small_body(reader, length, chunk=1024):
    """Reads a small request body, bounded and EOF-safe.

    Not readexactly(): that blocks indefinitely if a client sends a
    Content-Length larger than what it actually delivers, leaking the
    connection until something else times it out. This returns whatever
    genuinely arrives and stops at EOF, and refuses to buffer more than
    MAX_SMALL_BODY regardless of what the header claims -- the same
    shape as fserv.drain/stream_to_file, which already handle a lying
    length correctly."""
    if length <= 0:
        return b""
    if length > MAX_SMALL_BODY:
        length = MAX_SMALL_BODY
    out = bytearray()
    while len(out) < length:
        want = length - len(out)
        if want > chunk:
            want = chunk
        buf = await reader.read(want)
        if not buf:
            break
        out.extend(buf)
    return bytes(out)


async def _handle(reader, writer):
    try:
        request_line = await reader.readline()
        # Parse defensively: a phone's captive-portal probe, a port
        # scanner, or a half-open connection can all produce a request
        # line that doesn't split into three parts. The old unpack threw
        # and the socket closed with NOTHING sent back -- confirmed by
        # probing with a bare newline and with "GET /" (no version).
        # A silent close is the wrong answer here specifically because
        # captive-portal detection depends on the phone getting a real
        # HTTP response to its probe.
        parts = request_line.decode().split()
        if len(parts) < 2:
            await _send(writer, "400 Bad Request", "Malformed request.", "text/plain")
            return
        method, path = parts[0], parts[1]

        headers = {}
        header_count = 0
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b""):
                break
            header_count += 1
            if header_count > MAX_HEADERS:
                # Bounded so a broken or hostile client can't grow this
                # dict indefinitely on a memory-constrained board.
                await _send(writer, "400 Bad Request", "Too many headers.", "text/plain")
                return
            k, _, v = line.decode().partition(":")
            headers[k.strip().lower()] = v.strip()

        # Content-Length has to survive garbage too -- "abc" used to
        # throw here and close the socket silently, so an upload with a
        # broken header got no error message at all.
        try:
            content_length = int(headers.get("content-length", "0"))
        except ValueError:
            await _send(writer, "400 Bad Request", "Bad Content-Length.", "text/plain")
            return
        if content_length < 0:
            content_length = 0
        headers["content-length"] = str(content_length)

        peer = writer.get_extra_info("peername")
        identifier = billboard._extract_ip(peer)

        if method == "POST" and path.startswith("/chat"):
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            reply = _process_command(body.decode(), identifier)
            await _send(writer, "200 OK", reply, "text/plain")

        elif method == "POST" and path.startswith("/post"):
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            entry = ""
            for kv in body.decode().split("&"):
                if kv.startswith("entry="):
                    entry = billboard._url_decode(kv[6:])
            billboard._append_entry(entry, identifier)
            await writer.awrite("HTTP/1.1 303 See Other\r\nLocation: /billboard\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

        elif method == "GET" and path.startswith("/rrc/poll"):
            # Poll for new messages. The client sends the highest id it
            # already has, so only genuinely new lines come back rather
            # than the whole room every two seconds.
            q_room = _query(path, "room") or rrc.DEFAULT_ROOM
            try:
                since_id = int(_query(path, "since") or "0")
            except ValueError:
                since_id = 0
            user = rrc.touch_user(identifier)
            # The server's idea of which room this client is in wins --
            # /join from another tab, or a room that has since vanished,
            # both need to be reflected back rather than silently
            # leaving the client polling a room it isn't in.
            actual = user["room"]
            if not rrc.room_exists(actual):
                actual = rrc.DEFAULT_ROOM
                rrc.touch_user(identifier, room=actual)
            if actual != q_room:
                since_id = 0
            payload = {
                "room": actual,
                "nick": user["nick"],
                "topic": rrc.topic(actual),
                "rooms": rrc.room_names(),
                "messages": rrc.since(actual, since_id),
                # Private messages ride the same poll rather than a
                # second endpoint: one request per cycle instead of two
                # on a board where each connection costs real work, and
                # they share the message-id sequence so the client's
                # existing since/lastId bookkeeping covers both.
                "dms": rrc.dms_since(identifier, since_id),
            }
            await _send(writer, "200 OK", json.dumps(payload), "application/json")

        elif method == "POST" and path.startswith("/rrc/send"):
            length = int(headers.get("content-length", "0"))
            # The read below is already bounded, so an oversized paste
            # can't exhaust memory -- but silently truncating it means
            # the sender watches a message go out that nobody receives
            # in full. Refuse it and say so instead.
            if length > MAX_SMALL_BODY:
                await _send(writer, "413 Payload Too Large",
                             json.dumps({"replies": [
                                 "message too long (%d bytes, limit %d) -- nothing was sent"
                                 % (length, MAX_SMALL_BODY)], "room": None}),
                             "application/json")
                return
            body = await _read_small_body(reader, length)
            user = rrc.touch_user(identifier)
            # The SERVER decides which room this client is in, not the
            # client. X-Room is deliberately ignored: the client only
            # ever learns its room from /rrc/poll in the first place, so
            # a header that disagrees is stale (a second tab left on an
            # old room) or forged. Honouring it used to move the user --
            # silently pulling them out of the room they were actually
            # in, and letting anyone post into a room they never joined.
            room = user["room"] if rrc.room_exists(user["room"]) else rrc.DEFAULT_ROOM
            replies, new_room = rrc.handle_input(identifier, room, body.decode())
            await _send(writer, "200 OK",
                         json.dumps({"replies": replies, "room": new_room}),
                         "application/json")

        elif method == "GET" and path.startswith("/rrc"):
            user = rrc.touch_user(identifier)
            await _send(writer, "200 OK", rrc_ui.render_page(user["room"], user["nick"]))

        elif method == "GET" and path.startswith("/billboard"):
            await _send(writer, "200 OK", _page(_render_billboard_page()))

        elif method == "POST" and path.startswith("/upload"):
            fname = _clean_filename(headers.get("x-filename", "upload.bin"))
            hash_hex = headers.get("x-hash", "").strip()
            length = int(headers.get("content-length", "0"))

            # Validate and pick the destination BEFORE reading a single
            # byte of the body. The old order read the whole upload into
            # memory first and only then checked whether it could be
            # accepted -- so a rejected upload still had to fit in RAM,
            # which is the exact failure this restructure removes.
            # Rejections drain instead of buffering.
            if not fname:
                await fserv.drain(reader, length)
                await _send(writer, "400 Bad Request", "No filename given.", "text/plain")
            elif not fserv.sd_ok:
                await fserv.drain(reader, length)
                await _send(writer, "503 Service Unavailable", "No card in the slot right now.", "text/plain")
            elif length <= 0:
                await _send(writer, "400 Bad Request", "Empty upload.", "text/plain")
            else:
                table = fserv._awaiting()
                is_slot = bool(hash_hex) and hash_hex in table and not table[hash_hex]["fulfilled"]
                if is_slot:
                    dest = fserv.SD_MOUNT + "/slot_" + hash_hex + "_" + fname
                else:
                    dest = fserv.SHARED_DIR + "/" + fname

                ok, written = await fserv.stream_to_file(reader, dest, length)

                if not ok:
                    await _send(writer, "500 Internal Server Error",
                                 "Upload interrupted (" + str(written) + " of " +
                                 str(length) + " bytes) — nothing was kept. Try again.",
                                 "text/plain")
                elif is_slot:
                    # Only mark the slot fulfilled once the bytes are
                    # actually on disk -- marking it earlier would burn a
                    # one-shot slot on an upload that never completed.
                    table[hash_hex]["fulfilled"] = True
                    fserv._save_json(fserv.AWAITING_FILE, table)
                    await _send(writer, "200 OK", "Delivered to your awaiting slot.", "text/plain")
                else:
                    credited = fserv.credit_add(identifier, fserv.credit_cost(fname))
                    if fserv.CREDITS_ENABLED:
                        msg = "Uploaded. Your balance: " + str(credited)
                    else:
                        msg = "Uploaded. Thanks for bringing something."
                    await _send(writer, "200 OK", msg, "text/plain")

        elif method == "GET" and path.startswith("/download"):
            raw_f = path.split("f=", 1)[-1] if "f=" in path else ""
            fname = _clean_filename(billboard._url_decode(raw_f))
            fpath = fserv.SHARED_DIR + "/" + fname
            try:
                # stat first: confirms the file exists and gives the size
                # needed for Content-Length, without reading it into RAM.
                # Charging only after this means a missing/unreadable file
                # can't debit someone for something they never received.
                size = os.stat(fpath)[6]
                fserv.credit_add(identifier, -fserv.credit_cost(fname))
                await _send_file(writer, fpath, size, fname)
            except OSError:
                await _send(writer, "404 Not Found", "not found", "text/plain")

        else:
            await _send(writer, "200 OK", _page(_render_chat_page()))

    except Exception as e:
        print("[barkeep] request error:", e)
    finally:
        await writer.aclose()


async def run_barkeep_server(port=80):
    """Starts the one and only HTTP server.

    Reports its own bind result. If port 80 can't be bound the node
    would otherwise come up looking entirely healthy -- WiFi joined,
    mesh announced, AP broadcasting -- while serving nothing at all, and
    nothing in the boot log would say why."""
    try:
        await asyncio.start_server(_handle, "0.0.0.0", port)
    except Exception as e:
        print("[web] FAILED to bind port %d: %s" % (port, e))
        print("[web] the billboard, chat and file pages are NOT available.")
        raise
    print("[web] serving on port", port)
