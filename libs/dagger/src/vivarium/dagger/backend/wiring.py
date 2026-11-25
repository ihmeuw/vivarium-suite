"""Producer/consumer wiring: resolve declared file keys to nodes.

A pipeline names each file it reads or writes with a logical key. An output registers its key
against one resolved file node under the run's output directory, or against a run
directory node for a key registered by
:meth:`~vivarium.dagger.backend.wiring.FileRegistry.register_directory_output`.
An input naming that key resolves to the producer's own node. pytask draws the
producer-to-consumer edge from the node's path: a path node's signature is a hash of its
path, and pytask's graph keeps one node per signature. Sharing the producer's node keeps
the node's type, which matters for a run directory: a plain path node for the same
directory, added after the run directory node, replaces it in the graph and makes the
build fail with ``IsADirectoryError``. The output root and run name namespace produced
paths, so two runs write to distinct directories.

This is a plain registry, not a container class: a pipeline builds its tasks into
a list and hands them to :func:`~vivarium.dagger.backend.build.build`.
Register every output before resolving inputs. The guards here are the ones pytask
cannot make for us -- a hard error when an output's path, with symbolic links followed,
is not strictly inside the run's output directory (a step would otherwise write files
that other runs share or that nobody declared), a hard error when one output's path is
inside another's (a run directory is replaced on every run, and a file cannot hold
another file), a hard error when two keys resolve to one path (which pytask
would otherwise silently merge), a hard error when a step declares a root input's key
as its output (which would otherwise shadow the root input for every consumer), a
hard error when an output's path is a root input's path or a folder that holds one
(the step would otherwise overwrite or replace a source file), a hard error when one key is registered as both a file output and a run directory
output, and a did-you-mean on a mistyped key. Root inputs may point anywhere. An
unresolved input (no producer and no root-input file) is caught here with a
did-you-mean; everything heavier (a cycle, two tasks claiming one output) is left to
pytask, which fails safe.
"""

import difflib
import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path, PurePosixPath

from pytask import PathNode

from vivarium.dagger.backend.nodes import RunDirectoryNode, file_node, run_directory_node

FileKey = str
"""A logical file name, resolved to a node under the run's output directory.

The node is a file node, or a run directory node for a key registered by
:meth:`~vivarium.dagger.backend.wiring.FileRegistry.register_directory_output`.
"""

_LISTED_KEYS = 10
"""How many registered keys an unresolved input's message lists when no key is close."""


class WiringError(Exception):
    """A definition-time error in how a pipeline's files are wired or supplied."""


