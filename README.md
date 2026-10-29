# Software Development Agent Team (SDAT)

SDAT is a Python proof of concept that runs a shared-workspace workflow with
Business Analyst, Developer, Tester, and Reviewer agents. GitHub Copilot CLI
provides the model responses; role-specific prompts define each agent's job.
Agents exchange information through persisted workspace artifacts, not direct
agent-to-agent calls.

## Requirements

- Python 3.10 or newer
- GitHub Copilot CLI installed and authenticated
- For the npm installation of Copilot CLI: a compatible Node.js version
  (Copilot CLI 1.0.105 requires Node.js 24 or newer)

## Windows setup

Install and authenticate GitHub Copilot CLI:

```powershell
npm install -g @github/copilot
copilot
```

Inside Copilot, enter `/login` if sign-in is required and follow the browser
instructions. Exit Copilot before running SDAT. Your account must have Copilot
access. See the [official installation guide](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli)
for current installation options and prerequisites.

From the SDAT project directory, create and activate a virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

A virtual environment is recommended, not required. SDAT has no third-party
Python runtime dependencies. Copilot CLI is installed separately and must be
available on `PATH`.

If PowerShell prevents activation, use the environment's Python directly:

```powershell
.\.venv\Scripts\python.exe -m sdat --help
```

## Run

Prepare a UTF-8 Markdown or text file containing the requirements for the
project you want to generate. The repository's `requirement.MD` describes SDAT
itself; replace that input path with your own project's requirement.

From the SDAT project directory:

```powershell
python -m sdat run --requirement "C:\Projects\MyApp\requirement.md" --workspace "C:\Projects\MyApp\sdat-output"
```

After installing SDAT, the equivalent command is:

```powershell
sdat run --requirement "C:\Projects\MyApp\requirement.md" --workspace "C:\Projects\MyApp\sdat-output"
```

You can provide the requirement as text instead:

```powershell
python -m sdat run --requirement-text "Build a small Python task tracking CLI."
```

The requirement file can be anywhere. Relative paths such as `.\requirement.md`
are resolved from your terminal's current directory. `--workspace` selects the
output directory, which SDAT creates automatically; it is not required to be
named `workspace`. A dedicated output subdirectory is recommended.

### Workflow and rework

```text
Requirement -> Business Analyst -> Developer -> Tester -> Reviewer
                                      ^                    |
                                      +------ REWORK ------+
                                                           |
                                                        APPROVED
```

The workflow runs one initial implementation and permits up to three reviewer-
requested rework cycles by default. Change the limit with
`--max-rework-cycles <count>`; a negative count is invalid. If the limit is
reached without approval, SDAT stops with a `failed` status and preserves the
review and execution history. Set `--model <identifier>` to select a Copilot
model. Set `SDAT_COPILOT_EXECUTABLE` if the CLI executable is not named
`copilot` or is not on `PATH`.

For example, ten rework cycles allow up to eleven implementation attempts:

```powershell
python -m sdat run --requirement "C:\Projects\MyApp\requirement.md" --workspace "C:\Projects\MyApp\sdat-output" --max-rework-cycles 10
```

SDAT stops early if approved. A higher limit does not guarantee approval and
can increase model usage and execution time. Each invocation starts a new
workflow; it does not resume a failed run.

The CLI must be able to run unattended. SDAT invokes Copilot CLI with
`--allow-all-tools` as required for non-interactive prompting, but passes an
empty `--available-tools` list and disables its built-in MCP servers. SDAT
sends prompts through standard input, not command-line arguments, avoiding
Windows command-line length limits even for large implementation contexts. Agents
return generated file contents in their responses; the orchestrator writes
those files to the workspace. Copilot runs from an isolated temporary working
directory so it does not inspect the SDAT repository by default. Generated
application and test files are executed locally by the Tester stage, so only
process requirements you trust.

## Shared workspace

The default `workspace/` directory contains:

```text
workspace/
|-- requirement.md
|-- ba_output.md
|-- developer_output.md
|-- tester_output.md
|-- reviewer_output.md
|-- state.json
|-- iterations/
|   |-- run_<id>/
|       |-- iteration_01/
|           |-- implementation/
|           |-- tests/
|           |-- developer_response_raw.txt
|           |-- tester_response_raw.txt
|           |-- reviewer_response_raw.txt
|           |-- developer_output.md
|           |-- tester_output.md
|           |-- reviewer_output.md
|-- logs/
    |-- workflow.log
```

`state.json` tracks the stage, iteration, reviewer decision, test execution,
timestamps, and history. Iteration artifacts are retained when the developer
is asked to rework the implementation. Previous run directories are retained;
the top-level reports and state describe the latest run.

### Finding and running the generated application

If `--workspace` is `C:\Projects\MyApp\sdat-output`, the latest review is
`C:\Projects\MyApp\sdat-output\reviewer_output.md`.

Read `run_id` and `iteration` in `state.json` to find the final implementation:

```text
<workspace>\iterations\run_<run_id>\iteration_<NN>\implementation\
```

For example, iteration `2` is stored as `iteration_02`. Use the iteration from
an `approved` run, not necessarily the first iteration. Follow the generated
application's README or technical notes to run it; entry-point names vary by
project. SDAT does not automatically launch the delivered application.

## Current PoC limitations

- Approval is the model reviewer's assessment, not a guarantee of correctness.
  Review generated code before using it, especially with real data.
- The test runner executes Python `unittest` files matching `test_*.py` from
  the tester's separate test directory. It does not automatically execute all
  developer-supplied test suites or support arbitrary language test runners.
- Generated tests run with the Python interpreter that launched SDAT.
  Dependencies required by the generated application are not installed
  automatically.
- Tests that infer the application root from their own file location can fail
  because the implementation and tester tests are sibling directories.
- The reviewer receives implementation files and the tester report/execution
  output, but not the tester test files themselves. The reworking developer
  receives the requirement, BA analysis, and previous review feedback, not the
  previous implementation or full test results. These context limitations can
  cause repeated rework.
- There is no resume command or sandbox for generated code. A Python virtual
  environment isolates packages, not filesystem access.

## Troubleshooting

- **Rework limit reached:** inspect `reviewer_output.md`, `tester_output.md`,
  and the final iteration's files. Correct the underlying issue rather than
  assuming additional cycles will resolve it.
- **Invalid agent JSON:** inspect the relevant `*_response_raw.txt` file in
  the iteration directory. Agent responses are saved before parsing.
- **Copilot exits or returns no text:** read the reported diagnostic and match
  the failure time to a Copilot CLI log under `%USERPROFILE%\.copilot\logs`.
  The newest log may belong to another Copilot process.
- **Python cannot find SDAT:** run from the SDAT project directory or install
  it into the active environment with `python -m pip install -e .`.

## Validate

Run the standard-library test suite:

```powershell
python -m unittest discover -s tests -v
```

The regression suite uses scripted model responses and mocked process calls;
passing it does not establish that a live Copilot workflow will be approved.
