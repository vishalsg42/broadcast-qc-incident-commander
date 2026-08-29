"""Bounds the controller enforces, rather than asks the model to respect.

Three of these came from a review that read the code and asked what happens
under load. Each was real:

  a query with no limit returns whatever the backend has, and all of it lands
    in the model's context
  a run store that never evicts leaks on an instance pinned with
    min-instances=1, which never restarts to clean itself up
  and a repair that failed re-validation ended the run as DONE, leaving an
    operator to notice `resolved=false` in a stream of events
"""

from __future__ import annotations

import json

import pytest

from agent.autonomous import (
    MAX_QUERY_ROWS,
    MAX_TOOL_CHARS,
    AutonomousInvestigator,
    _failure_reason,
)
from agent.evidence import EvidenceLedger
from pipeline.policy import load_profile
from server.orchestrator import (
    MAX_ACTIVE_RUNS,
    MAX_RETAINED_RUNS,
    Orchestrator,
    Run,
    Status,
    TooManyRuns,
)


class _Tool:
    def __init__(self, name):
        self.name = name


def _agent() -> AutonomousInvestigator:
    return AutonomousInvestigator(
        ledger=EvidenceLedger(run_id="limits"),
        pipeline_run=None,
        profile=load_profile("ebu-r128-tv"),
        allowlist={},
    )


class TestFailedToolCallsAreNotEvidence:
    """The critical one: an errored tool must mint nothing citable.

    The local tools fail with {"ok": False}. The MCP tools never return `ok` at
    all - they return {"content": [...], "isError": true}. Checking only the
    first shape meant an errored Grafana call minted a step_id and was recorded
    as SUPPORTING, so an agent could clear the evidence floor with two
    authentication failures and reach a validated conclusion built on 401s.
    """

    def test_a_local_tool_failure_is_detected(self):
        assert _failure_reason({"ok": False, "error": "no such preset"}) == "no such preset"

    def test_an_mcp_error_is_detected(self):
        """MCP 1.x spelling."""
        payload = {"content": [{"type": "text", "text": "401 unauthorized"}], "isError": True}
        assert _failure_reason(payload) == "401 unauthorized"

    def test_the_snake_case_mcp_spelling_is_detected(self):
        """MCP 2.x spelling. ADK carries a helper for exactly this difference."""
        payload = {"content": [{"type": "text", "text": "boom"}], "is_error": True}
        assert _failure_reason(payload) == "boom"

    def test_adks_graceful_error_shape_is_detected(self):
        assert _failure_reason({"error": "connection refused"}) == "connection refused"

    def test_a_successful_result_is_not_a_failure(self):
        payload = {"content": [{"type": "text", "text": '{"data": []}'}], "isError": False}
        assert _failure_reason(payload) is None

    def test_an_errored_call_mints_no_citable_step(self):
        """End to end through the callback: no step, nothing to cite."""
        led = EvidenceLedger(run_id="err")
        agent = AutonomousInvestigator(
            ledger=led,
            pipeline_run=None,
            profile=load_profile("ebu-r128-tv"),
            allowlist={},
        )
        out = agent._after_tool(
            _Tool("query_loki_logs"),
            {"logql": "{x=`y`}"},
            None,
            {"content": [{"type": "text", "text": "401 unauthorized"}], "isError": True},
        )
        assert out is None, "an errored tool must not reach the model as a result"
        assert led.step_ids() == set(), "an errored tool minted a citable step"
        assert agent.calls[-1].ok is False

    def test_two_errored_calls_cannot_clear_the_evidence_floor(self):
        """The Grafana-trial-lapsed scenario, asserted.

        Every tool returns 401. Without this guard the agent gathers two
        "sources", clears MIN_SOURCES_BEFORE_CONCLUDING, and concludes.
        """
        led = EvidenceLedger(run_id="err")
        agent = AutonomousInvestigator(
            ledger=led,
            pipeline_run=None,
            profile=load_profile("ebu-r128-tv"),
            allowlist={},
        )
        error = {"content": [{"type": "text", "text": "401"}], "isError": True}
        agent._after_tool(_Tool("list_datasources"), {}, None, error)
        agent._after_tool(_Tool("query_loki_logs"), {}, None, error)

        out = agent._tool_conclude(
            json.dumps(
                [
                    {
                        "claim_type": "SOURCE_OUT_OF_SPEC",
                        "claim_value": "the source was out of spec",
                        "supporting_step_ids": ["step-01"],
                        "confidence": "high",
                    }
                ]
            ),
            "",
            "",
        )
        assert out["ok"] is False
        assert "investigate further" in out["error"]


