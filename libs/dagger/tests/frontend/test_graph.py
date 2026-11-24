"""Tests for drawing a pipeline's step graph (MIC-7765)."""

import importlib
import os
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytask
import pytest

from tests.backend.file_snapshot import age_files, changed_files, snapshot_files
from tests.frontend.fake_dot import FAKE_IMAGE, install_fake_dot
from tests.frontend.notebooks import write_source_notebook
from tests.frontend.pipeline_example.pipeline import (
    make_population,
    select_adults,
    total_adults,
)
from tests.frontend.simulations import summarize_run, write_branches, write_spec
from vivarium.dagger.backend.nodes import file_node
from vivarium.dagger.backend.task import Task
from vivarium.dagger.frontend import GraphError, Pipeline, graph, run
from vivarium.dagger.frontend.builder import freeze
from vivarium.dagger.harness import stable_code_id

_TEXT_EXTENSIONS = (".mmd", ".md", ".dot", ".gv")
"""The extensions graph() writes as text."""

_EXTENSIONS = (*_TEXT_EXTENSIONS, ".svg", ".png", ".pdf")
"""Every extension graph() accepts."""

_MERMAID_STEP = re.compile(r'  (\w+)\["(.*)"\]')
_MERMAID_FILE = re.compile(r'  (\w+)\[/"(.*)"/\]')
_MERMAID_EDGE = re.compile(r'  (\w+) -->(?:\|"(.*)"\|)? (\w+)')

_DOT_LABEL = r'"((?:[^"\\]|\\.)*)"'
"""A quoted DOT label, in which a backslash escapes the next character."""

_DOT_HEADER = ["digraph pipeline {", "  rankdir=LR;", "  node [shape=box];"]
_DOT_NODE = re.compile(rf"  (\w+) \[label={_DOT_LABEL}(, shape=parallelogram)?\];")
_DOT_EDGE = re.compile(rf"  (\w+) -> (\w+)(?: \[label={_DOT_LABEL}\])?;")


@dataclass(frozen=True)
class _Node:
    """A node of a drawn graph, with its label's line breaks and escapes undone."""

    id: str
    shape: str
    label: str


@dataclass(frozen=True)
class _Edge:
    """An edge of a drawn graph; ``label`` is ``None`` for an unlabeled edge."""

    source: str
    target: str
    label: str | None


@dataclass(frozen=True)
class _Drawing:
    """The nodes and edges of a drawn graph, in the order the text lists them."""

    nodes: list[_Node]
    edges: list[_Edge]

    def step(self, name: str) -> str:
        """Return the id of the one step node whose label's first line is ``name``."""
        found = [
            node.id
            for node in self.nodes
            if node.shape == "box" and node.label.split("\n")[0] == name
        ]
        assert len(found) == 1, (name, self.nodes)
        return found[0]

    def files(self, label: str) -> list[str]:
        """Return the ids of the file nodes labeled ``label``."""
        return [
            node.id
            for node in self.nodes
            if node.shape == "parallelogram" and node.label == label
        ]

    def boxes(self) -> list[tuple[str, str]]:
        """Return the id and label of each step node, in order."""
        return [(node.id, node.label) for node in self.nodes if node.shape == "box"]

    def labels(self) -> list[str]:
        """Return the label of every node and every labeled edge."""
        edges = [edge.label for edge in self.edges if edge.label is not None]
        return [node.label for node in self.nodes] + edges


_MERMAID_ENTITIES = {"35": "#", "quot": '"', "lt": "<", "gt": ">", "amp": "&"}
"""The character each Mermaid entity code in a label stands for."""


def _from_mermaid(label: str) -> str:
    """Undo the line breaks and entity codes of a Mermaid label."""
    # A label's own '<' is written as an entity, so a '<br/>' left in it is a line break.
    lines = label.split("<br/>")
    entity = re.compile(r"#(35|quot|lt|gt|amp);")
    return "\n".join(entity.sub(lambda m: _MERMAID_ENTITIES[m[1]], line) for line in lines)


def _from_dot(label: str) -> str:
    """Undo the escapes of a DOT label, reading a backslash-n as a line break."""
    unescaped = re.sub(r"\\(.)", lambda m: "\n" if m[1] == "n" else m[1], label)
    return unescaped.replace("&amp;", "&")


def _check_ids(drawing: _Drawing, text: str) -> _Drawing:
    """Assert that the node ids are unique and every edge joins two nodes."""
    ids = [node.id for node in drawing.nodes]
    assert len(ids) == len(set(ids)), text
    for edge in drawing.edges:
        assert edge.source in ids and edge.target in ids, (edge, text)
    return drawing


