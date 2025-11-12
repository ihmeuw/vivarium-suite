.. _getting_started_tutorial:

===============
Getting Started
===============

.. todo::

    MIC-6169. Below is a copy/paste from a slack thread that should be revised and
    improved upon.

To highlight how ``.get()`` works, let's make a two-layer tree of cats and their colors.

.. code-block:: python

    from layered_config_tree import LayeredConfigTree
    tree = LayeredConfigTree(layers=["base", "override"])

    # Add whipper with the incorrect color to the base layer
    tree.update({"pet": {"cat": {"whipper": "black"}}}, layer="base")

    # Update whipper's color and add burt macklin to the override layer
    tree.update({"pet": {"cat": {"whipper": "gray", "burt_macklin": "tuxedo"}}}, layer="override")

    tree

::

    pet:
        cat:
            whipper:
                override: gray
                    source: None
                base: black
                    source: None
            burt_macklin:
                override: tuxedo
                    source: None

.. testcode::
    :hide:
   
    from layered_config_tree import LayeredConfigTree

    tree = LayeredConfigTree(layers=["base", "override"])
    tree.update({"pet": {"cat": {"whipper": "black"}}}, layer="base")
    tree.update({"pet": {"cat": {"whipper": "gray", "burt_macklin": "tuxedo"}}}, layer="override")
    print(tree)

.. testoutput::

    pet:
        cat:
            whipper:
                override: gray
            burt_macklin:
                override: tuxedo

We can chain ``.get()`` calls to retrieve sub-trees.

.. code-block:: python

    tree.get("pet").get("cat")

::

    whipper:
        override: gray
            source: None
        base: black
            source: None
    burt_macklin:
        override: tuxedo
            source: None

Even better, we can pass a list to ``.get()`` to retrieve nested sub-trees in one call.

.. code-block:: python

    tree.get(["pet", "cat"])

::

    whipper:
        override: gray
            source: None
        base: black
            source: None
    burt_macklin:
        override: tuxedo
            source: None

This also works for ``.get_tree()``.

.. code-block:: python

    tree.get_tree(["pet", "cat"])

::

    whipper:
        override: gray
            source: None
        base: black
            source: None
    burt_macklin:
        override: tuxedo
            source: None

.. testcode::
    :hide:

    print(tree.get("pet").get("cat"))
    print(tree.get(["pet", "cat"]))
    print(tree.get_tree(["pet", "cat"]))

.. testoutput::

    whipper:
        override: gray
    burt_macklin:
        override: tuxedo
    whipper:
        override: gray
    burt_macklin:
        override: tuxedo
    whipper:    
        override: gray
    burt_macklin:
        override: tuxedo

Note that calling ``get_tree()``  will raise an error if the thing being returned
isn't actually a tree.

.. code-block:: python

    tree.get_tree(["pet", "cat", "whipper"])

