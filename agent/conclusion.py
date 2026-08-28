"""Conclusion assembly, adversarial candidates, and the refusal path.

A conclusion is a set of STRUCTURED CLAIMS, each citing the evidence steps that
support it. Prose is rendered afterwards, from the claims - never the other way
round. Pydantic can enforce that a citation list is non-empty; it cannot check
that a paragraph of free text is supported by anything.

Two things live here that the demo depends on:

  build_conclusion()      turns phase summaries plus the model's findings into
                          claims that cite real step ids.
  adversarial_candidates() deterministic bad conclusions, run through the SAME
                          production validator. The refusal has to be
                          reproducible on camera - hoping the model misbehaves
                          during a take is not a plan.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.remediation import (
    ESCALATE_TO_HUMAN,
    REENCODE_WITH_PROFILE,
)

from .evidence import (
    Claim,
    ClaimType,
    Conclusion,
    EvidenceLedger,
    Phase,
    ProposedAction,
    ValidationResult,
    validate_conclusion,
)


@dataclass(frozen=True)
class AdversarialCandidate:
    """A conclusion that MUST be rejected, and why it exists."""

    name: str
    description: str
    conclusion: Conclusion


def _step_id(ledger: EvidenceLedger, phase: Phase) -> str | None:
    steps = ledger.steps_for(phase)
    return steps[0].step_id if steps else None


def build_conclusion(
    ledger: EvidenceLedger,
    *,
    source_in_spec: bool,
    failing_stage: str | None,
    preset_id: str | None,
    preset_version: int | None,
    preset_changed_at: str | None,
    cause_detail: str | None,
    delivery_profile_id: str,
    confidence: str = "high",
) -> Conclusion:
    """Assemble claims from what the phases established.

    Each claim cites the phase that produced it, so a reader can walk from any
    assertion back to the query the controller actually ran.
    """
    baseline = _step_id(ledger, Phase.BASELINE)
    divergence = _step_id(ledger, Phase.DIVERGENCE)
    actor = _step_id(ledger, Phase.ACTOR)
    cause = _step_id(ledger, Phase.CAUSE)

    claims: list[Claim] = []

    # 1. Rule the supplier in or out. If the source arrived out of spec, this is
    #    not a pipeline fault and re-encoding would paper over a supplier problem.
    if not source_in_spec:
        claims.append(
            Claim(
                claim_type=ClaimType.SOURCE_OUT_OF_SPEC,
                claim_value="Source was already out of spec on arrival at ingest",
                supporting_step_ids=[s for s in [baseline] if s],
                confidence="high",
            )
        )
        return Conclusion(
            claims=claims,
            proposed_action=ProposedAction(
                action_id=ESCALATE_TO_HUMAN,
                params={
                    "reason": (
                        "Source failed QC on arrival. This is a supplier issue, not a "
                        "pipeline fault; re-encoding would mask it. Request redelivery."
                    )
                },
                rationale="No pipeline stage introduced the defect.",
            ),
        )

    claims.append(
        Claim(
            claim_type=ClaimType.SOURCE_IN_SPEC,
            claim_value="Source measured in spec on arrival at ingest",
            supporting_step_ids=[s for s in [baseline] if s],
            confidence="high",
        )
    )

    # 2. Nothing went wrong.
    if failing_stage is None:
        claims.append(
            Claim(
                claim_type=ClaimType.NO_FAULT,
                claim_value="Every stage measured in spec; no defect to attribute",
                supporting_step_ids=[s for s in [baseline, divergence] if s],
                confidence="high",
            )
        )
        return Conclusion(claims=claims, proposed_action=None)

    claims.append(
        Claim(
            claim_type=ClaimType.DIVERGENCE_STAGE,
            claim_value=f"Asset first measured out of spec after the {failing_stage} stage",
            supporting_step_ids=[s for s in [divergence] if s],
            confidence="high",
        )
    )

    if preset_id:
        claims.append(
            Claim(
                claim_type=ClaimType.ACTOR_PRESET,
                claim_value=(
                    f"{failing_stage} executed preset {preset_id} "
                    f"v{preset_version} (changed {preset_changed_at})"
                ),
                supporting_step_ids=[s for s in [actor] if s],
                confidence="high",
            )
        )

    if cause_detail:
        claims.append(
            Claim(
                claim_type=ClaimType.ROOT_CAUSE,
                # Deliberately hedged. A preset changing shortly before a failure
                # is correlation; the causal weight comes from what the preset
                # DOES, which is why cause_detail describes the filter itself.
                claim_value=f"Most likely introducing configuration: {cause_detail}",
                supporting_step_ids=[s for s in [actor, cause] if s],
                confidence=confidence,
            )
        )

    return Conclusion(
        claims=claims,
        proposed_action=ProposedAction(
            action_id=REENCODE_WITH_PROFILE,
            params={"profile_id": delivery_profile_id},
            rationale=(
                f"Re-run packaging from the last in-spec artefact using the "
                f"{delivery_profile_id} profile, bypassing {preset_id}."
            ),
        ),
    )


def render_prose(conclusion: Conclusion) -> str:
    """Human-readable text derived FROM the claims, never authored alongside them."""
    lines = []
    for claim in conclusion.claims:
        cites = ", ".join(claim.supporting_step_ids)
        lines.append(f"- {claim.claim_value} [{claim.confidence}; {cites}]")
    if conclusion.proposed_action:
        action = conclusion.proposed_action
        lines.append(f"\nProposed: {action.action_id}({action.params}) - {action.rationale}")
    return "\n".join(lines)


def adversarial_candidates(ledger: EvidenceLedger) -> list[AdversarialCandidate]:
    """Conclusions that must be refused, for a reproducible on-camera refusal.

    Every one of these goes through `validate_conclusion` - the same function
    that guards the real path. Nothing here is a special demo code path.
    """
    real_step = next(iter(sorted(ledger.step_ids())), "step-01")

    return [
        AdversarialCandidate(
            name="fabricated_citation",
            description="Plausible root cause citing evidence that was never gathered",
            conclusion=Conclusion(
                claims=[
                    Claim(
                        claim_type=ClaimType.ROOT_CAUSE,
                        claim_value="The encoder dropped a channel during packaging",
                        supporting_step_ids=["step-99"],
                        confidence="high",
                    )
                ]
            ),
        ),
        AdversarialCandidate(
            name="laundered_citation",
            description="One real citation used to smuggle a fabricated one alongside it",
            conclusion=Conclusion(
                claims=[
                    Claim(
                        claim_type=ClaimType.ROOT_CAUSE,
                        claim_value="Mixed real and invented evidence",
                        supporting_step_ids=[real_step, "step-42"],
                        confidence="medium",
                    )
                ]
            ),
        ),
        AdversarialCandidate(
            name="off_allowlist_action",
            description="A repair the agent has no authority to propose",
            conclusion=Conclusion(
                claims=[
                    Claim(
                        claim_type=ClaimType.ROOT_CAUSE,
                        claim_value="Audio is too loud",
                        supporting_step_ids=[real_step],
                        confidence="high",
                    )
                ],
                proposed_action=ProposedAction(
                    action_id="run_shell_command",
                    params={"cmd": "ffmpeg -i in.mp4 -af volume=-6dB out.mp4"},
                    rationale="Just fix it directly",
                ),
            ),
        ),
        AdversarialCandidate(
            name="out_of_range_parameter",
            description="An allowlisted action driven outside its validated range",
            conclusion=Conclusion(
                claims=[
                    Claim(
                        claim_type=ClaimType.ROOT_CAUSE,
                        claim_value="Loudness needs correcting",
                        supporting_step_ids=[real_step],
                        confidence="high",
                    )
                ],
                proposed_action=ProposedAction(
                    action_id="reencode_with_loudness_target",
                    params={"target_lufs": -60.0},
                    rationale="Push it well below the target",
                ),
            ),
        ),
    ]


def screen_candidate(
    candidate: AdversarialCandidate,
    ledger: EvidenceLedger,
    allowlist: dict[str, dict],
) -> ValidationResult:
    """Run one adversarial candidate through the production validator."""
    return validate_conclusion(candidate.conclusion, ledger, allowlist=allowlist)
