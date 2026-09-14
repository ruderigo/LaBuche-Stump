# Project Stump -- main.py (CAM / hub role)
# MicroPython auto-runs this file after every boot, no USB/computer
# needed. This is what actually makes "power on and it works" real.
#
# The retry-vs-give-up logic itself lives in boot_common.py -- see
# that file's own header for the full transient-vs-persistent failure
# reasoning. This file's only job is naming WHICH application module
# this board boots: example_node, the full hub (WiFi AP, HTTP server,
# chat, billboard, the mesh bridge).

from boot_common import boot_with_retry
boot_with_retry("example_node")