class TestToolPayloadIsBounded:
    def test_a_huge_line_based_result_drops_lines_and_stays_valid(self):
        """Drop rows, do not slice the string.

        Slicing handed the model a torn JSON document inside a JSON string, and
        because Loki returns newest first it kept the boilerplate and deleted
        the oldest entries - which is the evidence when the fault is at ingest.
        """
        from agent.autonomous import _bounded

        text = "\n".join(f'{{"line": {i}, "pad": "{"x" * 200}"}}' for i in range(400))
        huge = {"content": [{"type": "text", "text": text}], "isError": False}
        out = _bounded(huge)

        kept = out["content"][0]["text"].splitlines()
        assert 0 < len(kept) < 400
        assert out["dropped_lines"] == 400 - len(kept)
        # Every kept line is still parseable, and the OLDEST survived.
        assert json.loads(kept[0])["line"] == 0
        assert json.loads(kept[-1])["line"] == len(kept) - 1

    def test_a_result_with_nothing_line_shaped_says_so_rather_than_pretending(self):
        from agent.autonomous import _bounded

        huge = {"rows": ["x" * 200 for _ in range(500)]}
        out = _bounded(huge)
        assert out["truncated"] is True
        assert "could not be trimmed" in out["note"]

    def test_a_small_result_passes_through_unchanged(self):
        from agent.autonomous import _bounded

        small = {"ok": True, "stage": "package"}
        assert _bounded(small) == small

    def test_the_cap_is_smaller_than_a_context_window(self):
        assert 0 < MAX_TOOL_CHARS <= 20000


