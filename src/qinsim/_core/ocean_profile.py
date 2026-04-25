"""Depth-dependent ocean profile model (T, S, P, C, sound speed).

Ports Meridian's ``SVXCastSim.ProfileModel`` into ``_core`` so any driver
that needs a plausible profile as a function of depth can consume it —
SVXCast today, a future CTD port tomorrow. Pure physics: no I/O, no
timing, no dependence on driver state. Callers supply depth and config,
the model returns (temperature, salinity, pressure, conductivity,
sound_speed) snapped to realistic ocean bounds.

Formulae are documented against primary sources where a published
formula exists:

* Temperature / salinity — smooth tanh logistic between surface and
  deep endpoints centred on a thermocline depth. Not a published
  formula; chosen by Meridian to produce a visually plausible CTD
  trace. Free parameters are the endpoints + centre + thickness.
* Pressure — 10.1325 dbar surface + 1 dbar/m. ASSUMPTION: good to the
  ~1 % level for simulator purposes and matches Meridian's behaviour.
* Conductivity — empirical linear: 50 + 2·(S-35) - 0.1·(T-15) mS/cm,
  clamped to 38..62. ASSUMPTION: a Meridian hand-fit. Replace with a
  proper PSS-78 inverse only if a user presses — none have yet.
* Sound speed — DOCUMENTED: Mackenzie (1981) nine-term formula.

Gaussian noise is added per channel when the configured sigma > 0, and
the post-noise value is clamped back into physical bounds so downstream
software does not see nonsense from a pathological draw.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Tuple


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


@dataclass
class OceanProfileConfig:
    """Shape parameters + noise sigmas for :class:`OceanProfileModel`."""

    # Thermocline geometry.
    thermo_center_m: float = 50.0
    thermo_thickness_m: float = 20.0

    # Temperature endpoints (surface → deep).
    surface_temp_c: float = 20.0
    deep_temp_c: float = 4.0

    # Salinity endpoints.
    surface_sal_psu: float = 35.0
    deep_sal_psu: float = 35.2

    # Per-channel Gaussian noise standard deviations. Zero disables noise
    # for that channel — handy for golden-value tests.
    noise_temp_c: float = 0.005
    noise_sal_psu: float = 0.002
    noise_press_dbar: float = 0.05
    noise_cond_mscm: float = 0.050
    noise_sv_mps: float = 0.02


@dataclass
class OceanProfileModel:
    """Pure functions of depth + config, with optional Gaussian noise.

    Noise uses an injected :class:`random.Random` so tests pin reproducibility.
    The model is stateless between calls — the same ``(depth, config, rng_state)``
    input triple produces the same output.
    """

    config: OceanProfileConfig = field(default_factory=OceanProfileConfig)

    # ------------------------------------------------------------------
    # Per-quantity pure functions — exposed statically so callers can use
    # the model as a lookup even when no instance is handy.
    # ------------------------------------------------------------------

    @staticmethod
    def temperature_at_depth(
        z: float, surface: float, deep: float, center: float, thickness: float
    ) -> float:
        """Tanh logistic blend from ``surface`` above the thermocline to ``deep`` below."""
        k = max(1e-3, thickness / 2.0)
        return deep + (surface - deep) * 0.5 * (1.0 - math.tanh((z - center) / k))

    @staticmethod
    def salinity_at_depth(
        z: float, surface: float, deep: float, center: float, thickness: float
    ) -> float:
        """Tanh logistic for salinity; clamped to realistic open-ocean range."""
        k = max(1e-3, thickness / 2.0)
        base = deep + (surface - deep) * 0.5 * (1.0 - math.tanh((z - center) / k))
        return _clamp(base, 33.0, 36.5)

    @staticmethod
    def pressure_dbar_at_depth(z: float) -> float:
        """Absolute pressure approximation: 1 atm at surface + 1 dbar/m."""
        return 10.1325 + z * 1.0

    @staticmethod
    def conductivity_mscm(s_psu: float, t_c: float) -> float:
        """Linear empirical C(S, T), clamped to 38..62 mS/cm."""
        cond = 50.0 + 2.0 * (s_psu - 35.0) - 0.10 * (t_c - 15.0)
        return _clamp(cond, 38.0, 62.0)

    @staticmethod
    def sound_speed_mps_mackenzie(t_c: float, s_psu: float, depth_m: float) -> float:
        """Mackenzie (1981) nine-term sound speed, clamped to 1400..1600 m/s.

        DOCUMENTED formula from Mackenzie, *J. Acoust. Soc. Am.* 70, 807 (1981).
        """
        T, S, z = t_c, s_psu, depth_m
        c = (
            1448.96
            + 4.591 * T
            - 5.304e-2 * T * T
            + 2.374e-4 * T * T * T
            + 1.340 * (S - 35.0)
            + 1.630e-2 * z
            + 1.675e-7 * z * z
            - 1.025e-2 * T * (S - 35.0)
            - 7.139e-13 * T * z * z * z
        )
        return _clamp(c, 1400.0, 1600.0)

    # ------------------------------------------------------------------
    # Composite sample — what the driver actually calls each tick.
    # ------------------------------------------------------------------

    def sample(
        self,
        depth_m: float,
        rng: random.Random,
    ) -> Tuple[float, float, float, float, float]:
        """Return ``(temperature_c, salinity_psu, pressure_dbar, conductivity_mscm, sv_mps)``.

        Values include Gaussian noise per the config. Physically implausible
        draws (e.g. a large noise kick pushing salinity outside 33..36.5) are
        re-clamped before return so downstream consumers see only in-range
        values, matching Meridian's behaviour.
        """
        cfg = self.config
        t = self.temperature_at_depth(
            depth_m,
            cfg.surface_temp_c,
            cfg.deep_temp_c,
            cfg.thermo_center_m,
            cfg.thermo_thickness_m,
        )
        s = self.salinity_at_depth(
            depth_m,
            cfg.surface_sal_psu,
            cfg.deep_sal_psu,
            cfg.thermo_center_m,
            cfg.thermo_thickness_m,
        )
        p = self.pressure_dbar_at_depth(depth_m)
        cond = self.conductivity_mscm(s, t)
        sv = self.sound_speed_mps_mackenzie(t, s, depth_m)

        if cfg.noise_temp_c > 0:
            t += rng.gauss(0.0, cfg.noise_temp_c)
        if cfg.noise_sal_psu > 0:
            s += rng.gauss(0.0, cfg.noise_sal_psu)
        if cfg.noise_press_dbar > 0:
            p += rng.gauss(0.0, cfg.noise_press_dbar)
        if cfg.noise_cond_mscm > 0:
            cond += rng.gauss(0.0, cfg.noise_cond_mscm)
        if cfg.noise_sv_mps > 0:
            sv += rng.gauss(0.0, cfg.noise_sv_mps)

        # Re-clamp after noise so pathological draws do not escape bounds.
        t = _clamp(t, min(cfg.surface_temp_c, cfg.deep_temp_c) - 1.0,
                   max(cfg.surface_temp_c, cfg.deep_temp_c) + 1.0)
        s = _clamp(s, 33.0, 36.5)
        cond = _clamp(cond, 38.0, 62.0)
        sv = _clamp(sv, 1400.0, 1600.0)

        return t, s, p, cond, sv
