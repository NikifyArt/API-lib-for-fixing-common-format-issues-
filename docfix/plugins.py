"""Extension registration: adapters, rules, and third-party plugins.

Everything docfix can be extended with is registered here rather than listed in
a tuple inside the module that consumes it. That is the difference between a
fork that survives and one that rots: an adopter adding a format or a rule adds
a file and a registration call, and never edits `adapters/__init__.py` or
`detect/rules.py` -- the two files upstream also keeps changing. Their fork
rebases cleanly for as long as they care to maintain it.

Two ways in, depending on whether the extension ships with docfix or beside it:

1. **Direct registration**, for a fork or an application that already imports
   its own code::

       from docfix import register_adapter, register_rule
       register_adapter(MY_RTF_ADAPTER)
       register_rule(check_house_style, family="structure")

2. **An entry point**, for a separate distribution -- no fork at all. Declare
   one in your own `pyproject.toml`::

       [project.entry-points."docfix.plugins"]
       acme = "acme_docfix:register"

   `acme_docfix.register` is called once, with no arguments, the first time
   docfix looks for an adapter or a rule. Do the `register_*` calls in there.

A plugin that raises is contained, not fatal: the failure is recorded, the rest
of docfix keeps working, and `docfix rules` reports it. A broken third-party
package must not stop someone formatting a document.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

PLUGIN_GROUP = "docfix.plugins"

# Structure rules take (doc, config), source rules (text, config), and CV rules
# (doc, config) but only run when the document is a CV.
RULE_FAMILIES = ("structure", "source", "cv")


class PluginError(RuntimeError):
    """A registration was rejected, or an entry-point plugin failed to load."""


_adapters: list[Any] = []
_rules: dict[str, list[Callable]] = {family: [] for family in RULE_FAMILIES}
_errors: list[str] = []
_loaded = False


# --------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------


def _check_adapter(adapter: Any) -> None:
    for attribute in ("name", "extensions", "read_path", "write_path"):
        if not hasattr(adapter, attribute):
            raise PluginError(
                f"adapter is missing {attribute!r}; it must be a docfix.adapters.Adapter "
                "or something with the same shape"
            )
    if not isinstance(adapter.name, str) or not adapter.name:
        raise PluginError("adapter name must be a non-empty string")
    if not adapter.extensions:
        raise PluginError(f"adapter {adapter.name!r} claims no extensions")
    for extension in adapter.extensions:
        if not isinstance(extension, str) or not extension.startswith("."):
            raise PluginError(
                f"adapter {adapter.name!r}: extension {extension!r} must be a string "
                "beginning with a dot, like '.rtf'"
            )
    for attribute in ("read_path", "write_path"):
        if not callable(getattr(adapter, attribute)):
            raise PluginError(f"adapter {adapter.name!r}: {attribute} is not callable")


def register_adapter(adapter: Any, *, replace: bool = False) -> None:
    """Add a format adapter.

    A registered adapter is consulted before the built-in ones, so naming an
    extension docfix already handles overrides it -- that is deliberate, and how
    a fork substitutes its own PDF reader without touching the built-in.

    Registering the same *name* twice is an error unless `replace=True`, so a
    plugin loaded by accident twice is caught rather than silently duplicated.
    """
    _check_adapter(adapter)
    for index, existing in enumerate(_adapters):
        if existing.name == adapter.name:
            if not replace:
                raise PluginError(
                    f"an adapter named {adapter.name!r} is already registered; "
                    "pass replace=True to override it"
                )
            _adapters[index] = adapter
            return
    _adapters.append(adapter)


def unregister_adapter(name: str) -> bool:
    """Remove a registered adapter by name. Returns whether one was removed.

    Built-in adapters cannot be removed this way -- register one with the same
    extensions to override them instead.
    """
    for index, existing in enumerate(_adapters):
        if existing.name == name:
            del _adapters[index]
            return True
    return False


def registered_adapters() -> tuple[Any, ...]:
    """Every adapter registered from outside, most recently registered last."""
    load_plugins()
    return tuple(_adapters)


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


def register_rule(rule: Callable, *, family: str = "structure") -> None:
    """Add a detection rule to one of the three families.

    The rule must declare the ids it emits with `@emits(...)`. That is not
    ceremony: a config file addresses rules by id, so an undeclared rule could
    never be disabled, given a severity, or listed by `docfix rules`. Requiring
    the declaration means a third-party rule is configurable like any other.
    """
    if family not in RULE_FAMILIES:
        raise PluginError(
            f"unknown rule family {family!r}; expected one of {', '.join(RULE_FAMILIES)}"
        )
    if not callable(rule):
        raise PluginError("a rule must be callable")
    if not getattr(rule, "rule_ids", None):
        raise PluginError(
            f"rule {getattr(rule, '__name__', rule)!r} declares no rule ids; "
            "decorate it with @emits('my-rule-id') so it can be configured"
        )
    if rule in _rules[family]:
        return
    _rules[family].append(rule)


def unregister_rule(rule: Callable, *, family: str = "structure") -> bool:
    """Remove a registered rule. Returns whether one was removed."""
    if family not in RULE_FAMILIES:
        raise PluginError(f"unknown rule family {family!r}")
    try:
        _rules[family].remove(rule)
    except ValueError:
        return False
    return True


def registered_rules(family: str) -> tuple[Callable, ...]:
    """Every rule registered from outside for one family."""
    if family not in RULE_FAMILIES:
        raise PluginError(f"unknown rule family {family!r}")
    load_plugins()
    return tuple(_rules[family])


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def load_plugins(*, force: bool = False) -> tuple[str, ...]:
    """Discover and run every `docfix.plugins` entry point, once.

    Returns the errors collected, which is also what `plugin_errors()` reports
    later. Loading is deliberately lazy and memoised: importing docfix must not
    pay for plugins nobody asked for, and a plugin must not run twice.
    """
    global _loaded
    if _loaded and not force:
        return tuple(_errors)
    # Set before dispatching: a plugin that itself imports docfix would
    # otherwise re-enter this and run every plugin a second time.
    _loaded = True

    from importlib.metadata import entry_points

    try:
        found = entry_points(group=PLUGIN_GROUP)
    except Exception as exc:  # noqa: BLE001 - a broken environment, not our bug
        _errors.append(f"could not read {PLUGIN_GROUP} entry points: {exc}")
        return tuple(_errors)

    for entry in found:
        try:
            hook = entry.load()
        except BaseException as exc:  # noqa: BLE001 - includes native-import panics
            _errors.append(f"plugin {entry.name!r} failed to import: {exc}")
            continue
        try:
            hook()
        except BaseException as exc:  # noqa: BLE001
            _errors.append(f"plugin {entry.name!r} failed to register: {exc}")
    return tuple(_errors)


def plugin_errors() -> tuple[str, ...]:
    """Anything that went wrong loading plugins, for the CLI to surface."""
    return tuple(_errors)


def reset() -> None:
    """Forget every registration and re-arm entry-point discovery.

    For tests, and for an application that reconfigures docfix at runtime.
    """
    global _loaded
    _adapters.clear()
    for rules in _rules.values():
        rules.clear()
    _errors.clear()
    _loaded = False


__all__ = [
    "PLUGIN_GROUP",
    "RULE_FAMILIES",
    "PluginError",
    "load_plugins",
    "plugin_errors",
    "register_adapter",
    "register_rule",
    "registered_adapters",
    "registered_rules",
    "reset",
    "unregister_adapter",
    "unregister_rule",
]
