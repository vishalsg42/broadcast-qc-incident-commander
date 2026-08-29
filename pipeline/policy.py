"""The deterministic delivery gate. ZERO AI.

This module is the ONLY thing that decides pass/fail. The agent may investigate,
interpret and propose; it may never adjudicate compliance. The same code that
blocks a deliverable is the code that later clears the repaired one - that is what
makes the agent's conclusion falsifiable rather than merely plausible.

Black is a POLICY, not a boolean. Deliverables legitimately *mandate* black in
specific places (head black, bars and tone, slate, 2-pop, break black). A profile
that failed on any black frame would reject nearly every real master, so we
evaluate black against permitted regions plus a body rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .qc import BlackInterval, QCReport

PASS = "PASS"
BLOCKED = "BLOCKED"
UNMEASURABLE = "UNMEASURABLE"

# What this pipeline's QC probe can actually produce. ffmpeg's `ebur128`
# implements EBU R128 gating (absolute -70 LUFS, relative -10 LU). It does NOT
# implement dialogue gating, which needs a speech anchor and is a separate
# subsystem.
PROBE_CAPABILITIES = frozenset({"bs1770_gated"})


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    status: str  # PASS | BLOCKED
    message: str
    measured: float | str | None = None
    expected: str | None = None

    @property
    def failed(self) -> bool:
        return self.status == BLOCKED


@dataclass(frozen=True)
class Verdict:
    status: str
    profile_id: str
    profile_version: int
    asset_path: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.failed]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "asset_path": self.asset_path,
            "checks": [
                {
                    "check_id": c.check_id,
                    "status": c.status,
                    "message": c.message,
                    "measured": c.measured,
                    "expected": c.expected,
                }
                for c in self.checks
            ],
        }


class Profile:
    """A delivery profile. The file is the authority; this is just an accessor."""

    def __init__(self, data: dict):
        self._d = data

    @classmethod
    def load(cls, path: str | Path) -> Profile:
        with Path(path).open() as fh:
            return cls(yaml.safe_load(fh))

    @property
    def id(self) -> str:
        return self._d["id"]

    @property
    def version(self) -> int:
        return int(self._d.get("version", 1))

    @property
    def name(self) -> str:
        """Human-readable name, e.g. for the control room's profile picker."""
        return self._d.get("name", self.id)

    @property
    def standard(self) -> str:
        return self._d.get("standard", "")

    @property
    def required_measurement(self) -> str:
        """The measurement this profile must be adjudicated against."""
        return (self._d.get("measurement") or {}).get("requires", "bs1770_gated")

    @property
    def measurement_note(self) -> str:
        return (self._d.get("measurement") or {}).get("note", "").strip()

    @property
    def is_measurable(self) -> bool:
        """Whether this pipeline's probe can produce what the profile needs.

        A profile the probe cannot measure must not be adjudicated. Comparing a
        BS.1770 gated value against a dialogue-gated target compares two
        different quantities and yields a confident, wrong verdict.
        """
        return self.required_measurement in PROBE_CAPABILITIES

    @property
    def loudness_target(self) -> tuple[float, float]:
        """(target_lufs, tolerance_lu) for integrated loudness."""
        cfg = self._d["loudness"]["integrated_lufs"]
        return float(cfg["target"]), float(cfg["tolerance"])

    @property
    def true_peak_ceiling(self) -> float | None:
        cfg = self._d["loudness"].get("true_peak_dbtp")
        return float(cfg["max"]) if cfg else None

    @property
    def max_contiguous_body_black(self) -> float:
        return float(self._d["black"]["body"]["max_contiguous_black_s"])

    @property
    def remediation_allowlist(self) -> list[dict]:
        return self._d.get("remediation_allowlist", [])

    @property
    def plain(self) -> str:
        """One sentence a non-specialist can act on."""
        return (self._d.get("plain") or "").strip()

    @property
    def plain_unmeasurable_reason(self) -> str:
        """Why this profile cannot be judged here, in ordinary words."""
        return (self._d.get("measurement", {}).get("plain") or "").strip()

    @property
    def raw(self) -> dict:
        """Escape hatch for callers that need the whole document."""
        return self._d

    @property
    def black_detector_opts(self) -> dict:
        d = self._d["black"]["detector"]
        return {
            "min_duration_s": float(d["min_duration_s"]),
            "pixel_black_threshold": float(d["pixel_black_threshold"]),
            "picture_black_ratio": float(d["picture_black_ratio"]),
        }

    def resolved_permitted_regions(self, duration_s: float) -> list[dict]:
        """Absolute (start, end) for each permitted region.

        Negative times are relative to end-of-file, so a tail region can be
        expressed without knowing the asset's duration in advance.
        """
        out = []
        for r in self._d["black"].get("permitted_regions", []):
            start = float(r["start_s"])
            end = float(r["end_s"])
            start = duration_s + start if start < 0 else start
            end = duration_s + end if end <= 0 else end
            out.append(
                {
                    "id": r["id"],
                    "required": bool(r.get("required", False)),
                    "start_s": max(0.0, start),
                    "end_s": min(duration_s, end),
                }
            )
        return out


PROFILE_DIR = Path(__file__).parent / "profiles"


def available_profiles() -> list[Profile]:
    """Every delivery profile shipped, ordered so measurable ones come first.

    The unmeasurable one is not a broken entry to hide - it is the point. A
    system that declines to adjudicate what it cannot measure is worth more than
    one that always answers.
    """
    profiles = [Profile.load(p) for p in sorted(PROFILE_DIR.glob("*.yaml"))]
    return sorted(profiles, key=lambda p: (not p.is_measurable, p.id))


