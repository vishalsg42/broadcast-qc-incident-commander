"""Trace structure tests.

Guards one specific regression: if stage spans are not children of the run span,
Tempo shows three unrelated single-span traces instead of the asset's journey.
That silently destroys the only view this project has that nothing else produces,
and nothing else in the suite would notice.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pipeline import telemetry


@pytest.fixture
def exporter():
    exp = InMemorySpanExporter()
    telemetry.init_for_test(exp)
    yield exp
    telemetry.shutdown()


def _emit_three_stages() -> None:
    with telemetry.run_span(run_id="run-1", asset_id="asset-1", source_path="/tmp/a.mp4"):
        for stage, preset, ver in [
            ("ingest", "ingest_passthrough_v1", 1),
            ("normalize", "norm_ebu_v3", 3),
            ("package", "pkg_h264_v7", 7),
        ]:
            with telemetry.stage_span(
                stage,
                preset_id=preset,
                preset_version=ver,
                preset_changed_at="2026-08-29T14:02:00Z",
                run_id="run-1",
                asset_id="asset-1",
            ):
                pass


class TestTraceStructure:
    def test_all_stages_share_one_trace(self, exporter):
        _emit_three_stages()
        spans = exporter.get_finished_spans()
        trace_ids = {s.context.trace_id for s in spans}
        assert len(trace_ids) == 1, f"expected 1 trace, got {len(trace_ids)}"

    def test_one_root_and_three_children(self, exporter):
        _emit_three_stages()
        spans = exporter.get_finished_spans()
        roots = [s for s in spans if s.parent is None]
        children = [s for s in spans if s.parent is not None]
        assert len(spans) == 4
        assert len(roots) == 1
        assert roots[0].name == "delivery.run"
        assert len(children) == 3

    def test_every_stage_span_parents_to_the_run(self, exporter):
        _emit_three_stages()
        spans = exporter.get_finished_spans()
        root = next(s for s in spans if s.parent is None)
        for s in (x for x in spans if x.parent is not None):
            assert s.parent.span_id == root.context.span_id, s.name

    def test_preset_version_is_on_the_span(self, exporter):
        """The payoff of the demo, and the easiest thing to forget."""
        _emit_three_stages()
        pkg = next(s for s in exporter.get_finished_spans() if s.name == "stage.package")
        assert pkg.attributes["qc.preset_id"] == "pkg_h264_v7"
        assert pkg.attributes["qc.preset_version"] == 7
        assert pkg.attributes["qc.preset_changed_at"] == "2026-08-29T14:02:00Z"


class TestDisabledByDefault:
    def test_helpers_are_noops_without_an_endpoint(self):
        assert not telemetry.enabled()
        with telemetry.run_span(run_id="r", asset_id="a", source_path="p") as s:
            assert s is None
        telemetry.emit_qc_observation(
            stage="ingest",
            run_id="r",
            asset_id="a",
            preset_id="p",
            preset_version=1,
            verdict="PASS",
            measurements={},
        )


class TestRepeatedInit:
    """A long-lived server re-inits telemetry once per run.

    Two things go wrong silently if this is not handled: OTel refuses a second
    `set_tracer_provider` (warning only), and every `init()` used to append
    another logging handler, so handlers accumulated pointing at providers that
    had already been shut down. Log export then degrades after the first run
    with nothing in the output to explain it.
    """

    def test_handlers_do_not_accumulate(self):
        import logging

        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        log = logging.getLogger(telemetry.LOGGER_NAME)
        before = len(log.handlers)
        for _ in range(3):
            telemetry.init_for_test(InMemorySpanExporter())
            telemetry.shutdown()
        assert len(log.handlers) == before

    def test_each_cycle_produces_its_own_trace(self):
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        trace_ids = set()
        for i in range(3):
            exporter = InMemorySpanExporter()
            telemetry.init_for_test(exporter)
            with telemetry.run_span(run_id=f"r{i}", asset_id="a", source_path="p"):
                pass
            trace_ids.update(s.context.trace_id for s in exporter.get_finished_spans())
            telemetry.shutdown()
        assert len(trace_ids) == 3, "re-init must not reuse a trace"
