"""Unit tests for ISO 2859-1 AQL sampling logic — no database required."""
import pytest
from app.services.aql_service import compute_sample_size


class TestComputeSampleSize:
    def test_small_batch_normal_aql(self):
        # 5 items → code letter B → AQL 1.5 → sample 3, accept 0
        sample, accept = compute_sample_size(5, 1.5)
        assert sample == 3
        assert accept == 0

    def test_medium_batch_normal_aql(self):
        # 100 items → code letter G (91-150) → AQL 1.5 → sample 32, accept 1
        sample, accept = compute_sample_size(100, 1.5)
        assert sample == 32
        assert accept == 1

    def test_large_batch_normal_aql(self):
        # 500 items → code letter J → AQL 1.5 → sample 80, accept 3
        sample, accept = compute_sample_size(500, 1.5)
        assert sample == 80
        assert accept == 3

    def test_tightened_aql(self):
        # 100 items → code letter G (91-150) → AQL 1.0 → sample 32, accept 1
        sample, accept = compute_sample_size(100, 1.0)
        assert sample == 32
        assert accept == 1

    def test_reduced_aql(self):
        # 100 items → code letter G (91-150) → AQL 2.5 → sample 32, accept 2
        sample, accept = compute_sample_size(100, 2.5)
        assert sample == 32
        assert accept == 2

    def test_batch_at_boundary(self):
        # Exactly at boundary: 90 items → code letter F
        sample, accept = compute_sample_size(90, 1.5)
        assert sample == 20

    def test_large_batch_beyond_table(self):
        # 1_000_000 items → capped to code letter Q
        sample, accept = compute_sample_size(1_000_000, 1.5)
        assert sample == 1250

    def test_acceptance_threshold(self):
        # 200 items → code H (151-280) → AQL 1.5 → sample 50, accept 2
        sample, accept = compute_sample_size(200, 1.5)
        assert sample == 50
        assert accept == 2

    def test_aql_escalation_tightened_stricter(self):
        # Tightened (1.0) must be at least as strict as normal (1.5) acceptance
        # For the same batch size, tightened acceptance_number <= normal
        _, normal_accept = compute_sample_size(300, 1.5)
        _, tight_accept = compute_sample_size(300, 1.0)
        assert tight_accept <= normal_accept

    def test_aql_reduced_more_lenient(self):
        # Reduced (2.5) must be at least as lenient as normal (1.5)
        _, normal_accept = compute_sample_size(300, 1.5)
        _, reduced_accept = compute_sample_size(300, 2.5)
        assert reduced_accept >= normal_accept
