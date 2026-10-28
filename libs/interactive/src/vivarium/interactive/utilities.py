"""
=========
Utilities
=========

Notebook helpers used by :class:`~vivarium.interactive.simulation.Simulation`.

"""
from collections.abc import Generator, Sequence


def run_from_ipython() -> bool:
    """Taken from https://stackoverflow.com/questions/5376837/how-can-i-do-an-if-run-from-ipython-test-in-python"""
    try:
        __IPYTHON__  # type: ignore [name-defined]
        return True
    except NameError:
        return False


def log_progress(
    sequence: Sequence[int],
    every: int | None = None,
    size: int | None = None,
    name: str = "Items",
) -> Generator[int, None, None]:
    """Taken from https://github.com/alexanderkuk/log-progress"""
    from IPython.display import display
    from ipywidgets import HTML, IntProgress, VBox

    is_iterator = False
    if size is None:
        try:
            size = len(sequence)
        except TypeError:
            is_iterator = True
    if size is not None:
        if every is None:
            if size <= 200:
                every = 1
            else:
                every = int(size / 200)  # every 0.5%
    else:
        assert every is not None, "sequence is iterator, set every"

    if is_iterator:
        progress = IntProgress(min=0, max=1, value=1)
        progress.bar_style = "info"
    else:
        progress = IntProgress(min=0, max=size, value=0)
    label = HTML()
    box = VBox(children=[label, progress])
    display(box)  # type: ignore [no-untyped-call]

    index = 0
    failed = False
    try:
        for index, record in enumerate(sequence, 1):
            if index == 1 or index % every == 0:
                if is_iterator:
                    label.value = "{name}: {index} / ?".format(name=name, index=index)
                else:
                    progress.value = index
                    label.value = "{name}: {index} / {size}".format(
                        name=name, index=index, size=size
                    )
            yield record
    except Exception:
        failed = True
        progress.bar_style = "danger"
        raise
    finally:
        # Also runs when the caller stops iterating early, as run_until does once its
        # condition is met.
        if not failed:
            progress.bar_style = "success"
            progress.value = index
            label.value = "{name}: {index}".format(name=name, index=str(index or "?"))