def _parse_mermaid(text: str) -> _Drawing:
    """Return the nodes and edges of Mermaid text, failing on any other line."""
    assert text.endswith("\n"), repr(text)
    header, *lines = text.splitlines()
    assert header == "flowchart LR", text
    nodes: list[_Node] = []
    edges: list[_Edge] = []
    for line in lines:
        if match := _MERMAID_FILE.fullmatch(line):
            nodes.append(_Node(match[1], "parallelogram", _from_mermaid(match[2])))
        elif match := _MERMAID_STEP.fullmatch(line):
            nodes.append(_Node(match[1], "box", _from_mermaid(match[2])))
        elif match := _MERMAID_EDGE.fullmatch(line):
            label = None if match[2] is None else _from_mermaid(match[2])
            edges.append(_Edge(match[1], match[3], label))
        else:
            raise AssertionError(f"Unexpected Mermaid line {line!r} in:\n{text}")
    return _check_ids(_Drawing(nodes, edges), text)


def _parse_dot(text: str) -> _Drawing:
    """Return the nodes and edges of DOT text, failing on any other line."""
    assert text.endswith("\n"), repr(text)
    lines = text.splitlines()
    assert lines[:3] == _DOT_HEADER and lines[-1] == "}", text
    nodes: list[_Node] = []
    edges: list[_Edge] = []
    for line in lines[3:-1]:
        if match := _DOT_NODE.fullmatch(line):
            shape = "box" if match[3] is None else "parallelogram"
            nodes.append(_Node(match[1], shape, _from_dot(match[2])))
        elif match := _DOT_EDGE.fullmatch(line):
            label = None if match[3] is None else _from_dot(match[3])
            edges.append(_Edge(match[1], match[2], label))
        else:
            raise AssertionError(f"Unexpected DOT line {line!r} in:\n{text}")
    return _check_ids(_Drawing(nodes, edges), text)


def _module_sample(intro: str) -> str:
    """Return the literal block after the line ending ``intro`` in the module docstring."""
    module = importlib.import_module("vivarium.dagger.frontend.drawing")
    lines = (module.__doc__ or "").splitlines()
    starts = [index for index, line in enumerate(lines) if line.rstrip().endswith(intro)]
    assert len(starts) == 1, f"no single line ending {intro!r} in the module docstring"
    block: list[str] = []
    for line in lines[starts[0] + 1 :]:
        if line.strip() and not line.startswith(" "):
            break
        block.append(line)
    return textwrap.dedent("\n".join(block)).strip("\n") + "\n"


def _mermaid_sample() -> str:
    """Return the module docstring's Mermaid text for the pipeline example."""
    sample = _module_sample("the Mermaid text is::")
    assert sample.startswith("flowchart LR\n"), sample
    return sample


def _dot_sample() -> str:
    """Return the module docstring's DOT text for the pipeline example."""
    sample = _module_sample("the DOT text is::")
    assert sample.startswith("digraph pipeline {\n"), sample
    return sample


def _draw(pipeline: Pipeline, path: Path) -> str:
    """Write ``pipeline``'s graph to ``path`` and return the text written."""
    graph(pipeline, path)
    return path.read_text()


def _names_extension(message: str, extension: str) -> bool:
    """Return whether ``message`` holds ``extension`` not followed by a word character."""
    return re.search(rf"{re.escape(extension)}(?!\w)", message) is not None


def _constant() -> pd.DataFrame:
    """Return a fixed one-column table."""
    return pd.DataFrame({"n": [1, 2, 3]})


def _one(source: Path) -> pd.DataFrame:
    """Return the table at ``source``."""
    return pd.read_parquet(source)


def _two_outputs() -> dict[str, pd.DataFrame]:
    """Return a table under each of the names 'used' and 'spare'."""
    return {"used": _constant(), "spare": _constant()}


def _pair() -> dict[str, pd.DataFrame]:
    """Return a table under each of the names 'a' and 'b'."""
    return {"a": _constant(), "b": _constant()}


def _join(second: Path, first: Path) -> pd.DataFrame:
    """Return the tables at ``first`` and ``second``, one after the other."""
    return pd.concat([pd.read_csv(first), pd.read_csv(second)])


def _first(source: Path) -> dict[str, pd.DataFrame]:
    """Return the table at ``source`` under the names 'kept' and 'spare'."""
    frame = pd.read_csv(source)
    return {"kept": frame, "spare": frame}


def _second(raw: Path, early: Path) -> pd.DataFrame:
    """Return the table at ``early``."""
    return pd.read_parquet(early)


def _third(later: Path, again: Path, first: Path) -> dict[str, pd.DataFrame]:
    """Return the table at ``later`` under the names 'x' and 'w'."""
    frame = pd.read_parquet(later)
    return {"x": frame, "w": frame}


def _example_pipeline(tmp_path: Path) -> Pipeline:
    """Return the pipeline example's three steps, with the output root under ``tmp_path``."""
    p = Pipeline(tmp_path / "output", run_name="example")
    p.add_python_step(
        make_population, inputs={}, outputs="population.parquet", name="population"
    )
    p.add_python_step(
        select_adults,
        inputs={"population": "population.parquet"},
        outputs="adults.parquet",
        name="adults",
    )
    p.add_python_step(
        total_adults,
        inputs={"adults": "adults.parquet"},
        outputs="summary.csv",
        name="summary",
    )
    return p


