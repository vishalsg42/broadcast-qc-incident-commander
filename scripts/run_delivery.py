#!/usr/bin/env python
"""Run one delivery through the pipeline and adjudicate it.

    python scripts/run_delivery.py media/master_good.mp4
    python scripts/run_delivery.py media/master_good.mp4 --fault pkg_h264_v7

Telemetry is emitted when OTEL_EXPORTER_OTLP_ENDPOINT is set, e.g.
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 python scripts/run_delivery.py ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import telemetry
from pipeline.policy import BLOCKED, Profile, evaluate
from pipeline.stages import PACKAGE, run_pipeline

PROFILE = Path(__file__).resolve().parent.parent / "pipeline" / "profiles" / "ebu_r128.yaml"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="path to the source master")
    ap.add_argument("--fault", help="package preset id to inject, e.g. pkg_h264_v7")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--profile", default=str(PROFILE))
    ap.add_argument("--json", action="store_true", help="emit the run as JSON")
    args = ap.parse_args()

    profile = Profile.load(args.profile)
    on = telemetry.init()
    print(f"telemetry: {'ON -> ' + (telemetry._endpoint() or '') if on else 'OFF'}")

    try:
        run = run_pipeline(
            args.source,
            out_dir=args.out_dir,
            overrides={PACKAGE: args.fault} if args.fault else None,
            black_opts=profile.black_detector_opts,
            profile=profile,
        )
    finally:
        # Batch processors export on a timer; without this flush a short-lived
        # process drops exactly the telemetry the investigation needs.
        telemetry.shutdown()

    if args.json:
        print(json.dumps(run.to_dict(), indent=2))
        return 0

    print(f"\nrun_id  {run.run_id}")
    print(f"asset   {run.asset_id}")
    print(f"{'stage':<10} {'preset':<24} {'LUFS':>8}  verdict")
    print("-" * 58)
    delivered = None
    for s in run.stages:
        v = evaluate(profile, s.qc)
        delivered = v
        print(
            f"{s.stage:<10} {s.preset.id:<24} "
            f"{s.qc.loudness.integrated_lufs:>8.1f}  {v.status}"
        )

    print()
    if delivered and delivered.status == BLOCKED:
        print("DELIVERY BLOCKED")
        for c in delivered.failures:
            print(f"  - {c.check_id}: {c.message}")
            print(f"    expected: {c.expected}")
        return 1

    print("DELIVERY CLEARED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
