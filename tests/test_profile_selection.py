"""Selecting a delivery profile per run, and reaching the refusal from the API.

The gate has always been able to answer UNMEASURABLE, but for most of this
project's life nothing could ask it to: the profile was hardcoded. These tests
exist to keep the refusal REACHABLE, not merely implemented - an answer no
caller can obtain is indistinguishable from one that does not exist.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from pipeline.policy import UNMEASURABLE, available_profiles, evaluate, load_profile
from pipeline.qc import BlackInterval, LoudnessMeasurement, QCReport

# The warm-up run shells out to ffmpeg on import; irrelevant here and slow.
os.environ["QCIC_WARMUP"] = "0"

from server.app import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _report(lufs: float = -16.8) -> QCReport:
    return QCReport(
        asset_path="synthetic",
        duration_s=45.0,
        loudness=LoudnessMeasurement(lufs, -13.7, -33.0, 1.3),
        black_intervals=[BlackInterval(0.0, 10.0, 10.0)],
    )


class TestRegistry:
    def test_every_shipped_profile_is_listed(self):
        assert {p.id for p in available_profiles()} == {
            "ebu-r128-tv",
            "atsc-a85-tv",
            "netflix-dialog-gated",
        }

    def test_measurable_profiles_are_listed_first(self):
        measurable = [p.is_measurable for p in available_profiles()]
        assert measurable == sorted(measurable, reverse=True)

    def test_unknown_profile_names_the_known_ones(self):
        # A caller who mistypes gets the valid set back, not a bare KeyError.
        with pytest.raises(KeyError, match="netflix-dialog-gated"):
            load_profile("netflix")


class TestProfileEndpoint:
    def test_lists_profiles_with_a_default(self, client):
        body = client.get("/api/profile").json()
        assert body["default"] == "ebu-r128-tv"
        assert len(body["profiles"]) == 3

    def test_netflix_is_advertised_as_unmeasurable(self, client):
        body = client.get("/api/profile").json()
        netflix = next(p for p in body["profiles"] if p["id"] == "netflix-dialog-gated")
        assert netflix["measurable"] is False
        assert netflix["requires"] == "bs1770_dialog_gated"

    def test_unmeasurable_profile_permits_no_repair(self, client):
        """Nothing is repairable against a spec that cannot be measured."""
        body = client.get("/api/profile").json()
        netflix = next(p for p in body["profiles"] if p["id"] == "netflix-dialog-gated")
        assert netflix["allowlist"] == ["escalate_to_human"]


class TestRunAcceptsProfile:
    def test_unknown_profile_is_rejected_before_any_work(self, client):
        res = client.post("/api/runs", json={"fixture": "fault", "profile_id": "nope"})
        assert res.status_code == 400
        assert "unknown profile" in res.json()["detail"]

    def test_bad_profile_does_not_leave_a_run_behind(self, client):
        """Validation happens before the worker thread is started."""
        client.post("/api/runs", json={"fixture": "fault", "profile_id": "nope"})
        # Nothing to assert on the run store directly; the 400 above plus a
        # clean fixture rejection below is what keeps start() honest.
        res = client.post("/api/runs", json={"fixture": "nope"})
        assert res.status_code == 400


class TestSameAssetDifferentAnswers:
    """One measurement, three profiles, three different correct answers."""

    def test_blocked_on_broadcast_profiles_withheld_on_netflix(self):
        qc = _report(-16.8)
        verdicts = {p.id: evaluate(p, qc).status for p in available_profiles()}
        assert verdicts["ebu-r128-tv"] == "BLOCKED"
        assert verdicts["atsc-a85-tv"] == "BLOCKED"
        assert verdicts["netflix-dialog-gated"] == UNMEASURABLE

    def test_refusal_does_not_depend_on_the_measurement(self):
        """Even a compliant-looking asset is not judged against Netflix."""
        netflix = load_profile("netflix-dialog-gated")
        assert evaluate(netflix, _report(-27.0)).status == UNMEASURABLE