class FileRegistry:
    """Resolve declared file keys to nodes for one run.

    A key resolves to a file node, or to a run directory node when it is registered by
    :meth:`register_directory_output`. An input gets the node that its producer
    registered, so the node keeps its type, or a file node for a declared root input;
    pytask links a producer and its consumer by the node's path.

    Parameters
    ----------
    output_root
        Directory under which produced files live.
    run_name
        Names this run's output subdirectory, so two runs do not collide.
    root_inputs
        Source files the pipeline consumes but does not produce, keyed by the same
        logical key an input names. Resolved to a file node when the file exists.
    """

    def __init__(
        self,
        output_root: Path | str,
        run_name: str,
        root_inputs: Mapping[FileKey, Path | str] | None = None,
    ) -> None:
        self._run_dir = Path(output_root).resolve() / run_name
        given = root_inputs or {}
        self._root_inputs = {key: Path(p).resolve() for key, p in given.items()}
        # normpath drops '..' so the declared path's parents are real folders above it.
        declared = {
            key: Path(os.path.normpath(Path(p).absolute())) for key, p in given.items()
        }
        self._producers: dict[FileKey, PathNode] = {}
        # Root-input paths are seeded so key_for names a root input at its path. An
        # output never lands on one: check_not_root_input refuses it first.
        self._key_by_path: dict[Path, FileKey] = {}
        # Each folder that holds a root input, by its resolved path or its path as
        # declared, mapped to the first root input declared under it and that path.
        self._root_under: dict[Path, tuple[FileKey, Path]] = {}
        for key, source in self._root_inputs.items():
            self._key_by_path.setdefault(source, key)
            for path in (source, declared[key]):
                for parent in path.parents:
                    self._root_under.setdefault(parent, (key, path))
        # Each folder that holds a registered output, up to the run directory, mapped to
        # one output under it, so a new output that contains one is found in one lookup.
        self._key_under: dict[Path, FileKey] = {}

    def path_for(self, key: FileKey) -> Path:
        """Return the resolved path a produced ``key`` resolves to for this run."""
        return (self._run_dir / key).resolve()

    def key_for(self, path: Path | str) -> FileKey | None:
        """Return the key of the registered output or root input at ``path``, or ``None``.

        ``path`` is resolved first, so a relative path or one through a symlink still
        matches.

        Parameters
        ----------
        path
            A file path, such as a node's path.

        Returns
        -------
            The output key or root-input key registered at ``path``, or ``None`` if no
            key is registered there.
        """
        return self._key_by_path.get(Path(path).resolve())

    def check_not_root_input(self, path: Path | str, label: str) -> None:
        """Refuse an output at ``path`` that is, or is a folder that holds, a root input.

        ``path`` is resolved first, as for :meth:`key_for`, and compared with the
        resolved path of each declared root input. A folder is also compared with each
        root input's path as declared, made absolute, so a folder that holds a root
        input that is a symbolic link to a file elsewhere is refused too. The step
        methods' outputs go through this check when they are registered, and
        :meth:`~vivarium.dagger.frontend.builder.Pipeline.add_task` calls it for each
        output of a hand-built task, so no output can overwrite or replace a root input.

        Parameters
        ----------
        path
            The output's path.
        label
            How the message names the output, such as ``repr`` of its key, or of its
            path for an output that has no key.

        Raises
        ------
        WiringError
            If ``path`` is a declared root input's path: when ``label`` is ``repr`` of
            the root input's key, the message reads ``output <root key> is a declared
            root input at <path>; a step cannot also produce it.``, and otherwise it
            reads ``output <label> at <path> is the declared root input <root key>; a
            step cannot also produce it.`` If ``path`` is a folder that holds a declared
            root input, at any depth: the message reads ``output <label> at <path> holds
            the declared root input <root key> at <root path>; a step's output cannot
            hold a root input.``, with the first such root input in the order they were
            declared, and ``<root path>`` its resolved path or, when only that is inside
            the folder, its path as declared. When several root inputs share a path, the
            first declared is named.
        """
        resolved = Path(path).resolve()
        key = self._key_by_path.get(resolved)
        if key is not None and key in self._root_inputs:
            if label == repr(key):
                raise WiringError(
                    f"output {key!r} is a declared root input at {resolved}; a step cannot "
                    "also produce it."
                )
            raise WiringError(
                f"output {label} at {resolved} is the declared root input {key!r}; a step "
                "cannot also produce it."
            )
        held = self._root_under.get(resolved)
        if held is not None:
            key, source = held
            raise WiringError(
                f"output {label} at {resolved} holds the declared root input {key!r} at "
                f"{source}; a step's output cannot hold a root input."
            )

    def check_output(self, key: FileKey) -> Path:
        """Return ``key``'s path, raising as :meth:`register_output` would for it.

        Nothing is registered, so a step with several outputs can check every key before
        it registers any.

        Parameters
        ----------
        key
            The output's logical key.

        Returns
        -------
            The absolute path that ``key`` resolves to.

        Raises
        ------
        WiringError
            In each case in which :meth:`register_output` raises it for ``key``, with the
            same message.
        """
        resolved = self._claim_output_path(key)
        if isinstance(self._producers.get(key), RunDirectoryNode):
            raise WiringError(
                f"output {key!r} is already registered as a run directory; a step cannot "
                "also produce it as a file."
            )
        return resolved

    def register_output(self, key: FileKey) -> PathNode:
        """Register ``key`` as produced by a step and return its file node.

        Registering the same key again returns the same node, so two tasks claiming
        one output is left for pytask to reject.

        Parameters
        ----------
        key
            The output's logical key.

        Returns
        -------
            The file node the key resolves to, shared with any consumer of the key.

        Raises
        ------
        WiringError
            If ``key`` is itself a declared root input, which a step cannot also
            produce; if ``key``'s path is a declared root input's path or a folder that
            holds one, with the messages of :meth:`check_not_root_input`; if ``key``'s
            path, with symbolic links followed, is not strictly inside the run's output
            directory: when it is that directory itself, as for ``.`` or an empty key,
            the message names the key and the directory and says that an output must be
            a file or folder inside it, and when it is outside, as for a key with a
            ``..`` that leaves it or an absolute key outside it, the message names the
            key, its path and the run's output directory, and says to use a key that
            stays inside it, or a root input for a file outside the run; if another
            output's key already resolves to this key's path; if ``key``'s path is
            inside the path of another registered output, or contains one (the message
            names both keys and both paths, and says that a run directory is replaced
            on every run when the outer output is a run directory, or that one output
            cannot hold another otherwise); or if ``key`` is already registered as a directory output by
            :meth:`register_directory_output`. The last message names the key.
            Registering the same key again is not an error.
        """
        resolved = self.check_output(key)
        node = self._producers.get(key)
        if node is None:
            node = file_node(resolved)
            self._record(key, resolved, node)
        return node

    def register_directory_output(self, key: FileKey, sentinel: str) -> RunDirectoryNode:
        """Register ``key`` as a run directory produced by a step and return its node.

        The directory is at :meth:`path_for` of ``key``, and its state is the content of
        its ``sentinel`` file, as :class:`~vivarium.dagger.backend.nodes.RunDirectoryNode`
        describes. A consumer names ``key`` as its input, and :meth:`resolve_input`
        returns this same node, so the consumer receives the run directory node, which
        loads as the directory's path. pytask runs the consumer after the producer
        because the two steps name the same path. :meth:`key_for` of the directory's
        path returns ``key``. Registering the same key again returns the same node.

        Parameters
        ----------
        key
            The run directory's logical key.
        sentinel
            The file name, inside the directory, that the producing command writes last.

        Returns
        -------
            The run directory node the key resolves to, shared with any consumer of the
            key.

        Raises
        ------
        WiringError
            If ``key`` is a declared root input, if the directory's path is a declared
            root input's path or holds one, if the directory's path is not strictly
            inside the run's output directory, if another output's key already resolves
            to the directory's path, or if the directory is inside another registered
            output or contains one, with the same messages as :meth:`register_output`;
            or if ``key`` is already registered as a file output by
            :meth:`register_output`, with a message that names the key.
        """
        resolved = self._claim_output_path(key, directory=True)
        node = self._producers.get(key)
        if node is None:
            directory = run_directory_node(resolved, sentinel)
            self._record(key, resolved, directory)
            return directory
        if not isinstance(node, RunDirectoryNode):
            raise WiringError(
                f"output {key!r} is already registered as a file; a step cannot also "
                "produce it as a run directory."
            )
        return node

    def _claim_output_path(self, key: FileKey, directory: bool = False) -> Path:
        """Return ``key``'s path, refusing it if no output may have that key or path."""
        source = self._root_inputs.get(key)
        if source is not None:
            raise WiringError(
                f"output {key!r} is a declared root input at {source}; a step cannot "
                "also produce it."
            )
        resolved = self.path_for(key)
        run_dir = self.path_for(".")
        if resolved == run_dir:
            raise WiringError(
                f"output {key!r} resolves to the run's output directory {run_dir} itself; "
                "an output must be a file or folder inside it."
            )
        if run_dir not in resolved.parents:
            raise WiringError(
                f"output {key!r} resolves to {resolved} (with symbolic links followed), "
                f"which is not inside the run's output directory {run_dir}; use a key that "
                "stays inside it, or declare a file outside the run as a root input."
            )
        self.check_not_root_input(resolved, repr(key))
        claimant = self._key_by_path.get(resolved)
        if claimant is not None and claimant != key:
            raise WiringError(
                f"keys {claimant!r} and {key!r} resolve to one path {resolved}."
            )
        self._check_nesting(key, resolved, run_dir, directory)
        return resolved

    def _check_nesting(
        self, key: FileKey, path: Path, run_dir: Path, directory: bool
    ) -> None:
        """Refuse ``path`` if it is inside another registered output's path or holds one."""
        for parent in _folders_between(path, run_dir):
            outer = self._key_by_path.get(parent)
            if outer is not None and outer in self._producers:
                is_run_dir = isinstance(self._producers[outer], RunDirectoryNode)
                raise _nesting_error((key, path), (outer, parent), is_run_dir)
        inner = self._key_under.get(path)
        if inner is not None and inner != key:
            raise _nesting_error((inner, self.path_for(inner)), (key, path), directory)

    def _record(self, key: FileKey, path: Path, node: PathNode) -> None:
        """Register ``node`` as ``key``'s output at ``path``."""
        self._producers[key] = node
        self._key_by_path[path] = key
        for parent in _folders_between(path, self.path_for(".")):
            self._key_under.setdefault(parent, key)

    def resolve_input(self, key: FileKey) -> PathNode:
        """Resolve an input ``key`` to the node that produces it, or a root-input file.

        Parameters
        ----------
        key
            The input's logical key.

        Returns
        -------
            The node a producer registered for ``key``, shared so that the consumer's
            input keeps the producer's node type, such as a run directory node (pytask
            draws the edge from the node's path), or a file node for a declared root
            input that exists on disk.

        Raises
        ------
        WiringError
            If ``key`` has no registered producer and no root-input file on disk. The
            message for a key that is neither produced nor a root input reads
            ``input '<key>' is unresolved: no registered producer and no root input.``
            and ends with a did-you-mean suggestion, ``Did you mean '<close key>'?``,
            when a registered key is close. Closeness is judged on the file name
            without its suffix, among the registered keys in the same directory with
            the same suffix, so a shared directory and ``.parquet`` alone make no key
            close; when no registered key shares both, or ``key`` has no suffix, whole
            keys are compared. When no key is close,
            the message ends with the keys registered so far instead, produced keys
            and root-input keys together in sorted order: ``The keys registered so far
            are 'a.csv', 'b.csv'.``, or the first ten followed by ``, and <N> more.``
            when there are more than ten, or ``No keys are registered so far.`` when
            there are none. A step's producers are registered before the step, so the
            list holds only the keys of root inputs and of steps declared before it.
        """
        produced = self._producers.get(key)
        if produced is not None:
            return produced
        source = self._root_inputs.get(key)
        if source is not None:
            if not source.exists():
                raise WiringError(
                    f"input {key!r} is unresolved: declared as a root input but no file "
                    f"at {source}."
                )
            return file_node(source)
        known = sorted({*self._producers, *self._root_inputs})
        hint = _closest_key(key, known)
        ending = f"Did you mean {hint!r}?" if hint is not None else _registered_keys(known)
        raise WiringError(
            f"input {key!r} is unresolved: no registered producer and no root input. "
            f"{ending}"
        )


