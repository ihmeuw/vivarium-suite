.. _layers_and_priority_tutorial:

====================
Layers and Priority
====================

This tutorial explains how layers work and how priority determines which value
you see when multiple layers define the same key.

Defining Layers
===============

Layers are defined when you create a ``LayeredConfigTree``. The order matters:
later layers have higher priority and override earlier ones.

.. testcode::

    from layered_config_tree import LayeredConfigTree

    tree = LayeredConfigTree(
        layers=["defaults", "component", "user"]
    )

In this example, ``"user"`` has the highest priority, followed by
``"component"``, then ``"defaults"``.

How Priority Works
==================

When you read a value, the tree returns the value from the highest-priority
layer that has it set:

.. testcode::

    tree.update({"timeout": 30}, layer="defaults", source="system defaults")
    tree.update({"timeout": 60}, layer="component", source="web server component")
    tree.update({"timeout": 10}, layer="user", source="command line")

    print(tree.timeout)

.. testoutput::

    10

``user`` is the highest priority layer and set ``timeout`` to 10, so that's what we
get (even though the ``component`` and ``default`` layers also have values).

Partial Overrides
=================

You don't need to override everything at every layer. Values are only
overridden where explicitly set:

.. testcode::

    config = LayeredConfigTree(layers=["defaults", "overrides"])
    config.update(
        {"server": {"host": "localhost", "port": 8080, "debug": True}},
        layer="defaults",
    )
    config.update(
        {"server": {"host": "prod.example.com", "debug": False}},
        layer="overrides",
    )

    # 'host' and 'debug' are overridden; 'port' comes from defaults
    print(config.server.host)
    print(config.server.port)
    print(config.server.debug)

.. testoutput::

    prod.example.com
    8080
    False

Reading from a Specific Layer
=============================

Normally you get the highest-priority value. However, you can request a value
from a *specific* layer using the :meth`~layered_config_tree.main.LayeredConfigTree.get`
with the ``layer`` argument:

.. testcode::

    print(config.server.get("host", layer="defaults"))
    print(config.server.get("host", layer="overrides"))

.. testoutput::

    localhost
    prod.example.com

If the requested layer doesn't have a value for that key, a ``MissingLayerError`` 
is raised:

.. testcode::

    try:
        config.server.get("port", layer="overrides")
    except Exception as e:
        print(type(e).__name__)

.. testoutput::

    MissingLayerError

.. note::

    You can only request values from a specific layer via the :meth:`~layered_config_tree.main.LayeredConfigTree.get` 
    method. Dot notation access (e.g. ``config.server.host``) and the 
    :meth:`~layered_config_tree.main.LayeredConfigTree.get_tree` method always return 
    the highest-priority values.

.. note::

    The interaction between the :meth:`~layered_config_tree.main.LayeredConfigTree.get`
    ``default_value`` and ``layer`` arguments may sometimes be a cause of confusion. 
    The ``default_value`` at a requested ``layer`` will only be returned *if the requested 
    value does not exist at all at any layer*. If the requested value *does* exist - 
    just not at the requested layer - then you’ll get a ``MissingLayerError``.

    .. testcode::

        # url does not exist at any layer, so default_value is returned
        print(config.get("url", default_value="http://example.com", layer="overrides"))

        # port exists at base layer but not override layer, so MissingLayerError is raised
        try:
            print(config.get(["server", "port"], default_value=80, layer="overrides"))
        except Exception as e:
            print(type(e).__name__)


    .. testoutput::

        http://example.com
        MissingLayerError

No Duplicate Values per Layer
=============================

Each key can only be set **once** per layer. Attempting to set the same key
at the same layer a second time raises a ``DuplicatedConfigurationError``:

.. testcode::

    dup = LayeredConfigTree(layers=["base"])
    dup.update({"x": 1}, layer="base")

    try:
        dup.update({"x": 2}, layer="base")
    except Exception as e:
        print(type(e).__name__)

.. testoutput::

    DuplicatedConfigurationError

This prevents accidental overwrites within the same priority level.
