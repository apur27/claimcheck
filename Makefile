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
	uv run python scripts/check_falsify.py

# Offline validation of this repo's own .claude/ scaffolding: CLAUDE.md, skills, the reviewer
# agent, .mcp.json, and the MCP server's two separate invocations. It proves files parse and
# paths resolve -- it cannot prove Claude Code loaded any of them. Run /context and /mcp in a
# live session for that.
harness-check:
	uv run python test/harness_check.py