def load_profile(profile_id: str) -> Profile:
    for profile in available_profiles():
        if profile.id == profile_id:
            return profile
    known = ", ".join(p.id for p in available_profiles())
    raise KeyError(f"unknown profile {profile_id!r}; known: {known}")


def _subtract_regions(
    interval: BlackInterval, regions: list[dict]
) -> list[tuple[float, float]]:
    """Return the parts of `interval` that fall OUTSIDE every permitted region."""
    parts = [(interval.start_s, interval.end_s)]
    for reg in regions:
        nxt: list[tuple[float, float]] = []
        for a, b in parts:
            # No overlap - keep as is.
            if b <= reg["start_s"] or a >= reg["end_s"]:
                nxt.append((a, b))
                continue
            # Keep whatever sticks out either side of the permitted region.
            if a < reg["start_s"]:
                nxt.append((a, reg["start_s"]))
            if b > reg["end_s"]:
                nxt.append((reg["end_s"], b))
        parts = nxt
    return [(a, b) for a, b in parts if (b - a) > 1e-6]


def _check_loudness(profile: Profile, report: QCReport) -> list[CheckResult]:
    target, tol = profile.loudness_target
    measured = report.loudness.integrated_lufs
    deviation = measured - target
    expected = f"{target} +/- {tol} LUFS (integrated)"

    checks = [
        CheckResult(
            check_id="loudness.integrated",
            status=PASS if abs(deviation) <= tol else BLOCKED,
            message=(
                f"integrated loudness {measured} LUFS ({deviation:+.1f} LU vs target {target})"
            ),
            measured=measured,
            expected=expected,
        )
    ]

    tp_max = profile.true_peak_ceiling
    tp = report.loudness.true_peak_dbtp
    if tp_max is not None and tp is not None:
        checks.append(
            CheckResult(
                check_id="loudness.true_peak",
                status=PASS if tp <= tp_max else BLOCKED,
                message=f"true peak {tp} dBTP (ceiling {tp_max})",
                measured=tp,
                expected=f"<= {tp_max} dBTP",
            )
        )
    return checks


def _check_black(profile: Profile, report: QCReport) -> list[CheckResult]:
    regions = profile.resolved_permitted_regions(report.duration_s)
    max_body = profile.max_contiguous_body_black
    checks: list[CheckResult] = []

    # 1. Required regions must actually contain black.
    for reg in regions:
        if not reg["required"]:
            continue
        want = reg["end_s"] - reg["start_s"]
        covered = 0.0
        for b in report.black_intervals:
            covered += max(0.0, min(b.end_s, reg["end_s"]) - max(b.start_s, reg["start_s"]))
        ok = covered >= want * 0.9
        checks.append(
            CheckResult(
                check_id=f"black.required.{reg['id']}",
                status=PASS if ok else BLOCKED,
                message=(
                    f"{reg['id']} {reg['start_s']:.1f}-{reg['end_s']:.1f}s: "
                    f"{covered:.2f}s of {want:.2f}s black present"
                ),
                measured=round(covered, 2),
                expected=f">= {want * 0.9:.2f}s black",
            )
        )

    # 2. Black outside permitted regions is a body defect beyond max_contiguous.
    offences: list[tuple[float, float]] = []
    for b in report.black_intervals:
        for a, z in _subtract_regions(b, regions):
            if (z - a) > max_body:
                offences.append((a, z))

    if offences:
        worst = max(offences, key=lambda p: p[1] - p[0])
        detail = ", ".join(f"{a:.2f}-{z:.2f}s" for a, z in offences)
        checks.append(
            CheckResult(
                check_id="black.body",
                status=BLOCKED,
                message=(
                    f"illegal black inside programme body: {detail} "
                    f"(longest {worst[1] - worst[0]:.2f}s)"
                ),
                measured=round(worst[1] - worst[0], 2),
                expected=f"<= {max_body}s contiguous outside permitted regions",
            )
        )
    else:
        checks.append(
            CheckResult(
                check_id="black.body",
                status=PASS,
                message="no illegal black inside programme body",
                measured=0.0,
                expected=f"<= {max_body}s contiguous outside permitted regions",
            )
        )
    return checks


def evaluate(profile: Profile, report: QCReport) -> Verdict:
    """Adjudicate one QC report against one delivery profile. Pure function.

    Returns UNMEASURABLE when the profile requires a measurement this probe
    cannot produce. Declining is the correct answer: a wrong verdict delivered
    confidently is worse than no verdict.
    """
    if not profile.is_measurable:
        return Verdict(
            status=UNMEASURABLE,
            profile_id=profile.id,
            profile_version=profile.version,
            asset_path=report.asset_path,
            checks=[
                CheckResult(
                    check_id="measurement.capability",
                    status=UNMEASURABLE,
                    message=(
                        f"{profile.id} requires {profile.required_measurement}; this "
                        f"probe produces {', '.join(sorted(PROBE_CAPABILITIES))}. "
                        + (profile.measurement_note or "Not adjudicated.")
                    ),
                    measured=None,
                    expected=profile.required_measurement,
                )
            ],
        )

    checks = _check_loudness(profile, report) + _check_black(profile, report)
    return Verdict(
        status=BLOCKED if any(c.failed for c in checks) else PASS,
        profile_id=profile.id,
        profile_version=profile.version,
        asset_path=report.asset_path,
        checks=checks,
    )


if __name__ == "__main__":
    import json
    import sys

    from .qc import run_qc

    prof = Profile.load(Path(__file__).parent / "profiles" / "ebu_r128.yaml")
    for arg in sys.argv[1:]:
        rep = run_qc(arg, black_opts=prof.black_detector_opts)
        print(json.dumps(evaluate(prof, rep).to_dict(), indent=2))