def _root_input(directory: Path, name: str) -> Path:
    """Write an empty root input file ``name`` in ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("")
    return path


def _ordered_pipeline(tmp_path: Path) -> Pipeline:
    """Return a pipeline with two root inputs, three steps and three final outputs.

    'first' reads the root input 'z.csv' and writes 'first.parquet' and 'spare.csv';
    'second' reads the root input 'a.csv' and then 'first.parquet'; 'third' reads
    'second.parquet', 'z.csv' and 'first.parquet', in that order, and writes 'x.csv'
    and then 'w.csv'. 'a.csv' is declared before 'z.csv'.
    """
    inputs = tmp_path / "inputs"
    p = Pipeline(
        tmp_path / "out",
        "run1",
        root_inputs={
            "a.csv": _root_input(inputs, "a.csv"),
            "z.csv": _root_input(inputs, "z.csv"),
        },
        state=tmp_path / "state",
    )
    p.add_python_step(
        _first,
        inputs={"source": "z.csv"},
        outputs={"kept": "first.parquet", "spare": "spare.csv"},
        name="first",
    )
    p.add_python_step(
        _second,
        inputs={"raw": "a.csv", "early": "first.parquet"},
        outputs="second.parquet",
        name="second",
    )
    p.add_python_step(
        _third,
        inputs={"later": "second.parquet", "again": "z.csv", "first": "first.parquet"},
        outputs={"x": "x.csv", "w": "w.csv"},
        name="third",
    )
    return p


_ORDERED_MERMAID = """\
flowchart LR
  s0["first<br/>python step"]
  s1["second<br/>python step"]
  s2["third<br/>python step"]
  i0[/"z.csv"/]
  i1[/"a.csv"/]
  o0[/"spare.csv"/]
  o1[/"x.csv"/]
  o2[/"w.csv"/]
  i0 --> s0
  i1 --> s1
  s0 -->|"first.parquet"| s1
  s1 -->|"second.parquet"| s2
  i0 --> s2
  s0 -->|"first.parquet"| s2
  s0 --> o0
  s2 --> o1
  s2 --> o2
"""
"""The Mermaid text of :func:`_ordered_pipeline`, as the module docstring orders it."""


# Nodes and edges


def test_a_chain_of_python_steps_matches_the_module_docstrings_mermaid_sample(
    tmp_path: Path,
) -> None:
    """graph() of a Pipeline with the three python steps of the pipeline example (population, adults, summary) writes, to a .mmd file, exactly the Mermaid text in the frontend.graph module docstring."""
    pipeline = _example_pipeline(tmp_path)

    text = _draw(pipeline, tmp_path / "graph.mmd")

    assert text == _mermaid_sample()


def test_each_step_is_labeled_with_its_name_and_kind(tmp_path: Path) -> None:
    """A pipeline with a python, a notebook, a command and a simulation step and a task added with add_task gives one node per step, labeled '<name>' then, on the next line, 'python step', 'notebook step', 'command step', 'simulation step' and 'task'."""
    notebook = tmp_path / "notebooks" / "report.ipynb"
    source = write_source_notebook(notebook, "table = 'unset'")
    model = tmp_path / "model"
    p = Pipeline(tmp_path / "out", "run1", state=tmp_path / "state")
    p.add_python_step(_constant, inputs={}, outputs="made.parquet", name="make")
    p.add_notebook_step(
        source,
        executed="report.ipynb",
        env=sys.prefix,
        inputs={"table": "made.parquet"},
        name="report",
    )
    p.add_command_step(
        "cp {source} {target}",
        inputs={"source": "made.parquet"},
        outputs={"target": "copy.parquet"},
        name="copy",
    )
    p.add_simulation_step(
        spec=write_spec(model / "spec.yaml"),
        branches=write_branches(model / "branches.yaml"),
        outputs="sims/toy",
        env="simulation_env",
        options={"project": "proj_test"},
        name="toy",
    )
    hand = tmp_path.resolve() / "hand.txt"
    p.add_task(
        Task(
            name="hand",
            run=f"touch {hand}",
            inputs={},
            outputs={"product": file_node(hand)},
            resources={},
            env=None,
        )
    )

    text = _draw(p, tmp_path / "graph.mmd")

    assert _parse_mermaid(text).boxes() == [
        ("s0", "make\npython step"),
        ("s1", "report\nnotebook step"),
        ("s2", "copy\ncommand step"),
        ("s3", "toy\nsimulation step"),
        ("s4", "hand\ntask"),
    ], text
    assert '  s0["make<br/>python step"]' in text.splitlines(), text


def test_a_root_input_is_one_node_with_an_unlabeled_edge_to_each_step_that_reads_it(
    tmp_path: Path,
) -> None:
    """A root input read by two steps is one parallelogram node labeled with its key, with an unlabeled edge to each of the two steps; a declared root input that no step reads has no node."""
    inputs = tmp_path / "inputs"
    p = Pipeline(
        tmp_path / "out",
        "run1",
        root_inputs={
            "raw.parquet": _root_input(inputs, "raw.parquet"),
            "unused.parquet": _root_input(inputs, "unused.parquet"),
        },
        state=tmp_path / "state",
    )
    for name in ("left", "right"):
        p.add_python_step(
            _one, inputs={"source": "raw.parquet"}, outputs=f"{name}.parquet", name=name
        )

    text = _draw(p, tmp_path / "graph.mmd")

    drawing = _parse_mermaid(text)
    [raw] = drawing.files("raw.parquet")
    assert re.fullmatch(r"i\d+", raw), text
    from_raw = [edge for edge in drawing.edges if edge.source == raw]
    assert from_raw == [
        _Edge(raw, drawing.step("left"), None),
        _Edge(raw, drawing.step("right"), None),
    ], text
    assert not any("unused.parquet" in label for label in drawing.labels()), text


def test_an_output_no_step_reads_is_a_node_with_an_unlabeled_edge_from_its_step(
    tmp_path: Path,
) -> None:
    """A step with two outputs, one read by a later step and one read by none, has a labeled edge to the reading step and an unlabeled edge to a parallelogram node labeled with the unread output's key."""
    p = Pipeline(tmp_path / "out", "run1", state=tmp_path / "state")
    p.add_python_step(
        _two_outputs,
        inputs={},
        outputs={"used": "used.csv", "spare": "spare.csv"},
        name="split",
    )
    p.add_python_step(_one, inputs={"source": "used.csv"}, outputs="read.csv", name="reader")

    text = _draw(p, tmp_path / "graph.mmd")

    drawing = _parse_mermaid(text)
    split, reader = drawing.step("split"), drawing.step("reader")
    [spare] = drawing.files("spare.csv")
    assert re.fullmatch(r"o\d+", spare), text
    from_split = [edge for edge in drawing.edges if edge.source == split]
    assert sorted(from_split, key=lambda edge: edge.target) == sorted(
        [_Edge(split, reader, "used.csv"), _Edge(split, spare, None)],
        key=lambda edge: edge.target,
    ), text
    assert drawing.files("used.csv") == [], text


