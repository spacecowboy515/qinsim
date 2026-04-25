"""TSS1 motion message builder.

Pure functions — no I/O, no network code. Takes field values, returns
the ASCII-encoded sentence as :class:`bytes`.

TSS1 is a 27-byte fixed-length ASCII message emitted by marine motion
sensors (Applanix POS MV, NovAtel SPAN, Kongsberg Seapath when set to
TSS1 output, Norwegian Subsea MRUs, etc.) carrying heave, roll and
pitch. Qinsy's motion pipeline consumes it as a first-class input
format alongside PSXN,20 — the two are interchangeable from a template
point of view.

Ported from ``kmall-replay/src/kmall_replay/tss1.py`` (Sam's repo);
format VERIFIED against Applanix POS MV V5, Mathworks gFLogTss1Read,
NovAtel SPAN OEM7, Norwegian Subsea API.

Wire layout (27 bytes including CR+LF):

    :XXAAAA MHHHHQMRRRR MPPPP\\r\\n

where ``:`` is a literal start character, ``XX`` is a two-hex-digit
horizontal-acceleration field, ``AAAA`` is a four-hex-digit vertical-
acceleration field, ``M`` is a sign character (space or minus),
``HHHH``/``RRRR``/``PPPP`` are four-digit magnitudes (heave in cm,
roll/pitch in hundredths of a degree), and ``Q`` is a status flag.

We never model acceleration in the simulator — Qinsy's TSS1 driver
only reads roll/pitch/heave. The accel fields are zero-filled.
"""

from __future__ import annotations


def _format_signed_field(value: float, scale: float) -> str:
    """Format a signed decimal field for TSS1.

    Returns a 5-character string: one sign char (``' '`` or ``'-'``)
    followed by four zero-padded digits. ``value`` is multiplied by
    ``scale`` (100 for hundredths) and rounded to integer. Values whose
    magnitude exceeds four digits are clamped to ``9999`` — the sensor
    would saturate rather than wrap.
    """
    scaled = round(abs(value) * scale)
    if scaled > 9999:
        scaled = 9999
    sign = "-" if value < 0 else " "
    return f"{sign}{scaled:04d}"


def build_tss1(
    heave_m: float,
    roll_deg: float,
    pitch_deg: float,
    status: str = "G",
) -> bytes:
    """Build one TSS1 motion message and return its 27-byte encoding.

    ``status`` defaults to ``'G'`` (aided, stable). Qinsy's TSS1 driver
    accepts G/F/H and a few others; G is a safe default for a steady
    simulated state. Drivers that want to model degraded aiding can
    override.

    ASSUMPTION: horizontal and vertical acceleration fields are filled
    with zeros. Qinsy's TSS1 driver only uses roll/pitch/heave, and the
    replayer's TSS1 emitter has been shipping zero-accel TSS1 for months
    without Qinsy complaint — falsifier: Qinsy logs "TSS1 no accel".
    """
    heave_field = _format_signed_field(heave_m, 100.0)
    roll_field = _format_signed_field(roll_deg, 100.0)
    pitch_field = _format_signed_field(pitch_deg, 100.0)
    msg = f":000000 {heave_field}{status}{roll_field} {pitch_field}\r\n"
    return msg.encode("ascii")
