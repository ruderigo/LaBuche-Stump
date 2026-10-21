import sys
exec(open(sys.argv[1] + '/harness.py').read())
def up(hdrs, body=b'abc'):
    hl = [b'POST /upload HTTP/1.1\r\n', ('Content-Length: %d\r\n' % len(body)).encode()] + [h + b'\r\n' for h in hdrs] + [b'\r\n']
    w = Wr(); asyncio.run(barkeep._handle(Rd(hl, body), w)); r = b''.join(w.w)
    return r.split(b'\r\n')[0].decode(), r.partition(b'\r\n\r\n')[2].decode()
def last(): return sorted(os.listdir(fserv.SHARED_DIR), key=lambda n: os.stat(fserv.SHARED_DIR + '/' + n)[8])[-1]
for label, hdrs in (
    ("raw UTF-8 café.txt         ", [b'X-Filename: caf\xc3\xa9.txt']),
    ("raw Latin-1 th\xe9.txt      ", [b'X-Filename: th\xe9.txt']),
    ("page: th%C3%A9.txt + url    ", [b'X-Filename: th%C3%A9.txt', b'X-Filename-Encoding: url']),
    ("page: Japanese name + url   ", [b'X-Filename: %E6%97%A5%E6%9C%AC.jpg', b'X-Filename-Encoding: url']),
    ("page: emoji + url           ", [b'X-Filename: %F0%9F%94%A5fire.png', b'X-Filename-Encoding: url']),
    ("page: '100%+1.txt' encoded  ", [b'X-Filename: 100%25%2B1.txt', b'X-Filename-Encoding: url']),
    ("raw '100%.txt', no encoding ", [b'X-Filename: 100%.txt']),
):
    before = set(os.listdir(fserv.SHARED_DIR)); st, body = up(hdrs)
    print(label, '->', st, '| stored as:', [n for n in os.listdir(fserv.SHARED_DIR) if n not in before])
w = Wr(); asyncio.run(barkeep._handle(Rd([b'GET /fil\xe9s HTTP/1.1\r\n', b'\r\n']), w)); print("Latin-1 byte in the path   ->", b''.join(w.w).split(b'\r\n')[0].decode())
