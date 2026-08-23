"""Project configuration: which rules run, how loudly, and with what settings.

Discovered from `docfix.toml` in the working directory or any ancestor, or from
a `[tool.docfix]` table in `pyproject.toml`. A project can then be checked with
a bare `docfix check .` rather than a pile of flags.

```toml
[tool.docfix]
template = "technical"
exclude = ["vendor/**"]

[tool.docfix.rules]
"quotes-mixed" = false        # off
"heading-skip" = "error"      # louder

[tool.docfix.options]
"cv-bullet-too-long" = { max_length = 180 }
```
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

try:  # tomllib is stdlib from 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

CONFIG_NAME = "docfix.toml"
PYPROJECT = "pyproject.toml"

SEVERITIES = ("error", "warning", "info")
TOP_LEVEL_KEYS = {"template", "exclude", "rules", "options", "cv"}


class ConfigError(ValueError):
    """Raised when a configuration file is malformed."""


@dataclass
class Config:
    """Resolved configuration. The default instance changes nothing."""

    template: str | None = None
    exclude: list[str] = field(default_factory=list)
    # rule id -> False to disable, or a severity name to override it.
    rules: dict[str, bool | str] = field(default_factory=dict)
    # rule id -> {option: value}
    options: dict[str, dict[str, Any]] = field(default_factory=dict)
    cv: bool | None = None
    # Where this came from, for `docfix rules` and error messages.
    source: str | None = None

    def enabled(self, rule: str) -> bool:
        return self.rules.get(rule, True) is not False

    def severity(self, rule: str, default: str) -> str:
        setting = self.rules.get(rule)
        return setting if isinstance(setting, str) else default

    def option(self, rule: str, name: str, default: Any) -> Any:
        return (self.options.get(rule) or {}).get(name, default)

    def int_option(self, rule: str, name: str, default: int) -> int:
        """An integer option, with an error that says where it came from.

        Without this a bad value surfaces as a bare `invalid literal for int()`
        from deep inside a rule, naming neither the option nor the file.
        """
        value = self.option(rule, name, default)
        if isinstance(value, bool) or not isinstance(value, int):
            where = f" in {self.source}" if self.source else ""
            raise ConfigError(
                f"option {name!r} for rule {rule!r}{where} must be a whole "
                f"number; got {value!r}"
            )
        return value

    def apply(self, issues: list) -> list:
        """Drop disabled rules and apply severity overrides.

        A post-filter rather than skipping rule functions: one function can emit
        several rule ids -- `check_heading_levels` emits both `heading-skip` and
        `heading-multiple-h1` -- so skipping the function would be the wrong
        granularity.
        """
        if not self.rules:
            return issues

        from dataclasses import replace

        out = []
        for issue in issues:
            if not self.enabled(issue.rule):
                continue
            severity = self.severity(issue.rule, issue.severity)
            out.append(issue if severity == issue.severity else replace(issue, severity=severity))
        return out


DEFAULT = Config()


def _validate(data: dict, where: str) -> None:
    unknown = set(data) - TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(
            f"{where}: unknown key(s) {sorted(unknown)}; "
            f"valid keys are {sorted(TOP_LEVEL_KEYS)}"
        )

    template = data.get("template")
    if template is not None and not isinstance(template, str):
        raise ConfigError(f"{where}: 'template' must be a template name")

    exclude = data.get("exclude")
    if exclude is not None and (
        not isinstance(exclude, list) or not all(isinstance(p, str) for p in exclude)
    ):
        raise ConfigError(f"{where}: 'exclude' must be a list of glob patterns")

    cv = data.get("cv")
    if cv is not None and not isinstance(cv, bool):
        raise ConfigError(f"{where}: 'cv' must be true or false")

    rules = data.get("rules") or {}
    if not isinstance(rules, dict):
        raise ConfigError(f"{where}: 'rules' must be a table of rule settings")
    for name, setting in rules.items():
        if setting is False or setting is True:
            continue
        if isinstance(setting, str) and setting in SEVERITIES:
            continue
        raise ConfigError(
            f"{where}: rule {name!r} must be true, false, or one of "
            f"{list(SEVERITIES)}; got {setting!r}"
        )

    options = data.get("options") or {}
    if not isinstance(options, dict):
        raise ConfigError(f"{where}: 'options' must be a table of per-rule settings")
    for name, values in options.items():
        if not isinstance(values, dict):
            raise ConfigError(f"{where}: options for {name!r} must be a table")
        for option, value in values.items():
            if not isinstance(value, (str, int, float, bool)):
                raise ConfigError(
                    f"{where}: option {option!r} for {name!r} must be a single "
                    f"value, not {type(value).__name__}"
                )


def from_dict(data: dict, source: str | None = None) -> Config:
    _validate(data, source or "config")
    return Config(
        template=data.get("template"),
        exclude=list(data.get("exclude") or []),
        rules=dict(data.get("rules") or {}),
        options={k: dict(v) for k, v in (data.get("options") or {}).items()},
        cv=data.get("cv"),
        source=source,
    )


def load(path: str) -> Config:
    """Read one config file. Understands both file shapes."""
    try:
        with open(path, "rb") as handle:
            raw = tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    if os.path.basename(path) == PYPROJECT:
        data = (raw.get("tool") or {}).get("docfix")
        if data is None:
            raise ConfigError(f"{path} has no [tool.docfix] table")
    else:
        # A docfix.toml may use the bare keys or the same [tool.docfix] table.
        data = (raw.get("tool") or {}).get("docfix", raw)

    return from_dict(data, source=path)


def find(start: str | None = None) -> str | None:
    """Nearest config file, searching upward from `start`.

    `docfix.toml` wins over `pyproject.toml` in the same directory: a file named
    for the tool is a more deliberate statement than a shared table.
    """
    directory = os.path.abspath(start or os.getcwd())
    if os.path.isfile(directory):
        directory = os.path.dirname(directory)

    while True:
        candidate = os.path.join(directory, CONFIG_NAME)
        if os.path.isfile(candidate):
            return candidate

        pyproject = os.path.join(directory, PYPROJECT)
        if os.path.isfile(pyproject):
            try:
                with open(pyproject, "rb") as handle:
                    raw = tomllib.load(handle)
            except (OSError, tomllib.TOMLDecodeError):
                raw = {}
            if (raw.get("tool") or {}).get("docfix") is not None:
                return pyproject

        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def discover(
    start: str | None = None,
    explicit: str | None = None,
    use_config: bool = True,
) -> Config:
    """The config a run should use. Returns the default when there is none."""
    if not use_config:
        return Config()
    if explicit:
        return load(explicit)
    found = find(start)
    return load(found) if found else Config()


__all__ = [
    "CONFIG_NAME",
    "DEFAULT",
    "SEVERITIES",
    "Config",
    "ConfigError",
    "discover",
    "find",
    "from_dict",
    "load",
]
