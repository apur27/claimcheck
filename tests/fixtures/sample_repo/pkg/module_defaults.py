SDK_TIMEOUT = 30  # Defaults to 30 seconds if not overridden.


def configure() -> int:
    return SDK_TIMEOUT


MAX_RETRIES = 5  # default is 3 if not overridden.
