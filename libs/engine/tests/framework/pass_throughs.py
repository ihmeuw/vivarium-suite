"""Find the Builder interface methods that forward unchanged to a manager method."""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from types import FunctionType, ModuleType
from typing import Any

_FRAMEWORK_PACKAGE = "vivarium.engine.framework"


@dataclass(frozen=True)
class PassThrough:
    """An interface method whose body forwards its parameters to one manager method."""

    interface: type
    """The interface class that defines the method."""
    method_name: str
    """The name of the interface method."""
    manager: type
    """The manager class the interface forwards to."""
    target_name: str
    """The name of the manager attribute the interface method calls."""

    @property
    def qualname(self) -> str:
        """Return ``"<interface class name>.<method name>"``."""
        return f"{self.interface.__name__}.{self.method_name}"

    @property
    def interface_method(self) -> Callable[..., Any]:
        """Return the interface method as defined on the interface class."""
        method: Callable[..., Any] = self.interface.__dict__[self.method_name]
        return method

    @property
    def manager_method(self) -> Callable[..., Any] | None:
        """Return the target as a method defined on the manager class, or None.

        None means the manager class (including its bases) defines no function
        with the target name, for example when the attribute is only assigned
        on an instance at run time.
        """
        for klass in self.manager.__mro__:
            if self.target_name not in klass.__dict__:
                continue
            attribute = klass.__dict__[self.target_name]
            if isinstance(attribute, FunctionType):
                return attribute
            if isinstance(attribute, staticmethod):
                function: Callable[..., Any] = attribute.__func__
                return function
            if isinstance(attribute, classmethod):
                bound: Callable[..., Any] = getattr(self.manager, self.target_name)
                return bound
            return None
        return None


def find_pass_throughs(interface: type, manager: type) -> list[PassThrough]:
    """Find the pass-through methods that ``interface`` itself defines.

    A method is a pass-through when it is not a dunder method and its body,
    after an optional docstring, is a single statement (a ``return`` or a bare
    expression) that calls ``self._manager.<target>`` and forwards every
    parameter of the method unchanged, each exactly once and with nothing else.
    Positional arguments must forward the method's leading positional
    parameters by bare name in declared order, followed only by ``*name`` for
    the var-positional parameter once every positional parameter is forwarded.
    Every other parameter is passed as ``name=name``, or as ``**name`` for the
    var-keyword parameter. The target name may differ from the method name.

    Methods that return a bound manager method or a lambda without calling it,
    and methods that pass any argument that is not one of their own parameters,
    are not pass-throughs.

    Parameters
    ----------
    interface
        The interface class to inspect. Only methods defined in its own class
        body are considered.
    manager
        The manager class the interface forwards to.

    Returns
    -------
        The pass-throughs in the order the methods are defined.
    """
    pass_throughs = []
    for name, attribute in interface.__dict__.items():
        if _is_dunder(name) or not isinstance(attribute, FunctionType):
            continue
        target = _forwarding_target(attribute)
        if target is not None:
            pass_throughs.append(PassThrough(interface, name, manager, target))
    return pass_throughs


def find_framework_pass_throughs() -> list[PassThrough]:
    """Find every pass-through across the ``interface`` modules of ``vivarium.engine.framework``.

    Every ``vivarium.engine.framework.<subpackage>.interface`` module is scanned,
    so a newly added interface method or module is found without a list to
    update. Each ``Interface`` subclass defined in such a module is paired with
    the manager class named by the annotation of the ``manager`` parameter of
    its ``__init__``.

    Returns
    -------
        The pass-throughs, ordered by module name and then by definition order.

    Raises
    ------
    LookupError
        If the manager class named by an interface's ``__init__`` annotation
        cannot be resolved.
    """
    from vivarium.engine.manager import Interface

    framework = importlib.import_module(_FRAMEWORK_PACKAGE)
    subpackages = sorted(
        info.name for info in pkgutil.iter_modules(framework.__path__) if info.ispkg
    )

    pass_throughs: list[PassThrough] = []
    for subpackage in subpackages:
        package_name = f"{_FRAMEWORK_PACKAGE}.{subpackage}"
        interface_module = _import_if_present(f"{package_name}.interface")
        if interface_module is None:
            continue
        for attribute in list(vars(interface_module).values()):
            if (
                isinstance(attribute, type)
                and issubclass(attribute, Interface)
                and attribute is not Interface
                and attribute.__module__ == interface_module.__name__
            ):
                manager = _resolve_manager(attribute, interface_module, package_name)
                pass_throughs.extend(find_pass_throughs(attribute, manager))
    return pass_throughs


