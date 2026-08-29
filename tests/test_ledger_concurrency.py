"""The ledger under an agent that runs several tools before interpreting any.

The fixed four-phase investigation strictly alternated observe -> interpret, and
the ledger quietly depended on that: step ids were derived from the number of
INTERPRETED steps, and a single slot held the outstanding observation.

An agent that chooses its own tools breaks both assumptions. ADK executes
parallel tool calls through asyncio.gather, optionally on real threads, so these
tests pin the properties the provenance guarantee actually needs:

  every executed query gets its OWN id, its OWN raw-result reference, and a
  finding can only ever attach to the observation it names.
"""

from __future__ import annotations

import threading

import pytest

from agent.evidence import EvidenceLedger, LedgerError, Phase


def test_consecutive_observations_get_distinct_ids():
    """The original bug: ids came from len(_steps), which only grew on interpret."""
    led = EvidenceLedger(run_id="inv")
    ids = [led.observe(Phase.BASELINE, f"q{i}", {"n": i}).step_id for i in range(5)]
    assert len(set(ids)) == 5, f"colliding step ids: {ids}"


def test_each_observation_keeps_its_own_raw_result():
    """A shared ref meant the audit trail pointed at the last query for every step."""
    led = EvidenceLedger(run_id="inv")
    pend = [led.observe(Phase.ACTOR, f"q{i}", {"n": i}) for i in range(3)]
    for i, p in enumerate(pend):
        assert led.raw(p.raw_result_ref) == {"n": i}


def test_interpretation_attaches_to_the_named_observation():
    led = EvidenceLedger(run_id="inv")
    first = led.observe(Phase.BASELINE, "q1", {"n": 1})
    second = led.observe(Phase.DIVERGENCE, "q2", {"n": 2})

    step = led.record_interpretation("reading of the first", True, step_id=first.step_id)

    assert step.step_id == first.step_id
    assert step.phase is Phase.BASELINE
    assert step.query_used == "q1"
    # The other observation is untouched and still interpretable.
    later = led.record_interpretation("reading of the second", True, step_id=second.step_id)
    assert later.phase is Phase.DIVERGENCE


def test_ambiguous_interpretation_is_refused_rather_than_guessed():
    """Two outstanding observations and no step_id: bind nothing, say so."""
    led = EvidenceLedger(run_id="inv")
    led.observe(Phase.BASELINE, "q1", {})
    led.observe(Phase.ACTOR, "q2", {})
    with pytest.raises(LedgerError, match="name one with step_id"):
        led.record_interpretation("ambiguous", True)


def test_sequential_callers_still_need_no_step_id():
    """The scripted and single-tool paths must keep working unchanged."""
    led = EvidenceLedger(run_id="inv")
    led.observe(Phase.BASELINE, "q1", {})
    step = led.record_interpretation("unambiguous", True)
    assert step.phase is Phase.BASELINE


def test_an_observation_cannot_be_interpreted_twice():
    led = EvidenceLedger(run_id="inv")
    p = led.observe(Phase.CAUSE, "q", {})
    led.record_interpretation("once", True, step_id=p.step_id)
    with pytest.raises(LedgerError, match="no observation"):
        led.record_interpretation("twice", True, step_id=p.step_id)


def test_unknown_step_id_is_refused():
    """The model naming a step no tool produced must not mint evidence."""
    led = EvidenceLedger(run_id="inv")
    led.observe(Phase.BASELINE, "q", {})
    with pytest.raises(LedgerError, match="step-99"):
        led.record_interpretation("fabricated", True, step_id="step-99")


def test_concurrent_observations_do_not_collide():
    """ADK gathers parallel tool calls; the ledger is shared mutable state."""
    led = EvidenceLedger(run_id="inv")
    results: list[str] = []
    lock = threading.Lock()

    def observe(i: int) -> None:
        p = led.observe(Phase.ACTOR, f"q{i}", {"n": i})
        with lock:
            results.append(p.step_id)

    threads = [threading.Thread(target=observe, args=(i,)) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(results)) == 24, "step ids collided under concurrency"
    assert len({led.raw(f"raw://inv/{sid}") is not None for sid in results}) == 1