def test_several_files_between_two_steps_make_one_edge_with_each_key(
    tmp_path: Path,
) -> None:
    """A step that reads two files that one earlier step writes has a single edge from that step, labeled with both keys on separate lines, in the order the reading step declares its inputs."""
    p = Pipeline(tmp_path / "out", "run1", state=tmp_path / "state")
    p.add_python_step(_pair, inputs={}, outputs={"a": "a.csv", "b": "b.csv"}, name="pair")
    p.add_python_step(
        _join,
        inputs={"second": "b.csv", "first": "a.csv"},
        outputs="joined.csv",
        name="join",
    )

    mermaid = _draw(p, tmp_path / "graph.mmd")
    dot = _draw(p, tmp_path / "graph.dot")

    for text, drawing in [(mermaid, _parse_mermaid(mermaid)), (dot, _parse_dot(dot))]:
        pair, join = drawing.step("pair"), drawing.step("join")
        between = [edge for edge in drawing.edges if edge.source == pair]
        between = [edge for edge in between if edge.target == join]
        assert between == [_Edge(pair, join, "b.csv\na.csv")], text
    assert '  s0 -->|"b.csv<br/>a.csv"| s1' in mermaid.splitlines(), mermaid
    assert r'  s0 -> s1 [label="b.csv\na.csv"];' in dot.splitlines(), dot


def test_an_edge_exists_only_where_a_step_reads_a_file_another_writes(
    tmp_path: Path,
) -> None:
    """In a pipeline with two independent chains, the edges between steps are exactly the producer-to-reader pairs, and no edge joins the two chains."""
    p = Pipeline(tmp_path / "out", "run1", state=tmp_path / "state")
    for name, source in [("p", None), ("x", "p"), ("w", "x"), ("q", None), ("y", "q")]:
        output = f"{name}.parquet"
        if source is None:
            p.add_python_step(_constant, inputs={}, outputs=output, name=name)
        else:
            reads = {"source": f"{source}.parquet"}
            p.add_python_step(_one, inputs=reads, outputs=output, name=name)
    p.add_python_step(_constant, inputs={}, outputs="z.parquet", name="z")

    text = _draw(p, tmp_path / "graph.mmd")

    drawing = _parse_mermaid(text)
    names = {node_id: label.split("\n")[0] for node_id, label in drawing.boxes()}
    between = {
        (names[edge.source], names[edge.target], edge.label)
        for edge in drawing.edges
        if edge.source in names and edge.target in names
    }
    assert between == {
        ("p", "x", "p.parquet"),
        ("x", "w", "x.parquet"),
        ("q", "y", "q.parquet"),
    }, text


