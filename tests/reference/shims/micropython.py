"""CPython stand-in for MicroPython's micropython module (tests only)."""
def const(x):
    return x

def native(f):
    return f

viper = native
