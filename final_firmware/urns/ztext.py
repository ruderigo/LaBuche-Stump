# Packed text: more of a chat line in each LXMF packet.
#
# One LoRa packet holds about 295 bytes of LXMF content, and a chat line is
# far from random: pack() writes it in about 60% of its bytes (a /help
# reply in a sixth), so a batch that took two packets takes one, and one
# that needed a link fits a single packet. Between two ends that both said
# they read it (the "z" in their LXMF announce, urns/lxmf.py) -- to anyone
# else text goes as it always did.
#
# The format, version 1. An LXMF content whose first byte is
#   0xFF  is packed: the rest is the body of one DEFLATE block (RFC 1951);
#   0xFE  is the rest as it stands (for a content that starts with 0xFE or
#         0xFF itself: no UTF-8 text does);
#   anything else is plain, as before.
# The DEFLATE block is a final "dynamic Huffman" block whose header (the
# two code tables) is never sent: it is _HEAD below, the same for every
# message, with codes made for chat text in French, English and Spanish
# (a space or an "e" is 3 or 4 bits). Before it comes a preset dictionary,
# _DICT: the Stump's own replies, common words, the tokens of its lines.
# So to read one, any inflate will do:
#
#   inflate(stored block holding _DICT + _HEAD + what came after 0xFF)
#
# and drop the first len(_DICT) bytes. That is unpack() here: MicroPython's
# deflate module on a board, zlib on a computer; nothing of ours parses
# what the radio brings. pack() is the only code of ours, and it only
# writes: a greedy LZ77 over _DICT + the text, in Python, taking a match
# only where it costs fewer bits than the letters.
#
# _DICT, _LL, _DL and _HEAD are the format: they never change (another
# version gets another first byte). tests/mp/ztext.py holds their hash and
# fixed messages; FireFly carries the same file.
#
# This file is the same in Project Stump (final_firmware/urns/ztext.py)
# and in FireFly for Android (app/src/main/python/firefly/ztext.py).

PACKED = 0xFF
PLAIN = 0xFE
MAX = 2048            # the longest text packed, and the most unpack() gives back
MIN = 12              # shorter than this, not worth the try

