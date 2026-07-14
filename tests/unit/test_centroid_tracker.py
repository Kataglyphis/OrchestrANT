"""Unit tests for the centroid tracker (pure logic, no hardware needed)."""

from __future__ import annotations

from orchestr_ant_ion.pipeline.tracking.centroid import SimpleCentroidTracker


class TestTrackCreation:
    """Track creation from detections."""

    def test_first_update_creates_one_track_per_centroid(self) -> None:
        """Regression: first real update must construct actual Track objects.

        Track was once a TYPE_CHECKING-only import and the first update with
        detections raised NameError.
        """
        tracker = SimpleCentroidTracker()
        tracks = tracker.update([(0.2, 0.2), (0.8, 0.8)], now_ts=100.0)
        assert len(tracks) == 2
        assert sorted(t.track_id for t in tracks.values()) == [1, 2]
        assert all(len(t.points_norm) == 1 for t in tracks.values())

    def test_empty_update_returns_existing_tracks_untouched(self) -> None:
        """An empty detection list must neither create nor mutate tracks."""
        tracker = SimpleCentroidTracker()
        tracker.update([(0.5, 0.5)], now_ts=100.0)
        tracks = tracker.update([], now_ts=100.5)
        assert len(tracks) == 1
        assert tracks[1].last_seen_ts == 100.0

    def test_far_detection_spawns_new_track(self) -> None:
        """A detection beyond the match gate becomes a new track, not a match."""
        tracker = SimpleCentroidTracker(max_match_dist_norm=0.1)
        tracker.update([(0.1, 0.1)], now_ts=100.0)
        tracks = tracker.update([(0.9, 0.9)], now_ts=100.1)
        assert len(tracks) == 2
        assert len(tracks[1].points_norm) == 1
        assert tracks[2].points_norm[-1] == (0.9, 0.9)


class TestTrackMatching:
    """Greedy nearest-centroid association."""

    def test_nearby_detection_extends_same_track(self) -> None:
        """A detection inside the gate appends to the existing track's trail."""
        tracker = SimpleCentroidTracker(max_match_dist_norm=0.2)
        tracker.update([(0.50, 0.50)], now_ts=100.0)
        tracks = tracker.update([(0.55, 0.50)], now_ts=100.1)
        assert len(tracks) == 1
        assert list(tracks[1].points_norm) == [(0.50, 0.50), (0.55, 0.50)]
        assert tracks[1].last_seen_ts == 100.1

    def test_greedy_matching_prefers_closest_pair(self) -> None:
        """Each detection joins its closer track when both are within gate.

        Two tracks, two detections, all four pairings under the threshold —
        the greedy pass must still pick the two closest pairings.
        """
        tracker = SimpleCentroidTracker(max_match_dist_norm=1.0)
        tracker.update([(0.2, 0.5), (0.8, 0.5)], now_ts=100.0)
        tracks = tracker.update([(0.75, 0.5), (0.25, 0.5)], now_ts=100.1)
        assert len(tracks) == 2
        assert tracks[1].points_norm[-1] == (0.25, 0.5)
        assert tracks[2].points_norm[-1] == (0.75, 0.5)

    def test_trail_respects_max_points(self) -> None:
        """The per-track trail is capped at max_trail_points."""
        tracker = SimpleCentroidTracker(max_match_dist_norm=1.0, max_trail_points=3)
        for i in range(6):
            tracker.update([(0.5, 0.5 + i * 0.01)], now_ts=100.0 + i * 0.1)
        tracks = tracker.update([(0.5, 0.57)], now_ts=100.7)
        assert len(tracks) == 1
        assert len(tracks[1].points_norm) == 3


class TestTrackExpiry:
    """Age-based track removal."""

    def test_stale_track_expires(self) -> None:
        """A track unseen for longer than max_age_s is dropped."""
        tracker = SimpleCentroidTracker(max_age_s=1.0)
        tracker.update([(0.5, 0.5)], now_ts=100.0)
        tracks = tracker.update([(0.9, 0.9)], now_ts=102.0)
        assert len(tracks) == 1
        assert tracks[2].points_norm[-1] == (0.9, 0.9)

    def test_fresh_track_survives_expiry_sweep(self) -> None:
        """A recently-seen track must survive an update within max_age_s."""
        tracker = SimpleCentroidTracker(max_age_s=5.0, max_match_dist_norm=0.2)
        tracker.update([(0.5, 0.5)], now_ts=100.0)
        tracks = tracker.update([(0.52, 0.5)], now_ts=101.0)
        assert list(tracks.keys()) == [1]
