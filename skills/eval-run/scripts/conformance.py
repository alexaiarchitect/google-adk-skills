#!/usr/bin/env python3
"""Record and replay ADK conformance tests, with the two traps handled.

Conformance testing answers a different question from evaluation. An evalset
asks "is the agent still right?"; a conformance test asks "is the agent still
doing *exactly* what it did before?" — same model requests, same tool calls,
same results, replayed without touching a model. That makes it the cheap
regression net around a refactor, and the one check in this plugin whose own
exit code can be trusted: unlike `adk eval`, `adk conformance test` fails loudly.

Three things catch people out, and this wrapper handles all three:

* **Recording is not self-contained.** `adk conformance record` does not start
  an agent; it drives a dev server that must already be listening on
  127.0.0.1:8000, started with the recordings plugin loaded. Without it the
  command fails with a connection error that says nothing about the cause, so
  the port is checked first and the exact `adk web` line is printed.
* **The mode is a positional, not a flag.** `record` takes the paths first and
  then the streaming mode (`none`, `sse` or `bidi`) as a trailing argument.
  Writing it as `--mode` produces a usage error.
* **A case with no recording is skipped, not failed.** In replay mode ADK
  (2.9 through 2.11.0) prints `Skipping <category>/<name>: no recordings` and
  drops the case; if that
  leaves the streaming mode with nothing to run, the summary says "No tests were
  run." and the command still exits 0. A suite that records nothing therefore
  passes. Every case is checked for its recordings file here first (`CNF020`),
  before anything is replayed.

On exit codes, as of 2.11.0: `adk conformance test` raises on failures in the
first streaming mode it runs, which makes it the one command in this plugin whose
exit code can be trusted. It does *not* yet fail because a later streaming mode
failed, nor because a mode ran no test at all. Until that changes, the `CNF020`
pre-check is what stands between an unrecorded suite and a green run.

File names, read off the released code (2.9.2 and 2.11.0 alike): a case is a
directory holding `spec.yaml` (a `TestSpec`); `record` writes
`generated-recordings.yaml` and `generated-session.yaml` beside it (the `-sse`
variants for `sse`), and `test` replays from those. `adk conformance record
--help` on those releases still describes `input.yaml` -> `test.yaml`; that text
is stale (fixed on upstream main) and nothing reads either name, so a case
written to it is reported here (`CNF015`) rather than silently skipped. Only the
`none` and `sse` streaming modes can be recorded or replayed: the CLI accepts
`bidi`, and the recorder then raises "Unsupported streaming mode" (`CNF021`).

    conformance.py record <case-dir> [--mode none|sse|bidi] [--host H] [--port N]
                          [--no-port-check] [--dry-run] [--json]
    conformance.py test [dirs ...] [--mode replay|live] [--generate-report]
                        [--report-dir D] [--streaming-mode none|sse|bidi]
                        [--dry-run] [--json]

Exit codes: 0 clean, 1 a conformance test failed or a spec is malformed,
2 usage or environment (no dev server, no spec, no runner).
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from adk_skills.adk_subprocess import describe_runner, run_adk  # noqa: E402
from adk_skills.env import resolve_runner  # noqa: E402
from adk_skills.layout import detect_layout  # noqa: E402
from adk_skills.report import (  # noqa: E402
    EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, GapReport, exit_code_for,
)

RECORDINGS_PLUGIN = "google.adk.cli.plugins.recordings_plugin.RecordingsPlugin"
SPEC_NAME = "spec.yaml"
#: The names `adk conformance record --help` gives on 2.9.2-2.11.0. The code
#: loads neither: `_generated_file_utils.load_test_case` opens `spec.yaml`.
STALE_SPEC_NAMES = ("input.yaml", "test.yaml")
#: Streaming modes the recorder and the replayer implement; `bidi` is a CLI
#: choice that raises "Unsupported streaming mode" inside both.
SUPPORTED_STREAMING = ("none", "sse")

#: What `ConformanceTestRunner._discover_test_cases` looks for, per streaming
#: mode. Recordings are made one mode at a time, so each mode has its own file.
RECORDINGS_NAME = {
    "none": "generated-recordings.yaml",
    "sse": "generated-recordings-sse.yaml",
}

#: `TestSpec` sets `extra="forbid"`, so anything outside this set is rejected
#: by ADK rather than ignored.
SPEC_KEYS = {"description", "agent", "initial_state", "user_messages"}
REQUIRED_KEYS = ("description", "agent")
_SRC_SPEC = "adk-python: cli/conformance/test_case.py (TestSpec, UserMessage)"


# ---------------------------------------------------------------------------
# a deliberately small spec reader
# ---------------------------------------------------------------------------

def read_spec_shape(path: Path) -> dict[str, Any]:
    """Top-level keys and the shape of `user_messages`, without a YAML parser.

    This checks structure, not values: which keys are present, and whether each
    `user_messages` entry is a mapping (correct) or a bare scalar (the mistake
    this catches). Anything subtler is ADK's job to reject.
    """
    keys: list[str] = []
    entries: list[dict[str, Any]] = []
    in_messages = False

    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        top = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$", line)
        if top:
            keys.append(top.group(1))
            in_messages = top.group(1) == "user_messages"
            continue
        if in_messages:
            item = re.match(r"^\s*-\s*(.*)$", line)
            if item:
                body = item.group(1).strip()
                mapping = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):", body)
                entries.append({
                    "line": number,
                    "mapping": bool(mapping) or body == "",
                    "field": mapping.group(1) if mapping else None,
                    "raw": body,
                })
    return {"keys": keys, "user_messages": entries}


def validate_spec(path: Path, report: GapReport) -> None:
    shape = read_spec_shape(path)
    keys = shape["keys"]

    for required in REQUIRED_KEYS:
        if required not in keys:
            report.add("CNF010", "error", "conformance",
                       f"`{required}` is required in {SPEC_NAME}",
                       path=str(path), source=_SRC_SPEC)
    unknown = sorted(set(keys) - SPEC_KEYS)
    if unknown:
        report.add("CNF011", "error", "conformance",
                   f"unrecognized key(s): {', '.join(unknown)}",
                   path=str(path),
                   fix="TestSpec sets extra=\"forbid\", so ADK rejects the file rather "
                       "than ignoring the key",
                   source=_SRC_SPEC)

    entries = shape["user_messages"]
    if "user_messages" in keys and not entries:
        report.add("CNF012", "warn", "conformance",
                   "`user_messages` is empty: the recording will have no turns",
                   path=str(path), source=_SRC_SPEC)
    for entry in entries:
        if not entry["mapping"]:
            report.add("CNF013", "error", "conformance",
                       "a user message must be an object, not a bare string",
                       path=str(path), locator=f"line {entry['line']}",
                       fix='write `- text: "..."` (UserMessage has `text`, `content` '
                           'and `state_delta`, not a scalar form)',
                       source=_SRC_SPEC)
        elif entry["field"] not in (None, "text", "content", "state_delta"):
            report.add("CNF014", "error", "conformance",
                       f"unrecognized user-message field {entry['field']!r}",
                       path=str(path), locator=f"line {entry['line']}",
                       fix="UserMessage accepts `text`, `content` and `state_delta`",
                       source=_SRC_SPEC)


def recordings_file(case_dir: Path, streaming_mode: str | None) -> Path:
    """The recordings file replay needs for this case in this streaming mode."""
    return case_dir / RECORDINGS_NAME.get(streaming_mode or "none", RECORDINGS_NAME["none"])


def check_recordings(specs: list[Path], report: GapReport, *, streaming_mode: str | None) -> None:
    """Refuse to replay a case that has no recording.

    ADK skips such a case with a yellow warning and carries on; if that empties
    the streaming mode, it prints "No tests were run." and exits 0 (2.9-2.11.0),
    so the suite passes without replaying anything.
    """
    mode = streaming_mode or "none"
    for spec in specs:
        case_dir = spec.parent
        target = recordings_file(case_dir, mode)
        if target.is_file():
            continue
        report.add(
            "CNF020", "error", "conformance",
            f"no {target.name} for this case, so replay skips it",
            path=str(case_dir), locator=target.name,
            fix=f"record it first: `conformance.py record {case_dir} --mode {mode}` "
                "(needs a dev server started with the recordings plugin). ADK (2.9-2.11.0) "
                "only warns and moves on, and a streaming mode left with no cases still "
                "exits 0",
            source="adk-python: cli/conformance/cli_test.py "
                   "(ConformanceTestRunner._discover_test_cases)",
        )


def find_specs(target: Path) -> list[Path]:
    if target.is_file() and target.name == SPEC_NAME:
        return [target]
    return sorted(target.rglob(SPEC_NAME))


def stale_specs(target: Path) -> list[Path]:
    """`input.yaml` / `test.yaml` files in directories with no `spec.yaml`:
    cases written to the stale `--help` text, which ADK never loads."""
    if target.is_file():
        return [target] if target.name in STALE_SPEC_NAMES else []
    found = []
    for name in STALE_SPEC_NAMES:
        for path in target.rglob(name):
            if not (path.parent / SPEC_NAME).is_file():
                found.append(path)
    return sorted(found)


def no_spec_message(targets: list[Path]) -> str:
    """Why nothing was found, naming any stale-named case files."""
    stale = [path for target in targets for path in stale_specs(target)]
    message = f"error: no {SPEC_NAME} under {', '.join(str(t) for t in targets)}."
    if stale:
        message += (
            f" [CNF015] Found {', '.join(str(p) for p in stale)}: `adk conformance record "
            "--help` on ADK 2.9-2.11.0 still says input.yaml, but the code loads only "
            f"{SPEC_NAME} (TestSpec). Rename the file to {SPEC_NAME}."
        )
    else:
        message += (f" A conformance case is a directory holding {SPEC_NAME}; write one "
                    "from eval-run's template.")
    return message


def unsupported_mode_message(mode: str) -> str:
    return (f"error: [CNF021] streaming mode {mode!r} cannot be recorded or replayed: the "
            "CLI accepts it, but ADK's conformance recorder and replayer (2.9-2.11.0) "
            "implement only none and sse and raise \"Unsupported streaming mode\". "
            "Use --mode none or sse.")


# ---------------------------------------------------------------------------
# the dev server the recorder drives
# ---------------------------------------------------------------------------

def port_answers(host: str, port: int, *, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def web_command(agents_dir: str | None) -> str:
    return (f"adk web {agents_dir or '<agents-dir>'} -v "
            f"--extra_plugins={RECORDINGS_PLUGIN}")


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def cmd_record(args: argparse.Namespace) -> int:
    case_dir = Path(args.case_dir)
    if not case_dir.exists():
        print(f"error: {case_dir} does not exist", file=sys.stderr)
        return EXIT_USAGE

    if args.mode not in SUPPORTED_STREAMING:
        print(unsupported_mode_message(args.mode), file=sys.stderr)
        return EXIT_USAGE

    specs = find_specs(case_dir)
    report = GapReport(subject=str(case_dir), skill="eval-run")
    if not specs:
        print(no_spec_message([case_dir]), file=sys.stderr)
        return EXIT_USAGE
    for spec in specs:
        validate_spec(spec, report)

    report.discovered = {
        "case_dir": str(case_dir),
        "specs": [str(s) for s in specs],
        "streaming_mode": args.mode,
    }

    if report.has_errors():
        print(report.render("json" if args.json else "md"))
        return EXIT_FINDINGS

    layout = detect_layout(case_dir if case_dir.is_dir() else case_dir.parent)
    agents_dir = layout.agents_dir

    # `record` takes the paths first and the streaming mode as a trailing
    # positional; there is no --mode flag on it.
    command = ["conformance", "record", str(case_dir), args.mode]

    if args.dry_run:
        payload = {
            "command": "adk " + " ".join(command),
            "requires": web_command(agents_dir),
            "specs": [str(s) for s in specs],
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"requires a dev server: {payload['requires']}")
            print(payload["command"])
        return EXIT_OK

    if not args.no_port_check and not port_answers(args.host, args.port):
        print(
            f"error: nothing is listening on {args.host}:{args.port}.\n"
            "`adk conformance record` does not start an agent - it drives a running\n"
            "dev server, which must be started with the recordings plugin loaded:\n\n"
            f"    {web_command(agents_dir)}\n\n"
            "Leave that running in another terminal, then re-run this command.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    runner = resolve_runner(agents_dir, layout.project_root)
    if not runner.available:
        print(f"error: {runner.note}", file=sys.stderr)
        return EXIT_USAGE

    outcome = run_adk(command, runner=runner, cwd=layout.project_root or case_dir.parent)
    payload = {
        "action": "record",
        "case_dir": str(case_dir),
        "streaming_mode": args.mode,
        "runner": describe_runner(runner),
        "specs": [str(s) for s in specs],
        "command": outcome.to_dict(),
        "generated": sorted(str(p) for p in case_dir.rglob("generated-*.yaml")),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Runner: {payload['runner']}")
        print(outcome.combined)
        if payload["generated"]:
            print()
            print("recorded:")
            for path in payload["generated"]:
                print(f"  {path}")
            print("Review the recordings before committing them: a recording of the "
                  "agent doing the wrong thing pins the wrong thing.")
    return EXIT_OK if outcome.ok else EXIT_FINDINGS


def cmd_test(args: argparse.Namespace) -> int:
    dirs = [Path(d) for d in (args.dirs or ["tests"])]
    missing = [str(d) for d in dirs if not d.exists()]
    if missing:
        print(f"error: no such director{'y' if len(missing) == 1 else 'ies'}: "
              f"{', '.join(missing)}", file=sys.stderr)
        return EXIT_USAGE

    if args.streaming_mode and args.streaming_mode not in SUPPORTED_STREAMING:
        print(unsupported_mode_message(args.streaming_mode), file=sys.stderr)
        return EXIT_USAGE

    specs = [spec for d in dirs for spec in find_specs(d)]
    if not specs:
        print(no_spec_message(dirs), file=sys.stderr)
        return EXIT_USAGE

    report = GapReport(subject=", ".join(str(d) for d in dirs), skill="eval-run")
    report.discovered = {
        "specs": [str(s) for s in specs],
        "mode": args.mode or "replay",
        "streaming_mode": args.streaming_mode or "none (ADK runs none then sse)",
    }
    for spec in specs:
        validate_spec(spec, report)
    # Nothing is replayed on a dry run, so a missing recording is not yet a problem.
    if (args.mode or "replay") == "replay" and not args.skip_recordings_check and not args.dry_run:
        check_recordings(specs, report, streaming_mode=args.streaming_mode)
    if report.has_errors():
        print(report.render("json" if args.json else "md"))
        return EXIT_FINDINGS

    command = ["conformance", "test", *[str(d) for d in dirs]]
    if args.mode:
        command.append(f"--mode={args.mode}")
    if args.generate_report:
        command.append("--generate_report")
    if args.report_dir:
        command.append(f"--report_dir={args.report_dir}")
    if args.streaming_mode:
        command.append(f"--streaming-mode={args.streaming_mode}")

    if args.dry_run:
        payload = {"command": "adk " + " ".join(command),
                   "specs": [str(s) for s in specs]}
        print(json.dumps(payload, indent=2) if args.json else payload["command"])
        return EXIT_OK

    base = dirs[0] if dirs[0].is_dir() else dirs[0].parent
    layout = detect_layout(base)
    runner = resolve_runner(layout.agent_dir or base, layout.project_root)
    if not runner.available:
        print(f"error: {runner.note}", file=sys.stderr)
        return EXIT_USAGE

    outcome = run_adk(command, runner=runner, cwd=layout.project_root or base)
    payload = {
        "action": "test",
        "dirs": [str(d) for d in dirs],
        "cases": len(specs),
        "runner": describe_runner(runner),
        "mode": args.mode or "replay",
        "command": outcome.to_dict(),
        # Unlike `adk eval`, this command's own exit code is meaningful: 2.11.0
        # raises on failures in the first streaming mode it runs. It stays silent
        # about a later mode's failures, and about a mode that ran nothing, which
        # is why every case was checked for its recordings above.
        "exit_code": EXIT_OK if outcome.ok else EXIT_FINDINGS,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Runner: {payload['runner']}")
        print(f"Cases:  {len(specs)} in {', '.join(payload['dirs'])}")
        print()
        print(outcome.combined)
        if not outcome.ok:
            print()
            print("A conformance failure means the agent's interactions changed. "
                  "That is a regression if the change was unintended, and a recording "
                  "to refresh if it was deliberate.")
    return EXIT_OK if outcome.ok else EXIT_FINDINGS


def cmd_check(args: argparse.Namespace) -> int:
    """Validate specs without running anything - useful in a pre-commit hook."""
    target = Path(args.case_dir)
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return EXIT_USAGE
    specs = find_specs(target)
    if not specs:
        print(no_spec_message([target]), file=sys.stderr)
        return EXIT_USAGE
    report = GapReport(subject=str(target), skill="eval-run")
    report.discovered = {"specs": [str(s) for s in specs]}
    for spec in specs:
        validate_spec(spec, report)
    print(report.render("json" if args.json else "md"))
    return exit_code_for(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--dry-run", action="store_true", help="print the command and stop")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    record = subparsers.add_parser("record", parents=[common],
                                   help="record a case against a running dev server")
    record.add_argument("case_dir")
    record.add_argument("--mode", choices=("none", "sse", "bidi"), default="none",
                        help="streaming mode; passed as the trailing positional ADK expects")
    record.add_argument("--host", default="127.0.0.1")
    record.add_argument("--port", type=int, default=8000)
    record.add_argument("--no-port-check", action="store_true",
                        help="skip the dev-server check (for tests and unusual setups)")
    record.set_defaults(func=cmd_record)

    test = subparsers.add_parser("test", parents=[common], help="replay recorded cases")
    test.add_argument("dirs", nargs="*", help="directories holding cases (default: tests)")
    test.add_argument("--mode", choices=("replay", "live"),
                      help="replay compares against recordings; live is not implemented yet")
    test.add_argument("--generate-report", action="store_true", help="write a markdown report")
    test.add_argument("--report-dir", help="where to write the report")
    test.add_argument("--streaming-mode", choices=("none", "sse", "bidi"))
    test.add_argument("--skip-recordings-check", action="store_true",
                      help="replay even where a case has no recordings file (ADK will skip "
                           "those cases and can still exit 0)")
    test.set_defaults(func=cmd_test)

    check = subparsers.add_parser("check", parents=[common],
                                  help="validate spec.yaml files without running anything")
    check.add_argument("case_dir", nargs="?", default="tests")
    check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE
    args.dry_run = getattr(args, "dry_run", False)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