def test_a_simulation_run_directory_read_by_a_step_is_an_edge_labeled_with_its_key(
    tmp_path: Path,
) -> None:
    """A step that reads a simulation step's run directory has an edge from the simulation step labeled with the run directory's key."""
    model = tmp_path / "model"
    p = Pipeline(tmp_path / "out", "run1", state=tmp_path / "state")
    p.add_simulation_step(
        spec=write_spec(model / "spec.yaml"),
        branches=write_branches(model / "branches.yaml"),
        outputs="sims/toy",
        env="simulation_env",
        options={"project": "proj_test"},
        name="toy",
    )
    p.add_python_step(
        summarize_run,
        inputs={"simulation": "sims/toy"},
        outputs="summary.csv",
        name="summary",
    )

    text = _draw(p, tmp_path / "graph.mmd")

    drawing = _parse_mermaid(text)
    toy, summary = drawing.step("toy"), drawing.step("summary")
    into_summary = [edge for edge in drawing.edges if edge.target == summary]
    assert into_summary == [_Edge(toy, summary, "sims/toy")], text
    assert drawing.files("sims/toy") == [], text


def test_a_notebooks_source_and_a_steps_code_and_command_are_left_out(
    tmp_path: Path,
) -> None:
    """A notebook step's source notebook, which no step writes and which is not a root input, has no node, and no label anywhere holds a step's code identity, its command, or a pytask node name or signature."""
    source = write_source_notebook(
        tmp_path / "notebooks" / "source_nb.ipynb", "table = 'unset'"
    ).resolve()
    p = Pipeline(tmp_path / "out", "run1", state=tmp_path / "state")
    p.add_python_step(_constant, inputs={}, outputs="made.parquet", name="make")
    p.add_notebook_step(
        source,
        executed="executed.ipynb",
        env=sys.prefix,
        inputs={"table": "made.parquet"},
        name="report",
    )
    p.add_command_step(
        "cp {source} {target}",
        inputs={"source": "made.parquet"},
        outputs={"target": "copy.parquet"},
        name="copy",
    )
    hand = tmp_path.resolve() / "hand.txt"
    p.add_task(
        Task(
            name="hand",
            run=f"touch {hand} # the hand-built command",
            inputs={},
            outputs={"product": file_node(hand)},
            resources={},
            env=None,
            code_id=stable_code_id("hand::code"),
        )
    )

    text = _draw(p, tmp_path / "graph.mmd")

    drawing = _parse_mermaid(text)
    make, report = drawing.step("make"), drawing.step("report")
    assert _Edge(make, report, "made.parquet") in drawing.edges, text
    labels = drawing.labels()
    assert not any("source_nb" in label for label in labels), text
    # Every file but hand.txt is under the run's output directory and is named by its key.
    assert not any(str(tmp_path.resolve() / "out") in label for label in labels), text
    for fragment in ("hand::code", "papermill", "cp ", "touch "):
        assert fragment not in text, (fragment, text)
    for task in freeze(p).tasks:
        if isinstance(task.run, str):
            assert task.run not in text, (task.name, text)
        nodes = [*task.inputs.values(), *task.outputs.values()]
        if task.code_id is not None:
            nodes.append(task.code_id)
        for node in nodes:
            assert node.signature not in text, (task.name, node, text)


def test_an_add_task_output_without_a_key_is_labeled_by_its_path(tmp_path: Path) -> None:
    """A task added with add_task whose output file the registry does not know, and which a later add_task task reads, gives an edge labeled with that file's path as the task holds it; the same unread output is a node labeled with its path."""
    middle = tmp_path.resolve() / "middle.txt"
    last = tmp_path.resolve() / "last.txt"
    p = Pipeline(tmp_path / "out", "run1", state=tmp_path / "state")
    p.add_task(
        Task(
            name="write",
            run=f"touch {middle}",
            inputs={},
            outputs={"product": file_node(middle)},
            resources={},
            env=None,
        )
    )
    p.add_task(
        Task(
            name="read",
            run=f"cp {middle} {last}",
            inputs={"source": file_node(middle)},
            outputs={"product": file_node(last)},
            resources={},
            env=None,
        )
    )

    text = _draw(p, tmp_path / "graph.mmd")

    assert text == (
        "flowchart LR\n"
        '  s0["write<br/>task"]\n'
        '  s1["read<br/>task"]\n'
        f'  o0[/"{last}"/]\n'
        f'  s0 -->|"{middle}"| s1\n'
        "  s1 --> o0\n"
    )


