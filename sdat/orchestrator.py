from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sdat.agents import (
    AgentError,
    BUSINESS_ANALYST_INSTRUCTIONS,
    CopilotCLIProvider,
    DEVELOPER_INSTRUCTIONS,
    REVIEWER_INSTRUCTIONS,
    RoleAgent,
    TESTER_INSTRUCTIONS,
)
from sdat.workspace import SharedWorkspace, WorkspaceError


class WorkflowError(RuntimeError):
    """Raised when a workflow cannot safely continue."""


@dataclass
class WorkflowAgents:
    business_analyst: RoleAgent
    developer: RoleAgent
    tester: RoleAgent
    reviewer: RoleAgent

    @classmethod
    def from_provider(cls, provider: Any) -> "WorkflowAgents":
        return cls(
            business_analyst=RoleAgent(
                "Business Analyst Agent", BUSINESS_ANALYST_INSTRUCTIONS, provider
            ),
            developer=RoleAgent(
                "Developer Agent", DEVELOPER_INSTRUCTIONS, provider
            ),
            tester=RoleAgent("Tester Agent", TESTER_INSTRUCTIONS, provider),
            reviewer=RoleAgent("Reviewer Agent", REVIEWER_INSTRUCTIONS, provider),
        )


class Orchestrator:
    def __init__(
        self,
        workspace: SharedWorkspace,
        agents: WorkflowAgents,
        max_rework_cycles: int = 3,
        test_timeout_seconds: int = 120,
    ) -> None:
        if max_rework_cycles < 0:
            raise ValueError("max_rework_cycles cannot be negative.")
        if test_timeout_seconds <= 0:
            raise ValueError("test_timeout_seconds must be greater than zero.")
        self.workspace = workspace
        self.agents = agents
        self.max_rework_cycles = max_rework_cycles
        self.test_timeout_seconds = test_timeout_seconds

    def run(self, requirement: str) -> dict[str, Any]:
        if not requirement.strip():
            raise ValueError("The requirement cannot be empty.")

        self.workspace.initialize(requirement.strip())
        state = self.workspace.load_state()
        try:
            state["status"] = "running"
            self.workspace.save_state(state)
            self._set_stage(state, "business_analysis")
            ba_output = self.agents.business_analyst.run(
                self.workspace.read_text("requirement.md")
            )
            self.workspace.write_text("ba_output.md", ba_output)
            self._record(state, "business_analysis", "completed")

            for iteration in range(1, self.max_rework_cycles + 2):
                iteration_dir = (
                    self.workspace.iterations_dir / f"iteration_{iteration:02d}"
                )
                iteration_prefix = (
                    iteration_dir.relative_to(self.workspace.root).as_posix()
                )
                implementation_dir = iteration_dir / "implementation"
                tests_dir = iteration_dir / "tests"
                state["iteration"] = iteration
                state["stage"] = "development"
                self.workspace.save_state(state)

                developer_context = self._developer_context(state)
                developer_result = self.agents.developer.run(developer_context)
                self.workspace.write_text(
                    f"{iteration_prefix}/developer_response_raw.txt",
                    developer_result,
                )
                developer_data = self._parse_object(
                    developer_result, "Developer Agent"
                )
                technical_notes = self._required_string(
                    developer_data, "technical_notes", "Developer Agent"
                )
                implementation_files = self._string_mapping(
                    developer_data, "files", "Developer Agent"
                )
                if not implementation_files:
                    raise WorkflowError(
                        "Developer Agent returned no implementation files."
                    )
                self.workspace.write_files(implementation_dir, implementation_files)
                developer_output = self._format_developer_output(
                    technical_notes, implementation_files
                )
                self.workspace.write_text("developer_output.md", developer_output)
                self.workspace.write_text(
                    f"{iteration_prefix}/developer_output.md",
                    developer_output,
                )
                self._record(state, "development", "completed")

                state["stage"] = "testing"
                self.workspace.save_state(state)
                tester_context = self._tester_context(iteration)
                tester_result = self.agents.tester.run(tester_context)
                self.workspace.write_text(
                    f"{iteration_prefix}/tester_response_raw.txt",
                    tester_result,
                )
                tester_data = self._parse_object(tester_result, "Tester Agent")
                test_report = self._required_string(
                    tester_data, "report", "Tester Agent"
                )
                test_files = self._string_mapping(
                    tester_data, "tests", "Tester Agent"
                )
                self.workspace.write_files(tests_dir, test_files)
                test_execution = self._run_tests(implementation_dir, tests_dir)
                complete_test_report = (
                    f"{test_report}\n\n## Automated Test Execution\n\n"
                    f"```text\n{test_execution['output'] or '(no output)'}\n```\n\n"
                    f"Exit code: {test_execution['returncode']}"
                )
                self.workspace.write_text("tester_output.md", complete_test_report)
                self.workspace.write_text(
                    f"{iteration_prefix}/tester_output.md",
                    complete_test_report,
                )
                state["test_execution"] = test_execution
                self._record(
                    state,
                    "testing",
                    "passed" if test_execution["returncode"] == 0 else "failed",
                )

                state["stage"] = "review"
                self.workspace.save_state(state)
                reviewer_context = self._reviewer_context(iteration)
                reviewer_result = self.agents.reviewer.run(reviewer_context)
                self.workspace.write_text(
                    f"{iteration_prefix}/reviewer_response_raw.txt",
                    reviewer_result,
                )
                reviewer_data = self._parse_object(reviewer_result, "Reviewer Agent")
                decision = self._required_string(
                    reviewer_data, "decision", "Reviewer Agent"
                ).strip().upper()
                if decision not in {"APPROVED", "REWORK"}:
                    raise WorkflowError(
                        "Reviewer Agent decision must be APPROVED or REWORK."
                    )
                feedback = self._required_string(
                    reviewer_data, "feedback", "Reviewer Agent"
                )
                if decision == "APPROVED" and test_execution["returncode"] != 0:
                    decision = "REWORK"
                    feedback = (
                        "Approval was overridden because the automated tests "
                        "did not pass.\n\n" + feedback
                    )
                reviewer_output = f"# Review: {decision}\n\n{feedback}"
                self.workspace.write_text("reviewer_output.md", reviewer_output)
                self.workspace.write_text(
                    f"{iteration_prefix}/reviewer_output.md",
                    reviewer_output,
                )
                state["review_decision"] = decision
                self._record(state, "review", decision.lower())

                if decision == "APPROVED":
                    state["status"] = "approved"
                    state["stage"] = "completed"
                    state["error"] = None
                    self.workspace.save_state(state)
                    self.workspace.log(
                        f"Workflow approved after iteration {iteration}."
                    )
                    return state

                if state["rework_cycles"] >= self.max_rework_cycles:
                    state["status"] = "failed"
                    state["stage"] = "rework_limit_reached"
                    state["error"] = (
                        f"Reviewer requested rework after the configured "
                        f"{self.max_rework_cycles} rework cycles."
                    )
                    self.workspace.save_state(state)
                    self.workspace.log(state["error"])
                    return state

                state["rework_cycles"] += 1
                state["stage"] = "rework"
                self.workspace.save_state(state)
                self.workspace.log(
                    f"Reviewer requested rework; starting iteration {iteration + 1}."
                )

            raise WorkflowError("Workflow exited without an approval decision.")
        except (AgentError, WorkspaceError, WorkflowError, OSError, ValueError) as error:
            state["status"] = "failed"
            state["stage"] = "failed"
            state["error"] = str(error)
            self.workspace.save_state(state)
            self.workspace.log(f"Workflow failed: {error}")
            raise WorkflowError(str(error)) from error
        finally:
            self.workspace.close()

    def _set_stage(self, state: dict[str, Any], stage: str) -> None:
        state["stage"] = stage
        self.workspace.save_state(state)

    def _record(self, state: dict[str, Any], stage: str, result: str) -> None:
        state["history"].append(
            {
                "iteration": state["iteration"],
                "stage": stage,
                "result": result,
                "timestamp": self._now(),
            }
        )
        self.workspace.save_state(state)
        self.workspace.log(f"{stage}: {result}")

    def _developer_context(self, state: dict[str, Any]) -> str:
        sections = [
            "# Requirement\n" + self.workspace.read_text("requirement.md"),
            "# Business analysis\n" + self.workspace.read_text("ba_output.md"),
        ]
        if state["iteration"] > 1:
            sections.append(
                "# Previous review feedback\n"
                + self.workspace.read_text("reviewer_output.md")
            )
        return "\n\n".join(sections)

    def _tester_context(self, iteration: int) -> str:
        directory = self.workspace.iterations_dir / f"iteration_{iteration:02d}"
        files = self.workspace.read_files(directory / "implementation")
        return "\n\n".join(
            [
                "# Requirement\n" + self.workspace.read_text("requirement.md"),
                "# Business analysis\n" + self.workspace.read_text("ba_output.md"),
                "# Developer notes\n" + self.workspace.read_text("developer_output.md"),
                "# Implementation files\n" + self._format_files(files),
            ]
        )

    def _reviewer_context(self, iteration: int) -> str:
        directory = self.workspace.iterations_dir / f"iteration_{iteration:02d}"
        implementation = self.workspace.read_files(directory / "implementation")
        return "\n\n".join(
            [
                "# Requirement\n" + self.workspace.read_text("requirement.md"),
                "# Business analysis\n" + self.workspace.read_text("ba_output.md"),
                "# Developer output\n" + self.workspace.read_text("developer_output.md"),
                "# Implementation files\n" + self._format_files(implementation),
                "# Tester output and execution\n"
                + self.workspace.read_text("tester_output.md"),
            ]
        )

    def _run_tests(self, implementation_dir: Path, tests_dir: Path) -> dict[str, Any]:
        test_files = list(tests_dir.rglob("test_*.py")) if tests_dir.exists() else []
        if not test_files:
            return {
                "returncode": 1,
                "output": "No Python unittest files matching test_*.py were generated.",
            }

        environment = os.environ.copy()
        current_python_path = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = os.pathsep.join(
            part
            for part in (str(implementation_dir), current_python_path)
            if part
        )
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    str(tests_dir),
                    "-p",
                    "test_*.py",
                ],
                cwd=self.workspace.root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.test_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            output = "\n".join(
                part.decode("utf-8", errors="replace")
                if isinstance(part, bytes)
                else part or ""
                for part in (error.stdout, error.stderr)
            ).strip()
            return {
                "returncode": 1,
                "output": (
                    f"Test execution timed out after "
                    f"{self.test_timeout_seconds} seconds.\n{output}"
                ),
            }
        except OSError as error:
            return {"returncode": 1, "output": f"Could not execute tests: {error}"}

        return {
            "returncode": result.returncode,
            "output": (result.stdout + result.stderr).strip(),
        }

    @staticmethod
    def _parse_object(response: str, agent_name: str) -> dict[str, Any]:
        content = response.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            closing_fence = next(
                (
                    index
                    for index in range(1, len(lines))
                    if lines[index].strip().startswith("```")
                ),
                None,
            )
            if closing_fence is not None:
                content = "\n".join(lines[1:closing_fence]).strip()
        try:
            value = json.loads(content)
        except json.JSONDecodeError as error:
            value = None
            decoder = json.JSONDecoder()
            for index, character in enumerate(content):
                if character != "{":
                    continue
                try:
                    candidate, _ = decoder.raw_decode(content, index)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    value = candidate
                    break
            if value is None:
                excerpt = content[:500]
                raise WorkflowError(
                    f"{agent_name} must return a JSON object: {error}. "
                    f"Response began with: {excerpt!r}"
                ) from error
        if not isinstance(value, dict):
            raise WorkflowError(f"{agent_name} response must be a JSON object.")
        return value

    @staticmethod
    def _required_string(
        data: dict[str, Any], key: str, agent_name: str
    ) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise WorkflowError(
                f"{agent_name} response must include a non-empty '{key}' string."
            )
        return value

    @staticmethod
    def _string_mapping(
        data: dict[str, Any], key: str, agent_name: str
    ) -> dict[str, str]:
        value = data.get(key)
        if not isinstance(value, dict):
            raise WorkflowError(
                f"{agent_name} response must include a '{key}' object."
            )
        if any(not isinstance(path, str) or not isinstance(text, str) for path, text in value.items()):
            raise WorkflowError(
                f"Every '{key}' entry from {agent_name} must map a string path "
                "to string contents."
            )
        return value

    @staticmethod
    def _format_files(files: dict[str, str]) -> str:
        return "\n\n".join(
            f"## {path}\n\n```text\n{contents}\n```"
            for path, contents in sorted(files.items())
        ) or "(No files)"

    def _format_developer_output(
        self, notes: str, files: dict[str, str]
    ) -> str:
        return (
            "# Developer Output\n\n"
            f"{notes.strip()}\n\n"
            "## Generated Files\n\n"
            + "\n".join(f"- `{path}`" for path in sorted(files))
        )

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()


def create_default_orchestrator(
    workspace_path: Path,
    max_rework_cycles: int = 3,
    model: str | None = None,
) -> Orchestrator:
    provider = CopilotCLIProvider(
        executable=os.environ.get("SDAT_COPILOT_EXECUTABLE", "copilot"),
        model=model,
    )
    return Orchestrator(
        SharedWorkspace(workspace_path),
        WorkflowAgents.from_provider(provider),
        max_rework_cycles=max_rework_cycles,
    )
