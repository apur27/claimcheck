class CustomError(Exception):
    """PROPAGATES: no handler exists in this module for CustomError."""


def process(widget: str) -> None:
    if not widget:
        raise CustomError("empty widget")
