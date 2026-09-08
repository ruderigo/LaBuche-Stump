"""
µReticulum — Node Configuration
================================
"""

from lora_boards import LORA_BOARDS

# ---- Node settings ----
WIFI_SSID = "Bob's Glitch"
WIFI_PASS = 'SalutComment'
NODE_NAME = 'LaBuche'

# The walk-up hotspot's own name -- separate from NODE_NAME (the mesh
# identity's display name) on purpose; these are different concerns.
# None means "use the default hosted page" (captive_portal.DEFAULT_SSID,
# currently "LaBuche-Stump.web.app") -- a real, publicly hosted page
# that explains what this network is, so someone can read the name off
# their phone's WiFi list and type it straight into a browser on their
# own data, before ever joining. Set to a string to use a custom name
# instead.
SSID_NAME = None

# Whether the AP's own IP address is appended to the broadcast WiFi
# name. Only meaningful with a CUSTOM SSID_NAME -- the default hosted-
# page domain (21 characters) plus an IP suffix is 33 characters, one
# over WiFi's hard 32-byte SSID limit, and truncating a real web
# address by even one character breaks it as something a browser can
# resolve. Defaults to False specifically because SSID_NAME defaults
# to None (the hosted page) -- the two defaults have to be consistent
# with each other out of the box, not just after the Provisioner wizard
# runs (which enforces this by construction: the IP question is only
# ever asked in the custom-name branch). Confirmed directly: the raw
# template previously shipped with this at True, which is broken
# paired with the default SSID_NAME -- caught it from a headless boot
# of the unprovisioned template producing a truncated, non-resolving
# URL ("LaBuche-Stump.web." instead of "LaBuche-Stump.web.app").
SSID_INCLUDE_IP = False

# The name the local greeter answers to on the web pages. Purely
# cosmetic and per-node -- it has nothing to do with NODE_NAME above,
# which is the identity mesh peers see.
BOT_NAME = 'Concierge'

# Sent once to each mesh peer the first time they message this node.
# Blank disables it entirely.
#
# Once per peer, not once per message, on purpose: every LXMF send costs
# real airtime and roughly seven seconds of crypto on the S3, so an
# auto-reply on every inbound message would answer a three-word question
# with a paragraph and do it again for the next three words. A greeting
# on first contact says who this node is and what it offers; after that
# the conversation is the point.
MESH_GREETING = "Bonjour Hi je suis Concierge et je m'occupe de cette Buche. Plus d'info LaBuche-Stump.web.app"
MESH_GREETING_MAX = 200

# How often this node announces its LXMF identity to the mesh, in
# seconds. Every announce is a fully synchronous crypto operation that
# freezes the whole event loop for however long signing takes -- a
# shorter interval means more frequent freezes (and, per a real field
# report, a better chance of tripping the Heltec bridge's own
# disconnect tolerance and forcing a full radio reconfigure). A longer
# interval means slower route discovery for anyone new to the mesh.
# 120s is the value this project shipped with before this setting
# existed; not connected to CONFIG["probe"]'s own announce_interval
# below, which governs a separate, disabled-by-default diagnostic
# destination, not this node's main identity.
REANNOUNCE_INTERVAL = 120

# Announce rebroadcast throttling. When this node relays for the mesh,
# it won't rebroadcast more than ANNOUNCE_RATE_MAX announces from the
# same source within ANNOUNCE_RATE_WINDOW seconds -- protects the wider
# mesh from any one chatty node's announces being relayed excessively.
# These match urns/const.py's own built-in defaults; only set them here
# if a deployment specifically needs to tune the throttle.
ANNOUNCE_RATE_MAX = 6
ANNOUNCE_RATE_WINDOW = 60

WEBREPL_PASSWORD = "changeme"

DEBUG = 2


CONFIG = {
    "loglevel": 3,
    "enable_transport": True,
    "lora_boards": LORA_BOARDS,

    "probe": {
        "enabled": False,
        "app_name": "urns",
        "aspect": "probe",
        "announce_interval": 60 * 60,
    },

    "time_sync": {
        "enabled": True,
        "trusted_nodes": [],
        "min_sources": 2,
        "tolerance": 120,
    },

    "interfaces": [

        # ---- SX1262 SPI LoRa -- DISABLED: wrong board preset for the Freenove ----
        {
            "type": "LoRaInterface",
            "board": "xiao_esp32s3_sx1262",
            "name": "LoRa",
            "enabled": False,
            "freq_khz": 868800,
            "sf": 8,
            "bw": "125",
            "coding_rate": 5,
            "tx_power": 22,
            "preamble_len": 8,
            "crc_en": True,
            "syncword": 0x1424,
        },

        # ---- TCP Client -- DISABLED: unrelated placeholder target ----
        {
            "type": "TCPClientInterface",
            "name": "WiFi TCP",
            "enabled": False,
            "target_host": "192.168.1.10",
            "target_port": 4243,
        },

        # ---- WiFi Serial (Heltec V3 RNode over WiFi Remote) -- THE BRIDGE ----
        # Static IP, set directly on the Heltec via `rnodeconf -w STATION
        # --ip 192.168.0.222 --nm 255.255.255.0` -- no longer DHCP-assigned,
        # so this value should not need editing between sessions.
        {
            "type": "WiFiSerialInterface",
            "name": "Heltec Bridge",
            "enabled": True,
            "target_host": '192.168.0.222',
            "target_port": 7633,
        },

    ],
}

# ---- Sensor Network config ----
SENSOR_HUB = ""

# ---- Credit economy ----
# CREDITS_ENABLED = False is "free mode": nothing costs anything, nothing
# is earned, and the credit UI disappears entirely (no per-file cost
# labels, no balance command). Files still upload and download normally
# -- this only removes the economy layered on top of them.
#
# CREDIT_WEIGHTS is what an upload of each file class EARNS, and equally
# what a download of it COSTS. Set every value to 1 for a flat
# one-file-in-one-file-out economy; raise a class to make it scarcer.
CREDITS_ENABLED = False
CREDIT_WEIGHTS = {'video': 3, 'music': 2, 'document': 1, 'other': 1}

# ---- Plugin settings (added by the Provisioner) ----
AUTH_ADMIN_PASSWORD = 'TeK'
AUTH_MODE = 'open'
FSERVBOT_BROADCAST_MINS = 5
FSERVBOT_OP_PASSWORD = 'TeK_Knoh'
FSERVBOT_TRIGGER_PREFIX = '!'
