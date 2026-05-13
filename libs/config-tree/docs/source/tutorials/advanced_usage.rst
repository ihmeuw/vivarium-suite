.. _advanced_usage_tutorial:

==============
Advanced Usage
==============

This tutorial covers additional features of :class:`~layered_config_tree.main.LayeredConfigTree`,
including freezing, iteration, deletion, and error handling.

Freezing a Tree
===============

Call :meth:`~layered_config_tree.main.LayeredConfigTree.freeze` to make a tree
read-only. This recursively freezes all children:

.. testcode::

    from layered_config_tree import LayeredConfigTree

    config = LayeredConfigTree({"database": {"host": "localhost", "port": 5432}})
    config.freeze()

    try:
        config.database.host = "new-host"
    except Exception as e:
        print(type(e).__name__)

.. testoutput::

    ConfigurationError

Freezing is useful to enforce that configuration is not accidentally modified
after initialization.

Iterating Over Keys
===================

``LayeredConfigTree`` supports dictionary-style iteration:

.. testcode::

    config = LayeredConfigTree({"a": 1, "b": 2, "c": 3})

    for key in config:
        print(key)

.. testoutput::

    a
    b
    c

You can also use :meth:`~layered_config_tree.main.LayeredConfigTree.keys`,
:meth:`~layered_config_tree.main.LayeredConfigTree.values`, and
:meth:`~layered_config_tree.main.LayeredConfigTree.items`:

.. testcode::

    print(list(config.keys()))
    print(len(config))

.. testoutput::

    ['a', 'b', 'c']
    3

Membership Testing
==================

Use ``in`` to check whether a key exists:

.. testcode::

    print("a" in config)
    print("missing" in config)

.. testoutput::

    True
    False

Deleting Keys
=============

Keys can be removed using ``del``:

.. testcode::

    del config["c"]
    print(list(config.keys()))

.. testoutput::

    ['a', 'b']

Error Handling
==============

The package provides a hierarchy of specific exceptions:

- :class:`~layered_config_tree.exceptions.ConfigurationError` — base class for all
  configuration errors.
- :class:`~layered_config_tree.exceptions.ConfigurationKeyError` — a key doesn't exist
  (also a :class:`KeyError`).
- :class:`~layered_config_tree.exceptions.DuplicatedConfigurationError` — a key is set
  twice at the same layer.
- :class:`~layered_config_tree.exceptions.MissingLayerError` — a value exists but not at
  the requested layer.
- :class:`~layered_config_tree.exceptions.ImproperAccessError` — a dunder key is accessed
  with dot notation.

Since ``ConfigurationKeyError`` inherits from both ``ConfigurationError`` and
``KeyError``, you can catch it with either:

.. testcode::

    tree = LayeredConfigTree({"a": 1})

    try:
        tree["nonexistent"]
    except KeyError:
        print("caught as KeyError")

.. testoutput::

    caught as KeyError
