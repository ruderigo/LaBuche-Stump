# MicroPython's compiler hints, as no-ops on a computer (docs/PC.md).
def const(x):
    return x


def native(f):
    return f


def viper(f):
    return f


def mem_info(*args):
    pass


def opt_level(*args):
    return 0


def alloc_emergency_exception_buf(n):
    pass


def schedule(fn, arg):
    fn(arg)
