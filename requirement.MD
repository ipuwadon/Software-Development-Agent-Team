# **Software Development Agent Team (SDAT)**

**Project Overview
- SDAT is a proof-of-concept (PoC) project designed to simulate an AI Software Development Team using multiple AI agents.
- The objective is to build an autonomous workflow where AI agents collaborate to transform a business requirement into a software solution.
- The workflow should be orchestrated automatically and support iterative feedback loops unit the final deliverable is approved.

### **Business Goal**
1. Receiving a business requirement.
2. Performing requirement analysis.
3. Creating implementations tasks.
4. Generating source code.
5. Performing testing activities.
6. Reviewing implementation quality.
7. Iterating unit the implementation is approved.

The entire process should be automated through an Orchestrator.
A human should only be required to provide the initial requirement and review the final output.

### **Agent Roles**
- Business Analyst Agent
    Responsibilities
    - Analyze business requirements.
    - Create user stories.
    - Create acceptance criteria.
    - Break requirements into implementation tasks.
    - Produce structured analysis documents.
    Output
    - User Stories
    - Acceptance Criteria
    - Task Breakdown
- Developer Agent
    Responsibilities
    - Read BA outputs.
    - Design the solution.
    - Generate source code.
    - Refine implementation based on review feedback.
    - Produce executable artifacts.
    Output
    - Source Code
    - Technical Notes
- Tester Agent
    Responsibilities
    - Read source code.
    - Generate test cases.
    - Validate implementation against acceptance criteria.
    - Identify issues and potential defects.
    - Produce test reports.
    Output
    - Test Cases
    - Test Results
    - Defect Reports
- Reviewer Agent
    Responsibilities
    - Review implementation.
    - Evaluate code quality.
    - Verify functional coverage.
    - Validate test results.
    - Decide whether implementation is approved.
    Output
    - APROVED or REWORK
    - Detailed feedback.

### **Workflow**
```text
Requirement
    |
    v
Business Analyst
    |
    v
Developer
    |
    v
Tester
    |
    v
Reviewer
    |
    +----------------+
    |                |
    | APPROVED       | REWORK
    |                |
    v                |
Done                 |
                     v
                  Developer
                     |
                     v
                   Tester
                     |
                     v
                  Reviewer
```

### **Orchestrator Requirements**
The system shall provide a central Orchestrator responsible for:
- Managing workflow execution.
- Invoking agents in the correct sequence.
- Managing shared state.
- Handling approval and rejection decisions.
- Supporting workflow loops.
- Determining workflow completion.
The Orchestrator should be implemented in Python.

### **Shared State**
The system shall maintain a shared workspace accessible by all agents.
The shared workspace should contain:
- Requirement
- BA Output
- Developer Output
- Test Result
- Review Result
- Workflow Status
- Execution Logs
Agents should only communicate through the shared workspace.
Direct agent-to-agent communication should be avoided.

### **Workspace Structure**
Suggested structure:
```text
workspace/
|__ requirement.md
|__ ba_output.md
|__ developer_output.md
|__ tester_output.md
|__ reviewer_output.md
|__ state.json
|__ logs/
```

### **Automation Requirements**
The entire workflow shall execute automatically after the requirement is provided.
Expected execution flow:
1. User provides requirement.
2. Orchestrator invokes BA Agent.
3. BA output is stored.
4. Orchestrator invokes Devloper Agent.
5. Developer output is stored.
6. Orchestrator invokes Tester Agent.
7. Tester output is stored.
8. Orchestrator invokes Reviewer Agent.
9. Reviewer detemines:
    - APPROVED
    - REWORK
10. If APPROVED:
    - Workflow Complete.
11. If REWORK:
    - Return to Developer Agent.
    - Continue until approval is reached.

### **Technical Constraints**
- Python implementation preferred.
- All agents may use the same LLM (Can we use Github Copilot).
- Different agent behavior should be driven by role-specific prompts.
- Agent outputs should be persisted as files.
- Workflow state should be traceable for debugging.

### **Future Enhancements (Out of Scope for POC)**
- Jira Integration
- GitHub Integration
- MCP Integration
- RAG Knowledge Base
- Human Approval Gates
- Multiple Developer Agents
- CI/CD Integration
- Agent Performance Metrics

### **Success Criteria**
The project is successful when:
- Multiple AI agents can collaborate.
- Workflow is controlled by an Orchestrator.
- Shared State is maintained correctly.
- Review feedback can trigger rework loops.
- Workflow can complete without human intervention.
- Final output is produced after approval.