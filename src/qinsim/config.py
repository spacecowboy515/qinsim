"""YAML scenario loader + hand-rolled validator.

A qinsim scenario is one YAML file describing the destination(s), the
drivers to run, their per-tick state, and the fault-injection effects
to install on each driver's channel. The schema is small enough that a
hand-rolled walker beats Pydantic on size and import time — and the
exe is tens of MB lighter for it.

Errors raise :class:`ConfigError` carrying the dotted YAML path
(``drivers.gnss_primary.rate_hz``) and a human-readable reason. The
TUI catches that and surfaces a one-line diagnostic; the operator
edits the YAML and re-presses the scenario key.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

VALID_KINDS = ("gnss", "heading", "motion", "depth", "env")


class ConfigError(Exception):
    """Raised on any structural or value error in a scenario YAML.

    The string form is ``<dotted.path>: <reason>`` so the TUI can render
    it without further formatting.
    """

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


@dataclass(frozen=True)
class Destination:
    """One UDP destination — every driver's channel sends to all of these."""

    host: str
    port: int


@dataclass
class EffectSpec:
    """Channel effect, in dict form ready for ``effect_from_dict``."""

    kind: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, **self.params}


@dataclass
class DriverSpec:
    """One driver's full spec — kind, rate, initial state, effects."""

    name: str
    kind: str
    rate_hz: float
    state: dict[str, Any] = field(default_factory=dict)
    effects: list[EffectSpec] = field(default_factory=list)


@dataclass
class Config:
    """Top-level scenario config."""

    name: str
    destinations: list[Destination]
    drivers: list[DriverSpec]


# ---------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------


def load_config(path: Path) -> Config:
    """Read ``path``, parse YAML, validate, and return a :class:`Config`.

    Raises :class:`ConfigError` for any structural / value problem and
    :class:`FileNotFoundError` if the path does not exist.
    """
    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError("<root>", f"YAML parse error: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("<root>", "top-level must be a mapping")
    return validate_config(raw, default_name=path.stem)


def validate_config(raw: Mapping[str, Any], *, default_name: str = "scenario") -> Config:
    """Walk ``raw`` and produce a typed :class:`Config` or raise."""
    name = raw.get("name", default_name)
    if not isinstance(name, str):
        raise ConfigError("name", "must be a string")

    destinations = _validate_destinations(raw.get("destinations"))
    drivers = _validate_drivers(raw.get("drivers"))
    return Config(name=name, destinations=destinations, drivers=drivers)


def _validate_destinations(raw: Any) -> list[Destination]:
    if raw is None or raw == []:
        raise ConfigError("destinations", "at least one destination required")
    if not isinstance(raw, list):
        raise ConfigError("destinations", "must be a list")
    out: list[Destination] = []
    for i, entry in enumerate(raw):
        path = f"destinations[{i}]"
        if not isinstance(entry, dict):
            raise ConfigError(path, "must be a mapping with host + port")
        host = entry.get("host")
        port = entry.get("port")
        if not isinstance(host, str) or not host:
            raise ConfigError(f"{path}.host", "must be a non-empty string")
        if not isinstance(port, int) or not (0 < port < 65536):
            raise ConfigError(f"{path}.port", "must be an integer in 1..65535")
        out.append(Destination(host=host, port=port))
    return out


def _validate_drivers(raw: Any) -> list[DriverSpec]:
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("drivers", "must be a non-empty mapping of name -> spec")
    out: list[DriverSpec] = []
    for name, spec in raw.items():
        if not isinstance(name, str) or not name:
            raise ConfigError("drivers", f"driver name must be a non-empty string, got {name!r}")
        out.append(_validate_driver(name, spec))
    return out


def _validate_driver(name: str, raw: Any) -> DriverSpec:
    base = f"drivers.{name}"
    if not isinstance(raw, dict):
        raise ConfigError(base, "must be a mapping")

    kind = raw.get("kind")
    if kind not in VALID_KINDS:
        raise ConfigError(f"{base}.kind", f"must be one of {list(VALID_KINDS)}, got {kind!r}")

    rate = raw.get("rate_hz")
    if not isinstance(rate, (int, float)) or rate <= 0:
        raise ConfigError(f"{base}.rate_hz", "must be a positive number")

    state = raw.get("state", {})
    if not isinstance(state, dict):
        raise ConfigError(f"{base}.state", "must be a mapping")

    effects_raw = raw.get("effects", [])
    if not isinstance(effects_raw, list):
        raise ConfigError(f"{base}.effects", "must be a list")
    effects = [_validate_effect(f"{base}.effects[{i}]", e) for i, e in enumerate(effects_raw)]

    return DriverSpec(
        name=name,
        kind=kind,
        rate_hz=float(rate),
        state=dict(state),
        effects=effects,
    )


def _validate_effect(path: str, raw: Any) -> EffectSpec:
    if not isinstance(raw, dict):
        raise ConfigError(path, "must be a mapping with a 'kind' field")
    kind = raw.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ConfigError(f"{path}.kind", "must be a non-empty string")
    # Everything-but-kind becomes the param dict. effect_from_dict
    # validates the inner shape per effect type — we don't duplicate
    # that here, just surface the wrapped error cleanly later.
    params = {k: v for k, v in raw.items() if k != "kind"}
    return EffectSpec(kind=kind, params=params)


# ---------------------------------------------------------------------
# Scenario discovery
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioEntry:
    """One scenario as seen by the picker."""

    path: Path
    name: str


def list_scenarios(directory: Path) -> list[ScenarioEntry]:
    """Return ``*.yaml`` files in ``directory``, sorted by filename.

    Empty list if the directory does not exist — the runtime treats
    that as "operator hasn't extracted bundled scenarios yet" and the
    CLI bootstrap routine handles it.
    """
    if not directory.is_dir():
        return []
    entries: list[ScenarioEntry] = []
    for path in sorted(directory.glob("*.yaml")):
        entries.append(ScenarioEntry(path=path, name=path.stem))
    return entries
