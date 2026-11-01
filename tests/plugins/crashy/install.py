STUMP_API = 1
def setup(stump):
    stump.gate(lambda ctx: 1/0, priority=25)
    raise RuntimeError("setup broke")
