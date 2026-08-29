class LonelyError(Exception):
    """PROPAGATES: no handler exists anywhere for LonelyError."""


def risky_operation() -> None:
    raise LonelyError("nothing catches this")
