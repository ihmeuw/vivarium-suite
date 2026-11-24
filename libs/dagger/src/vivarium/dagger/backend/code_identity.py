"""The code part of a step's identity: the first-party files that the step imports.

A step's code part is the set of first-party files that its starting point imports,
directly or through other files, each with a hash of its code. The import analysis
reads the files and imports nothing. It follows every ``import`` statement at any depth,
including imports inside functions, but not imports under ``if TYPE_CHECKING:`` and not
imports inside the ``if __name__ == "__main__":`` block of a file other than the starting
point. It resolves each name against a search path, in order, as the interpreter would.

First-party code is code in the pipeline's declared repository root, or in the source of
an editable install. An editable install is recognised in either of the two forms that
setuptools writes into a ``site-packages`` directory on the search path: a ``.pth`` file
that adds a source directory to the search path (compat mode), or an editable finder
module, ``__editable___*_finder.py``, whose literal ``MAPPING`` maps each top-level
package to its source directory, which an import of that package then resolves to.
Neither form is imported or run. Code that the environment installs,
including anything under a ``site-packages`` or ``dist-packages`` directory or a virtual
environment inside the repository, is not first-party; it belongs to the environment
part of the identity, and the analysis stops there. The standard library and built-in
modules are installed code.

A module that opens a data file while it is imported, at the module's top level, by a
path built only from the module's own location (``__file__``) and string literals, adds
that data file to the code part. A Python file is hashed with
:func:`~vivarium.dagger.backend.code_hash.python_file_hash`, and a data file by its bytes.
Each file is named by its path relative to the root that holds it, so the same content
in two checkouts at different paths gives the same code part.

The code part is not established when the starting point is outside every first-party
root, or when an import resolves to a file that is neither first-party nor installed.
A node whose state is not established makes its step run on every build.

"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CodePart:
    """The first-party files of a step's code part, or the reason it is not established."""

    files: Mapping[str, str]
    """Each first-party file's name, relative to the root that holds it, mapped to the
    digest of its code (a Python file) or of its bytes (a data file). Empty when the code
    part is not established."""
    reason: str | None = None
    """Why the code part is not established, naming the file that caused it, or ``None``
    when it is established."""

    @property
    def established(self) -> bool:
        """Whether the code part could be computed."""
        return self.reason is None


def code_part(
    start: Path | str,
    repository_root: Path | str | None,
    search_path: Sequence[str],
) -> CodePart:
    """Return the code part of a step whose starting point is the Python file ``start``.

    Parameters
    ----------
    start
        The Python file the step's code starts in, such as the file of the module that
        defines a step's function. The starting file's own ``__main__`` block is followed.
    repository_root
        The pipeline's declared repository root, or ``None`` when the pipeline declares
        none; then only the directories of editable installs are first-party.
    search_path
        The directories that imports are resolved against, in order, as in
        :data:`sys.path`. Editable installs are recognised from the ``site-packages``
        directories on it, and entries that are not directories, such as an editable
        finder's path hook, are ignored.

    Returns
    -------
        The code part. It is not established when ``start`` is outside every
        first-party root, or when an import resolves to a file outside the first-party
        roots that is not installed code. A first-party file that cannot be parsed is
        listed with its bytes hash, and the analysis follows no imports from it.
    """
    raise NotImplementedError


@dataclass
class CodeIdentityNode:
    """A step's identity node, whose state is a hash of the step's code part.

    The node implements pytask's node protocol. Its state is computed from
    :func:`code_part` for the file ``start``, with the pipeline's ``repository_root`` and
    the build process's :data:`sys.path`. When the code part is not established, the state
    is a new string on every call, so the step runs on every build. The state is never
    ``None`` or empty, and :meth:`state` never raises. Within one process the code part of
    one starting point is analysed once for each content of the files it lists.

    Two nodes with the same ``name``, ``start`` and ``repository_root`` are equal.
    """

    name: str
    """Identifier of the node, unique within its task, such as ``"<step>::code"``."""
    start: Path
    """The resolved Python file the step's code starts in."""
    repository_root: Path | None = None
    """The pipeline's resolved repository root, or ``None`` when it declares none."""
    attributes: dict[Any, Any] = field(default_factory=dict, compare=False)
    """pytask node attributes; unused, kept to satisfy the node protocol."""

    @property
    def signature(self) -> str:
        """Return a hash of the node's name, which is stable across processes."""
        raise NotImplementedError

    def state(self) -> str:
        """Return a hash of the code part, or a new string when it is not established."""
        raise NotImplementedError

    def load(self, is_product: bool = False) -> "CodeIdentityNode":
        """Return the node itself; a step's function does not receive it."""
        raise NotImplementedError

    def save(self, value: Any) -> None:
        """Refuse to save, since the node is read-only.

        Raises
        ------
        NotImplementedError
            Always.
        """
        raise NotImplementedError
