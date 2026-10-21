# The sectioned Tools page: loose tools from older cards moved into their
# folders, one section per folder (title and summary from its README.md),
# /tools.json, and /tool?f= one folder deep and no further.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


T = fserv.TOOLS_DIR
def put(rel, data=b'x'):
    open(T + '/' + rel, 'wb').write(data)
def tree():
    out = []
    for n in sorted(os.listdir(T)):
        if fserv._is_dir(T + '/' + n):
            out += [n + '/' + m for m in sorted(os.listdir(T + '/' + n))]
        else:
            out.append(n)
    return out

# A card as an older Provisioner left it: everything loose.
for n in ("provisioner.py", "README.md", "LICENSE", "Stump_Beta_A.zip", "esptool-bundle.js",
          "esptool-js-LICENSE.txt", "FireFly-Android-0.2.11.apk", "FireFly-RK3326-0.6.2.zip",
          "rnfs-0.1.1-py3-none-any.whl", "lxmf-1.2.0-py3-none-any.whl", "rnspure-1.5.7-py3-none-any.whl",
          "rnfs-install.sh", "rnfs-install.bat", "rnfs-INSTALL.txt", "rnfs-LICENSE.txt",
          "reticulum-LICENSE.txt", "my-notes.txt"):
    put(n)
os.mkdir(T + '/rnfs'); put('rnfs/rnfs-install.sh', b'newer')     # already in its folder
moved = fserv.migrate_tools()
t = tree()
check("loose tools moved into folders", moved == 16, (moved, t))
check("provisioner folder", all(x in t for x in ("provisioner/provisioner.py", "provisioner/LICENSE",
      "provisioner/Stump_Beta_A.zip", "provisioner/Stump-README.md")), t)
check("flasher, firefly, rnfs folders", all(x in t for x in ("flasher/esptool-bundle.js",
      "firefly/FireFly-Android-0.2.11.apk", "rnfs/README.md", "rnfs/lxmf-1.2.0-py3-none-any.whl",
      "rnfs/reticulum-LICENSE.txt")), t)
check("the folder's own copy wins", open(T + '/rnfs/rnfs-install.sh').read() == 'newer')
check("unknown files stay loose", "my-notes.txt" in t and "rnfs-install.sh" not in t, t)
check("second run moves nothing", fserv.migrate_tools() == 0)

open(T + '/rnfs/README.md', 'w').write(
    "# rnfs — Reticulum Network File System\n\nFiles on the Reticulum network,\nby content ID.\n\n"
    "https://example\n\n## Installing\n\n- one\n")
open(T + '/flasher/README.md', 'w').write("# Browser flasher\n\nFor the /flash page.\n")
secs = {s["dir"]: s for s in fserv.tool_sections()}
check("sections by folder, loose last", [s["dir"] for s in fserv.tool_sections()] ==
      ["firefly", "flasher", "provisioner", "rnfs", ""], list(secs))
r = secs["rnfs"]
check("title and summary from README", r["title"] == "rnfs — Reticulum Network File System"
      and r["summary"] == "Files on the Reticulum network, by content ID.", (r["title"], r["summary"]))
check("README not listed as a file", "README.md" not in [n for n, _ in r["files"]])
check("no README: folder name", secs["provisioner"]["title"] == "provisioner" and secs["provisioner"]["readme"] is None)
apps = fserv.find_apps()
check("apps found in their folder", apps["android"]["file"] == "firefly/FireFly-Android-0.2.11.apk"
      and apps["android"]["name"] == "FireFly-Android-0.2.11.apk", apps)

open(T + '/rnfs/README.fr.md', 'w').write("# rnfs — système de fichiers Reticulum\n\nDes fichiers sur le réseau.\n")
fr = {x["dir"]: x for x in fserv.tool_sections('fr')}["rnfs"]
check("French README for a French reader", fr["title"] == "rnfs — système de fichiers Reticulum"
      and fr["summary"] == "Des fichiers sur le réseau." and fr["readme_file"] == "README.fr.md", fr)
es = {x["dir"]: x for x in fserv.tool_sections('es')}["rnfs"]
check("no Spanish one: the default README", es["readme_file"] == "README.md" and es["title"].startswith("rnfs — Reticulum"), es)
check("translated READMEs aren't listed as files", "README.fr.md" not in [n for n, _ in fr["files"]])
os.remove(T + '/rnfs/README.fr.md')
st, ct, page = req('/tools?lang=en')
import re
h2 = re.compile("<h2>([^<]*)</h2>")
heads = []
i = 0
while True:
    m = h2.search(page[i:])
    if not m:
        break
    heads.append(m.group(1)); i += page[i:].find(m.group(0)) + len(m.group(0))
check("page: a section per folder", heads[1:] == ["Browser flasher", "provisioner", "rnfs — Reticulum Network File System",
      "Autres fichiers"] and heads[0] in ("Applications", "Apps"), heads)
check("page: summary and README", "<p class='sub'>Files on the Reticulum network, by content ID.</p>" in page
      and "<details class='readme'>" in page and "## Installing" in page)
check("page: links one folder down", "/tool?f=rnfs%2Frnfs-install.sh" in page or "/tool?f=rnfs/rnfs-install.sh" in page, page[:200])
check("page: firefly section only under Apps", "<h2>firefly</h2>" not in page)

st, ct, b = req('/tools.json'); d = json.loads(b)
check("json: apps keep a bare file name", [a["file"] for a in d["apps"]] ==
      ["FireFly-Android-0.2.11.apk", "FireFly-RK3326-0.6.2.zip"], d["apps"])
names = [(f["section"], f["name"]) for f in d["files"]]
check("json: files flat, with their section", ("rnfs", "rnfs-install.sh") in names
      and ("", "my-notes.txt") in names and ("rnfs", "README.md") not in names, names)
check("json: sections", [s["dir"] for s in d["sections"]] == ["flasher", "provisioner", "rnfs", ""]
      and d["sections"][2]["readme"] and d["sections"][1]["readme"] is None, d["sections"])

def get(f):
    s, c, body = req('/tool?f=' + f)
    return s[:3], body
check("download from a folder", get('rnfs%2Frnfs-install.sh') == ('200', 'newer'))
check("download from a folder, plain slash", get('rnfs/rnfs-install.sh') == ('200', 'newer'))
check("download a loose file", get('my-notes.txt')[0] == '200')
put('old-tool.bin', b'loose')
check("folder path falls back to a loose file", get('flasher/old-tool.bin') == ('200', 'loose'))
open(fserv.SD_MOUNT + '/secret.txt', 'w').write('no')
for bad in ('..%2Fsecret.txt', 'rnfs%2F..%2F..%2Fsecret.txt', 'a%2Fb%2Fc.txt', '%2Fsecret.txt', '..'):
    s, body = get(bad)
    check("refused: " + bad, s == '404' and body != 'no', (s, body))
check("flasher page loads the bundle from its folder", "/tool?f=flasher/esptool-bundle.js" in open('flasher_ui.py').read())
print("FAILURES", FAIL)