def test_a_dependency_through_a_node_that_is_not_a_file_has_no_edge_or_node(
    tmp_path: Path,
) -> None:
    """Two add_task tasks that share a pytask PythonNode, one as an output and the other as an input, have no edge between them, and the PythonNode has no node; the reader's file output is still drawn."""
    value = pytask.PythonNode(name="shared_value", value=3)
    last = tmp_path.resolve() / "last.txt"
    p = Pipeline(tmp_path / "out", "run1", state=tmp_path / "state")
    p.add_task(
        Task(
            name="make_value",
            run="true",
            inputs={},
            outputs={"value": value},
            resources={},
            env=None,
        )
    )
    p.add_task(
        Task(
            name="use_value",
            run=f"touch {last}",
            inputs={"value": value},
            outputs={"product": file_node(last)},
            resources={},
            env=None,
        )
    )

    text = _draw(p, tmp_path / "graph.mmd")

    assert text == (
        "flowchart LR\n"
        '  s0["make_value<br/>task"]\n'
        '  s1["use_value<br/>task"]\n'
        f'  o0[/"{last}"/]\n'
        "  s1 --> o0\n"
    )


def test_the_order_of_nodes_and_edges_follows_the_steps(tmp_path: Path) -> None:
    """With root inputs, several steps and several final outputs, the Mermaid lines list the steps as s0, s1, ... in step order, then the root inputs as i0, i1, ... in the order steps first read them, then the final outputs as o0, o1, ..., then the edges into each step in step order, then the edges to final outputs, as the module docstring describes."""
    pipeline = _ordered_pipeline(tmp_path)

    text = _draw(pipeline, tmp_path / "graph.mmd")

    assert text == _ORDERED_MERMAID


_ODD_NAME = 'say "hi" \\ now #1 <a> & b'
"""A step name with a double quote, a backslash, '#', '<', '>' and '&'."""

_ODD_NODE_KEY = "b<1>&#35;.csv"
"""A file key, drawn as a node, that already holds the text of a Mermaid entity code."""

_ODD_EDGE_KEY = "e&<2>#x.csv"
"""A file key, drawn as an edge label, with '&', '<', '>' and '#'."""

_ESCAPED_MERMAID = r"""flowchart LR
  s0["say #quot;hi#quot; \ now #35;1 #lt;a#gt; #amp; b<br/>python step"]
  s1["reader<br/>python step"]
  o0[/"b#lt;1#gt;#amp;#35;35;.csv"/]
  o1[/"out.csv"/]
  s0 -->|"e#amp;#lt;2#gt;#35;x.csv"| s1
  s0 --> o0
  s1 --> o1
"""
"""The Mermaid text of the pipeline in the escaping test."""

_ESCAPED_DOT = r"""digraph pipeline {
  rankdir=LR;
  node [shape=box];
  s0 [label="say \"hi\" \\ now #1 <a> &amp; b\npython step"];
  s1 [label="reader\npython step"];
  o0 [label="b<1>&amp;#35;.csv", shape=parallelogram];
  o1 [label="out.csv", shape=parallelogram];
  s0 -> s1 [label="e&amp;<2>#x.csv"];
  s0 -> o0;
  s1 -> o1;
}
"""
"""The DOT text of the pipeline in the escaping test."""


def _odd_outputs() -> dict[str, pd.DataFrame]:
    """Return a table under each of the names 'odd' and 'read'."""
    return {"odd": _constant(), "read": _constant()}


def test_quotes_and_backslashes_in_labels_are_escaped(tmp_path: Path) -> None:
    """A step whose name holds a double quote and a backslash is written as '#quot;' for the quote in the Mermaid text, and with a backslash before the quote and before the backslash in the DOT text. In a step name, a node's key and an edge's key, Mermaid writes '#' as '#35;' (before any other code), '<' as '#lt;', '>' as '#gt;' and '&' as '#amp;', and DOT writes '&' as '&amp;' and keeps '#', '<' and '>'."""
    p = Pipeline(tmp_path / "out", "run1", state=tmp_path / "state")
    p.add_python_step(
        _odd_outputs,
        inputs={},
        outputs={"odd": _ODD_NODE_KEY, "read": _ODD_EDGE_KEY},
        name=_ODD_NAME,
    )
    p.add_python_step(
        _one, inputs={"source": _ODD_EDGE_KEY}, outputs="out.csv", name="reader"
    )

    mermaid = _draw(p, tmp_path / "graph.mmd")
    dot = _draw(p, tmp_path / "graph.dot")

    assert mermaid == _ESCAPED_MERMAID
    assert dot == _ESCAPED_DOT
    for drawing in (_parse_mermaid(mermaid), _parse_dot(dot)):
        assert drawing.boxes() == [
            ("s0", f"{_ODD_NAME}\npython step"),
            ("s1", "reader\npython step"),
        ]
        assert drawing.files(_ODD_NODE_KEY) == ["o0"]
        assert drawing.edges[0] == _Edge("s0", "s1", _ODD_EDGE_KEY)


# Formats


