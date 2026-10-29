"""Producer/consumer wiring: resolve declared artifact keys to shared nodes.

A pipeline names each artifact with a logical key. An output registers its key
against one resolved file node under the run's output directory, and an input
naming that key resolves to the identical node object, so pytask draws the
producer-to-consumer edge from shared node identity rather than from any separate
declaration. The output root and run name namespace produced paths, so two runs
write to distinct directories.

This is a plain registry, not a container class: a pipeline builds its tasks into
a list and hands them to :func:`~vivarium.workflow.backend.build.build`.
Register every output before resolving inputs. The guards here are the ones pytask
cannot make for us -- a hard error when two keys resolve to one path (which pytask
would otherwise silently merge), a hard error when a step declares a root input's key
as its output (which would otherwise shadow the root input for every consumer), and a
did-you-mean on a mistyped key. An unresolved input (no producer and no root-input
file) is caught here with a did-you-mean; everything heavier (a cycle, two tasks
claiming one product) is left to pytask, which fails safe.
"""

import difflib
from collections.abc import Mapping
from pathlib import Path

from pytask import PathNode

from vivarium.workflow.backend.nodes import file_node

ArtifactKey = str
"""A logical artifact name, resolved to a file node under the run's output directory."""


class WiringError(Exception):
    """A definition-time error in how a pipeline's artifacts are wired or supplied."""


class ArtifactRegistry:
    """Resolve declared artifact keys to shared file nodes for one run.

    Parameters
    ----------
    output_root
        Directory under which produced artifacts live.
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
        root_inputs: Mapping[ArtifactKey, Path | str] | None = None,
    ) -> None:
        self._run_dir = Path(output_root).resolve() / run_name
        self._root_inputs = {key: Path(p).resolve() for key, p in (root_inputs or {}).items()}
        self._producers: dict[ArtifactKey, PathNode] = {}
        # Seed the collision guard with root-input paths so an output cannot land on
        # one and be silently merged into it by pytask.
        self._key_by_path: dict[Path, ArtifactKey] = {
            path: key for key, path in self._root_inputs.items()
        }

    def path_for(self, key: ArtifactKey) -> Path:
        """Return the resolved path a produced ``key`` resolves to for this run."""
        return (self._run_dir / key).resolve()

    def key_for(self, path: Path | str) -> ArtifactKey | None:
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

    def register_output(self, key: ArtifactKey) -> PathNode:
        """Register ``key`` as produced by a step and return its file node.

        Registering the same key again returns the same node, so two tasks claiming
        one product is left for pytask to reject.

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
            produce, or if a different key -- an output or a declared root input --
            already resolves to this key's path.
        """
        source = self._root_inputs.get(key)
        if source is not None:
            raise WiringError(
                f"output {key!r} is a declared root input at {source}; a step cannot "
                "also produce it."
            )
        resolved = self.path_for(key)
        claimant = self._key_by_path.get(resolved)
        if claimant is not None and claimant != key:
            raise WiringError(
                f"keys {claimant!r} and {key!r} resolve to one path {resolved}."
            )
        node = self._producers.get(key)
        if node is None:
            node = file_node(resolved)
            self._producers[key] = node
            self._key_by_path[resolved] = key
        return node

    def resolve_input(self, key: ArtifactKey) -> PathNode:
        """Resolve an input ``key`` to the node that produces it, or a root-input file.

        Parameters
        ----------
        key
            The input's logical key.

        Returns
        -------
            The identical node object a producer registered for ``key`` (so the
            consumer gets a real edge to it), or a file node for a declared root
            input that exists on disk.

        Raises
        ------
        WiringError
            If ``key`` has no registered producer and no root-input file on disk. An
            unknown key carries a did-you-mean suggestion from the registered keys.
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
        known = list(self._producers) + list(self._root_inputs)
        hint = difflib.get_close_matches(key, known, n=1)
        suffix = f" Did you mean {hint[0]!r}?" if hint else ""
        raise WiringError(
            f"input {key!r} is unresolved: no registered producer and no root input."
            f"{suffix}"
        )