class TestEvictionProtectsActiveRunsAndCleansUp:
    """Eviction bounds two things, and one of them is 25MB of video per run."""

    def _orch(self, tmp_path):
        return Orchestrator(grafana_url="http://localhost:3000", out_dir=str(tmp_path))

    def test_a_run_awaiting_approval_is_never_evicted(self, tmp_path):
        """Evicting one leaves approve() returning 409 forever.

        AWAITING_APPROVAL lasts up to five minutes, which is plenty of time for
        25 other runs to start on a shared demo link.
        """
        o = self._orch(tmp_path)
        waiting = Run(run_id="waiting", fixture="fault")
        waiting.status = Status.AWAITING_APPROVAL
        o._runs["waiting"] = waiting
        for i in range(MAX_RETAINED_RUNS + 5):
            done = Run(run_id=f"done{i}", fixture="clean")
            done.status = Status.DONE
            o._runs[done.run_id] = done
            o._evict_old_runs()

        assert "waiting" in o._runs, "a run awaiting approval was evicted"
        assert o.get("waiting") is not None

    def test_finished_runs_are_evicted_oldest_first(self, tmp_path):
        o = self._orch(tmp_path)
        for i in range(MAX_RETAINED_RUNS + 10):
            run = Run(run_id=f"r{i}", fixture="clean")
            run.status = Status.DONE
            o._runs[run.run_id] = run
            o._evict_old_runs()
        assert len(o._runs) == MAX_RETAINED_RUNS
        assert "r0" not in o._runs
        assert f"r{MAX_RETAINED_RUNS + 9}" in o._runs

    def test_eviction_deletes_the_artefacts_too(self, tmp_path):
        """Bounding the dict and leaving the video is bounding the wrong thing."""
        o = self._orch(tmp_path)
        stale = Run(run_id="stale", fixture="fault")
        stale.status = Status.DONE
        stale.pipeline_run_id = "run-aaaa"
        o._runs["stale"] = stale

        for suffix in ("_normalize.mp4", "_package.mp4", "_repair_01.mp4"):
            (tmp_path / f"run-aaaa{suffix}").write_bytes(b"x" * 1024)
        keep = tmp_path / "run-bbbb_package.mp4"
        keep.write_bytes(b"x" * 1024)
        assert len(list(tmp_path.glob("*.mp4"))) == 4

        o._remove_artefacts(stale)

        assert list(tmp_path.glob("run-aaaa*")) == [], "artefacts survived eviction"
        assert keep.exists(), "eviction deleted another run's artefacts"

    def test_eviction_itself_deletes_artefacts(self, tmp_path):
        """Through _evict_old_runs, not by calling the helper directly.

        Testing the helper in isolation passes even when nothing calls it, which
        is how a cleanup that was never wired up would look identical to one
        that works.
        """
        o = self._orch(tmp_path)
        for i in range(MAX_RETAINED_RUNS + 3):
            run = Run(run_id=f"r{i}", fixture="fault")
            run.status = Status.DONE
            run.pipeline_run_id = f"run-{i:04d}"
            (tmp_path / f"run-{i:04d}_package.mp4").write_bytes(b"x" * 1024)
            o._runs[run.run_id] = run
            o._evict_old_runs()

        # The three oldest were evicted, so their artefacts must be gone.
        for i in range(3):
            assert not list(tmp_path.glob(f"run-{i:04d}*")), (
                f"run-{i:04d} was evicted but its artefacts survived"
            )
        # The retained ones keep theirs.
        assert (tmp_path / f"run-{MAX_RETAINED_RUNS + 2:04d}_package.mp4").exists()

    def test_removing_artefacts_is_safe_when_there_are_none(self, tmp_path):
        """A run that failed before the pipeline produced anything."""
        o = self._orch(tmp_path)
        run = Run(run_id="empty", fixture="clean")
        run.status = Status.FAILED
        o._remove_artefacts(run)  # must not raise

    def test_everything_active_holds_rather_than_stranding_a_run(self, tmp_path):
        """Better to exceed the bound than to strand someone mid-approval."""
        o = self._orch(tmp_path)
        for i in range(MAX_RETAINED_RUNS + 5):
            run = Run(run_id=f"live{i}", fixture="fault")
            run.status = Status.RUNNING
            o._runs[run.run_id] = run
        o._evict_old_runs()
        assert len(o._runs) == MAX_RETAINED_RUNS + 5


class TestRunStoreIsBounded:
    def test_old_runs_are_evicted(self):
        """min-instances=1 means the process never restarts to clean up."""
        o = Orchestrator(grafana_url="http://localhost:3000", out_dir="out")
        from server.orchestrator import Run

        for i in range(MAX_RETAINED_RUNS + 10):
            run = Run(run_id=f"r{i}", fixture="clean")
            o._runs[run.run_id] = run
            while len(o._runs) > MAX_RETAINED_RUNS:
                o._runs.popitem(last=False)

        assert len(o._runs) == MAX_RETAINED_RUNS
        # The newest survive; the oldest are gone.
        assert "r0" not in o._runs
        assert f"r{MAX_RETAINED_RUNS + 9}" in o._runs


