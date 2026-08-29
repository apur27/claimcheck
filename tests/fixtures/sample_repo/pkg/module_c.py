from tests.fixtures.sample_repo.pkg.module_a import CustomError


def handle_processing(widget: str) -> str:
    try:
        return widget.upper()
    except CustomError:
        return "handled"