def _is_dunder(name: str) -> bool:
    """Return whether ``name`` is a dunder name."""
    return name.startswith("__") and name.endswith("__")


def _forwarding_target(function: FunctionType) -> str | None:
    """Return the manager attribute ``function`` forwards all its parameters to, if any."""
    try:
        source = textwrap.dedent(inspect.getsource(function))
        tree = ast.parse(source)
    except (OSError, TypeError, SyntaxError):
        return None
    if not tree.body or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    definition = tree.body[0]

    body = list(definition.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], (ast.Return, ast.Expr)):
        return None
    call = body[0].value
    if not isinstance(call, ast.Call):
        return None

    arguments = definition.args
    positional = [*arguments.posonlyargs, *arguments.args]
    if not positional:
        return None
    self_name = positional[0].arg
    positional_names = [argument.arg for argument in positional[1:]]
    named = {*positional_names, *(argument.arg for argument in arguments.kwonlyargs)}
    var_positional = arguments.vararg.arg if arguments.vararg else None
    var_keyword = arguments.kwarg.arg if arguments.kwarg else None

    target = call.func
    if not (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Attribute)
        and target.value.attr == "_manager"
        and isinstance(target.value.value, ast.Name)
        and target.value.value.id == self_name
    ):
        return None

    # Positional arguments bind by position, so they must repeat the method's own
    # leading positional parameters, with *args only after all of them. The length
    # checks also keep *args last.
    forwarded: list[str] = []
    for argument in call.args:
        if isinstance(argument, ast.Starred):
            if not (
                isinstance(argument.value, ast.Name)
                and argument.value.id == var_positional
                and len(forwarded) == len(positional_names)
            ):
                return None
            forwarded.append(argument.value.id)
        elif (
            isinstance(argument, ast.Name)
            and len(forwarded) < len(positional_names)
            and argument.id == positional_names[len(forwarded)]
        ):
            forwarded.append(argument.id)
        else:
            return None
    for keyword in call.keywords:
        if not isinstance(keyword.value, ast.Name):
            return None
        if keyword.arg is None:
            if keyword.value.id != var_keyword:
                return None
        elif keyword.arg != keyword.value.id or keyword.arg not in named:
            return None
        forwarded.append(keyword.value.id)

    expected = named | {name for name in (var_positional, var_keyword) if name is not None}
    if len(forwarded) != len(set(forwarded)) or set(forwarded) != expected:
        return None
    return target.attr


def _import_if_present(module_name: str) -> ModuleType | None:
    """Import ``module_name``, or return None if it does not exist."""
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name == module_name:
            return None
        raise


def _resolve_manager(
    interface: type, interface_module: ModuleType, package_name: str
) -> type:
    """Return the manager class named by the ``manager`` annotation of ``interface.__init__``."""
    parameter = inspect.signature(interface).parameters.get("manager")
    if parameter is None or parameter.annotation is inspect.Parameter.empty:
        raise LookupError(
            f"{interface.__qualname__}.__init__ has no annotated 'manager' parameter."
        )
    annotation = parameter.annotation
    if isinstance(annotation, type):
        return annotation

    name = str(annotation).strip().strip("'\"")
    namespaces: list[ModuleType] = [interface_module]
    package = _import_if_present(package_name)
    if package is not None:
        namespaces.append(package)
    manager_module = _import_if_present(f"{package_name}.manager")
    if manager_module is not None:
        namespaces.append(manager_module)

    for namespace in namespaces:
        resolved = _lookup_dotted(namespace, name)
        if isinstance(resolved, type):
            return resolved
    raise LookupError(
        f"Cannot resolve manager class {name!r} for {interface.__qualname__} in "
        f"{', '.join(namespace.__name__ for namespace in namespaces)}."
    )


def _lookup_dotted(namespace: ModuleType, name: str) -> Any:
    """Return the object a dotted ``name`` refers to within ``namespace``, or None."""
    current: Any = namespace
    for part in name.split("."):
        current = getattr(current, part, None)
        if current is None:
            return None
    return current
