from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class WorkspaceError(RuntimeError):
    """Raised when a shared workspace operation is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SharedWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.logs_dir = self.root / "logs"
        self.iterations_dir = self.root / "iterations"
        self.state_path = self.root / "state.json"
        self._logger = logging.getLogger(f"sdat.workspace.{self.root}")

    def initialize(self, requirement: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(exist_ok=True)
        self._ensure_inside_workspace(self.logs_dir)
        run_id = uuid.uuid4().hex[:12]
        self.iterations_dir = self.root / "iterations" / f"run_{run_id}"
        self.iterations_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_inside_workspace(self.iterations_dir)
        self.write_text("requirement.md", requirement)
        self._configure_logging()
        self.save_state(
            {
                "status": "pending",
                "stage": "initializing",
                "run_id": run_id,
                "iteration": 0,
                "rework_cycles": 0,
                "review_decision": None,
                "started_at": utc_now(),
                "updated_at": utc_now(),
                "error": None,
                "history": [],
            }
        )
        self.log("Workflow initialized.")

    def _configure_logging(self) -> None:
        if self._logger.handlers:
            return
        handler = logging.FileHandler(
            self.logs_dir / "workflow.log", encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        self._logger.addHandler(handler)
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

    def log(self, message: str, level: int = logging.INFO) -> None:
        self._logger.log(level, message)

    def close(self) -> None:
        for handler in list(self._logger.handlers):
            self._logger.removeHandler(handler)
            handler.close()

    def write_text(self, relative_path: str, content: str) -> Path:
        path = self.resolve_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_files(self, directory: Path, files: dict[str, str]) -> list[Path]:
        directory.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for relative_path, content in files.items():
            path = self.resolve_under(directory, relative_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(path)
        return written

    def resolve_path(self, relative_path: str) -> Path:
        return self.resolve_under(self.root, relative_path)

    def resolve_under(self, directory: Path, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute() or not relative_path.strip():
            raise WorkspaceError(
                f"Generated file path must be a non-empty relative path: "
                f"{relative_path!r}"
            )
        base = directory.resolve()
        self._ensure_inside_workspace(base)
        resolved = (base / candidate).resolve()
        try:
            resolved.relative_to(base)
        except ValueError as error:
            raise WorkspaceError(
                f"Generated file path escapes its output directory: "
                f"{relative_path!r}"
            ) from error
        return resolved

    def _ensure_inside_workspace(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.root)
        except ValueError as error:
            raise WorkspaceError(
                f"Output directory escapes the shared workspace: {path}"
            ) from error

    def save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = utc_now()
        temporary_path = self.state_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self.state_path)

    def load_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WorkspaceError(f"Could not read workflow state: {error}") from error
        if not isinstance(value, dict):
            raise WorkspaceError("Workflow state must contain a JSON object.")
        return value

    def read_text(self, relative_path: str) -> str:
        path = self.resolve_path(relative_path)
        try:
            return path.read_text(encoding="utf-8")
        except OSError as error:
            raise WorkspaceError(
                f"Could not read workspace artifact '{relative_path}': {error}"
            ) from error

    def read_files(self, directory: Path) -> dict[str, str]:
        if not directory.exists():
            return {}
        return {
            path.relative_to(directory).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(directory.rglob("*"))
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        }
