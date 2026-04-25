"""Validation coverage for ``qinsim.config``."""

from __future__ import annotations

from pathlib import Path

import pytest

from qinsim.config import (
    Config,
    ConfigError,
    DriverSpec,
    EffectSpec,
    list_scenarios,
    load_config,
    validate_config,
)


def _minimal_raw() -> dict:
    return {
        "name": "test",
        "destinations": [{"host": "127.0.0.1", "port": 13130}],
        "drivers": {
            "gnss_primary": {
                "kind": "gnss",
                "rate_hz": 10,
                "state": {"latitude": 0.0, "longitude": 0.0},
            }
        },
    }


def test_minimal_valid_config_round_trips() -> None:
    cfg = validate_config(_minimal_raw())
    assert isinstance(cfg, Config)
    assert cfg.name == "test"
    assert cfg.destinations[0].host == "127.0.0.1"
    assert cfg.destinations[0].port == 13130
    assert len(cfg.drivers) == 1
    assert cfg.drivers[0].kind == "gnss"
    assert cfg.drivers[0].rate_hz == 10.0


@pytest.mark.parametrize(
    "mutation,expected_path_prefix",
    [
        # destinations
        (lambda r: r.update(destinations=[]), "destinations"),
        (lambda r: r.update(destinations="nope"), "destinations"),
        (lambda r: r["destinations"][0].pop("host"), "destinations[0].host"),
        (lambda r: r["destinations"][0].update(port=0), "destinations[0].port"),
        (lambda r: r["destinations"][0].update(port=70000), "destinations[0].port"),
        # drivers
        (lambda r: r.update(drivers={}), "drivers"),
        (lambda r: r["drivers"]["gnss_primary"].update(kind="laser"),
         "drivers.gnss_primary.kind"),
        (lambda r: r["drivers"]["gnss_primary"].update(rate_hz=-1),
         "drivers.gnss_primary.rate_hz"),
        (lambda r: r["drivers"]["gnss_primary"].update(state="nope"),
         "drivers.gnss_primary.state"),
        (lambda r: r["drivers"]["gnss_primary"].update(effects="nope"),
         "drivers.gnss_primary.effects"),
        # effect malformed
        (lambda r: r["drivers"]["gnss_primary"].update(effects=[{"prob": 0.1}]),
         "drivers.gnss_primary.effects[0].kind"),
    ],
)
def test_invalid_config_raises_with_path(mutation, expected_path_prefix) -> None:
    raw = _minimal_raw()
    mutation(raw)
    with pytest.raises(ConfigError) as exc:
        validate_config(raw)
    assert exc.value.path.startswith(expected_path_prefix)


def test_effect_spec_to_dict_includes_kind_and_params() -> None:
    spec = EffectSpec(kind="dropout", params={"prob": 0.1})
    assert spec.to_dict() == {"type": "dropout", "prob": 0.1}


def test_load_config_reads_yaml_file(tmp_path: Path) -> None:
    yaml_path = tmp_path / "scenario.yaml"
    yaml_path.write_text(
        "name: t\n"
        "destinations:\n"
        "  - {host: 127.0.0.1, port: 13130}\n"
        "drivers:\n"
        "  d:\n"
        "    kind: depth\n"
        "    rate_hz: 5\n"
        "    state: {depth_m: 10.0}\n",
        encoding="utf-8",
    )
    cfg = load_config(yaml_path)
    assert cfg.name == "t"
    assert cfg.drivers[0].kind == "depth"


def test_list_scenarios_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    assert list_scenarios(tmp_path / "nope") == []


def test_list_scenarios_sorts_by_filename(tmp_path: Path) -> None:
    (tmp_path / "b.yaml").write_text("name: b", encoding="utf-8")
    (tmp_path / "a.yaml").write_text("name: a", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("ignored", encoding="utf-8")
    entries = list_scenarios(tmp_path)
    assert [e.name for e in entries] == ["a", "b"]


def test_all_bundled_scenarios_validate(bundled_scenario_paths: list[Path]) -> None:
    """Every YAML shipped inside the package must validate cleanly."""
    assert bundled_scenario_paths, "no bundled scenarios found — bundle is empty"
    for path in bundled_scenario_paths:
        cfg = load_config(path)
        assert cfg.drivers, f"{path.name}: no drivers"
        for ds in cfg.drivers:
            assert ds.rate_hz > 0
