#!/usr/bin/env python3
"""MCP stdio server exposing claimcheck's deterministic verifiers and scorer.

Lets another agent settle a single claim, or score a set of predictions, without shelling out to
the `claimcheck` CLI and parsing its terminal output.

WHY HAND-ROLLED, NOT THE `mcp` PYTHON SDK
-----------------------------------------
Deliberate. The MCP stdio transport is newline-delimited JSON-RPC 2.0 over stdin/stdout, and the
three methods a tools-only server needs (`initialize`, `tools/list`, `tools/call`) are about
eighty lines of stdlib. Taking the SDK would add a runtime dependency to a repo whose entire
architectural point is that `domain` and `services` stay free of third-party imports, for a
surface this small. The trade is real and worth stating: the SDK tracks spec revisions for you and
this file does not -- `PROTOCOL_VERSION` below is pinned by hand and goes stale silently when the
spec revs. Re-check it against https://modelcontextprotocol.io/specification when touching this.

Protocol revision 2026-07-28, read from https://modelcontextprotocol.io/specification on
2026-08-29. Pinned as a literal string, never computed.

TWO INVOCATIONS, AND WHY THEY ARE SEPARATE
------------------------------------------
    python mcp_server/scorer_server.py --selftest        tool logic against domain/ and services/
    python mcp_server/scorer_server.py --check-imports   the transport layer can be constructed
    python mcp_server/scorer_server.py                   serve on stdio (what .mcp.json runs)

`--selftest` never touches the transport; `--check-imports` never calls a tool function. A server
whose logic tests green and which cannot start is the failure this split exists to catch, and a
single combined check would hide it.

Exit 3 from `--check-imports` means specifically "the server's own dependencies would not import",
distinguishable from exit 1, which is any other failure. Stated honestly: because the claimcheck
imports below are module-level, an uninstalled package today fails *both* modes at import time, so
exit 3 is reachable only if those imports are ever made lazy or an SDK is adopted. The value the
split delivers today is the construction-and-schema probe, not the import probe.

WHAT THIS CANNOT PROVE
----------------------
Nothing here proves a client negotiated a session or discovered the tools. Run `/mcp` inside a live
Claude Code session for that; it has no offline substitute.

SCOPE GRANTED
-------------
`verify_claim` takes a caller-supplied `repo_root` and reads Python source under it with
`ast.parse` -- it never imports, execs or runs scanned code, the same constraint
`domain/verifiers.py` holds. A misconfigured or hostile caller can therefore use it to learn the
existence and parse-shape of files anywhere this process can read, and the evidence strings quote
source-derived text back to the caller. `claim.file` is confined to `repo_root` here (see
`_resolve_inside`), but `repo_root` itself is whatever the caller passes. Do not expose this server
to an untrusted network peer; it is a local stdio server on purpose.
"""

from __future__ import annotations

import importlib
import json
import math
import sys
from pathlib import Path
from typing import Any, TextIO

from claimcheck.domain.models import VALID_SHAPES, Claim
from claimcheck.domain.scorer import PredictionPair, score
from claimcheck.domain.verifiers import verify
from claimcheck.services.eval import DATA_PATH

PROTOCOL_VERSION = "2026-07-28"
SERVER_NAME = "claimcheck-verifiers"
SERVER_VERSION = "0.1.0"

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_REPO = REPO_ROOT / "tests" / "fixtures" / "sample_repo"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_TRANSPORT_UNAVAILABLE = 3

_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603

_TRANSPORT_DEPENDENCIES = (
    "claimcheck.domain.models",
    "claimcheck.domain.scorer",
    "claimcheck.domain.verifiers",
)

_SELFTEST_TOLERANCE = 1e-9
_EXPECTED_SELFTEST_PRECISION = 2 / 3
_EXPECTED_SELFTEST_RECALL = 2 / 3

