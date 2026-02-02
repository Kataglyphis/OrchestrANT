"""Simple centroid-based tracking utilities."""

from __future__ import annotations

from collections import deque

from orchestr_ant_ion.pipeline.types import Track


class SimpleCentroidTracker:
    """Very lightweight centroid tracker for a single class (e.g. persons)."""

    def __init__(
        self,
        *,
        max_age_s: float = 0.75,
        max_match_dist_norm: float = 0.08,
        max_trail_points: int = 40,
    ) -> None:
        """Initialize tracker configuration and internal state."""
        self._max_age_s = float(max_age_s)
        self._max_match_dist_norm = float(max_match_dist_norm)
        self._max_trail_points = int(max_trail_points)

        self._next_id = 1
        self._tracks: dict[int, Track] = {}

    def update(
        self, centroids_norm: list[tuple[float, float]], now_ts: float
    ) -> dict[int, Track]:
        """Update tracks with the latest normalized centroids."""
        self._expire_tracks(now_ts)

        if not centroids_norm:
            return self._tracks

        if not self._tracks:
            self._initialize_tracks(centroids_norm, now_ts)
            return self._tracks

        used_dets = self._associate_tracks(centroids_norm, now_ts)
        self._add_unmatched(centroids_norm, used_dets, now_ts)

        return self._tracks

    def _expire_tracks(self, now_ts: float) -> None:
        expired_ids = [
            tid
            for tid, tr in self._tracks.items()
            if (now_ts - tr.last_seen_ts) > self._max_age_s
        ]
        for tid in expired_ids:
            self._tracks.pop(tid, None)

    def _initialize_tracks(
        self, centroids_norm: list[tuple[float, float]], now_ts: float
    ) -> None:
        for centroid in centroids_norm:
            self._tracks[self._next_id] = Track(
                track_id=self._next_id,
                points_norm=deque([centroid], maxlen=self._max_trail_points),
                last_seen_ts=now_ts,
            )
            self._next_id += 1

    def _associate_tracks(
        self, centroids_norm: list[tuple[float, float]], now_ts: float
    ) -> set[int]:
        track_ids = list(self._tracks.keys())
        prev_centroids = [self._tracks[tid].points_norm[-1] for tid in track_ids]

        candidates: list[tuple[float, int, int]] = []
        for ti, (px, py) in enumerate(prev_centroids):
            for di, (cx, cy) in enumerate(centroids_norm):
                dist = float(((px - cx) ** 2 + (py - cy) ** 2) ** 0.5)
                candidates.append((dist, ti, di))
        candidates.sort(key=lambda item: item[0])

        used_tracks: set[int] = set()
        used_dets: set[int] = set()

        for dist, ti, di in candidates:
            if dist > self._max_match_dist_norm:
                break
            if ti in used_tracks or di in used_dets:
                continue
            tid = track_ids[ti]
            self._tracks[tid].points_norm.append(centroids_norm[di])
            self._tracks[tid].last_seen_ts = now_ts
            used_tracks.add(ti)
            used_dets.add(di)
        return used_dets

    def _add_unmatched(
        self,
        centroids_norm: list[tuple[float, float]],
        used_dets: set[int],
        now_ts: float,
    ) -> None:
        for di, centroid in enumerate(centroids_norm):
            if di in used_dets:
                continue
            self._tracks[self._next_id] = Track(
                track_id=self._next_id,
                points_norm=deque([centroid], maxlen=self._max_trail_points),
                last_seen_ts=now_ts,
            )
            self._next_id += 1