::

    Traceback (most recent call last):
    File "<stdin>", line 1, in <module>
    File "/mnt/share/homes/sbachmei/repos/layered_config_tree/src/layered_config_tree/main.py", line 451, in get_tree
        raise ConfigurationError(
    layered_config_tree.exceptions.ConfigurationError: The data you accessed using ['pet', 'cat', 'whipper'] with get_tree was of type <class 'str'>, but get_tree must return a LayeredConfigTree.

``get()`` is designed to work like dict.get(), i.e. it will return a default 
(None by default) if the value doesn't exist.

.. code-block:: python

    # get burt_macklin's color
    tree.get(["pet", "cat", "burt_macklin"], default_value="oops")
    # get garfield's color - but garfield doesn't exist
    tree.get(["pet", "cat", "garfield"], default_value="oops")

::

    tuxedo
    oops

This works when using ``.get()`` to return a sub-tree as well.

.. code-block:: python

    # get the entire cat tree (hah)
    tree.get(["pet", "cat"], default_value="oops")
    # get the non-existing dog tree
    tree.get(["pet", "dog"], default_value="oops")
    
::

    whipper:
        override: gray
            source: None
        base: black
            source: None
    burt_macklin:
        override: tuxedo
            source: None
    oops

.. testcode::
    :hide:

    print(tree.get(["pet", "cat", "burt_macklin"], default_value="oops"))
    print(tree.get(["pet", "cat", "garfield"], default_value="oops"))
    print(tree.get(["pet", "cat"], default_value="oops"))
    print(tree.get(["pet", "dog"], default_value="oops"))

.. testoutput::

    tuxedo
    oops
    whipper:
        override: gray
    burt_macklin:
        override: tuxedo
    oops

Also note that ``.get_tree()`` does *not* have this functionality, i.e. there is 
no default_value arg to that method!

You can also request a specific layer from the tree using the ``layer`` argument.

.. code-block:: python

    # get whipper's color from the default layer (outermost)
    tree.get(["pet", "cat", "whipper"])
    # get whipper's color from the base layer
    tree.get(["pet", "cat", "whipper"], layer="base")

::

    gray
    black

.. testcode::
    :hide:

    print(tree.get(["pet", "cat", "whipper"]))
    print(tree.get(["pet", "cat", "whipper"], layer="base"))

.. testoutput::

    gray
    black

Note that this call will raise if the layer doesn't actually exist for a given 
sub-tree. For example, we are able to retrieve Whipper's color at the "base" layer:

.. code-block:: python

    tree.get(["pet", "cat", "whipper"], layer="base")

::

    gray

But trying to get Burt Macklin's color at the "base" layer will raise an error
since that layer doesn't exist for him:

.. code-block:: python

    tree.get(["pet", "cat", "burt_macklin"], layer="base")

::

    Traceback (most recent call last):
    File "<stdin>", line 1, in <module>
    File "/mnt/share/homes/sbachmei/repos/layered_config_tree/src/layered_config_tree/main.py", line 412, in get
        return tree.get(final_key, default_value=default_value, layer=layer)
    File "/mnt/share/homes/sbachmei/repos/layered_config_tree/src/layered_config_tree/main.py", line 406, in get
        return child.get_value(layer=layer)
    File "/mnt/share/homes/sbachmei/repos/layered_config_tree/src/layered_config_tree/main.py", line 135, in get_value
        value = self._get_value_with_source(layer)[1]
    File "/mnt/share/homes/sbachmei/repos/layered_config_tree/src/layered_config_tree/main.py", line 220, in _get_value_with_source
        raise MissingLayerError(
    layered_config_tree.exceptions.MissingLayerError: No value stored in this ConfigNode cat at layer base.

One final note. The interaction between default_value and layer may sometimes be 
a cause of confusion. The default_value will only be returned when also providing 
a specific layer if and only if the requested value doesn't exist at all. If the value does 
exist, just not at the requested layer, then you'll get the MissingLayerError.

For example, let's get Garfield's color at the "base" layer (noting that Garfield
does not exist in the tree) and provide a default return value of "foo":

.. code-block:: python

    tree.get(["pet", "cat", "garfield"], default_value="foo", layer="base")

::

    foo

.. testcode::
    :hide:

    print(tree.get(["pet", "cat", "garfield"], default_value="foo", layer="base"))

.. testoutput::

    foo

Now let's do the same thing for Burt Macklin (who *does* exist in the tree but does
not have a "base" layer defined):

.. code-block:: python

    tree.get(["pet", "cat", "burt_macklin"], default_value="foo", layer="base")

::

    Traceback (most recent call last):
    File "<stdin>", line 1, in <module>
    File "/mnt/share/homes/sbachmei/repos/layered_config_tree/src/layered_config_tree/main.py", line 412, in get
        return tree.get(final_key, default_value=default_value, layer=layer)
    File "/mnt/share/homes/sbachmei/repos/layered_config_tree/src/layered_config_tree/main.py", line 406, in get
        return child.get_value(layer=layer)
    File "/mnt/share/homes/sbachmei/repos/layered_config_tree/src/layered_config_tree/main.py", line 135, in get_value
        value = self._get_value_with_source(layer)[1]
    File "/mnt/share/homes/sbachmei/repos/layered_config_tree/src/layered_config_tree/main.py", line 220, in _get_value_with_source
        raise MissingLayerError(
    layered_config_tree.exceptions.MissingLayerError: No value stored in this ConfigNode cat at layer base.

