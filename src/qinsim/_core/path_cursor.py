"""Waypoint traversal cursor for path-following simulations.

Ports Meridian's ``PathCursor`` (GNSS and Heading simulators share the same
class body) into a pure-Python dataclass with explicit types. The cursor
owns a sequence of lat/lon points and an offset along the current segment;
drivers call :meth:`step` each tick to advance it by ``speed * dt`` metres
and read :meth:`current_position` / :meth:`target_bearing` to update their
own state.

The class is deliberately passive — it does not know about time, speed, or
output rates. That keeps it reusable across GNSS (needs position + bearing),
Heading-only devices (needs bearing only), and any future motion preview.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .geo import bearing_to_waypoint, forward_project, haversine_distance

LatLon = tuple[float, float]


@dataclass
class PathCursor:
    """Mutable cursor tracking position along a polyline of lat/lon points.

    After :meth:`load_points` the cursor sits at the start of the first
    segment. :meth:`step` advances ``seg_offset_m`` metres along the current
    segment, rolling onto subsequent segments as needed. On exhaustion the
    behaviour depends on ``loop``:

    - ``loop=True``  — wrap back to segment 0, offset 0.
    - ``loop=False`` — clamp to the end of the final segment.
    """

    points: list[LatLon] = field(default_factory=list)
    segment_bearings: list[float] = field(default_factory=list)
    segment_lengths: list[float] = field(default_factory=list)
    seg_index: int = 0
    seg_offset_m: float = 0.0
    total_length_m: float = 0.0
    loop: bool = True

    def load_points(self, points: Sequence[LatLon], loop: bool = True) -> None:
        """Initialise segments from a sequence of ``(lat, lon)`` points.

        Zero-length segments (duplicate consecutive points) are silently
        dropped — otherwise :meth:`step` would divide by zero on them.
        """
        self.points = list(points)
        self.loop = loop
        self.segment_bearings = []
        self.segment_lengths = []
        self.total_length_m = 0.0
        for i in range(len(self.points) - 1):
            lat1, lon1 = self.points[i]
            lat2, lon2 = self.points[i + 1]
            dist = haversine_distance(lat1, lon1, lat2, lon2)
            if dist <= 0.0:
                continue
            self.segment_bearings.append(bearing_to_waypoint(lat1, lon1, lat2, lon2))
            self.segment_lengths.append(dist)
            self.total_length_m += dist
        self.seg_index = 0
        self.seg_offset_m = 0.0

    def has_path(self) -> bool:
        return len(self.segment_lengths) > 0

    def current_position(self) -> LatLon:
        """Return the cursor's current ``(lat, lon)``.

        Raises ``RuntimeError`` if no path has been loaded — callers are
        expected to guard with :meth:`has_path` first.
        """
        if not self.has_path():
            raise RuntimeError("No path loaded")
        i = self.seg_index
        lat1, lon1 = self.points[i]
        return forward_project(lat1, lon1, self.segment_bearings[i], self.seg_offset_m)

    def step(self, distance_m: float) -> None:
        """Advance the cursor by ``distance_m`` metres along the path.

        Negative or zero distances are no-ops. If the distance overruns the
        current segment the cursor cascades onto subsequent segments until
        it runs out of distance, loops, or clamps at the path end.
        """
        if not self.has_path() or distance_m <= 0.0:
            return
        remaining = distance_m
        # Epsilon guards float comparisons at segment boundaries — without
        # it, a distance that lands exactly at seg_len + 1e-15 could either
        # stay on the current segment or advance, depending on rounding.
        eps = 1e-6
        while remaining > 0.0:
            seg_len = self.segment_lengths[self.seg_index]
            available = seg_len - self.seg_offset_m
            if remaining < available - eps:
                self.seg_offset_m += remaining
                return
            remaining -= available
            self.seg_index += 1
            self.seg_offset_m = 0.0
            if self.seg_index >= len(self.segment_lengths):
                if self.loop:
                    self.seg_index = 0
                else:
                    # Clamp to end of final segment and stop consuming.
                    self.seg_index = len(self.segment_lengths) - 1
                    self.seg_offset_m = self.segment_lengths[self.seg_index]
                    return

    def target_bearing(self, lookahead_m: float = 5.0) -> float:
        """Return the bearing a driver should steer toward.

        For a point still inside the current segment this is the segment's
        bearing. Past the lookahead horizon it returns the next segment's
        bearing so path followers turn in advance of the waypoint rather
        than overshooting.
        """
        if not self.has_path():
            return 0.0
        i = self.seg_index
        seg_len = self.segment_lengths[i]
        off = self.seg_offset_m + lookahead_m
        if off < seg_len:
            return self.segment_bearings[i]
        if self.loop:
            return self.segment_bearings[(i + 1) % len(self.segment_lengths)]
        return self.segment_bearings[min(i + 1, len(self.segment_lengths) - 1)]
