# Stump OS: the Raspberry Pi image (for maintainers)

Testers' guide: `final_firmware/docs/PI.md`. This is how the image is made.

**What it is:** Raspberry Pi OS Lite (64-bit) with the Stump set up by its
own Provisioner (`python3 provisioner.py --pc --answers pi/answers.json`, the
computer-Stump path), built with the Raspberry Pi Foundation's **pi-gen**
(arm64 branch) plus one stage of ours, `pi/stage-stump`.

| On the Pi | What does it |
|---|---|
| The Stump, from boot, port 80, restarted if it stops | `stump.service` (user `stump`, `CAP_NET_BIND_SERVICE`) |
| Open hotspot `Stump-XXXX` on 10.42.0.1, every boot; other Wi-Fi profiles give the chip up | `stump-net` / `stump-net.service` (root, before the Stump) |
| The sign-in pop-up: every DNS name answered with 10.42.0.1, hotspot clients only | `/etc/NetworkManager/dnsmasq-shared.d/stump-captive.conf` |
| Its name, first boot (`NODE_NAME`, `PC_HOTSPOT_NAME` = Stump-XXXX from the serial) | `stump-net` (once: `/var/lib/stump-os/named`) |
| Its identity: made by the Stump at its first start (none in the image) | `02-run-chroot.sh` removes the build's |
| The admin password: chosen at the first visit to `/admin` | `ADMIN_CLAIM = True` (admin_web `_claimable`) |
| Admin password reset: `stump-reset-admin.txt` on the boot partition | `stump-net` |
| Update in place from `/admin` | `.update-request` → `stump-update.path` → `pi/stump_update.py` (user `stump`) |
| Shut down from `/admin` | `.shutdown-request` → `stump-shutdown.path` → `systemctl poweroff` |
| A Heltec on USB later | config `RNodeSerialInterface` port `auto`; `stump` in `dialout` |

No SSH, no password login (`stump` is locked), Wi-Fi country CA (channel 6,
legal everywhere).

**The updater** downloads the newest release (betas included), checks
`SHA256SUMS` against its signature with the key already on the Pi
(`/home/stump/stump-src/RELEASE_SIGNING_KEY.txt`), the zip against
`SHA256SUMS`, then runs the new release's Provisioner on the Stump folder
(`--answers pi/update-answers.json`): firmware replaced, settings, identity,
packs and files kept. It updates the Stump, not the OS: a change to the
files above (`stump-net`, the units) reaches Pis through a new image.

**Built by CI** (`.github/workflows/pi-image.yml`, GitHub's `ubuntu-24.04-arm`
runner): on every push touching `pi/` (build and check only), and after
every release (the Release workflow calls it): the image goes on that
release as `stump-os-<version>.img.xz`, and Raspberry Pi Imager's catalogue
(`os_list.json` on the `stump-os` release, from `pi/os_list.py`) points at it.

`pi/check_image.sh` checks the image without booting it: its files and
services, no identity baked in, and the Stump started inside it (chroot, a
copy of its folder, port 8080) serving its pages and the claim page. **The
first real boot is a bench test.**

By hand, on an arm64 Linux machine (a Pi 4/5 with 64-bit Raspberry Pi OS
does), with pi-gen's host packages (see the workflow) and the internet:

```
sudo -E bash pi/build.sh 1.0.0-beta.87 dist
sudo bash pi/check_image.sh dist/stump-os-1.0.0-beta.87.img.xz
```
