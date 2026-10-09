"""Lets the firmware run under CPython for tests.

tests/shims stands in for the MicroPython-only modules urns imports
(micropython, uhashlib, ucryptolib); final_firmware/ goes on the path the way
it sits at the root of the board's filesystem.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "shims"))
sys.path.insert(0, os.path.join(ROOT, "final_firmware"))
sys.path.insert(0, ROOT)  # provisioner.py
