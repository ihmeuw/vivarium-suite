.. _getting_started_tutorial:

===============
Getting Started
===============

This tutorial introduces :class:`~layered_config_tree.main.LayeredConfigTree`, a 
configuration data structure where values can be set at multiple priority layers. 
By default, reading a value returns the one from the highest-priority layer that 
has it defined.

Creating a Tree
===============

At its simplest, a tree can be created from a dictionary:

.. testcode::

    from layered_config_tree import LayeredConfigTree

    print(LayeredConfigTree({"greeting": "hello"}))

.. testoutput::

    greeting:
        base: hello

Note in the example above that by default a single `"base"` layer is used. You can 
also specify layers in order from lowest to highest priority. For more on layers and
priority, see :ref:`layers_and_priority_tutorial`.

.. note::

    Layers only pertain to values, not to sub-trees.

Adding Data
===========

Use :meth:`~layered_config_tree.main.LayeredConfigTree.update` to add data at a
specific layer. Data is provided as a (possibly nested) dictionary:

.. testcode::

    tree = LayeredConfigTree(layers=["base", "override"])
    tree.update(
        {"name": "some_service", "database": {"host": "localhost", "port": 5432}},
        layer="base",
        source="defaults.yaml",
    )
    tree.update(
        {"database": {"host": "prod-server"}},
        layer="override",
        source="environment",
    )

The ``source`` parameter is optional metadata that records *where* a value came
from, which is useful for debugging.

In addition to calling the ``update()`` method, you can update an existing key using
direct assignment (``=``). This sets the value at the highest-priority layer available
for that key and records the source as ``None``:

.. testcode::

    t = LayeredConfigTree({"x": 1}, layers=["base", "override"])
    t.x = 99
    print(repr(t))

.. testoutput::

    x:
        override: 99
            source: None
        base: 1
            source: initial data

If a value has already been set for that key at the highest priority level, a ``DuplicatedConfigurationError``
is raised:

.. testcode::

    try:
        t.x = 88
    except Exception as e:
        print(type(e).__name__)
    
.. testoutput::

    DuplicatedConfigurationError

New keys **cannot** be created via assignment — you must use :meth:`~layered_config_tree.main.LayeredConfigTree.update`
for that:

.. testcode::

    try:
        t.new_key = 5
    except Exception as e:
        print(type(e).__name__)

.. testoutput::

    ConfigurationKeyError

In additional to providing data directly, you can initialize or update a tree from 
YAML strings or a path to a YAML file.

.. testcode::

    print(LayeredConfigTree("server:\n  host: localhost\n  port: 8080\n"))

.. testoutput::

    server:
        host:
            base: localhost
        port:
            base: 8080

Reading Values
==============

There are four ways to read from a ``LayeredConfigTree``, each with different behavior.

.. note::

    All four access methods can be chained together and/or mixed and matched as desired.

Dot and bracket notation
------------------------

Both dot access (``tree.key``) and bracket notation (``tree["key"]``) return the 
child of the highest-priority layer:

.. testcode::

    print(tree.name)
    print(tree["database"])

.. testoutput::

    some_service
    host:
        override: prod-server
    port:
        base: 5432

Notice that ``host`` returns ``"prod-server"`` (from the ``override`` layer), not
``"localhost"`` (from the ``base`` layer). The ``port`` value was only set at the
``base`` layer, so that value is returned.

A ``ConfigurationKeyError`` will be raised of the requested key does not exist at any layer.

.. note::

    Keys that look like Python dunder attributes (starting and ending with double
    underscores __) can only be accessed via bracket notation to avoid conflicting
    with Python’s internal attribute machinery:

    .. testcode::

        answer = LayeredConfigTree({"__special__": 42})

        # Bracket notation works
        print(answer["__special__"])

        # Dot notation raises an error
        try:
            answer.__special__
        except Exception as e:
            print(type(e).__name__)

    .. testoutput::

        42
        ImproperAccessError

.. note::

    Keys that are not valid Python variable names (e.g. those containing spaces or
    special characters) can also only be accessed via bracket notation:
    
    .. testcode::

        weird = LayeredConfigTree({"space key": "foo", "dash-key": "bar"})

        # Bracket notation works
        print(weird["space key"])
        print(weird["dash-key"])

    .. testoutput::

        foo
        bar

    Dot notation will not work for these keys. ``weird.space key`` is a
    ``SyntaxError`` (Python cannot parse it at all), and ``weird.dash-key`` is
    interpreted as ``weird.dash - key``, which raises a ``ConfigurationKeyError``
    because ``"dash"`` is not a key in the tree.

    .. testcode::

        try:
            print(weird.dash-key)
        except Exception as e:
            print(type(e).__name__)

    .. testoutput::

        ConfigurationKeyError

get() method access
-------------------

:meth:`~layered_config_tree.main.LayeredConfigTree.get` works like :meth:`dict.get` 
and returns a default value (``None`` by default) when the key is missing instead of 
raising an error. It also accepts a list of keys for nested lookups and supports a 
``layer`` parameter to read from a specific layer:

.. testcode::

    print(tree.get("name"))                                 # same as dot access
    print(tree.get("missing"))                              # returns None
    print(tree.get("missing", default_value="fallback"))    # custom default
    print(tree.get(["database", "host"]))                   # nested lookup
    print(tree.database.get("host", layer="base"))          # specific layer

.. testoutput::

    some_service
    None
    fallback
    prod-server
    localhost
       
get_tree() method access
------------------------

:meth:`~layered_config_tree.main.LayeredConfigTree.get_tree` *guarantees* the result 
is a sub-tree. Note that it does *not* support a ``layer`` argument or return a default
value like :meth:`~layered_config_tree.main.LayeredConfigTree.get`.

.. testcode::

    print(tree.get_tree("database"))  # OK — returns a sub-tree

.. testoutput::

    host:
        override: prod-server
    port:
        base: 5432

If the value at the key path is a leaf, it raises ``ConfigurationError``:

.. testcode::

    try:
        tree.get_tree("name")
    except Exception as e:
        print(type(e).__name__)

.. testoutput::

    ConfigurationError

Printing a Tree
===============

Printing a tree (``str``) shows each value at its highest-priority layer:

.. testcode::

    print(tree)

.. testoutput::

    name:
        base: some_service
    database:
        host:
            override: prod-server
        port:
            base: 5432

Meanwhile, the ``repr`` shows *all* layers along with source information:

.. testcode::

    print(repr(tree))

.. testoutput::

    name:
        base: some_service
            source: defaults.yaml
    database:
        host:
            override: prod-server
                source: environment
            base: localhost
                source: defaults.yaml
        port:
            base: 5432
                source: defaults.yaml

Converting to a Dictionary
==========================

Use :meth:`~layered_config_tree.main.LayeredConfigTree.to_dict` to extract a plain
dictionary of highest priority information. 

.. testcode::

    print(tree.to_dict())

.. testoutput::

    {'name': 'some_service', 'database': {'host': 'prod-server', 'port': 5432}}

Note that all layer and source metadata is discarded.
