Staged firmware images go here. Any .bin in this folder is copied to
/sd/fw/ during provisioning and becomes available to the browser
flasher at http://<node>/flash

To add hardware support:
  1. Put the .bin here
  2. Add a matching entry to ../flasher/catalog.json with "enabled": true
No code changes are needed anywhere.

LICENSING -- read this before staging a real binary here:

This folder is currently empty except for this file: nothing GPL-licensed
is staged or distributed by this project today. But catalog.json's own
"rnode-heltec-v3" entry names its source as "the RNode_Firmware_CE
releases" -- and that firmware is GPL-3.0. The moment a real .bin obtained
that way lands here and gets flashed to a device, this node starts
CONVEYING that object code to anyone who downloads it over /fw?f= or
/tool?f=. GPL-3.0 section 6 requires that conveyance of object code be
accompanied by the corresponding source, or a written offer for it valid
for a set period -- LICENSE (this project's own MIT terms) does not
provide that, and pushing the LICENSE file to the same node does not
satisfy it either.

If you stage a GPL-3.0 binary here: also stage its actual source (or a
file with a written offer for it, per GPL-3.0 section 6) somewhere this
node serves it from, and update the corresponding catalog.json entry's
notes to point at it. Don't rely on this README or on LICENSE to cover
it -- neither currently does.
