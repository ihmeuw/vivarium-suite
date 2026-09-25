from vivarium.cluster_tools.core.backend.task import Task


def test_task_accepts_callable_run() -> None:
    """Construct a Task whose run is a Python callable and assert it validates."""
    pass


def test_task_accepts_command_string_run() -> None:
    """Construct a Task whose run is a shell-command string and assert it validates."""
    pass


def test_task_accepts_structurally_conforming_nodes() -> None:
    """Accept an in-test node class with the six PNode members and no pytask base."""
    pass


def test_task_stores_nodes_by_reference() -> None:
    """Assert a node shared between one Task's outputs and another's inputs is one object."""
    pass


def test_task_construction_calls_no_node_methods() -> None:
    """Construct a Task from nodes whose state, load, and save raise; assert it succeeds."""
    pass


def test_task_rejects_empty_name() -> None:
    """Reject an empty name with an error naming the field."""
    pass


def test_task_rejects_run_that_is_neither_callable_nor_str() -> None:
    """Reject a run that is neither callable nor str with an error naming the task."""
    pass


def test_task_rejects_non_node_values() -> None:
    """Reject a bare Path in inputs, outputs, or code_id, naming the task and the key."""
    pass


def test_task_rejects_node_missing_pnode_member() -> None:
    """Reject a node lacking the attributes member, the five-member PoC shape."""
    pass


def test_task_rejects_reserved_output_key() -> None:
    """Reject an outputs key named "return", which pytask reserves for return values."""
    pass


def test_check_unique_task_names_raises_on_duplicates() -> None:
    """Raise from the module-level uniqueness check when two Tasks share a name."""
    pass


def test_check_unique_task_names_accepts_distinct_names() -> None:
    """Accept an iterable of Tasks with distinct names without raising."""
    pass
