#!/bin/bash
# Checks a Stump OS image (pi/README.md) on an arm64 Linux machine, as
# root: what's in it, and the Stump actually running inside it (a copy of
# its folder, on port 8080 -- the image itself is checked on a copy too).
# CI can't boot a Pi; the first real boot is a bench test.
#
#   sudo pi/check_image.sh dist/stump-os-1.0.0-beta.87.img.xz
set -u
IMG_XZ="${1:?image .img.xz}"
W=$(mktemp -d)
R="$W/root"
fail=0
pass() { echo "PASS $1"; }
bad() { echo "FAIL $1"; fail=1; }
check() { if "$@" >/dev/null 2>&1; then :; else return 1; fi; }

cleanup() {
	[ -n "${PID:-}" ] && kill "$PID" 2>/dev/null
	sleep 1
	for m in "$R/sys" "$R/dev" "$R/proc" "$R/boot/firmware" "$R"; do umount -l "$m" 2>/dev/null; done
	[ -n "${LOOP:-}" ] && losetup -d "$LOOP" 2>/dev/null
	rm -rf "$W"
}
trap cleanup EXIT

xz -dc "$IMG_XZ" > "$W/img"
LOOP=$(losetup -fP --show "$W/img")
mkdir -p "$R"
mount "${LOOP}p2" "$R"
mount "${LOOP}p1" "$R/boot/firmware"
S="$R/home/stump/Stump"

[ -f "$S/stump_pc.py" ] && [ -x "$S/venv/bin/python" ] && [ -f "$S/example_node.py" ] \
	&& pass "the Stump folder, with its own Python" || bad "the Stump folder, with its own Python"
[ ! -e "$S/rns/identity" ] && [ ! -e "$S/pn_mailbox_identity" ] && [ -z "$(find "$R/home/stump" -name '*.stump-identity')" ] \
	&& pass "no identity baked in: every Pi makes its own" || bad "no identity baked in: every Pi makes its own"
ok=1
for k in ADMIN_CLAIM STUMP_OS PC_HOTSPOT_IP PC_HTTP_PORT AUTH_ADMIN_PASSWORD FSERVBOT_OP_PASSWORD; do
	[ "$(grep -c "^$k *=" "$S/config.py")" = 1 ] || ok=0
done
grep -q "^ADMIN_CLAIM = True" "$S/config.py" && grep -q "^STUMP_OS = True" "$S/config.py" \
	&& grep -q "^AUTH_ADMIN_PASSWORD = ''" "$S/config.py" || ok=0
[ "$ok" = 1 ] && pass "Stump OS's settings, each on one line; no admin password (claimed at /admin)" \
	|| bad "Stump OS's settings, each on one line; no admin password (claimed at /admin)"
E="$R/etc/systemd/system"
[ -L "$E/multi-user.target.wants/stump.service" ] && [ -L "$E/multi-user.target.wants/stump-net.service" ] \
	&& [ -L "$E/paths.target.wants/stump-update.path" ] && [ -L "$E/paths.target.wants/stump-shutdown.path" ] \
	&& pass "its services enabled (the Stump, the hotspot, update and shutdown requests)" \
	|| bad "its services enabled (the Stump, the hotspot, update and shutdown requests)"
grep -q 'address=/#/10.42.0.1' "$R/etc/NetworkManager/dnsmasq-shared.d/stump-captive.conf" 2>/dev/null \
	&& [ -x "$R/usr/local/sbin/stump-net" ] && pass "the hotspot script and the sign-in pop-up's DNS" \
	|| bad "the hotspot script and the sign-in pop-up's DNS"
grep -q '^dialout:.*\bstump\b' "$R/etc/group" && chroot "$R" passwd -S stump | grep -q ' L ' \
	&& pass "the stump user: the serial port (a Heltec later), no password login" \
	|| bad "the stump user: the serial port (a Heltec later), no password login"
[ -f "$R/home/stump/stump-src/pi/stump_update.py" ] && [ -f "$R/home/stump/stump-src/RELEASE_SIGNING_KEY.txt" ] \
	&& [ -f "$R/home/stump/stump-src/provisioner.py" ] \
	&& pass "the source the updater starts from, with the release key" \
	|| bad "the source the updater starts from, with the release key"
[ ! -e "$R/boot/firmware/ssh" ] && [ ! -L "$E/multi-user.target.wants/ssh.service" ] \
	&& pass "no SSH" || bad "no SSH"

# The Stump running inside the image.
mount --bind /proc "$R/proc"; mount --bind /dev "$R/dev"; mount --bind /sys "$R/sys"
cp -a "$S" "$R/tmp/StumpTest"
python3 - "$R/tmp/StumpTest/config.py" <<'PY'
import re, sys
p = sys.argv[1]
t = open(p).read()
t = re.sub(r"^PC_HTTP_PORT\s*=.*$", "PC_HTTP_PORT = 8080", t, flags=re.M)
t = re.sub(r"^PC_HOTSPOT_IP\s*=.*$", "PC_HOTSPOT_IP = '127.0.0.1'", t, flags=re.M)
open(p, "w").write(t)
PY
chroot "$R" su stump -s /bin/sh -c 'cd /tmp/StumpTest && exec venv/bin/python -u stump_pc.py' > "$W/stump.log" 2>&1 &
PID=$!
up=0
for i in $(seq 1 120); do
	if curl -fs http://127.0.0.1:8080/node.json > "$W/node.json" 2>/dev/null; then up=1; break; fi
	sleep 1
done
if [ "$up" = 1 ]; then
	pass "the Stump starts inside the image and serves /node.json"
	for p in / /rrc /about /tools; do
		curl -fs "http://127.0.0.1:8080$p" -o /dev/null && pass "page $p" || bad "page $p"
	done
	curl -fs http://127.0.0.1:8080/admin | grep -q "admin_claim" \
		&& pass "/admin asks its first visitor to choose the password" \
		|| bad "/admin asks its first visitor to choose the password"
	[ -f "$R/tmp/StumpTest/rns/identity" ] && pass "its identity made at its first start" \
		|| bad "its identity made at its first start"
else
	bad "the Stump starts inside the image and serves /node.json"
	tail -60 "$W/stump.log"
fi
exit $fail
