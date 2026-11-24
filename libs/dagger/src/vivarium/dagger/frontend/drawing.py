"""Draw a pipeline's steps and the files between them as a static graph.

:func:`graph` writes the graph of a :class:`~vivarium.dagger.frontend.builder.Pipeline`
to a file, and ``dagger graph -c DEF FILE`` does the same for a definition. Neither runs
a step or reads pytask's state, and the file asked for is the only thing written.

The graph has these nodes:

- One node per step, in the order the definition adds the steps, labeled with the step's
  name and, on a second line, its kind: ``python step``, ``notebook step``, ``command
  step``, ``simulation step``, or ``task`` for a task added with
  :meth:`~vivarium.dagger.frontend.builder.Pipeline.add_task`.
- One node per root input that a step reads, labeled with its file key, in the order the
  steps first read them.
- One node per output that no step reads, labeled with its file key, in the order of the
  steps that write them and then of each step's outputs. These are the pipeline's final
  results.

The edges are these:

- From a step to each step that reads a file it writes, labeled with that file's key. When
  the second step reads several files the first writes, there is one edge, labeled with
  each key on its own line, in the order the reading step declares its inputs.
- From a root input's node to each step that reads it, and from a step to the node of each
  of its outputs that no step reads. These edges have no label, because the file's node
  carries its key.

A file is matched by its resolved path. A run directory, such as a simulation step's, is a
file here, named by its key. A file that the pipeline's registry does not know is named by
its path as the step holds it: this is the case for an output of a task added with
``add_task``. A file that a step reads, that no step writes and that is not a root input,
such as a notebook's source or a simulation's model specification, is left out. A step's
code identity and its command are not files and are left out, and so is a dependency
carried by a node that is not a file, such as a ``PythonNode`` between two tasks added
with ``add_task``: no edge is drawn for it.

The format follows the output file's extension, compared without regard to case:

- ``.mmd``: Mermaid flowchart text, which GitHub, VS Code's Markdown preview and JupyterLab
  draw.
- ``.md``: the same Mermaid text in a Markdown ``mermaid`` code block.
- ``.dot`` or ``.gv``: Graphviz DOT text.
- ``.svg``, ``.png`` or ``.pdf``: an image. Graphviz's ``dot`` program draws it from the
  DOT text to its standard output, and :func:`graph` then writes it to the file, so a
  failing ``dot`` writes no file. These need ``dot`` on ``PATH``, for example from
  ``conda install graphviz``.

For the three steps of the ``dagger`` command's example pipeline, a chain of ``population``,
``adults`` and ``summary``, the Mermaid text is::

    flowchart LR
      s0["population<br/>python step"]
      s1["adults<br/>python step"]
      s2["summary<br/>python step"]
      o0[/"summary.csv"/]
      s0 -->|"population.parquet"| s1
      s1 -->|"adults.parquet"| s2
      s2 --> o0

and the DOT text is::

    digraph pipeline {
      rankdir=LR;
      node [shape=box];
      s0 [label="population\\npython step"];
      s1 [label="adults\\npython step"];
      s2 [label="summary\\npython step"];
      o0 [label="summary.csv", shape=parallelogram];
      s0 -> s1 [label="population.parquet"];
      s1 -> s2 [label="adults.parquet"];
      s2 -> o0;
    }

Steps are ``s0``, ``s1``, ... in step order, root inputs ``i0``, ``i1``, ... and final
outputs ``o0``, ``o1``, ... in the orders above. Both texts list the step nodes, then the
root-input nodes, then the final-output nodes, each node on its own line, and then the
edges: for each step in order, the edges into it, ordered by the first input each edge
carries, and then, for each step in order, the edges to its final outputs. A root input or
final output is drawn as a parallelogram (``[/"..."/]`` in Mermaid) and a step as a box.
In a Mermaid label, ``#`` is written ``#35;`` first, and then a double quote is written
``#quot;``, ``<`` is written ``#lt;``, ``>`` is written ``#gt;`` and ``&`` is written
``#amp;``. In a DOT label, a double quote or a backslash is escaped with a backslash and
``&`` is written ``&amp;``. Mermaid and Graphviz decode these, so each label is drawn as
the step name or key it holds. Each text ends with a newline.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytask

from vivarium.dagger.backend.build import CODE_ID_KEY, COMMAND_KEY
from vivarium.dagger.frontend.builder import Pipeline, _PipelineSnapshot, freeze

TEXT_FORMATS = (".mmd", ".md", ".dot", ".gv")
"""The extensions :func:`graph` writes as text, with no other program."""

IMAGE_FORMATS = (".svg", ".png", ".pdf")
"""The extensions :func:`graph` writes as an image, with Graphviz's ``dot`` program."""