# The preset dictionary (most used last: the nearer, the fewer bits).
_DICT = (
    "cambiar tu nombre\n"
    "change your name\n"
    "changer de nom\n"
    "unirte o crear una sala\n"
    "join or create a room\n"
    "rejoindre ou créer une salle\n"
    "salir, volver a #main\n"
    "leave, back to #main\n"
    "quitter, retour à #main\n"
    "listar las salas\n"
    "list rooms\n"
    "lister les salles\n"
    "quién está en esta sala\n"
    "who's in this room\n"
    "qui est dans cette salle\n"
    "establecer el tema de la sala\n"
    "set the room topic\n"
    "changer le sujet de la salle\n"
    "mensaje privado, solo esa persona lo ve\n"
    "private message, only they see it\n"
    "message privé, seul(e) cette personne le voit\n"
    "hablar en tercera persona\n"
    "speak in the third person\n"
    "parler à la troisième personne\n"
    "los últimos anuncios (o los de esas palabras); /board <n.º> abre uno\n"
    "the newest notices (or those about the words); /board <id> opens one\n"
    "les dernières annonces (ou celles sur ces mots) ; /board <n°> en ouvre une\n"
    "un nuevo anuncio en el tablón\n"
    "a new notice on the billboard\n"
    "une nouvelle annonce au babillard\n"
    "borrar tu propia vista\n"
    "clear your own view\n"
    "effacer ta propre vue\n"
    "esta lista\n"
    "this list\n"
    "cette liste\n"
    "✓ llegó · ✗ se fue · ✎ cambió · → a · ⊖ nadie con ese nombre · ~ en la malla · [DM] mensaje "
    "privado · = ya estás ahí · ? comando desconocido · ⊘ rechazado · ♪ nota de voz · ⧗ más despacio\n"
    "✓ joined · ✗ left · ✎ changed · → to · ⊖ no one by that name · ~ on the mesh · [DM] private "
    "message · = already there · ? unknown command · ⊘ refused · ♪ voice note · ⧗ slow down\n"
    "✓ arrivé · ✗ parti · ✎ changé · → vers · ⊖ personne de ce nom · ~ sur le maillage · [DM] message "
    "privé · = déjà là · ? commande inconnue · ⊘ refusé · ♪ note vocale · ⧗ ralentis\n"
    "<nombre>\n"
    "<acción>\n"
    "<action>\n"
    "[palabras]\n"
    "<título>\n"
    "comando desconocido: /\n"
    "  (prueba /help)\n"
    "unknown command: /\n"
    "  (try /help)\n"
    "commande inconnue : /\n"
    "  (essaie /help)\n"
    "ya estás en #\n"
    "you're already in #\n"
    "tu es déjà dans #\n"
    "estás en #main — no hay adónde volver\n"
    "you're in #main -- nowhere to part to\n"
    "tu es dans #main — nulle part où retourner\n"
    "ese ya es tu nombre\n"
    "that's already your name\n"
    "c'est déjà ton nom\n"
    "uso: /msg <quién> <mensaje>\n"
    "usage: /msg <who> <message>\n"
    "usage : /msg <qui> <message>\n"
    "hablar solo es gratis — prueba con alguien más\n"
    "talking to yourself is free -- try someone else\n"
    "se parler à soi-même, c'est gratuit — essaie quelqu'un d'autre\n"
    "tu mensaje llegó con \n"
    " min de retraso (dejado en un nodo de propagación) — no se hizo nada; reenvíalo directamente\n"
    "your message arrived \n"
    " min late (left at a propagation node) — nothing was done; send it again directly\n"
    "ton message est arrivé avec \n"
    " min de retard (laissé à un nœud de propagation) — rien n'a été fait ; renvoie-le directement\n"
    "(mensaje privado vía el chat de este nodo — para responder: /msg <quién> <mensaje>)\n"
    "(private message via this node's chat — to reply: /msg <who> <message>)\n"
    "(message privé via le clavardage de ce nœud — pour répondre : /msg <qui> <message>)\n"
    "escribes demasiado rápido — espera un poco\n"
    "you're typing too fast — wait a little\n"
    "tu écris trop vite — attends un peu\n"
    "se requiere identidad verificada — envía /auth para verificarte\n"
    " recibido prueba funciona calor frío viento nieve lluvia batería radio mensajes mensaje luego "
    "hasta noches buenas días buenos favor perdón claro vale llegué llego casa lejos cerca oeste "
    "este norte camino campamento comida agua ayuda necesito durante desde entre donde cuando otro "
    "otra nunca siempre después antes tiempo tarde noche mañana allí aquí ahora dónde cómo cuándo "
    "adiós hola gracias buena bueno bien nadie alguien algo nada todas toda todos todo quiere quiero "
    "querer puede puedo poder dijo decir vamos hace hago hacer tienen tiene tengo tener están estamos "
    "está estás estoy estar ustedes usted nosotros ellos ella también sobre porque estos esta pero "
    "como para  terminé reçu bien test essai fonctionne marche chaud froid vent neige pluie dehors "
    "ensemble tard plus bientôt nuit bonne plaît vous s'il désolé d'accord peut-être personne monde "
    "tout messages message batterie radio icitte tantôt correct t'es j'suis qu'on l'on d'une d'un "
    "qu'il n'est j'ai c'est quelqu'un arrivé arrive maison loin près ouest nord route chemin camp "
    "aide besoin pendant depuis vers chez entre sous sans avant après maintenant aujourd'hui demain "
    "matin soir jour temps chose quelque rien jamais toujours encore donc alors parce pourquoi comment "
    "quand quoi bonsoir bonjour salut merci trois deux voir vient viens venir aller allez vais dire "
    "veut veux peux peut très même leurs leur votre notre cette aussi comme était sont êtes sommes "
    "suis avez avons avoir être fait faire toutes toute tous mais elles nous avec dans pour elle "
    " over copy testing test working power wind snow rain weather channel station found while another "
    "same little long last each between should through around down always never before check messages "
    "message let's isn't won't doesn't didn't what's that's you're can't don't it's there's nothing "
    "something someone everyone anyone nice great sure maybe really again still left right home coming "
    "near west east south north road trail camp food water battery signal radio meeting meet night "
    "morning today tomorrow tonight help need hello sorry please thank thanks okay where more much "
    "very here going said been were most give these because want even well first work after back "
    "also think come only look then than other them could some good your year into people take know "
    "just time like make when which about what their there would will they from this with have that "
    " https:// http://192.168.4.1/ #wifi-mesh #lora-mesh #main /join # /nick /names /rooms /help "
    "/part /whoami /topic /hello /auth AUTH-CHALLENGE AUTH-OK AUTH-FAIL not verified this session "
    "-- /auth to prove an identity [ROOM] # wifi 1 lora 1\n"
    "[NAMES 1/2] [NAMES 2/2] \n"
    "[NAMES] - guest- mesh- @Stump \n"
    "⊖ \n"
    "⊘ # \n"
    "✗ \n"
    "✎ # \n"
    "✓ ~\n"
    "⧗ \n"
    "= # — \n"
    "? /\n"
    "→ #\n"
    "* \n"
    "[DM>] <\n"
    "[DM] <>: ♪ 5.0 s /msg 0123456789abcdef. , ! ? :) ...\n"
    "#: , ·0  ·1  [hybrid] [minted]\n"
    "<~@> \n"
    "<"
).encode()

