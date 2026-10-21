# Provisioner

The tool that sets up and updates Stump nodes, from a laptop over USB: the exact version this node was provisioned with.

## What's here

- `provisioner.py` — the Provisioner itself.
- `Stump_Beta_A.zip` — the firmware source this node runs.
- `Stump-README.md` — the full manual.
- `LICENSE` — the licence it all ships under (MIT).

## Using it

Download the files into one folder and unzip `Stump_Beta_A.zip` there
(it holds `final_firmware/`). Then, with the node plugged in by USB:

    python3 provisioner.py --check-tools     # esptool, mpremote, rnodeconf
    python3 provisioner.py                   # the wizard

Updating a node that's already deployed, keeping its identity and its
SD card: `python3 provisioner.py --upload-app PORT`. Everything else is
in `Stump-README.md`.

For a fresh setup, the release download from
https://github.com/ruderigo/SandBox_Stump_On/releases is the same thing
in one zip.
