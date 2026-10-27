"""Acceptance tests for the python_step frontend (``core.frontend.python_step``)."""


def test_python_step_returns_a_task_wired_to_the_registry() -> None:
    """python_step returns one Task whose product and inputs are the registry's nodes."""


def test_running_a_step_calls_the_target_and_writes_the_product() -> None:
    """Running the built task calls the target with loaded inputs and autosaves its DataFrame."""


def test_python_step_carries_resources_and_env() -> None:
    """The resources dict and env passed to python_step are carried on the Task."""


def test_two_steps_run_end_to_end_through_a_shared_key() -> None:
    """A producer and a consumer wired through one key run in order and the data flows."""


def test_unknown_input_key_surfaces_a_did_you_mean() -> None:
    """An input key with no producer or root input raises the registry's did-you-mean."""


def test_unsupported_product_suffix_is_rejected() -> None:
    """A produces key whose suffix is not a supported save format is rejected."""


def test_a_step_that_writes_no_product_is_a_failure() -> None:
    """A target whose product is missing after the body runs is reported as a failure."""