class TestQueryShapingMatchesTheRealSchemas:
    """The clamp only touches tools that actually accept a limit.

    The first version of this clamped every "query tool". The Loki discovery
    tools declare additionalProperties: false and REJECT an unexpected `limit`;
    the Tempo tools ignore it. Clamping by category would have broken the
    model's own discovery calls.
    """

    def _shape(self, tool_name, args):
        agent = AutonomousInvestigator(
            ledger=EvidenceLedger(run_id="shape"),
            pipeline_run=None,
            profile=load_profile("ebu-r128-tv"),
            allowlist={},
        )
        agent._before_tool(_Tool(tool_name), args, None)
        return args

    def test_loki_query_gets_a_clamped_limit(self):
        args = self._shape("query_loki_logs", {"logql": "{x=`y`}", "limit": 5000})
        assert args["limit"] == MAX_QUERY_ROWS

    def test_loki_query_gets_the_compact_format(self):
        """This is what actually shrinks the payload: 74KB against 186KB."""
        args = self._shape("query_loki_logs", {"logql": "{x=`y`}"})
        assert args["format"] == "compact"

    def test_an_explicit_format_is_respected(self):
        args = self._shape("query_loki_logs", {"logql": "{x=`y`}", "format": "raw"})
        assert args["format"] == "raw"

    def test_discovery_tools_are_never_given_a_limit(self):
        """They reject unknown arguments outright."""
        for name in ("list_loki_label_names", "list_loki_label_values"):
            args = self._shape(name, {"datasourceUid": "loki"})
            assert "limit" not in args, f"{name} would be sent an unknown argument"
            assert "format" not in args

    def test_tempo_tools_are_left_alone(self):
        for name in ("tempo_traceql-search", "tempo_get-trace"):
            args = self._shape(name, {"query": "{}"})
            assert "limit" not in args

    def test_a_nonsense_limit_does_not_raise(self):
        """A tool that raises aborts the whole ADK invocation."""
        args = self._shape("query_loki_logs", {"logql": "{x=`y`}", "limit": "lots"})
        assert args["limit"] == MAX_QUERY_ROWS

    def test_a_smaller_limit_is_left_alone(self):
        args = self._shape("query_loki_logs", {"logql": "{x=`y`}", "limit": 5})
        assert args["limit"] == 5


class TestConcurrentRunsAreBounded:
    """The work is bounded, not the HTTP concurrency.

    One ffmpeg pass saturates both vCPUs, so two concurrent runs already halve
    each other. Lowering Cloud Run's containerConcurrency instead would have
    locked visitors out of loading the page, since one page load is eighteen
    static assets and an SSE stream holds a slot for the length of the run.
    """

    def test_a_third_concurrent_run_is_refused(self, tmp_path):
        o = Orchestrator(grafana_url="http://localhost:3000", out_dir=str(tmp_path))
        for i in range(MAX_ACTIVE_RUNS):
            run = Run(run_id=f"live{i}", fixture="fault")
            run.status = Status.RUNNING
            o._runs[run.run_id] = run
        with pytest.raises(TooManyRuns, match="already measuring"):
            o.start("clean")

    def test_runs_waiting_for_a_human_do_not_block_new_ones(self, tmp_path):
        """An abandoned tab must not lock everyone out for the approval timeout.

        A run parked at the approval gate consumes no CPU. Counting it would let
        two closed tabs deny the service for five minutes, which is worse than
        the contention the bound exists to prevent.
        """
        o = Orchestrator(grafana_url="http://localhost:3000", out_dir=str(tmp_path))
        for i in range(MAX_ACTIVE_RUNS + 3):
            run = Run(run_id=f"waiting{i}", fixture="fault")
            run.status = Status.AWAITING_APPROVAL
            o._runs[run.run_id] = run
        working = sum(1 for r in o._runs.values() if r.status in o.WORKING_STATUSES)
        assert working == 0, "runs awaiting approval counted as working"
        # And they are still protected from eviction.
        assert all(r.status in o.ACTIVE_STATUSES for r in o._runs.values())

    def test_finished_runs_do_not_count_against_the_limit(self, tmp_path):
        o = Orchestrator(grafana_url="http://localhost:3000", out_dir=str(tmp_path))
        for i in range(20):
            run = Run(run_id=f"done{i}", fixture="clean")
            run.status = Status.DONE
            o._runs[run.run_id] = run
        # Does not raise. It will fail later for want of ffmpeg, not for capacity.
        assert o._runs is not None
        active = sum(1 for r in o._runs.values() if r.status in o.ACTIVE_STATUSES)
        assert active == 0
