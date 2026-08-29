"""Command-line wiring for claimcheck.

Will host `claimcheck src/` and `claimcheck --diff`. Parses input, calls a
service, formats output — no business logic lives here.
"""


def main() -> None:
    """Entry point registered as the `claimcheck` console script.

    Not yet implemented: argument parsing and service wiring land in a later
    slice. PROPAGATES: raises NotImplementedError to the top level and
    terminates the process with a traceback until that slice lands.
    """
    raise NotImplementedError("claimcheck CLI is not yet implemented")
