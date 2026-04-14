.. _sources_and_metadata_tutorial:

======================
Sources and Metadata
======================

One of the key features of ``LayeredConfigTree`` is **provenance tracking**:
every value records which layer it was set at and where it came from.

Setting Sources
===============

The ``source`` parameter on :meth:`~layered_config_tree.main.LayeredConfigTree.update`
records the origin of a value:

.. testcode::

    from layered_config_tree import LayeredConfigTree

    config = LayeredConfigTree(layers=["file", "env", "cli"])

    config.update(
        {"log_level": "WARNING", "workers": 4},
        layer="file",
        source="config.yaml",
    )
    config.update(
        {"log_level": "DEBUG"},
        layer="env",
        source="LOG_LEVEL env var",
    )
    config.update(
        {"workers": 1},
        layer="cli",
        source="--workers flag",
    )

If ``source`` is omitted, it defaults to ``None`` while data passed at construction
time gets the source ``"initial data"``:

.. testcode::

    defaults = LayeredConfigTree({"timeout": 30}, layers=["base", "override"])
    defaults.update({"timeout": 60})
    print(repr(defaults))

.. testoutput::

    timeout:
        override: 60
            source: None
        base: 30
            source: initial data

When loading from a YAML file path, the file path is automatically used as the source .

Inspecting Metadata
===================

Use ``repr()`` to see all layers and their sources at a glance:

.. testcode::

    print(repr(config))

.. testoutput::

    log_level:
        env: DEBUG
            source: LOG_LEVEL env var
        file: WARNING
            source: config.yaml
    workers:
        cli: 1
            source: --workers flag
        file: 4
            source: config.yaml

You can also inspect metadata for a specific key programmatically using
:meth:`~layered_config_tree.main.LayeredConfigTree.metadata`:

.. testcode::

    for entry in config.metadata("workers"):
        print(f"layer={entry['layer']}, value={entry['value']}, source={entry['source']}")

.. testoutput::

    layer=file, value=4, source=config.yaml
    layer=cli, value=1, source=--workers flag

Each entry is a dictionary with ``'layer'``, ``'value'``, and ``'source'``
keys, ordered from lowest to highest priority.

Tracking Unused Keys
====================

The :meth:`~layered_config_tree.main.LayeredConfigTree.unused_keys` method returns
a list of keys that have been set but never read. This is helpful for detecting
typos or leftover configuration:

.. testcode::

    fresh = LayeredConfigTree({"a": 1, "b": 2, "c": 3})

    # Only read 'a'
    _ = fresh.a

    print(fresh.unused_keys())

.. testoutput::

    ['b', 'c']

.. _loading_from_yaml:
