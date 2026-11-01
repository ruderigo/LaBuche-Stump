# store.py, on the board's MicroPython: a save is whole or not at all (a
# save that fails half-way leaves the previous file as it was, readable),
# never raises, and the modules that used to write in place go through it.
import sys, os
sys.path.insert(0, '.')
import store
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


D = os.getcwd() + '/_store_test'
try:
    os.mkdir(D)
except OSError:
    pass
p = D + '/x.json'
check("save, then read back", store.save_json(p, {"a": 1, "é": [1, 2]}) and store.load_json(p) == {"a": 1, "é": [1, 2]})
check("save again: replaced", store.save_json(p, {"b": 2}) and store.load_json(p) == {"b": 2})


os.mkdir(p + ".tmp")          # the temporary file can't be written: the save fails before the rename
ok = store.save_json(p, {"c": 3})
check("a save that fails: False, and the previous file is intact", not ok and store.load_json(p) == {"b": 2},
      (ok, store.load_json(p)))
os.rmdir(p + ".tmp")
check("a folder that doesn't exist: False, no exception", store.save_json(D + '/nope/y.json', {}) is False)
check("nothing saved yet: the default", store.load_json(D + '/none.json', {"d": 1}) == {"d": 1})
with open(D + '/broken.json', 'w') as f:
    f.write('{"half": ')
check("a file that doesn't parse: the default", store.load_json(D + '/broken.json', "dflt") == "dflt")

# The modules that wrote in place now go through store (whole or not at all).
import layout, theme, radio, profiles
layout.LAYOUT_FILE = D + '/layout.json'
check("layout saves through it", layout.save("nav", ["files", "chat"]) and store.load_json(layout.LAYOUT_FILE)["nav"] == ["files", "chat"])
radio.SETTINGS_FILE = D + '/radio.json'
check("radio settings save through it", radio.save({"freq": 1}) and store.load_json(radio.SETTINGS_FILE) == {"freq": 1})
for name, src in (("layout", "layout.py"), ("theme", "theme.py"), ("radio", "radio.py"), ("profiles", "profiles.py"),
                  ("plugins", "plugins.py"), ("propagation", "propagation.py"), ("keepalive", "keepalive.py")):
    s = open(sys.argv[1] + "/../../final_firmware/" + src).read()
    check(name + ": no JSON written in place", "store.save_json(" in s and 'open(SETTINGS_FILE, "w")' not in s
          and 'open(LAYOUT_FILE, "w")' not in s and 'open(STATE_FILE, "w")' not in s)
print('FAILURES:', FAIL)