_MERMAID_CODES = (('"', "#quot;"), ("<", "#lt;"), (">", "#gt;"), ("&", "#amp;"))
"""The characters a Mermaid label writes as entity codes, after ``#`` as ``#35;``."""


class GraphError(RuntimeError):
    """Graphviz's ``dot`` program failed to draw a pipeline's graph."""


def graph(pipeline: Pipeline, path: Path | str) -> None:
    """Write the graph of ``pipeline``'s steps and the files between them to ``path``.

    The module docstring describes the graph and the formats. Like
    :func:`~vivarium.dagger.frontend.execution.preview`, this first freezes ``pipeline``,
    so a step added afterwards is not drawn. Nothing runs, pytask's state is not read,
    and ``path`` is the only file written. Its folder must exist. For an image, ``dot``
    draws the image to its standard output and this function then writes it to ``path``,
    so a failing ``dot`` writes no file.

    Parameters
    ----------
    pipeline
        The pipeline to draw.
    path
        The file to write. Its extension picks the format.

    Raises
    ------
    ValueError
        If the extension of ``path`` is not one of :data:`TEXT_FORMATS` or
        :data:`IMAGE_FORMATS`. The message names the extension, or says the path has none,
        and lists the formats that work. Also if the extension is one of
        :data:`IMAGE_FORMATS` and ``dot`` is not on ``PATH``; the message names the
        extension, says that it needs Graphviz's ``dot`` program, lists the text formats
        that work without it, and says how to install Graphviz. Nothing is written in
        either case.
    GraphError
        If ``dot`` exits with a nonzero status. The message names the extension and
        ``dot``'s exit status and includes its standard error. No file is written.
    OSError
        If ``path`` cannot be written, for example because its folder does not exist or
        cannot be written to. This holds for every format, images included.
    TypeError
        If ``pipeline`` is not a :class:`~vivarium.dagger.frontend.builder.Pipeline`.
    """
    _graph(freeze(pipeline), path)


def _graph(pipeline: _PipelineSnapshot, path: Path | str) -> None:
    """Write the graph of the snapshot ``pipeline`` to ``path``, as :func:`graph` does."""
    path = Path(path)
    suffix = path.suffix
    extension = suffix.lower()
    if extension not in TEXT_FORMATS + IMAGE_FORMATS:
        named = f"the extension {suffix!r}" if suffix else "no extension"
        raise ValueError(
            f"Cannot write a graph to {str(path)!r}, which has {named}. Use one of the "
            f"formats {_listed(TEXT_FORMATS + IMAGE_FORMATS)}."
        )
    dot: str | None = None
    if extension in IMAGE_FORMATS:
        dot = shutil.which("dot")
        if dot is None:
            raise ValueError(
                f"Writing a {suffix} image needs Graphviz's dot program, which is not on "
                f"PATH. Write one of the text formats {_listed(TEXT_FORMATS)}, which need "
                "no other program, or install Graphviz, for example with 'conda install "
                "graphviz'."
            )
    nodes, edges = _layout(pipeline)
    if dot is not None:
        content = _draw(dot, _dot(nodes, edges), suffix)
    elif extension in (".dot", ".gv"):
        content = _dot(nodes, edges).encode("utf-8")
    else:
        text = _mermaid(nodes, edges)
        if extension == ".md":
            text = f"```mermaid\n{text}```\n"
        content = text.encode("utf-8")
    path.write_bytes(content)


@dataclass(frozen=True)
class _Node:
    """A node of the graph: a step, or a root input's or final output's file."""

    id: str
    lines: tuple[str, ...]
    is_file: bool


@dataclass
class _Edge:
    """An edge of the graph, with the keys of the files it carries, if any."""

    source: str
    target: str
    labels: list[str] = field(default_factory=list)