def _folders_between(path: Path, run_dir: Path) -> Iterator[Path]:
    """Yield each folder that holds ``path``, up to but not including ``run_dir``."""
    for parent in path.parents:
        if parent == run_dir:
            return
        yield parent


def _nesting_error(
    inner: tuple[FileKey, Path], outer: tuple[FileKey, Path], outer_is_run_dir: bool
) -> WiringError:
    """Return the error for an output whose path is inside another output's path."""
    if outer_is_run_dir:
        reason = (
            "a run directory is replaced on every run, so no other output may be inside it"
        )
    else:
        reason = "one output cannot hold another"
    return WiringError(
        f"output {inner[0]!r} at {inner[1]} is inside output {outer[0]!r} at {outer[1]}; "
        f"{reason}."
    )


def _closest_key(key: FileKey, known: list[FileKey]) -> FileKey | None:
    """Return the key in ``known`` closest to ``key``, comparing stems among its siblings."""
    path = PurePosixPath(key)
    # Filtering on parent and suffix makes each stem name exactly one key.
    by_stem = {
        PurePosixPath(name).stem: name
        for name in known
        if path.suffix
        and PurePosixPath(name).suffix == path.suffix
        and PurePosixPath(name).parent == path.parent
    }
    if by_stem:
        match = difflib.get_close_matches(path.stem, list(by_stem), n=1)
        return by_stem[match[0]] if match else None
    match = difflib.get_close_matches(key, known, n=1)
    return match[0] if match else None


def _registered_keys(keys: list[FileKey]) -> str:
    """Return the sentence listing up to ``_LISTED_KEYS`` of the sorted ``keys``."""
    if not keys:
        return "No keys are registered so far."
    return f"The keys registered so far are {_quoted_list(keys, _LISTED_KEYS)}."


def _quoted_list(names: Sequence[str], limit: int) -> str:
    """Quote and join the first ``limit`` names, then count the rest as ``and N more``."""
    listed = ", ".join(repr(name) for name in names[:limit])
    rest = len(names) - limit
    return f"{listed}, and {rest} more" if rest > 0 else listed
