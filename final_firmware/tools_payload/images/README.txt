Staged firmware images go here. Any .bin in this folder is copied to
/sd/fw/ during provisioning and becomes available to the browser
flasher at http://<node>/flash

To add hardware support:
  1. Put the .bin here
  2. Add a matching entry to ../flasher/catalog.json with "enabled": true
No code changes are needed anywhere.
