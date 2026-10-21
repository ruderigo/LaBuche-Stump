# BarKeep the librarian (HF-003 revived, librarian.py) and the sort /
# filter bars of /files and /billboard: through the real request handler,
# in French, English and Spanish.
import sys, os, time
exec(open(sys.argv[1] + '/harness.py').read())
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:500]))
    if not c:
        FAIL.append(n)


import i18n, librarian, features, fserv, billboard
IP = '192.168.4.50'

# The shelf: names chosen for accents, spaces and apostrophes.
shelf = [("Le_jardinage_bio.pdf", 3000), ("Big Buck Bunny.mp4", 900000), ("Nuit étoilée.mp3", 50000),
         ("garden-tips.epub", 2000), ("photo_marché.jpg", 40000), ("notes.zip", 10),
         ("résumé été 🌲.txt", 120), ("l'astuce.pdf", 500)]
for i, (n, size) in enumerate(shelf):
    with open(ROOT + '/shared/' + n, 'w') as f:
        # In pieces: one 900 KB string needs a 900 KB gap in the test's
        # 2 MB heap, which the setup shouldn't depend on.
        for k in range(0, size, 8192):
            f.write('x' * min(8192, size - k))
    time.sleep(0.01)
billboard._append_entry("Vélo à réparer", "Atelier samedi, apportez vos chambres à air.", "a")
billboard._append_entry("Soupe partagée", "Jeudi soir.\nApporte un bol.", "b")
billboard._append_entry("<script>alert(1)</script>", "x", "c")


def ask(q, lang='en'):
    i18n.set_lang(IP, lang)
    s, ct, b = req('/chat', q.encode())
    return b


# parsing
check("parse: filler gone, intent found", librarian.parse("Hey, do you have a movie?") == (["video"], []))
check("parse: French, accents folded", librarian.parse("As-tu un livre sur le jardinage ?") == (["document"], ["jardinage"]))
check("parse: Spanish", librarian.parse("¿Tienes algo de música?") == (["music"], []))
check("parse: billboard", "board" in librarian.parse("What's new on the billboard?")[0])

# files
b = ask("Hey, do you have a movie?")
check("EN movie: the video, linked", "Big Buck Bunny.mp4" in b and "/download?f=Big%20Buck%20Bunny.mp4" in b and "Yes!" in b, b)
b = ask("As-tu un livre sur le jardinage ?", 'fr')
check("FR book about gardening: the matching book first", b.find("Le_jardinage_bio.pdf") >= 0 and "Oui" in b, b)
b = ask("do you have a book about gardening?")
check("EN: 'gardening' finds garden-tips and jardinage? at least garden-tips", "garden-tips.epub" in b, b)
b = ask("¿Tienes algo de música?", 'es')
check("ES music: the mp3, accents intact, link encoded", "Nuit étoilée.mp3" in b and "Nuit%20%C3%A9toil%C3%A9e.mp3" in b and "¡Sí!" in b, b)
b = ask("any pictures?")
check("images", "photo_marché.jpg" in b, b)
b = ask("résumé")
check("accented topic finds the accented file", "r%C3%A9sum%C3%A9%20%C3%A9t%C3%A9%20%F0%9F%8C%B2.txt" in b, b)
b = ask("do you have a movie about sourdough?")
check("nothing on the topic: the videos instead", "Nothing on the shelf about" in b and "Big Buck Bunny" in b, b)
b = ask("anything about quantum?")
check("nothing anywhere: says so, invites an upload", "anything about" in b and "quantum" in b and "Files page" in b, b)

# billboard
b = ask("What's new on the billboard?")
check("billboard: the newest notices, linked", "Soupe partagée" in b and "/billboard" in b, b)
b = ask("vélo", 'fr')
check("billboard: a notice by its title (accents)", "Vélo à réparer" in b and "babillard" in b, b)
check("the topic is shown as typed", "« vélo »" in b, b)
b = ask("chambres à air", 'fr')
check("billboard: a word in the body, with a snippet", "Vélo à réparer" in b and "chambres" in b, b)
b = ask("script")
check("XSS in a notice is shown as text", "<script>alert" not in b and "&lt;script&gt;" in b, b)
b = ask("<img src=x onerror=alert(1)> movie")
check("XSS in the question isn't echoed raw", "<img" not in b, b)

# apps
fserv.TOOLS_DIR = ROOT + '/tools'
os.mkdir(ROOT + '/tools/firefly')
open(ROOT + '/tools/firefly/FireFly-RK3326-0.2.11.zip', 'w').write('z' * 100)
b = ask("do you have the firefly app?")
check("apps: the app on the card, linked", "FireFly-RK3326-0.2.11.zip" in b and "/tool?f=" in b, b)

# the old commands still work
check("'menu' still answers the menu", "menu" in ask("menu").lower())
check("'files' still lists everything", "notes.zip" in ask("files"))
check("hello: says what BarKeep does", "film" in ask("hello") or "movie" in ask("hello").lower())

# JSON
i18n.set_lang(IP, 'en')
s, ct, b = req('/chat', b'a movie', headers=('Accept: application/json',))
d = json.loads(b)
check("JSON: findings as data", ct.startswith('application/json') and d["found"]["files"][0]["name"] == "Big Buck Bunny.mp4"
      and d["found"]["intents"] == ["video"] and "reply" in d, b[:300])
