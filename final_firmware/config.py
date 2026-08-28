"""
µReticulum — Node Configuration
================================
"""

from lora_boards import LORA_BOARDS

# ---- Node settings ----
WIFI_SSID = "Bob's Glitch"
WIFI_PASS = 'SalutComment'
NODE_NAME = 'LaBuche'

# The name the local greeter answers to on the web pages. Purely
# cosmetic and per-node -- it has nothing to do with NODE_NAME above,
# which is the identity mesh peers see.
BOT_NAME = 'Assistant'

# Sent once to each mesh peer the first time they message this node.
# Blank disables it entirely.
#
# Once per peer, not once per message, on purpose: every LXMF send costs
# real airtime and roughly seven seconds of crypto on the S3, so an
# auto-reply on every inbound message would answer a three-word question
# with a paragraph and do it again for the next three words. A greeting
# on first contact says who this node is and what it offers; after that
# the conversation is the point.
MESH_GREETING = "Coucou je suis un projet communautaire plus d'info sur github Ruderigo/LaBuche-Stump"
MESH_GREETING_MAX = 200

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
FSERVBOT_BROADCAST_MINS = 5
FSERVBOT_OP_PASSWORD = 'TeK_Knoh'
FSERVBOT_TRIGGER_PREFIX = '!'