def _layout(pipeline: _PipelineSnapshot) -> tuple[list[_Node], list[_Edge]]:
    """Return the nodes and edges of ``pipeline``'s graph in the order they are written."""
    producers: dict[Path, tuple[int, str]] = {}
    for index, task in enumerate(pipeline.tasks):
        for node in task.outputs.values():
            file = _file(node)
            if file is not None:
                producers.setdefault(file.resolve(), (index, _name(pipeline, file)))

    read: set[Path] = set()
    root_ids: dict[Path, str] = {}
    root_nodes: list[_Node] = []
    into_steps: list[_Edge] = []
    for index, task in enumerate(pipeline.tasks):
        edges: dict[str, _Edge] = {}
        for name, node in task.inputs.items():
            file = _file(node)
            if name in (CODE_ID_KEY, COMMAND_KEY) or file is None:
                continue
            resolved = file.resolve()
            read.add(resolved)
            target = f"s{index}"
            if resolved in producers:
                producer, label = producers[resolved]
                edge = edges.setdefault(f"s{producer}", _Edge(f"s{producer}", target))
                if label not in edge.labels:
                    edge.labels.append(label)
                continue
            key = None if pipeline.registry is None else pipeline.registry.key_for(file)
            if key is None:
                continue
            if resolved not in root_ids:
                root_ids[resolved] = f"i{len(root_nodes)}"
                root_nodes.append(_Node(root_ids[resolved], (key,), is_file=True))
            source = root_ids[resolved]
            edges.setdefault(source, _Edge(source, target))
        into_steps.extend(edges.values())

    step_nodes = [
        _Node(f"s{index}", (task.name, _kind(pipeline, task.name)), is_file=False)
        for index, task in enumerate(pipeline.tasks)
    ]
    output_nodes: list[_Node] = []
    to_outputs: list[_Edge] = []
    written: set[Path] = set()
    for index, task in enumerate(pipeline.tasks):
        for node in task.outputs.values():
            file = _file(node)
            if file is None:
                continue
            resolved = file.resolve()
            if resolved in read or resolved in written:
                continue
            written.add(resolved)
            output_id = f"o{len(output_nodes)}"
            output_nodes.append(_Node(output_id, (_name(pipeline, file),), is_file=True))
            to_outputs.append(_Edge(f"s{index}", output_id))
    return step_nodes + root_nodes + output_nodes, into_steps + to_outputs


def _file(node: pytask.PNode) -> Path | None:
    """Return ``node``'s path if it is a PathNode on a :class:`pathlib.Path`."""
    if isinstance(node, pytask.PathNode) and isinstance(node.path, Path):
        return node.path
    return None


def _name(pipeline: _PipelineSnapshot, file: Path) -> str:
    """Return the registry key of ``file``, else its path as the step holds it."""
    key = None if pipeline.registry is None else pipeline.registry.key_for(file)
    return str(file) if key is None else key


def _kind(pipeline: _PipelineSnapshot, step: str) -> str:
    """Return the second line of ``step``'s label, which names its kind."""
    kind = pipeline.kinds.get(step, "task")
    return "task" if kind == "task" else f"{kind} step"


def _mermaid(nodes: list[_Node], edges: list[_Edge]) -> str:
    """Return the Mermaid flowchart text of ``nodes`` and ``edges``."""
    lines = ["flowchart LR"]
    for node in nodes:
        label = _mermaid_label(node.lines)
        shape = f'[/"{label}"/]' if node.is_file else f'["{label}"]'
        lines.append(f"  {node.id}{shape}")
    for edge in edges:
        arrow = f' -->|"{_mermaid_label(edge.labels)}"| ' if edge.labels else " --> "
        lines.append(f"  {edge.source}{arrow}{edge.target}")
    return "\n".join(lines) + "\n"


def _mermaid_label(lines: Sequence[str]) -> str:
    """Join ``lines`` into a Mermaid label, escaping each line."""
    return "<br/>".join(_mermaid_escape(line) for line in lines)


def _mermaid_escape(text: str) -> str:
    """Write each character Mermaid would decode or cut a label at as its entity code."""
    # '#' goes first, so the '#' of each code written after it stays as it is.
    text = text.replace("#", "#35;")
    for character, code in _MERMAID_CODES:
        text = text.replace(character, code)
    return text


def _dot(nodes: list[_Node], edges: list[_Edge]) -> str:
    """Return the Graphviz DOT text of ``nodes`` and ``edges``."""
    lines = ["digraph pipeline {", "  rankdir=LR;", "  node [shape=box];"]
    for node in nodes:
        shape = ", shape=parallelogram" if node.is_file else ""
        lines.append(f'  {node.id} [label="{_dot_label(node.lines)}"{shape}];')
    for edge in edges:
        label = f' [label="{_dot_label(edge.labels)}"]' if edge.labels else ""
        lines.append(f"  {edge.source} -> {edge.target}{label};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _dot_label(lines: Sequence[str]) -> str:
    """Join ``lines`` into a DOT label, escaping each line."""
    return "\\n".join(
        line.replace("\\", "\\\\").replace('"', '\\"').replace("&", "&amp;") for line in lines
    )


def _draw(dot: str, text: str, suffix: str) -> bytes:
    """Return the image Graphviz's ``dot`` draws from the DOT ``text`` on its stdout."""
    result = subprocess.run(
        [dot, f"-T{suffix.lower()[1:]}"],
        input=text.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise GraphError(
            f"Graphviz's dot program could not draw the {suffix} image: it exited with "
            f"status {result.returncode}.\n{error}".rstrip()
        )
    return result.stdout


def _listed(extensions: Sequence[str]) -> str:
    """Join ``extensions`` as a list in a sentence."""
    return f"{', '.join(extensions[:-1])} or {extensions[-1]}"
