from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sdat.agents import AgentError, CopilotCLIProvider, RoleAgent
from sdat.orchestrator import Orchestrator, WorkflowAgents, WorkflowError
from sdat.workspace import SharedWorkspace, WorkspaceError


class ScriptedProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return next(self.responses)


class OrchestratorTests(unittest.TestCase):
    def make_orchestrator(
        self, root: Path, responses: list[str], max_rework_cycles: int = 3
    ) -> tuple[Orchestrator, ScriptedProvider]:
        provider = ScriptedProvider(responses)
        agents = WorkflowAgents.from_provider(provider)
        return (
            Orchestrator(
                SharedWorkspace(root),
                agents,
                max_rework_cycles=max_rework_cycles,
            ),
            provider,
        )

    @staticmethod
    def response(role: str, **values: object) -> str:
        return json.dumps(values)

    def one_iteration(self, decision: str = "APPROVED") -> list[str]:
        return [
            "# Analysis\n\n## Acceptance Criteria\n- It runs.",
            self.response(
                "developer",
                technical_notes="A minimal Python app.",
                files={"app.py": "def answer():\n    return 42\n"},
            ),
            self.response(
                "tester",
                report="Checks the answer.",
                tests={
                    "test_app.py": (
                        "import unittest\n"
                        "from app import answer\n\n"
                        "class AppTests(unittest.TestCase):\n"
                        "    def test_answer(self):\n"
                        "        self.assertEqual(answer(), 42)\n"
                    )
                },
            ),
            self.response("reviewer", decision=decision, feedback="Reviewed."),
        ]

    def test_approved_workflow_persists_outputs_and_passes_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "workspace"
            orchestrator, provider = self.make_orchestrator(
                root, self.one_iteration()
            )

            state = orchestrator.run("Build a tiny answer service.")

            self.assertEqual(state["status"], "approved")
            self.assertEqual(state["iteration"], 1)
            self.assertEqual(state["test_execution"]["returncode"], 0)
            self.assertEqual(len(provider.prompts), 4)
            for artifact in (
                "requirement.md",
                "ba_output.md",
                "developer_output.md",
                "tester_output.md",
                "reviewer_output.md",
                "state.json",
                "logs/workflow.log",
            ):
                self.assertTrue((root / artifact).is_file(), artifact)
            run_id = state["run_id"]
            self.assertTrue(
                (
                    root
                    / "iterations"
                    / f"run_{run_id}"
                    / "iteration_01"
                    / "implementation"
                    / "app.py"
                ).is_file()
            )
            self.assertIn("APPROVED", (root / "reviewer_output.md").read_text())

    def test_new_execution_uses_a_fresh_iteration_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "workspace"
            first, _ = self.make_orchestrator(root, self.one_iteration())
            second, _ = self.make_orchestrator(root, self.one_iteration())

            first_state = first.run("Build a tiny answer service.")
            second_state = second.run("Build a tiny answer service.")

            self.assertNotEqual(first_state["run_id"], second_state["run_id"])
            for state in (first_state, second_state):
                artifact = (
                    root
                    / "iterations"
                    / f"run_{state['run_id']}"
                    / "iteration_01"
                    / "implementation"
                    / "app.py"
                )
                self.assertTrue(artifact.is_file())

    def test_reviewer_rework_feedback_is_used_and_preserved(self) -> None:
        responses = self.one_iteration("REWORK") + self.one_iteration("APPROVED")[1:]
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "workspace"
            orchestrator, provider = self.make_orchestrator(root, responses)

            state = orchestrator.run("Build a tiny answer service.")

            self.assertEqual(state["status"], "approved")
            self.assertEqual(state["iteration"], 2)
            self.assertEqual(state["rework_cycles"], 1)
            self.assertIn("Previous review feedback", provider.prompts[4])
            self.assertTrue(
                (
                    root
                    / "iterations"
                    / f"run_{state['run_id']}"
                    / "iteration_01"
                    / "reviewer_output.md"
                ).is_file()
            )
            self.assertTrue(
                (
                    root
                    / "iterations"
                    / f"run_{state['run_id']}"
                    / "iteration_02"
                    / "reviewer_output.md"
                ).is_file()
            )

    def test_rework_limit_marks_workflow_failed_after_three_cycles(self) -> None:
        responses = self.one_iteration("REWORK")
        for _ in range(3):
            responses.extend(self.one_iteration("REWORK")[1:])
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "workspace"
            orchestrator, _ = self.make_orchestrator(root, responses)

            state = orchestrator.run("Keep reworking.")

            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["iteration"], 4)
            self.assertEqual(state["rework_cycles"], 3)
            self.assertEqual(state["stage"], "rework_limit_reached")

    def test_failed_tests_override_reviewer_approval(self) -> None:
        responses = self.one_iteration()
        responses[2] = self.response(
            "tester",
            report="Test must fail.",
            tests={"test_app.py": "raise AssertionError('failure')\n"},
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "workspace"
            orchestrator, _ = self.make_orchestrator(
                root, responses, max_rework_cycles=0
            )

            state = orchestrator.run("Build an app.")

            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["review_decision"], "REWORK")
            self.assertIn("overridden", (root / "reviewer_output.md").read_text())

    def test_invalid_agent_json_is_reported_and_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "workspace"
            orchestrator, _ = self.make_orchestrator(
                root, ["# Analysis", "not markdown"]
            )

            with self.assertRaises(WorkflowError):
                orchestrator.run("A requirement.")

            state = json.loads((root / "state.json").read_text())
            self.assertEqual(state["status"], "failed")
            self.assertIn("JSON object", state["error"])
            raw_response = next(
                (root / "iterations").rglob("developer_response_raw.txt")
            )
            self.assertEqual(raw_response.read_text(), "not markdown")

    def test_agent_json_wrapped_in_prose_or_markdown_fence_is_parsed(self) -> None:
        expected = {"decision": "APPROVED"}

        self.assertEqual(
            Orchestrator._parse_object(
                'Here is the result:\n{"decision":"APPROVED"}\nDone.',
                "Reviewer Agent",
            ),
            expected,
        )
        self.assertEqual(
            Orchestrator._parse_object(
                '```json\n{"decision":"APPROVED"}\n```\n',
                "Reviewer Agent",
            ),
            expected,
        )

    def test_workspace_rejects_paths_that_escape_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = SharedWorkspace(Path(temporary_directory) / "workspace")

            with self.assertRaises(WorkspaceError):
                workspace.write_files(
                    workspace.root / "implementation",
                    {"..\\outside.txt": "not allowed"},
                )

    def test_role_prompt_contains_role_and_shared_workspace_context(self) -> None:
        provider = ScriptedProvider(["ok"])
        agent = RoleAgent("Tester Agent", "Test the implementation.", provider)

        result = agent.run("Requirement from the shared workspace.")

        self.assertEqual(result, "ok")
        self.assertIn("Tester Agent", provider.prompts[0])
        self.assertIn("Requirement from the shared workspace.", provider.prompts[0])

    @unittest.skipUnless(os.name == "nt", "Windows launcher behavior")
    def test_copilot_windows_shim_receives_multiline_prompt_via_stdin(self) -> None:
        prompt = "Role instructions\n\nRequirement details\n- First item"
        result = type(
            "Result",
            (),
            {"returncode": 0, "stdout": "response", "stderr": ""},
        )()
        with (
            patch(
                "sdat.agents.shutil.which",
                side_effect=lambda name: {
                    "copilot": r"C:\tools\copilot.CMD",
                    "node": r"C:\tools\node.EXE",
                }.get(name),
            ),
            patch("sdat.agents.Path.is_file", return_value=True),
            patch(
                "sdat.agents.subprocess.run",
                return_value=result,
            ) as run,
        ):
            response = CopilotCLIProvider().complete(prompt * 1000)

        self.assertEqual(response, "response")
        command = run.call_args.args[0]
        self.assertEqual(command[0], r"C:\tools\node.EXE")
        self.assertTrue(command[1].endswith("npm-loader.js"))
        self.assertLess(sum(len(part) for part in command), 1000)
        self.assertNotIn("--prompt", command)
        self.assertIn("--available-tools=", command)
        self.assertIn("--disable-builtin-mcps", command)
        self.assertIn("github-mcp-server", command)
        self.assertEqual(run.call_args.kwargs["input"], prompt * 1000)
        isolated_cwd = Path(run.call_args.kwargs["cwd"])
        self.assertTrue(isolated_cwd.name.startswith("sdat-copilot-"))
        self.assertNotEqual(isolated_cwd, Path.cwd())

    def test_empty_copilot_response_includes_cli_diagnostic(self) -> None:
        result = type(
            "Result",
            (),
            {"returncode": 0, "stdout": "", "stderr": "Model service unavailable"},
        )()
        with (
            patch("sdat.agents.shutil.which", return_value=r"C:\tools\copilot.exe"),
            patch("sdat.agents.subprocess.run", return_value=result),
        ):
            with self.assertRaisesRegex(AgentError, "Model service unavailable"):
                CopilotCLIProvider().complete("Return a JSON object.")


if __name__ == "__main__":
    unittest.main()
