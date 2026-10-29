from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sdat.orchestrator import WorkflowError, create_default_orchestrator
from sdat.workspace import WorkspaceError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sdat",
        description="Turn a business requirement into a reviewed implementation.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="run the agent workflow")
    requirement = run_parser.add_mutually_exclusive_group(required=True)
    requirement.add_argument(
        "--requirement",
        type=Path,
        help="path to a UTF-8 Markdown or text requirement",
    )
    requirement.add_argument(
        "--requirement-text",
        help="requirement text to process directly",
    )
    run_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
        help="shared workspace output directory (default: ./workspace)",
    )
    run_parser.add_argument(
        "--max-rework-cycles",
        type=int,
        default=3,
        help="maximum reviewer-requested rework cycles (default: 3)",
    )
    run_parser.add_argument(
        "--model",
        help="optional Copilot model identifier",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_rework_cycles < 0:
        parser.error("--max-rework-cycles cannot be negative")

    try:
        requirement = (
            args.requirement_text
            if args.requirement_text is not None
            else args.requirement.read_text(encoding="utf-8")
        )
        orchestrator = create_default_orchestrator(
            args.workspace,
            max_rework_cycles=args.max_rework_cycles,
            model=args.model,
        )
        state = orchestrator.run(requirement)
    except (OSError, WorkflowError, WorkspaceError, ValueError) as error:
        print(f"SDAT failed: {error}", file=sys.stderr)
        return 1

    print(f"Workflow status: {state['status']}")
    print(f"Workspace: {args.workspace.expanduser().resolve()}")
    if state.get("error"):
        print(f"Reason: {state['error']}")
    if state["status"] == "approved":
        print(
            f"Final review: "
            f"{args.workspace.expanduser().resolve() / 'reviewer_output.md'}"
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