TOOLS: list[dict[str, Any]] = [
    {
        "name": "verify_claim",
        "description": (
            "Settle one prose claim against the code it describes, using ast parsing only -- "
            "the scanned repository is never imported or executed. Returns one of the frozen "
            "reason codes ok / contradicted / unverifiable / unparsed with a one-line evidence "
            "string. 'unparsed' means the claim text yielded nothing to compare; 'unverifiable' "
            "means the code did not."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_root": {
                    "type": "string",
                    "description": "Absolute path to the repository root being scanned.",
                },
                "file": {
                    "type": "string",
                    "description": "Path to the file holding the claim, relative to repo_root.",
                },
                "line": {
                    "type": "integer",
                    "description": "1-indexed line the claim's text starts on.",
                },
                "claim_text": {
                    "type": "string",
                    "description": "The prose making the claim.",
                },
                "shape": {
                    "type": "string",
                    "enum": sorted(VALID_SHAPES),
                    "description": (
                        "Which deterministic verifier applies. 'other' has none and always "
                        "resolves unverifiable."
                    ),
                },
            },
            "required": ["repo_root", "file", "line", "claim_text", "shape"],
        },
    },
    {
        "name": "score_predictions",
        "description": (
            "Score predicted reason codes against labelled ground truth. Precision and recall "
            "are returned separately, each with its own denominator, never blended: precision is "
            "true_positives/findings, recall is true_positives/labelled_contradicted, and either "
            "is null when its denominator is zero. A prediction of 'unparsed' against a "
            "'contradicted' label counts as a false negative and is never excluded."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pairs": {
                    "type": "array",
                    "description": "One entry per claim.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "predicted": {"type": "string"},
                            "labelled": {"type": "string"},
                        },
                        "required": ["predicted", "labelled"],
                    },
                },
            },
            "required": ["pairs"],
        },
    },
]


class ToolInputError(ValueError):
    """A tool call carried arguments this server cannot act on.

    PROPAGATES: never past `_call_tool`, which catches it and returns a JSON-RPC error object with
    code -32602 so the session stays alive. It exists to keep argument validation out of the
    transport layer, not to reach a caller.
    """


# --- tool logic (no transport, no MCP concepts) ----------------------------


def _resolve_inside(repo_root: Path, relative: str) -> Path | None:
    """Resolve `relative` under `repo_root`, or `None` if it escapes the root.

    A caller-supplied `file` of `../../etc/passwd` would otherwise have the verifiers read a path
    the caller never named a root for.
    """
    candidate = (repo_root / relative).resolve()
    if candidate == repo_root or repo_root in candidate.parents:
        return candidate
    return None


def tool_verify_claim(arguments: dict[str, Any]) -> dict[str, Any]:
    """Settle one claim through `domain/verifiers.verify`. Returns a `Verdict` as a plain dict."""
    for required in ("repo_root", "file", "line", "claim_text", "shape"):
        if required not in arguments:
            raise ToolInputError(required)

    shape = str(arguments["shape"])
    if shape not in VALID_SHAPES:
        return {
            "reason": "unparsed",
            "evidence": f"shape {shape!r} is not one of {sorted(VALID_SHAPES)}",
        }

    repo_root = Path(str(arguments["repo_root"])).expanduser().resolve()
    if not repo_root.is_dir():
        return {"reason": "unverifiable", "evidence": f"repo_root {repo_root} is not a directory"}

    relative = str(arguments["file"])
    if _resolve_inside(repo_root, relative) is None:
        return {
            "reason": "unverifiable",
            "evidence": f"file {relative!r} resolves outside repo_root {repo_root}",
        }

    claim = Claim(
        file=relative,
        line=int(arguments["line"]),
        claim_text=str(arguments["claim_text"]),
        shape=shape,
        source=str(arguments.get("source", "docstring")),
    )
    verdict = verify(claim, repo_root)
    return {"reason": verdict.reason, "evidence": verdict.evidence}


