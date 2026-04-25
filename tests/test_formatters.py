"""Round-trip the new qinsim formatters through pynmea2.

Existing aqps formatters are tested in aqps's own suite. These tests
cover the four sentence builders qinsim added: DBT, DPT, MTW, XDR.
"""

from __future__ import annotations

import pynmea2
import pytest

from qinsim._core.formatters.nmea_depth import build_dbt, build_dpt
from qinsim._core.formatters.nmea_xdr import XdrMeasurement, build_mtw, build_xdr


def test_build_dbt_round_trips_through_pynmea2() -> None:
    sentence = build_dbt(25.4, talker_id="SD")
    parsed = pynmea2.parse(sentence)
    assert parsed.sentence_type == "DBT"
    # The metres field is the third in the comma list and survives the
    # round trip with the documented precision.
    assert float(parsed.depth_meters) == pytest.approx(25.4, abs=0.05)


def test_build_dpt_round_trips_through_pynmea2() -> None:
    sentence = build_dpt(25.4, offset_m=0.5, talker_id="SD")
    parsed = pynmea2.parse(sentence)
    assert parsed.sentence_type == "DPT"
    assert float(parsed.depth) == pytest.approx(25.4, abs=0.05)
    assert float(parsed.offset) == pytest.approx(0.5, abs=0.05)


def test_build_mtw_round_trips_through_pynmea2() -> None:
    sentence = build_mtw(18.5, talker_id="YX")
    parsed = pynmea2.parse(sentence)
    assert parsed.sentence_type == "MTW"
    assert float(parsed.temperature) == pytest.approx(18.5, abs=0.05)


def test_build_xdr_round_trips_two_quads_through_pynmea2() -> None:
    quads = [
        XdrMeasurement(type_code="P", value=1.013, unit="B", identifier="BARO"),
        XdrMeasurement(type_code="C", value=18.5, unit="C", identifier="TEMP"),
    ]
    sentence = build_xdr(quads)
    parsed = pynmea2.parse(sentence)
    assert parsed.sentence_type == "XDR"
    # pynmea2 surfaces XDR fields as a flat tuple of (type, value, unit,
    # id) repeated; the wire content is what we actually care about for
    # interop with Qinsy, so verify by string-matching the body.
    assert "P,1.01,B,BARO" in str(parsed)
    assert "C,18.50,C,TEMP" in str(parsed)


def test_build_xdr_rejects_empty_measurements() -> None:
    with pytest.raises(ValueError):
        build_xdr([])


def test_xdr_measurement_rejects_multichar_type_code() -> None:
    with pytest.raises(ValueError):
        XdrMeasurement(type_code="CC", value=1.0, unit="C", identifier="TEMP")


def test_xdr_measurement_rejects_empty_identifier() -> None:
    with pytest.raises(ValueError):
        XdrMeasurement(type_code="C", value=1.0, unit="C", identifier="")
