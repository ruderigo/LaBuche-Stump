# Project Stump -- librarian.py: BarKeep as the node's librarian.
#
# A visitor asks in French, English or Spanish -- "do you have a movie?",
# "as-tu un livre sur le jardinage ?", "¿tienes algo de música?" -- and
# gets what's on the node: files on the shelf, billboard notices, the
# apps on the card. No language model: three plain steps.
#   1. normalise: lowercase, accents folded, punctuation and filler
#      words ("hey", "do you have", "est-ce que", "tienes"...) dropped;
#   2. intent: words that name a kind of file (video, music, document,
#      image), the billboard, or the apps;
#   3. match what's left (the topic) against file names and notices.
# search() returns data; barkeep.py turns it into a reply (HTML or
# JSON), fservbot's !find into one short line.

_FOLD = {}
for _plain, _accented in (("a", "àâäáãå"), ("e", "éèêë"), ("i", "îïíì"), ("o", "ôöóòõ"),
                          ("u", "ùûüú"), ("c", "ç"), ("n", "ñ"), ("y", "ÿý")):
    for _ch in _accented:
        _FOLD[_ch] = _plain

STOP = set((
    "hey hi hello hola salut bonjour bonsoir allo yo please svp merci thanks gracias porfavor "
    "do does you your have has got any is are there something anything some about like want "
    "need looking find search show me my the a an of on for with what whats which new latest "
    "can could i we it to in at by or and any one ones "
    "tu as avez vous est ce que qu un une des du de la le les sur pour avec quelque chose "
    "il y a-t t je j me moi cherche veux voudrais avoir montre quoi nouveau nouveaux "
    "tienes tiene hay algo sobre de del el la los las un una unos unas para con que me "
    "busco quiero tengo muestra mostrar nuevo nuevos"
).split())

# intent -> the words that name it (accents already folded)
INTENTS = {
    "video": "movie movies film films video videos cinema pelicula peliculas watch regarder ver serie series",
    "music": "music musique musica song songs chanson chansons cancion canciones audio podcast podcasts "
             "album albums listen ecouter escuchar",
    "document": "book books livre livres libro libros read lire leer pdf doc docs document documents "
                "documento documentos zine zines guide guides manual manuals manuel text texte article articles",
    "image": "photo photos image images picture pictures pic pics art drawing drawings dessin dessins "
             "imagen imagenes foto fotos",
    "apps": "app apps apk android firefly r36s rk3326 handheld console tool tools outil outils "
            "aplicacion aplicaciones herramienta herramientas",
    "board": "billboard notice notices news event events annonce annonces avis aviso avisos board "
             "babillard tablon happening evenement evenements",
}
_WORD_INTENT = {}
for _k, _words in INTENTS.items():
    for _w in _words.split():
        _WORD_INTENT[_w] = _k
CATS = ("video", "music", "document", "image")


_PUNCT = "¿¡«»…—–‘’“”·•°€£×÷§¶"


def fold(text):
    """Lowercase, accents folded, anything else not a letter or digit a
    space. (Letters beyond Latin -- 日本 -- are kept as they are.)"""
    out = []
    for ch in text.lower():
        ch = _FOLD.get(ch, ch)
        keep = ch.isalpha() or ch.isdigit() or (ord(ch) > 127 and ch not in _PUNCT)
        out.append(ch if keep else " ")
    return "".join(out)


def parse(text):
    """(intents, topic words) of a question."""
    intents, words = [], []
    for w in fold(text).split():
        k = _WORD_INTENT.get(w)
        if k:
            if k not in intents:
                intents.append(k)
        elif w not in STOP and len(w) > 1 and w not in words:
            words.append(w)
    return intents, words[:6]


def stem(w):
    """Enough of a word to find its relatives: "gardening" finds "garden"."""
    return w[:6] if len(w) > 6 else (w[:-1] if len(w) > 4 and w[-1] == "s" else w)


def search(text, files_on=True, board_on=True, limit=5):
    """What the node has for a question: a dict
    {"intents", "words", "files", "posts", "apps", "counts", "files_on",
     "board_on", "sd"}. files: fserv.query_files() rows; posts:
    billboard.search_entries() rows; apps: fserv.find_apps() values;
    others: files of the kind asked for when none matched the topic;
    counts: {cat: n} of the whole shelf, for "none of those, but..."."""
    import fserv
    import billboard
    intents, words = parse(text)
    cat = None
    for c in CATS:
        if c in intents:
            cat = c
            break
    # The topic as the visitor wrote it ("vélo", not "velo"), for replies.
    shown = []
    for raw in text.lower().split():
        f = fold(raw).strip()
        if f in words and raw.strip("?!.,;:¿¡«»\"'") not in shown:
            shown.append(raw.strip("?!.,;:¿¡«»\"'"))
    r = {"topic": " ".join(shown) or " ".join(words),
         "intents": intents, "words": words, "files": [], "others": [], "posts": [], "apps": [],
         "counts": {}, "files_on": files_on, "board_on": board_on, "sd": fserv.sd_ok}
    if files_on and fserv.sd_ok and (words or cat):
        r["files"] = fserv.query_files(words, cat, "newest", limit)
        if not r["files"] and cat and words:
            # Nothing on that topic: the files of that kind, then.
            r["others"] = fserv.query_files(None, cat, "newest", limit)
        if not r["files"] and not r.get("others"):
            for row in fserv.query_files(None, None, "newest", 1000):
                r["counts"][row["cat"]] = r["counts"].get(row["cat"], 0) + 1
    if board_on and (words or "board" in intents):
        r["posts"] = billboard.search_entries(words, 3)
    if "apps" in intents and fserv.sd_ok:
        r["apps"] = list(fserv.find_apps().values())
    return r
