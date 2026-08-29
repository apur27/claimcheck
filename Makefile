.PHONY: install format check exceptions clean check-falsify harness-check

install:
	time uv sync --all-extras

format:
	uv run ruff format .

check:
	uv run ruff format --check .
	uv run ruff check --no-fix .
	uv run mypy
	uv run lint-imports
	uv run python scripts/check_exceptions.py
	uv run pytest --cov --cov-fail-under=80

exceptions:
	uv run python scripts/check_exceptions.py

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} +

check-falsify:
	@echo "check-falsify: not yet implemented — lands in slice 4/session 2"

harness-check:
	@echo "harness-check: not yet implemented — lands in slice 4/session 2"