# Code lengths, one hex digit each: the 286 literal/length codes, the 26
# distance codes (up to 8192 back). _HEAD is the block header that says so.
_LL = (
    "ddddddddd7dddddddddddddddddddddd49ddddd8999d67699878999998896d6987989799989d68889a778abddbdddddb"
    "95766568758966556a556589879dddddddddddddabddccbbbbbbbbbbbbbbbbbbabbbbbbbb8bbbbbbbbbbbbbbbbbbbbbb"
    "dda7dbddddddddddddddddddddddddddddbdddddddddddddbddddddddddddddd5d35567899bddddddddddddddddddd"
)
_DL = "99888888777778686644332338"
_HEAD = bytes.fromhex(
    "02b4e71680712c597746389930335c87643b3d6d6b6492dc696b2c8f6d392dc96f466f930dad6bbaab35257757f5abea"
    "92350ef3dbe4ff9719dff26a99199237d93033c38679c39c544e558fec0d33dbc221b5baef3de73b47"
)

_LB = (3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258)
_LE = (0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0)
_DB = (1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145)
_DE = (0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11)
_MATCH = 4            # the shortest match looked for
_WBITS = 13           # an 8 KB window: _DICT and MAX bytes of text

assert len(_DICT) + MAX <= 1 << _WBITS

# The stored block that hands _DICT to an inflate, then the block header.
_n = len(_DICT)
_PRE = bytes((0, _n & 255, _n >> 8, ~_n & 255, (~_n >> 8) & 255)) + _DICT + _HEAD
del _n

_codes = []           # [lengths, codes] for literals/lengths, then for distances: made on first use


def _canon(lengths):
    """The canonical Huffman codes of these code lengths (RFC 1951,
    3.2.2), each one's bits reversed: DEFLATE writes them first bit first
    into a stream filled from the low bit."""
    count = [0] * 16
    for l in lengths:
        count[l] += 1
    nxt = [0] * 16
    code = 0
    for b in range(1, 16):
        code = (code + count[b - 1]) << 1
        nxt[b] = code
    out = []
    for l in lengths:
        c = nxt[l]
        nxt[l] += 1
        r = 0
        for _ in range(l):
            r = (r << 1) | (c & 1)
            c >>= 1
        out.append(r)
    return out


def _tables():
    if not _codes:
        ll = bytes(int(c, 16) for c in _LL)
        dl = bytes(int(c, 16) for c in _DL)
        _codes.extend((ll, _canon(ll), dl, _canon(dl)))
    return _codes