def test_dot_text_has_the_same_nodes_and_edges_as_the_mermaid_text(
    tmp_path: Path,
) -> None:
    """graph() of the pipeline example to a .dot file writes exactly the DOT text in the frontend.graph module docstring, and for a larger pipeline the DOT text has the same node ids, labels and edges as the Mermaid text."""
    example = _example_pipeline(tmp_path / "example")
    larger = _ordered_pipeline(tmp_path / "larger")

    example_dot = _draw(example, tmp_path / "example.dot")
    larger_dot = _draw(larger, tmp_path / "larger.dot")
    larger_mermaid = _draw(larger, tmp_path / "larger.mmd")

    assert example_dot == _dot_sample()
    assert _parse_dot(larger_dot) == _parse_mermaid(larger_mermaid), (
        larger_dot,
        larger_mermaid,
    )
    assert len(_parse_dot(larger_dot).nodes) == 8, larger_dot


def test_md_is_the_mermaid_text_in_a_mermaid_code_block(tmp_path: Path) -> None:
    """graph() to a .md file writes '```mermaid', a newline, the Mermaid text that a .mmd file gets, and '```' with a final newline."""
    pipeline = _ordered_pipeline(tmp_path)

    mermaid = _draw(pipeline, tmp_path / "graph.mmd")
    markdown = _draw(pipeline, tmp_path / "graph.md")

    assert markdown == f"```mermaid\n{mermaid}```\n"


@pytest.mark.parametrize("name", ["graph.MMD", "graph.Dot", "graph.gv"])
def test_the_extension_is_matched_without_regard_to_case_and_gv_is_dot(
    tmp_path: Path, name: str
) -> None:
    """graph.MMD gets Mermaid text, and graph.Dot and graph.gv get DOT text."""
    pipeline = _example_pipeline(tmp_path)

    text = _draw(pipeline, tmp_path / name)

    expected = _mermaid_sample() if name == "graph.MMD" else _dot_sample()
    assert text == expected


@pytest.mark.parametrize("name", ["graph.txt", "graph", "graph.jpeg"])
def test_an_unknown_extension_is_refused_naming_it_and_the_formats_that_work(
    tmp_path: Path, name: str
) -> None:
    """graph() to a file with an extension that is not a text or image format raises ValueError naming the extension (or saying there is none) and each of .mmd, .md, .dot, .gv, .svg, .png and .pdf, and writes no file."""
    pipeline = _example_pipeline(tmp_path)
    graphs = tmp_path / "graphs"
    graphs.mkdir()

    with pytest.raises(ValueError) as info:
        graph(pipeline, graphs / name)

    message = str(info.value)
    extension = Path(name).suffix
    if extension:
        assert _names_extension(message, extension), message
    else:
        says_none = r"(?i)\bno\b[^.]*\bextension|\bextension\b[^.]*\bnone\b"
        assert re.search(says_none, message), message
    for known in _EXTENSIONS:
        assert _names_extension(message, known), (known, message)
    assert list(graphs.iterdir()) == []


def test_an_image_without_dot_on_path_is_refused_naming_graphviz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no 'dot' program on PATH, graph() to a .svg file raises ValueError naming '.svg', Graphviz's dot program, the text formats, and 'conda install graphviz', and writes no file."""
    pipeline = _example_pipeline(tmp_path)
    graphs = tmp_path / "graphs"
    graphs.mkdir()
    empty = tmp_path / "empty_bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    with pytest.raises(ValueError) as info:
        graph(pipeline, graphs / "graph.svg")

    message = str(info.value)
    assert _names_extension(message, ".svg"), message
    assert "Graphviz" in message, message
    assert re.search(r"\bdot\b", message.replace(".dot", "")), message
    for known in _TEXT_EXTENSIONS:
        assert _names_extension(message, known), (known, message)
    assert "conda install graphviz" in message, message
    assert list(graphs.iterdir()) == []


@pytest.mark.parametrize("extension", [".svg", ".png", ".pdf"])
def test_an_image_is_drawn_by_dot_from_the_dot_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extension: str
) -> None:
    """With a fake 'dot' program first on PATH that records its arguments and standard input, graph() to an image file runs it once with only the argument -T<format> (svg, png or pdf), gives it the DOT text that a .dot file would get on standard input, and writes the bytes dot wrote to standard output to the path."""
    fake = install_fake_dot(tmp_path / "fake", monkeypatch)
    pipeline = _example_pipeline(tmp_path)
    dot_text = _draw(pipeline, tmp_path / "graph.dot")
    image = tmp_path / f"graph{extension}"

    graph(pipeline, image)

    assert fake.calls() == [[f"-T{extension[1:]}"]]
    assert fake.stdin() == dot_text
    assert image.read_bytes() == FAKE_IMAGE