def tool_score_predictions(arguments: dict[str, Any]) -> dict[str, Any]:
    """Score (predicted, labelled) pairs through the same `domain/scorer.py` the eval uses."""
    raw = arguments.get("pairs")
    if not isinstance(raw, list):
        raise ToolInputError("pairs")
    pairs = [
        PredictionPair(predicted=str(entry["predicted"]), labelled=str(entry["labelled"]))
        for entry in raw
    ]
    result = score(pairs)
    return {
        "true_positives": result.true_positives,
        "false_positives": result.false_positives,
        "false_negatives": result.false_negatives,
        "findings": result.findings,
        "labelled_contradicted": result.labelled_contradicted,
        "precision": result.precision,
        "recall": result.recall,
    }


_TOOL_FUNCTIONS = {
    "verify_claim": tool_verify_claim,
    "score_predictions": tool_score_predictions,
}


# --- transport (JSON-RPC 2.0 over newline-delimited stdio) -----------------


def _result(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _call_tool(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    name = str(params.get("name", ""))
    function = _TOOL_FUNCTIONS.get(name)
    if function is None:
        return _error(request_id, _INVALID_PARAMS, f"unknown tool {name!r}")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _error(request_id, _INVALID_PARAMS, "arguments must be a JSON object")
    try:
        payload = function(arguments)
    except ToolInputError as exc:
        return _error(request_id, _INVALID_PARAMS, f"missing or invalid argument: {exc}")
    except (KeyError, TypeError, ValueError, OSError) as exc:
        return _error(request_id, _INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
    return _result(
        request_id,
        {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
            "structuredContent": payload,
            "isError": False,
        },
    )


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    """Turn one JSON-RPC request into one response, or `None` for a notification."""
    request_id = message.get("id")
    if request_id is None:
        return None
    method = str(message.get("method", ""))
    params = message.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        return _call_tool(request_id, params)
    return _error(request_id, _METHOD_NOT_FOUND, f"unknown method {method!r}")


def serve(stdin: TextIO, stdout: TextIO) -> int:
    """Read newline-delimited JSON-RPC from `stdin`, write responses to `stdout`."""
    for raw_line in stdin:
        text = raw_line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        response = handle(message)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return EXIT_OK


# --- modes -----------------------------------------------------------------


def _selftest_cases() -> list[tuple[str, str, str]]:
    """Each case is (label, expected, actual). Exercises domain/ and services/ directly."""
    cases: list[tuple[str, str, str]] = []
    fixture = str(FIXTURE_REPO)

    contradicted = tool_verify_claim(
        {
            "repo_root": fixture,
            "file": "pkg/module_a.py",
            "line": 2,
            "claim_text": "PROPAGATES: no handler exists in this module for CustomError.",
            "shape": "raises_propagates",
        }
    )
    cases.append(("verify_claim raises_propagates", "contradicted", contradicted["reason"]))

    stale_default = tool_verify_claim(
        {
            "repo_root": fixture,
            "file": "pkg/module_defaults.py",
            "line": 8,
            "claim_text": "default is 3 if not overridden.",
            "shape": "defaults_to",
        }
    )
    cases.append(("verify_claim defaults_to (stale)", "contradicted", stale_default["reason"]))

    accurate_default = tool_verify_claim(
        {
            "repo_root": fixture,
            "file": "pkg/module_defaults.py",
            "line": 1,
            "claim_text": "Defaults to 30 seconds if not overridden.",
            "shape": "defaults_to",
        }
    )
    cases.append(("verify_claim defaults_to (accurate)", "ok", accurate_default["reason"]))

    unknown_shape = tool_verify_claim(
        {
            "repo_root": fixture,
            "file": "pkg/module_a.py",
            "line": 2,
            "claim_text": "anything",
            "shape": "not_a_real_shape",
        }
    )
    cases.append(("verify_claim rejects unknown shape", "unparsed", unknown_shape["reason"]))

    escaped = tool_verify_claim(
        {
            "repo_root": fixture,
            "file": "../../../etc/passwd",
            "line": 1,
            "claim_text": "anything",
            "shape": "returns_type",
        }
    )
    cases.append(("verify_claim confines file to repo_root", "unverifiable", escaped["reason"]))

    scored = tool_score_predictions(
        {
            "pairs": [
                {"predicted": "contradicted", "labelled": "contradicted"},
                {"predicted": "contradicted", "labelled": "contradicted"},
                {"predicted": "contradicted", "labelled": "ok"},
                {"predicted": "unparsed", "labelled": "contradicted"},
            ],
        }
    )
    precision_ok = scored["precision"] is not None and math.isclose(
        scored["precision"], _EXPECTED_SELFTEST_PRECISION, abs_tol=_SELFTEST_TOLERANCE
    )
    recall_ok = scored["recall"] is not None and math.isclose(
        scored["recall"], _EXPECTED_SELFTEST_RECALL, abs_tol=_SELFTEST_TOLERANCE
    )
    cases.append(("score_predictions precision 2/3", "True", str(precision_ok)))
    cases.append(("score_predictions recall 2/3", "True", str(recall_ok)))
    cases.append(("score_predictions counts unparsed as FN", "1", str(scored["false_negatives"])))

    empty = tool_score_predictions({"pairs": []})
    cases.append(("score_predictions 0-over-0 precision null", "None", str(empty["precision"])))
    cases.append(("services/eval.py labelled set on disk", "True", str(DATA_PATH.is_file())))
    return cases


def run_selftest() -> int:
    """Exercise the tool functions against `domain/` and `services/`. No transport, no MCP SDK."""
    try:
        cases = _selftest_cases()
    except (KeyError, TypeError, ValueError, OSError) as exc:
        print(f"selftest: raised {type(exc).__name__}: {exc}")
        return EXIT_FAILED

    failed = 0
    for label, expected, actual in cases:
        matched = actual == expected
        if not matched:
            failed += 1
        print(f"{'ok  ' if matched else 'FAIL'} {label}: expected {expected}, got {actual}")

    print(f"selftest: {len(cases) - failed}/{len(cases)} passed")
    print("Proves the tool logic, NOT that any MCP client can reach it. Run /mcp for that.")
    return EXIT_FAILED if failed else EXIT_OK


def run_check_imports() -> int:
    """Prove the transport constructs and its schemas serialise. Calls no tool function."""
    for name in _TRANSPORT_DEPENDENCIES:
        try:
            importlib.import_module(name)
        except ImportError as exc:
            print(f"transport dependency {name} would not import: {exc}", file=sys.stderr)
            return EXIT_TRANSPORT_UNAVAILABLE

    probe = handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
    if probe is None or probe.get("result", {}).get("protocolVersion") != PROTOCOL_VERSION:
        print(f"initialize did not report protocolVersion {PROTOCOL_VERSION}", file=sys.stderr)
        return EXIT_FAILED

    listing = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    if listing is None:
        print("tools/list returned no response", file=sys.stderr)
        return EXIT_FAILED

    tools = listing["result"]["tools"]
    advertised = {str(tool["name"]) for tool in tools}
    if advertised != set(_TOOL_FUNCTIONS):
        print(f"advertised tools {sorted(advertised)} != implemented tools", file=sys.stderr)
        return EXIT_FAILED
    json.dumps(tools)

    print(
        f"transport constructs: MCP {PROTOCOL_VERSION}, hand-rolled stdio, "
        f"tools: {', '.join(sorted(advertised))}"
    )
    return EXIT_OK


def main(argv: list[str]) -> int:
    """Dispatch on the single supported flag; no flag means serve on stdio."""
    if "--selftest" in argv:
        return run_selftest()
    if "--check-imports" in argv:
        return run_check_imports()
    return serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