def _deflate(data):
    """The block's body for `data`: literals and (length, distance) pairs
    in the fixed codes, then the end-of-block code."""
    LL, LC, DL, DC = _tables()
    w = _DICT + data
    n = len(w)
    i = len(_DICT)
    out = bytearray()
    acc = nb = 0
    while i < n:
        l = 0
        maxl = n - i
        if maxl > 258:
            maxl = 258
        if maxl >= _MATCH:
            # The nearest place the next bytes were seen; then, as long as
            # a longer match exists anywhere before, the nearest of those.
            p = w.rfind(w[i:i + _MATCH], 0, i + _MATCH - 1)
            if p >= 0:
                l = _MATCH
                while True:
                    while l < maxl and w[p + l] == w[i + l]:
                        l += 1
                    if l >= maxl:
                        break
                    q = w.rfind(w[i:i + l + 1], 0, i + l)
                    if q < 0:
                        break
                    p = q
                    l += 1
                d = i - p
                k = 28
                while _LB[k] > l:
                    k -= 1
                j = 25
                while _DB[j] > d:
                    j -= 1
                # Taken only when cheaper than the same bytes as literals.
                cost = LL[257 + k] + _LE[k] + DL[j] + _DE[j]
                lit = 0
                for x in range(i, i + l):
                    lit += LL[w[x]]
                    if lit > cost:
                        break
                if lit <= cost:
                    l = 0
        if l:
            acc |= LC[257 + k] << nb
            nb += LL[257 + k]
            if _LE[k]:
                acc |= (l - _LB[k]) << nb
                nb += _LE[k]
            acc |= DC[j] << nb
            nb += DL[j]
            if _DE[j]:
                acc |= (d - _DB[j]) << nb
                nb += _DE[j]
            i += l
        else:
            c = w[i]
            acc |= LC[c] << nb
            nb += LL[c]
            i += 1
        while nb >= 8:
            out.append(acc & 255)
            acc >>= 8
            nb -= 8
    acc |= LC[256] << nb
    nb += LL[256]
    while nb > 0:
        out.append(acc & 255)
        acc >>= 8
        nb -= 8
    return out


try:
    import zlib as _zlib
    if not hasattr(_zlib, "decompressobj"):
        raise ImportError         # (a board's zlib, if it has one, isn't a computer's)

    def _inflate(body):
        d = _zlib.decompressobj(-15)
        out = d.decompress(_PRE + body, len(_DICT) + MAX + 1)
        if not d.eof:
            return None           # cut short, or longer than MAX
        return out[len(_DICT):]
except ImportError:
    try:
        import deflate as _mp_deflate
        import io as _io

        def _inflate(body):
            s = _mp_deflate.DeflateIO(_io.BytesIO(_PRE + body), _mp_deflate.RAW, _WBITS)
            if len(s.read(len(_DICT))) != len(_DICT):
                return None
            out = s.read(MAX + 1)
            return out if len(out) <= MAX else None
    except ImportError:
        _inflate = None           # this MicroPython has no deflate: plain text only


def available():
    """True where packed text can be read (so, where it may be asked for)."""
    return _inflate is not None


def pack(data):
    """`data` (bytes) as it goes to someone who reads packed text: packed
    when that is shorter, else as it is."""
    n = len(data)
    marked = n > 0 and data[0] >= PLAIN
    if MIN <= n <= MAX:
        try:
            z = _deflate(bytes(data))
            if len(z) + 1 < n:
                return b"\xff" + bytes(z)
        except Exception:
            pass
    return (b"\xfe" + bytes(data)) if marked else data


def unpack(data):
    """What pack() was given. Plain content comes back as it is; None for
    a packed one that can't be read (broken, or longer than MAX)."""
    if not data or data[0] < PLAIN:
        return data
    if data[0] == PLAIN:
        return bytes(data[1:])
    if _inflate is None:
        return None
    try:
        return _inflate(bytes(data[1:]))
    except Exception:
        return None
