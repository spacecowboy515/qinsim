"""Driver protocol — every kind exposes ``tick(dt) -> list[bytes]``.

The runtime treats drivers polymorphically: it knows the rate, the
channel, and the tick method, and nothing else. A driver owns its state
and its sentence-formatting; it does not own its channel's lifetime
(the runtime constructs and tears down channels) and it has no concept
of a clock — the runtime calls ``tick`` at the configured period.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Driver(Protocol):
    """Minimum surface every qinsim driver implements."""

    def tick(self, dt_seconds: float) -> list[bytes]:
        """Advance state by ``dt_seconds`` and emit one tick of sentences.

        Returns the bytes written to the channel, in emission order, so
        the runtime / status panel can show what just went out without
        tapping the channel's subscriber path.
        """
