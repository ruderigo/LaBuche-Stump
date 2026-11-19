#!/bin/bash -e
# Stump OS: Raspberry Pi OS Lite (64-bit) with the Stump installed and set
# up, built with the Raspberry Pi Foundation's own tool, pi-gen (its arm64
# branch). See pi/README.md.
#
#   sudo -E pi/build.sh 1.0.0-beta.87 dist/
#
# Needs an arm64 Linux machine (CI: GitHub's ubuntu-24.04-arm runner), root,
# pi-gen's host packages (pi/README.md) and the internet. Writes
# stump-os-<version>.img.xz into the output folder.
VERSION="${1:?version, e.g. 1.0.0-beta.87}"
OUT="$(mkdir -p "${2:?output folder}" && cd "$2" && pwd)"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${PIGEN_WORK:-/tmp/stump-pi-gen}"
PIGEN_REF="${PIGEN_REF:-arm64}"

if [ ! -d "$WORK/.git" ]; then
	git clone --depth 1 --branch "$PIGEN_REF" https://github.com/RPi-Distro/pi-gen.git "$WORK"
fi
echo "pi-gen: $(git -C "$WORK" log -1 --format='%H %cs %s')"
# pi-gen's host packages, from its own list (lines "command[:package]").
if [ "${INSTALL_DEPS:-0}" = 1 ]; then
	pkgs=$(grep -v '^\s*#' "$WORK/depends" | sed -e 's/^[^:]*://' | tr '\n' ' ')
	apt-get install -y -q $pkgs
fi
rm -rf "$WORK/stage-stump" "$WORK/deploy"
cp -r "$SRC/pi/stage-stump" "$WORK/stage-stump"
touch "$WORK/stage2/SKIP_IMAGES"          # the one image is Stump OS's

cat > "$WORK/config" <<EOF
IMG_NAME="stump-os-$VERSION"
PI_GEN_RELEASE="Stump OS $VERSION"
TARGET_HOSTNAME="stump"
FIRST_USER_NAME="stump"
FIRST_USER_PASS="$(head -c 24 /dev/urandom | base64 | tr -d '/+=')"
DISABLE_FIRST_BOOT_USER_RENAME=1
ENABLE_SSH=0
WPA_COUNTRY="CA"
LOCALE_DEFAULT="en_US.UTF-8"
KEYBOARD_KEYMAP="us"
KEYBOARD_LAYOUT="English (US)"
TIMEZONE_DEFAULT="UTC"
DEPLOY_COMPRESSION="xz"
COMPRESSION_LEVEL=6
STAGE_LIST="stage0 stage1 stage2 stage-stump"
EOF

export STUMP_SRC="$SRC"
(cd "$WORK" && ./build.sh)

img=$(ls "$WORK"/deploy/*.img.xz | head -1)
[ -n "$img" ] || { echo "pi-gen made no image"; exit 1; }
cp "$img" "$OUT/stump-os-$VERSION.img.xz"
echo "Stump OS: $OUT/stump-os-$VERSION.img.xz"
