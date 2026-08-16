from workflow_store import _normalise_initial_tasks


def test_newline_separated_initial_tasks_are_not_split_into_characters() -> None:
    initial_tasks = (
        "Task 1 — Retrieve the saved search\n"
        "Task 2 — Select the relevant articles\n"
        "Task 3 — Draft the summaries\n"
        "Task 4 — Format the email"
    )

    assert _normalise_initial_tasks(initial_tasks) == [
        "Task 1 — Retrieve the saved search",
        "Task 2 — Select the relevant articles",
        "Task 3 — Draft the summaries",
        "Task 4 — Format the email",
    ]


def test_single_initial_task_string_remains_one_task() -> None:
    assert _normalise_initial_tasks("Retrieve the saved search") == ["Retrieve the saved search"]
