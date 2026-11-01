import sys
exec(open(sys.argv[1] + '/harness.py').read())
J = ('Accept: application/json',)
def posts(): return json.loads(req('/billboard.json')[2])["posts"]
print("== POST /post")
print("  web form              ->", req('/post', b'title=Hello&body=world')[0])
st, ct, b = req('/post', b'title=Market&body=Saturday', J); d = json.loads(b)
print("  form + Accept JSON    ->", st, d, "| id on /billboard.json:", d["id"] in [p["id"] for p in posts()])
st, ct, b = req('/post', json.dumps({"title": "Marché d'été", "body": "à 10 h"}).encode(), J + ('Content-Type: application/json',))
p = [x for x in posts() if x["id"] == json.loads(b)["id"]][0]
print("  JSON body             ->", st, "| saved:", p["title"], "/", p["body"])
print("  no title              ->", req('/post', b'title=&body=', J)[0], req('/post', b'title=&body=', J)[2])
print("  body only             ->", req('/post', b'body=Only%0Asecond', J)[0])
n = len(posts()); real = billboard.storage_file
billboard.storage_file = lambda: '/nonexistent_dir/b.txt'
print("  storage fails         ->", req('/post', b'title=Lost', J)[:1], req('/post', b'title=Lost', J)[2])
billboard.storage_file = real
print("  ...nothing saved:", len(posts()) == n)
print("== POST /upload")
st, ct, b = req('/upload', b'hello file', J + ('X-Filename: notes.txt',)); print("  JSON                  ->", st, ct, b)
print("  Files page            ->", req('/upload', b'hello again', ('X-Filename: notes2.txt',)))
print("  no filename           ->", req('/upload', b'x', J + ('X-Filename: ',))[2])
print("  empty                 ->", req('/upload', b'', J + ('X-Filename: e.txt',), method='POST')[2])
fserv.sd_ok = False; print("  no card               ->", req('/upload', b'x', J + ('X-Filename: a.txt',))[2]); fserv.sd_ok = True
r = fserv.make_room_fifo; fserv.make_room_fifo = lambda n: False
print("  card full             ->", req('/upload', b'x', J + ('X-Filename: a.txt',))[2]); fserv.make_room_fifo = r
print("  interrupted           ->", req('/upload', b'only part', J + ('X-Filename: cut.txt',), declared=500)[:1], req('/upload', b'only part', J + ('X-Filename: cut2.txt',), declared=500)[2])
print("== GET /tools.json")
for n2, sz in (("FireFly-Android-0.2.11.apk", 4000), ("FireFly-Android-0.2.9.apk", 100), ("FireFly-RK3326-0.6.2.zip", 2000), ("provisioner.py", 300)):
    open(fserv.TOOLS_DIR + '/' + n2, 'wb').write(bytes(sz))
st, ct, b = req('/tools.json'); print("  ", st, ct); d = json.loads(b)
for a in d["apps"]: print("   app:", a)
print("   files:", d["files"])
fserv.sd_ok = False; print("  no card ->", req('/tools.json')[2])
import rrc, features
print("== GET /node.json")
rrc.NODE = {"name": "TestStump", "lxmf": "53d91d2f00000000000000000000e48e"}
rrc.NODE_INFO = {"version": "Beta A", "release": "1.0.0-beta.2"}
before = len(rrc._users)
st, ct, b = req('/node.json'); d = json.loads(b)
print("  ", st, ct)
print("   keys:", sorted(d))
print("   ", d["name"], d["lxmf"], d["version"], d["release"], d["ap_ip"], d["chain"], d["features"])
print("   joined the chat:", len(rrc._users) != before)
real = features._CONFIGURED; features._CONFIGURED = ["billboard"]
print("   chat off ->", req('/node.json')[0], json.loads(req('/node.json')[2])["features"])
features._CONFIGURED = real
