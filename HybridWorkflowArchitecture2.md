from pathlib import Path

content = """# KoreStack Native Python Workflow Architecture

## Purpose

KoreStack needs a reliable way to combine deterministic application logic with language-model work.

The existing agent-centric approach is useful for exploratory and interactive work.

It is less suitable for scheduled or repeatable jobs where the procedural sequence must be stable.

A scheduled job should not depend on the LLM remembering which tool to call next.

It should not depend on the model correctly interpreting procedural instructions.

It should not publish a clarification response when the intended output was a report.

The revised architecture treats native Python as the orchestration layer.

The LLM remains a reasoning, interpretation and text-generation component.

Python owns sequence, state, validation, retries, branching and publication.

KoreStack services remain separate services.

Initial services include:

- KoreData;
- KoreDocs;
- KoreChat;
- KoreComms;
- KoreCron;
- other fixed KoreStack services added over time.

The workflow layer should integrate with these services directly.

MCP is no longer required as the universal internal application boundary.

Existing MCP paths may remain temporarily where they are already working.

The target is one underlying capability implementation with multiple possible adapters.

Those adapters may include:

- native Python calls;
- LLM JSON tool definitions;
- temporary MCP exposure;
- UI actions;
- external APIs.

The native Python implementation is the primary deterministic path.

---

## Core architectural principle

The system should separate deterministic orchestration from probabilistic reasoning.

Python is responsible for deterministic orchestration.

The LLM is responsible only for work that benefits from language-model capability.

A workflow should therefore look like ordinary Python.

Example:

```python
def daily_ai_news():
    workspace.clear()

    comms.bind(
        to_list="AI-News",
        subject="AI News Daily",
        start_paused=True,
    )

    news = koredata.saved_search("AINews")

    require_nonempty(news)

    report = korechat.prompt(
        "Select the five most significant stories and write a complete HTML email.",
        inputs={"news": news},
    )

    require_html(report, min_length=1000)

    comms.publish(report)