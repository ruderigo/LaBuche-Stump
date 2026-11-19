#!/bin/bash -e
# Host side: the Stump's source and Stump OS's own files into the image.
# STUMP_SRC: the repository (pi/build.sh exports it).
F="$(dirname "$0")/files"
install -d "${ROOTFS_DIR}/home/stump/stump-src"
rsync -a --delete --exclude '.git' --exclude 'build' --exclude 'node_modules' --exclude 'tests' \
	--exclude '__pycache__' --exclude 'dist' "${STUMP_SRC}/" "${ROOTFS_DIR}/home/stump/stump-src/"
install -m 755 "$F/stump-net" "${ROOTFS_DIR}/usr/local/sbin/stump-net"
for u in stump.service stump-net.service stump-update.path stump-update.service stump-shutdown.path stump-shutdown.service; do
	install -m 644 "$F/$u" "${ROOTFS_DIR}/etc/systemd/system/$u"
done
install -d "${ROOTFS_DIR}/etc/NetworkManager/dnsmasq-shared.d"
install -m 644 "$F/stump-captive.conf" "${ROOTFS_DIR}/etc/NetworkManager/dnsmasq-shared.d/stump-captive.conf"
