#!/usr/bin/env python3
"""Verify claimcheck's own Claude Code scaffolding -- everything that is checkable offline.

Adapted from RainMaker's `.claude/templates/harness-check.py`, which was written against
`.claude/standards/harness/claude-code.md` as verified 2026-08-17 against
https://code.claude.com/docs/en/memory and /skills, with the MCP page fetched 2026-08-29. The
checks are harness facts; only the constants below are claimcheck facts.

WHAT THIS CANNOT DO
-------------------
It cannot prove an instruction file was actually *loaded*. Only `/context` inside a live session
lists what Claude Code read, and nothing offline substitutes for it. Every failure mode below is
one that produces no error at all -- a skill in a directory that is never scanned, an import that
silently resolves nowhere, a `description` truncated past the point where it still triggers. That
is the whole reason this file exists: the silent ones are the ones a test has to catch.

    make harness-check      # this file
    /context                # in a session -- the only proof instruction files loaded
    /mcp                    # in a session -- server status and tool discovery

TWO INVOCATIONS, NOT ONE
------------------------
`check_mcp_server_runs` runs the server twice on purpose: once for its logic (`--selftest`) and
once to prove it can construct (`--check-imports`). The first passed green while the second would
have failed, on the day an SDK renamed a class -- a server whose logic tested clean and which
could not start. That split is kept here. A check that only exercises the part you thought to
check is how a broken thing ships with a green gate.

PROPAGATES: nothing. Every condition becomes a printed line and an exit code; this file is a
standalone entry point and is never imported by the package under test.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP_SERVER = ROOT / "mcp_server" / "scorer_server.py"

# From the harness reference, all documented limits:
CLAUDE_MD_TARGET_LINES = 200  # "target under 200 lines"; guidance, not enforced by the harness
SKILL_BODY_MAX_LINES = 500  # "body should stay under 500 lines"
LISTING_TRUNCATION = 1536  # description + when_to_use truncated at 1,536 chars in the listing
MAX_IMPORT_DEPTH = 4  # "maximum depth four hops"

# A project skill's allowed-tools applies even in an untrusted folder, and a hooks: field
# registers hooks for the session. Both act on the machine of whoever clones this repo.
REVIEW_BLOCKING_FIELDS = ("hooks", "allowed-tools", "disallowed-tools")

# `mcp_server/scorer_server.py` returns this specifically for "dependencies would not import",
# so it stays distinguishable from exit 1, which is any other failure.
EXIT_TRANSPORT_UNAVAILABLE = 3
BOTH_INSTRUCTION_FILES = 2
SUBPROCESS_TIMEOUT_SECONDS = 120

failures: list[str] = []
warnings: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def note(msg: str) -> None:
    notes.append(msg)


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML-ish frontmatter without a yaml dependency. Flat key: value only."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    block, body = text[4:end], text[end + 4 :]
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if line.strip() and not line.startswith((" ", "\t")) and ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields, body


def check_instruction_file() -> None:
    """CLAUDE.md must exist at a location Claude Code actually reads."""
    candidates = [ROOT / "CLAUDE.md", ROOT / ".claude" / "CLAUDE.md"]
    found = [p for p in candidates if p.exists()]
    if not found:
        fail("no CLAUDE.md at ./CLAUDE.md or ./.claude/CLAUDE.md — agents open this repo blind")
        return
    if len(found) == BOTH_INSTRUCTION_FILES:
        # Discovery is walk-up-and-concatenate, not nearest-wins: both would load.
        warn("both ./CLAUDE.md and ./.claude/CLAUDE.md exist — both load, concatenated")

    path = found[0]
    lines = path.read_text().splitlines()
    note(f"{path.relative_to(ROOT)}: {len(lines)} lines")
    if len(lines) > CLAUDE_MD_TARGET_LINES:
        warn(
            f"{path.relative_to(ROOT)} is {len(lines)} lines, over the documented "
            f"{CLAUDE_MD_TARGET_LINES}-line target. Nothing truncates it; the cost is context "
            "budget and adherence."
        )
    check_imports(path, depth=0, seen=set())

    if (ROOT / "AGENTS.md").exists():
        first = next((ln for ln in lines if ln.strip()), "")
        if "@AGENTS.md" not in path.read_text():
            warn(
                "AGENTS.md exists but CLAUDE.md does not import it. Claude Code does not read "
                "AGENTS.md natively — the two files will drift. Bridge with a first-line "
                f"@AGENTS.md import. (first line is {first!r})"
            )


def check_imports(path: Path, depth: int, seen: set[Path]) -> None:
    """Follow @path imports. Depth cap 4; outside-repo imports show an approval dialog once."""
    if depth > MAX_IMPORT_DEPTH:
        fail(f"{path.relative_to(ROOT)}: import chain deeper than {MAX_IMPORT_DEPTH} hops")
        return
    if path in seen:
        return
    seen.add(path)

    text = path.read_text()
    # Import parsing skips code spans and fenced blocks, so strip those before matching.
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`[^`]*`", "", text)

    for match in re.finditer(r"(?:^|\s)@([\w./~-]+)", text):
        target = match.group(1)
        resolved = (path.parent / target).resolve()
        try:
            resolved.relative_to(ROOT)
        except ValueError:
            fail(
                f"{path.relative_to(ROOT)}: imports {target!r} from outside the repo. That shows "
                "a one-time approval dialog to whoever opens this repo, and declining disables "
                "it permanently and silently."
            )
            continue
        if not resolved.exists():
            fail(f"{path.relative_to(ROOT)}: imports {target!r}, which does not exist")
            continue
        check_imports(resolved, depth + 1, seen)


def check_skills() -> None:
    """Skills live at .claude/skills/<name>/SKILL.md; the DIRECTORY name is the command."""
    skills_dir = ROOT / ".claude" / "skills"
    if not skills_dir.is_dir():
        note("no .claude/skills/")
        return

    found = 0
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            warn(f".claude/skills/{child.name} is not a directory — it will not be discovered")
            continue
        skill = child / "SKILL.md"
        if not skill.exists():
            fail(f".claude/skills/{child.name}/ has no SKILL.md — silently not a skill")
            continue
        found += 1
        check_one_skill(child, skill)

    names = ", ".join(p.name for p in sorted(skills_dir.iterdir()) if p.is_dir())
    note(f"{found} skill(s): {names}")


def check_one_skill(child: Path, skill: Path) -> None:
    """Frontmatter checks for a single SKILL.md."""
    fields, body = split_frontmatter(skill.read_text())

    if not fields.get("description"):
        warn(f"/{child.name}: no description — the model has nothing to trigger on")

    # The command name comes from the directory. A mismatched `name` is a display label
    # only, which makes it a quiet trap for anyone reading the frontmatter.
    declared = fields.get("name")
    if declared and declared != child.name:
        warn(
            f"/{child.name}: frontmatter name is {declared!r} but the command comes from the "
            f"directory, so this is /{child.name}. The name field is a display label here."
        )

    listing = len(fields.get("description", "")) + len(fields.get("when_to_use", ""))
    if listing > LISTING_TRUNCATION:
        warn(
            f"/{child.name}: description + when_to_use is {listing} chars, truncated at "
            f"{LISTING_TRUNCATION} in the listing"
        )

    body_lines = len(body.splitlines())
    if body_lines > SKILL_BODY_MAX_LINES:
        warn(
            f"/{child.name}: body is {body_lines} lines, over the {SKILL_BODY_MAX_LINES} "
            "guidance. Once invoked it stays in context for the rest of the session."
        )

    for field in REVIEW_BLOCKING_FIELDS:
        if field in fields:
            fail(
                f"/{child.name}: carries {field!r}. A project skill's tool permissions apply "
                "even in a folder the opener never trusted, and a hooks field registers hooks "
                "for the session — this acts on the machine of whoever clones the repo. "
                "Remove it, or have a human explicitly approve it."
            )


def check_agents() -> None:
    """Subagents need parseable frontmatter to be discovered at all."""
    agents_dir = ROOT / ".claude" / "agents"
    if not agents_dir.is_dir():
        note("no .claude/agents/")
        return
    paths = sorted(agents_dir.glob("*.md"))
    for path in paths:
        fields, _ = split_frontmatter(path.read_text())
        for required in ("name", "description"):
            if not fields.get(required):
                fail(f".claude/agents/{path.name}: no {required} in frontmatter")
        for field in REVIEW_BLOCKING_FIELDS:
            if field in fields:
                fail(
                    f".claude/agents/{path.name}: carries {field!r} — that acts on the machine "
                    "of whoever clones this repo, not just on this session."
                )
    note(f"{len(paths)} agent(s): {', '.join(p.stem for p in paths)}")


def check_mcp() -> None:
    """Project MCP config is .mcp.json at the repo root, under mcpServers. Approved once."""
    path = ROOT / ".mcp.json"
    if not path.exists():
        note("no .mcp.json")
        return
    try:
        config = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        fail(f".mcp.json does not parse: {exc}")
        return

    servers = config.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        fail(".mcp.json has no non-empty 'mcpServers' object — nothing will load")
        return

    for name, spec in servers.items():
        check_one_mcp_server(name, spec)

    note("project MCP config prompts for approval on first load — that dialog is a feature")
    note(".mcp.json parsing is not loading: only /mcp in a live session proves a server started")


def check_one_mcp_server(name: str, spec: dict[str, object]) -> None:
    """Name, credentials, transport and referenced paths for one server entry."""
    secret = re.compile(r"(sk-|ghp_|Bearer\s+[A-Za-z0-9])", re.I)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        fail(f".mcp.json: server name {name!r} — only letters, numbers, hyphens, underscores")

    blob = json.dumps(spec)
    if secret.search(blob):
        fail(f".mcp.json: server {name!r} looks like it contains a literal credential")

    url = spec.get("url")
    if url:
        fail(
            f".mcp.json: server {name!r} is remote ({url}). This repo ships stdio-only config on "
            "purpose — anyone approving a remote entry is trusting that endpoint, and a server "
            "that fetches external content can carry prompt injection into the session."
        )

    # ${VAR} in a project-scoped entry needs a default, or it expands to nothing.
    for var in re.findall(r"\$\{([^}]*)\}", blob):
        if ":-" not in var:
            fail(
                f".mcp.json: server {name!r} uses ${{{var}}} with no default. In a "
                "project-scoped entry that expands empty — use ${" + var + ":-.} or similar."
            )

    command = spec.get("command")
    args = spec.get("args") if isinstance(spec.get("args"), list) else []
    if command and not url:
        for arg in args:
            if isinstance(arg, str) and arg.endswith(".py") and not (ROOT / arg).exists():
                fail(f".mcp.json: server {name!r} points at {arg}, which does not exist")
        rendered = " ".join(str(a) for a in args)
        note(f".mcp.json: {name} = stdio, {command} {rendered}")


def check_mcp_server_runs() -> None:
    """The stdio server must pass its own selftest AND be able to construct.

    Two separate runs on purpose. `--selftest` exercises the tool logic against domain/ and
    services/ and never touches the transport; `--check-imports` constructs the transport and
    calls no tool function. Collapsing them into one invocation is how a server that tests clean
    and cannot start ships green.
    """
    if not MCP_SERVER.exists():
        note(f"no {MCP_SERVER.relative_to(ROOT)}")
        return

    proc = subprocess.run(
        [sys.executable, str(MCP_SERVER), "--selftest"],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    if proc.returncode != 0:
        fail(f"mcp_server selftest failed (exit {proc.returncode}):\n{proc.stdout}{proc.stderr}")
    else:
        note("mcp_server --selftest passed (tool logic only; proves nothing about the transport)")

    imports = subprocess.run(
        [sys.executable, str(MCP_SERVER), "--check-imports"],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    if imports.returncode == EXIT_TRANSPORT_UNAVAILABLE:
        detail = imports.stderr.strip().splitlines()
        warn(
            "mcp_server cannot import its own dependencies, so the server will not start under "
            "Claude Code even though its logic is sound. Run `uv sync --all-extras` and make "
            f"sure .mcp.json's command uses that environment. Reported: "
            f"{detail[0] if detail else 'no detail'}"
        )
    elif imports.returncode != 0:
        fail(f"mcp_server --check-imports failed (exit {imports.returncode}):\n{imports.stderr}")
    else:
        note(imports.stdout.strip())


def main() -> int:
    check_instruction_file()
    check_skills()
    check_agents()
    check_mcp()
    check_mcp_server_runs()

    for n in notes:
        print(f"     {n}")
    for w in warnings:
        print(f"warn {w}")
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)

    print(
        f"\n{len(notes)} note(s), {len(warnings)} warning(s), {len(failures)} failure(s)\n"
        "\nThis proves nothing about what Claude Code actually LOADED. Run /context in a session\n"
        "for that, and /mcp for server status. Both are cheap; neither has an offline substitute."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