s, ct, b = req('/files.json?cat=music')
check("/files.json?cat=", [f["name"] for f in json.loads(b)["files"]] == ["Nuit étoilée.mp3"], b)
s, ct, b = req('/files.json?q=jardin&sort=newest')
check("/files.json?q=", [f["name"] for f in json.loads(b)["files"]] == ["Le_jardinage_bio.pdf"], b)
s, ct, b = req('/files.json?sort=large')
names = [f["name"] for f in json.loads(b)["files"]]
check("/files.json?sort=large", names[0] == "Big Buck Bunny.mp4" and names[-1] == "notes.zip", names)
s, ct, b = req('/files.json')
check("/files.json: still by name, with cat and mtime", [f["name"] for f in json.loads(b)["files"]][0] == "Big Buck Bunny.mp4"
      and "cat" in json.loads(b)["files"][0], b[:200])
s, ct, b = req('/billboard.json?q=velo')
check("/billboard.json?q= (accents ignored)", [p["title"] for p in json.loads(b)["posts"]] == ["Vélo à réparer"], b)
s, ct, b = req('/billboard.json?sort=title')
check("/billboard.json?sort=title", [p["title"] for p in json.loads(b)["posts"]][-1] == "Vélo à réparer", b)

# pages
for lang in ('fr', 'en', 'es'):
    i18n.set_lang(IP, lang)
    s, ct, b = req('/files')
    check("files page (%s): rows carry data-*, the bar and the drawer" % lang,
          "data-cat='video'" in b and "data-mtime=" in b and "id='flt'" in b and "id='srt'" in b
          and "data-cat='image'" in b and "id='askq'" in b and "class='ask'" in b, lang)
    s, ct, b = req('/billboard')
    check("billboard page (%s): search, sort, drawer, data-*" % lang,
          "id='bsearch'" in b and "id='bsort'" in b and "id='askq'" in b and "data-ts=" in b and "ul class='board'" in b, lang)
i18n.set_lang(IP, 'fr')
s, ct, b = req('/files')
check("French placeholder whole, inside its attribute", "placeholder='Demande-moi un film, un livre…'" in b
      and "title='Demander à BarKeep'" in b, b[b.find("ff-btn") - 100:][:300])
check("sort: one icon in any language, the translated choices in its list", "class='sortbox'" in b
      and "Plus récents" in b and "content:'\\21C5'" in b)
check("the librarian: a firefly that opens into a chat, with suggestions", "class='ff-btn'" in b
      and "id='ffp'" in b and " hidden>" in b[b.find("id='ffp'"):][:200] and "As-tu un film ?</button>" in b)
for lang, h2 in (('fr', 'Un Stump, trois montages'), ('en', 'One Stump, three setups'), ('es', 'Un Stump, tres montajes')):
    i18n.set_lang(IP, lang)
    s, ct, b = req('/about')
    check("About (%s): the three setups and who connects" % lang, h2 in b and "ESP32-S3-CAM + Heltec V3" in b
          and "macOS / Windows / Linux</span>" in b and "R36S / R36MAX" in b and "#wifi-mesh" in b, lang)
i18n.set_lang(IP, 'fr')
s, ct, b = req('/concierge')
check("/concierge: labelled as the librarian", "fichiers, avis, applis" in b, b[:300])

# feature switches
features_saved = features.enabled
features.enabled = lambda f: f != "files"
b = ask("do you have a movie?")
check("files off: says file sharing isn't offered", "isn't offered" in b and "Bunny" not in b, b)
b = ask("vélo")
check("files off: the billboard still answers", "Vélo à réparer" in b, b)
features.enabled = lambda f: f != "billboard"
b = ask("vélo")
check("billboard off: no notices", "Vélo" not in b, b)
features.enabled = features_saved

# !find in the chat
import rrc, plugins
plugins.load_all()
from fservbot import core as fb
i18n.DEFAULT_LANG = 'en'
r = fb.maybe_respond('main', 'rod', '!find movie')
check("!find: one short line", r and "Big Buck Bunny.mp4" in r and len(r) < 200 and "\n" not in r, r)
r = fb.maybe_respond('main', 'rod', '!find vélo')
check("!find: notices too", r and "Vélo à réparer" in r, r)
r = fb.maybe_respond('main', 'rod', '!find')
check("!find alone: how to use it", r and "!find" in r, r)
r = fb.maybe_respond('main', 'rod', '!find quantum')
check("!find nothing: says so", r and "quantum" in r, r)

# the release, on the About page and in /admin (set at boot from example_node.py)
import barkeep
rrc.NODE_INFO = {"version": "Beta A", "release": "1.0.0-beta.9"}
s, ct, b = req('/about')
check("About page: the release", "Stump Beta A · 1.0.0-beta.9" in b, b[-400:])
b = barkeep._render_admin_file_list_page('en', [], 'pw')
check("/admin: the release under the header", "1.0.0-beta.9" in b and b.find("1.0.0-beta.9") < b.find("HTTPS"), b[:400])
rrc.NODE_INFO = {}
check("no release known: no line", "release" not in barkeep._release_line())
print('FAILURES:', FAIL)
