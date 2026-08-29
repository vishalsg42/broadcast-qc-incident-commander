"""Investigation controller tests against a live Loki/Tempo stack.

Skipped unless the local stack is reachable:

    docker compose -f docker/docker-compose.yml up -d

These are slow (a real pipeline run plus telemetry ingestion) but they are the
only tests that prove the investigation works against real telemetry rather than
against a fixture someone hand-wrote to match the parser.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from agent.evidence import PHASE_ORDER, EvidenceLedger, Phase
from agent.grafana import GrafanaClient, GrafanaConfig, GrafanaError
from agent.investigator import InvestigationError, Investigator
from pipeline import telemetry
from pipeline.policy import Profile
from pipeline.stages import PACKAGE, PresetLibrary, run_pipeline

ROOT = Path(__file__).parent.parent
MEDIA = ROOT / "media"
PROFILE = ROOT / "pipeline" / "profiles" / "ebu_r128.yaml"
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:3000")
OTLP = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")


def _stack_up() -> bool:
    try:
        return (
            GrafanaClient(GrafanaConfig(url=GRAFANA_URL), timeout=3).health().get("database")
            == "ok"
        )
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (MEDIA / "master_good.mp4").exists(), reason="fixtures missing"),
    pytest.mark.skipif(not _stack_up(), reason=f"no Grafana stack at {GRAFANA_URL}"),
]


@pytest.fixture(scope="module")
def profile() -> Profile:
    return Profile.load(PROFILE)


@pytest.fixture(scope="module")
def faulted_run(profile, tmp_path_factory):
    """A real blocked delivery, with telemetry shipped to the stack."""
    telemetry.shutdown()
    telemetry.init(OTLP)
    try:
        run = run_pipeline(
            MEDIA / "master_good.mp4",
            out_dir=tmp_path_factory.mktemp("inv"),
            overrides={PACKAGE: "pkg_h264_v7"},
            black_opts=profile.black_detector_opts,
            profile=profile,
            asset_id=f"itest-{uuid.uuid4().hex[:6]}",
        )
    finally:
        telemetry.shutdown()
    return run


@pytest.fixture
def investigator(faulted_run):
    ledger = EvidenceLedger(run_id=f"inv-{faulted_run.run_id}")
    client = GrafanaClient(GrafanaConfig(url=GRAFANA_URL))
    inv = Investigator(
        client, ledger, run_id=faulted_run.run_id, asset_id=faulted_run.asset_id
    )
    # Loki ingestion is quick but not instant; fail loudly rather than flakily.
    client.wait_for_logs(
        f'{{service_name="qc-pipeline"}} | qc_run_id="{faulted_run.run_id}"',
        expected=3,
        timeout_s=60,
    )
    return inv


class TestPhases:
    def test_baseline_finds_the_source_in_spec(self, investigator):
        r = investigator.gather_baseline()
        assert r.summary["source_in_spec"] is True
        assert r.summary["integrated_lufs"] == pytest.approx(-23.0, abs=0.6)

    def test_divergence_locates_the_failing_stage(self, investigator):
        r = investigator.gather_divergence()
        assert r.summary["first_failing_stage"] == PACKAGE
        assert r.summary["last_good_stage"] == "normalize"

    def test_normalize_is_exonerated(self, investigator):
        """The whole point: the fault is downstream of a correct normalise."""
        r = investigator.gather_divergence()
        norm = next(s for s in r.summary["stages"] if s["stage"] == "normalize")
        assert norm["verdict"] == "PASS"

    def test_actor_attributes_the_preset_version_from_the_trace(self, investigator):
        r = investigator.gather_actor(PACKAGE)
        assert r.summary["preset_id"] == "pkg_h264_v7"
        assert r.summary["preset_version"] == 7
        assert r.summary["preset_changed_at"] == "2026-08-29T14:02:00Z"

    def test_cause_surfaces_the_channel_remap(self, investigator):
        lib = PresetLibrary.load()
        preset = lib.get(PACKAGE, "pkg_h264_v7")
        r = investigator.gather_cause(
            preset.id,
            {
                "audio_filter": preset.audio_filter,
                "description": preset.description,
                "changed_at": preset.changed_at,
            },
        )
        assert "c0+c1" in r.summary["audio_filter"]


class TestEvidenceProvenance:
    def test_controller_binds_the_query_the_model_cannot(self, investigator):
        investigator.gather_baseline()
        step = investigator.ledger._pending
        assert step is not None
        assert step.phase == Phase.BASELINE
        assert 'qc_stage="ingest"' in step.query_used
        assert step.query_hash and len(step.query_hash) == 16

    def test_raw_result_is_retained(self, investigator):
        investigator.gather_baseline()
        step = investigator.ledger.record_interpretation(finding="in spec", supports=True)
        raw = investigator.ledger.raw(step.raw_result_ref)
        assert raw and isinstance(raw, list)
        assert "labels" in raw[0]

    def test_four_phases_produce_four_steps(self, investigator):
        led = investigator.ledger
        investigator.gather_baseline()
        led.record_interpretation(finding="source in spec", supports=True)

        d = investigator.gather_divergence()
        led.record_interpretation(finding="diverged at package", supports=True)

        investigator.gather_actor(d.summary["first_failing_stage"])
        led.record_interpretation(finding="preset v7 ran package", supports=True)

        lib = PresetLibrary.load()
        preset = lib.get(PACKAGE, "pkg_h264_v7")
        investigator.gather_cause(preset.id, {"audio_filter": preset.audio_filter})
        led.record_interpretation(finding="channels summed", supports=True)

        assert len(led.steps) == 4
        assert all(led.phase_complete(p) for p in PHASE_ORDER)
        assert [s.phase for s in led.steps] == list(Phase)


class TestClientBehaviour:
    def test_missing_run_raises_rather_than_returning_empty(self, investigator):
        investigator.run_id = "run-does-not-exist"
        with pytest.raises(InvestigationError, match="no ingest observation"):
            investigator.gather_baseline()

    def test_trace_wait_times_out_with_a_useful_message(self):
        client = GrafanaClient(GrafanaConfig(url=GRAFANA_URL))
        with pytest.raises(GrafanaError, match="timed out"):
            client.wait_for_traces(
                '{name="delivery.run" && .qc.run_id="nope"}', timeout_s=4, interval_s=1
            )