def test_a_failing_dot_raises_graph_error_with_its_status_and_error_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a fake 'dot' that writes 'boom' to standard error and exits 3, graph() to a .png raises GraphError whose message names '.png', the status 3 and 'boom'."""
    install_fake_dot(tmp_path / "fake", monkeypatch, status=3, stderr="boom\n")
    pipeline = _example_pipeline(tmp_path)

    with pytest.raises(GraphError) as info:
        graph(pipeline, tmp_path / "graph.png")

    message = str(info.value)
    assert _names_extension(message, ".png"), message
    assert "boom" in message, message
    # A pytest folder such as 'pytest-3' must not stand in for the status.
    without_paths = message.replace(str(tmp_path.resolve()), "").replace(str(tmp_path), "")
    assert re.search(r"\b3\b", without_paths), message


def test_a_failing_dot_writes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a fake 'dot' that writes an image to standard output only when it succeeds and here exits 3, graph() to a .png that did not exist raises GraphError and leaves no file at the path."""
    install_fake_dot(tmp_path / "fake", monkeypatch, status=3, stderr="boom\n")
    pipeline = _example_pipeline(tmp_path)
    graphs = tmp_path / "graphs"
    graphs.mkdir()

    with pytest.raises(GraphError):
        graph(pipeline, graphs / "graph.png")

    assert list(graphs.iterdir()) == []


def test_an_image_into_a_missing_folder_raises_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a fake 'dot' that succeeds, graph() to a .png whose folder does not exist raises OSError and creates no folder."""
    install_fake_dot(tmp_path / "fake", monkeypatch)
    pipeline = _example_pipeline(tmp_path)

    with pytest.raises(OSError):
        graph(pipeline, tmp_path / "missing" / "graph.png")

    assert not (tmp_path / "missing").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to any folder")
def test_an_image_into_a_folder_that_cannot_be_written_raises_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a fake 'dot' that succeeds, graph() to a .png in an existing read-only folder raises OSError and leaves the folder empty."""
    install_fake_dot(tmp_path / "fake", monkeypatch)
    pipeline = _example_pipeline(tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        with pytest.raises(OSError):
            graph(pipeline, locked / "graph.png")

        assert list(locked.iterdir()) == []
    finally:
        locked.chmod(0o755)


# What graph() touches


def test_graph_runs_nothing_and_writes_only_its_file(tmp_path: Path) -> None:
    """graph() of a pipeline, before it has run and after a local run, runs no step and changes no file under the output root or the pipeline's state, apart from the graph file when it is written there."""
    pipeline = _example_pipeline(tmp_path)
    before = snapshot_files(tmp_path)

    graph(pipeline, tmp_path / "before.mmd")

    assert changed_files(before, snapshot_files(tmp_path)) == ["before.mmd"]
    assert not (tmp_path / "output").exists()

    run(pipeline)
    assert (tmp_path / "output" / "example" / "summary.csv").exists()
    assert pipeline.state.is_relative_to(tmp_path.resolve())
    age_files(tmp_path)
    before = snapshot_files(tmp_path)

    graph(pipeline, tmp_path / "output" / "after.mmd")

    assert changed_files(before, snapshot_files(tmp_path)) == ["output/after.mmd"]


def test_a_step_added_after_graph_is_not_drawn(tmp_path: Path) -> None:
    """graph() draws the steps added so far; a step added to the Pipeline after the call does not change the file already written."""
    p = Pipeline(tmp_path / "out", "run1", state=tmp_path / "state")
    p.add_python_step(_constant, inputs={}, outputs="first.parquet", name="first")
    target = tmp_path / "graph.mmd"

    graph(p, target)
    written = target.read_bytes()
    p.add_python_step(
        _one, inputs={"source": "first.parquet"}, outputs="second.parquet", name="second"
    )

    assert target.read_bytes() == written
    assert _parse_mermaid(written.decode()).boxes() == [("s0", "first\npython step")]
    again = _parse_mermaid(_draw(p, tmp_path / "again.mmd"))
    assert again.boxes() == [("s0", "first\npython step"), ("s1", "second\npython step")]


def test_graph_into_a_missing_folder_raises_os_error(tmp_path: Path) -> None:
    """graph() to a path whose folder does not exist raises OSError and creates no folder."""
    pipeline = _example_pipeline(tmp_path)

    with pytest.raises(OSError):
        graph(pipeline, tmp_path / "missing" / "graph.mmd")

    assert not (tmp_path / "missing").exists()


def test_graph_of_something_that_is_not_a_pipeline_raises_type_error(
    tmp_path: Path,
) -> None:
    """graph() given an object that is not a Pipeline raises TypeError and writes no file."""
    pipeline = _example_pipeline(tmp_path)
    graphs = tmp_path / "graphs"
    graphs.mkdir()

    for not_a_pipeline in (object(), None, freeze(pipeline)):
        with pytest.raises(TypeError):
            graph(not_a_pipeline, graphs / "graph.mmd")  # type: ignore[arg-type]

    assert list(graphs.iterdir()) == []
