"""Sea-state-driven multi-sine roll/pitch/heave synthesizer.

Ports Meridian's ``MotionModel`` from ``motion_simulator.py`` into the
shared ``_core`` library so it can be reused by any driver that wants a
plausible attitude signal without modelling real hydrodynamics.

The model superposes two sinusoids per axis at vessel-characteristic
periods (tuned in Meridian for a ~70 m platform), modulates the combined
signal at a much slower period so the amplitude envelope breathes, and
adds a small amount of Gaussian noise. Amplitudes scale off a sea-state
index (0..5, Beaufort-ish) through lookup tables hand-picked by Meridian
to match SIS-recorded attitude envelopes.

Not a physical model — do not use this to evaluate motion compensation
algorithms. It exists to produce a signal that *looks* like a vessel in
a seaway so downstream software pipelines have something more realistic
than a zero trace to process.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# Amplitude envelopes at sea states 0..5. VERIFIED against Meridian's
# motion_simulator — kept identical so any recording captured against
# Meridian replays with the same numeric range against this port.
_ROLL_AMP_DEG_TABLE: tuple[float, ...] = (0.3, 0.5, 0.8, 1.2, 1.8, 2.5)
_PITCH_AMP_DEG_TABLE: tuple[float, ...] = (0.1, 0.3, 0.5, 0.8, 1.2, 1.5)
_HEAVE_AMP_M_TABLE: tuple[float, ...] = (0.1, 0.25, 0.5, 0.9, 1.2, 1.5)


@dataclass
class MotionModel:
    """Multi-sine roll/pitch/heave synth scaled by sea state."""

    # Injected for determinism — tests pass a seeded Random so phases are
    # reproducible. Production callers leave it None and get fresh phases
    # on every instantiation.
    rng: random.Random = field(default_factory=random.Random)

    # Simulation time, advanced by ``update(dt, ...)``. Kept as a float so
    # long runs don't accumulate precision loss the way a datetime would.
    t: float = 0.0

    def __post_init__(self) -> None:
        # Phase offsets pinned at construction time so the signal is
        # continuous across ticks. Each axis gets two independent phases
        # because we superpose two sinusoids per axis; a slow 'mod' phase
        # drives the amplitude envelope.
        self._phases = {
            "roll1": self.rng.uniform(0.0, 2.0 * math.pi),
            "roll2": self.rng.uniform(0.0, 2.0 * math.pi),
            "pitch1": self.rng.uniform(0.0, 2.0 * math.pi),
            "pitch2": self.rng.uniform(0.0, 2.0 * math.pi),
            "heave1": self.rng.uniform(0.0, 2.0 * math.pi),
            "heave2": self.rng.uniform(0.0, 2.0 * math.pi),
            "mod": self.rng.uniform(0.0, 2.0 * math.pi),
        }
        # Periods (seconds) tuned for a ~70 m vessel. The 60 s "mod"
        # period is what gives the output the long-wavelength envelope
        # Sam is used to seeing in recorded sea-state-3 traces.
        self._periods = {
            "roll1": 9.5, "roll2": 11.0,
            "pitch1": 7.5, "pitch2": 8.7,
            "heave1": 8.0, "heave2": 10.5,
            "mod": 60.0,
        }

    def update(self, dt_seconds: float, sea_state: int) -> tuple[float, float, float]:
        """Advance the model by ``dt_seconds`` and return ``(roll, pitch, heave)``.

        ``sea_state`` is clamped into ``[0, 5]`` so callers can feed raw
        UI-level integers without a prior bounds check. Roll/pitch are in
        degrees (starboard-down / bow-down positive). Heave is in metres
        with positive-down sign so TSS1/PSXN,20 consumers need no sign flip.
        """
        if dt_seconds < 0:
            raise ValueError("dt_seconds must be non-negative")

        self.t += dt_seconds

        sea = max(0, min(5, int(sea_state)))
        a_roll = _ROLL_AMP_DEG_TABLE[sea]
        a_pitch = _PITCH_AMP_DEG_TABLE[sea]
        a_heave = _HEAVE_AMP_M_TABLE[sea]

        # Slow amplitude modulation — 0.85..1.15 — so a steady sea state
        # still has a breathing envelope rather than a locked amplitude.
        mod = 1.0 + 0.15 * math.sin(
            2.0 * math.pi * self.t / self._periods["mod"] + self._phases["mod"]
        )

        roll = (
            0.6 * math.sin(2.0 * math.pi * self.t / self._periods["roll1"] + self._phases["roll1"])
            + 0.4 * math.sin(2.0 * math.pi * self.t / self._periods["roll2"] + self._phases["roll2"])
        )
        pitch = (
            0.6 * math.sin(2.0 * math.pi * self.t / self._periods["pitch1"] + self._phases["pitch1"])
            + 0.4 * math.sin(2.0 * math.pi * self.t / self._periods["pitch2"] + self._phases["pitch2"])
        )
        heave = (
            0.6 * math.sin(2.0 * math.pi * self.t / self._periods["heave1"] + self._phases["heave1"])
            + 0.4 * math.sin(2.0 * math.pi * self.t / self._periods["heave2"] + self._phases["heave2"])
        )

        roll_deg = a_roll * mod * roll
        pitch_deg = a_pitch * mod * pitch
        heave_m = a_heave * mod * heave

        # 5 %-of-amplitude white noise — enough to break any downstream
        # "equal to last tick" short-circuit but well below the envelope.
        roll_deg += self.rng.gauss(0.0, max(1e-6, a_roll * 0.05))
        pitch_deg += self.rng.gauss(0.0, max(1e-6, a_pitch * 0.05))
        heave_m += self.rng.gauss(0.0, max(1e-6, a_heave * 0.05))

        return roll_deg, pitch_deg, heave_m

    # ------------------------------------------------------------------
    # Envelope introspection — handy for tests and UI
    # ------------------------------------------------------------------

    @staticmethod
    def envelope(sea_state: int) -> tuple[float, float, float]:
        """Return nominal ``(roll_deg, pitch_deg, heave_m)`` envelopes.

        These are the amplitude-table values before the slow modulation
        and noise are applied. Useful for sanity-checking bounds in tests
        and for driving UI sliders that preview a sea-state intensity.
        """
        sea = max(0, min(5, int(sea_state)))
        return (
            _ROLL_AMP_DEG_TABLE[sea],
            _PITCH_AMP_DEG_TABLE[sea],
            _HEAVE_AMP_M_TABLE[sea],
        )
