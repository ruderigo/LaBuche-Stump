#!/bin/bash -e
# Inside the image: the Stump set up by its own Provisioner, as on any
# computer (python3 provisioner.py --pc), then made Stump OS.
usermod -aG dialout,netdev stump
chown -R stump:stump /home/stump
su stump -s /bin/sh -c 'cd /home/stump/stump-src && python3 provisioner.py --pc /home/stump/Stump --answers pi/answers.json </dev/null'

# Every Pi its own identity: made by the Stump at its first start, never
# one baked into the image and shared by every card.
rm -f /home/stump/Stump/rns/identity /home/stump/Stump/pn_mailbox_identity
find /home/stump -name '*.stump-identity' -delete

# Stump OS's settings, each key on one line only (settings.write_config,
# and the claim at /admin, rewrite that line).
python3 - /home/stump/Stump/config.py <<'PY'
import re, sys, os
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
want = [("PC_HOTSPOT_IP", "'10.42.0.1'"), ("PC_HTTP_PORT", "80"), ("AUTH_ADMIN_PASSWORD", "''"),
        ("FSERVBOT_OP_PASSWORD", "''"), ("ADMIN_CLAIM", "True"), ("STUMP_OS", "True")]
for key, value in want:
    pat = re.compile(r"^%s\s*=.*\n?" % re.escape(key), re.M)
    text = pat.sub("", text)
text = text.rstrip("\n") + "\n\n# ---- Stump OS (pi/, 1.0.0-beta.87) ----\n" + "".join("%s = %s\n" % kv for kv in want)
open(path + ".tmp", "w", encoding="utf-8").write(text)
os.replace(path + ".tmp", path)
PY
chown stump:stump /home/stump/Stump/config.py

systemctl enable stump-net.service stump.service stump-update.path stump-shutdown.path
passwd -l stump
