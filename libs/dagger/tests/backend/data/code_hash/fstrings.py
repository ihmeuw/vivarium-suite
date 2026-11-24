"""Format values with f-strings."""

WIDTH = 10
PRECISION = 3


def describe(name: str, value: float, items: list[str]) -> str:
    """Return a description of a value."""
    header = f"{name}: {value!r:>{WIDTH}}"
    number = f"{value:{WIDTH}.{PRECISION}f}"
    debug = f"{value=}"
    joined = f"{', '.join(items)} ({len(items)} items)"
    nested = f"{'yes' if value > 0 else 'no'} {f'{name!s}'}"
    braces = f"{{literal}} {name.upper()}"
    return "\n".join([header, number, debug, joined, nested, braces])
