# Project Stump -- fservbot plugin package
# Place the WHOLE FOLDER at: final_firmware/fservbot/
#
# A drop-in mIRC-style fserv bot for the RRC channel. Self-contained:
# every file it needs is in this folder, and it modifies none of
# Stump's own modules on disk. See README.md in this folder for the
# single activation line and the provisioner wizard spec.

FSERVBOT_VERSION = "1.0"

# Nothing is imported here on purpose. Importing this package must stay
# cheap and side-effect-free -- activation is an explicit call in
# install.py, so merely having the folder present can never change how
# the node behaves. A plugin that starts doing things just by existing
# is a plugin nobody can bisect a boot failure around.
