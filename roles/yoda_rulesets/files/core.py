import rules_uu
from rules_uu import *


def __getattr__(name: str):
    val = getattr(rules_uu, name)
    globals()[name] = val
    return val
