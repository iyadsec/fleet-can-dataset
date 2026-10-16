"""Build the master dataset manifest: one JSON object per session (data/manifest.jsonl).

Every field comes from the session's metadata.json, validation reports and file listing, so a
run is discoverable without opening its frame file. Sessions without version metadata
(created before schema 1.0) are listed with schema_version "pre-1.0" and are not mixable.

Usage: .venv/bin/python scripts/build_manifest.py [--data data] [--prefix M5_] [--out data/manifest.jsonl]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import storage  # noqa: E402

MANIFEST_FIELDS = [
    "session_id", "experiment_id", "dataset_version", "schema_version", "generator_version", "descriptor_version",
    "scenario_id", "tags", "fleet_size", "vehicle_ids", "vehicle_profiles", "profile_assignment", "switch_on_ns", "ecu_jitter",
    "attack_types", "attack_ids", "campaign_ids", "campaign_sizes", "campaign_coordinated", "delay_patterns",
    "configured_relative_delays_s", "seed", "duration_s", "sim_end_s", "execution_mode", "wall_seconds",
    "frame_count", "dropped_count", "storage_format", "raw_location", "ground_truth_location",
    "descriptor_location", "descriptor_count", "git_commit", "git_dirty", "generator_validation",
    "descriptor_validation", "bytes_on_disk",
]


def session_entry(data: Path, sid: str) -> dict:
    md = json.loads((data / "metadata" / sid / "metadata.json").read_text())
    val = data / "validation" / sid / "validation_report.json"
    gen_val = json.loads(val.read_text())["overall"] if val.exists() else "missing"
    dval = data / "analysis" / sid / "descriptor_validation.json"
    desc_base = data / "analysis" / sid / "descriptors"
    versions = md.get("versions") or {}
    sched = md.get("campaign_schedule") or []
    attacks = md.get("attack_configuration") or []
    size = sum(p.stat().st_size for k in ("raw", "ground_truth", "metadata", "validation", "analysis")
               for p in (data / k / sid).rglob("*") if p.is_file())
    n_desc = None
    if storage.exists(desc_base):
        n_desc = len(storage.read_table(desc_base, columns=["descriptor_id"]))
    return {
        "session_id": sid, "experiment_id": md["experiment_id"],
        "dataset_version": versions.get("dataset_version", "unversioned"),
        "schema_version": versions.get("schema_version", "pre-1.0"),
        "generator_version": versions.get("generator_version", "pre-1.0"),
        "descriptor_version": versions.get("descriptor_version", "pre-1.0"),
        "scenario_id": md["scenario_id"], "tags": md.get("tags") or {},
        "fleet_size": md["fleet"]["vehicle_count"],
        "vehicle_ids": [v["vehicle_id"] for v in md["fleet"]["vehicles"]],
        "vehicle_profiles": {v["vehicle_id"]: v["profile"] for v in md["fleet"]["vehicles"]},
        "profile_assignment": (md["fleet"].get("assignment") or {}).get("profile_assignment"),
        "switch_on_ns": {v["vehicle_id"]: v.get("switch_on_ns", 0) for v in md["fleet"]["vehicles"]},
        "ecu_jitter": (md["fleet"].get("assignment") or {}).get("ecu_jitter") or {"model": "none", "fraction": 0.0},
        "attack_types": sorted({a["type"] for a in attacks}),
        "attack_ids": [a["attack_id"] for a in attacks],
        "campaign_ids": [c["campaign_id"] for c in sched],
        "campaign_sizes": {c["campaign_id"]: len(c["members"]) for c in sched},
        "campaign_coordinated": {c["campaign_id"]: c["coordinated"] for c in sched},
        "delay_patterns": {c["campaign_id"]: c["delay_pattern"] for c in sched},
        "configured_relative_delays_s": {c["campaign_id"]: [m["relative_delay_ns"] / 1e9 for m in c["members"]] for c in sched},
        "seed": md["random_seed"], "duration_s": md["simulation"]["duration_s_configured"],
        "sim_end_s": md["simulation"]["sim_end_ns"] / 1e9,
        "execution_mode": (md["simulation"].get("execution") or {}).get("mode", "realtime"),
        "wall_seconds": (md["simulation"].get("execution") or {}).get("wall_seconds"),
        "frame_count": md["transport_stats"]["total"]["matched"],
        "dropped_count": sum(v["dropped"] for v in md["can"]["bus_totals"].values()),
        "storage_format": storage.table_format(data / "raw" / sid / "can_frames"),
        "raw_location": str((data / "raw" / sid).relative_to(data.parent)),
        "ground_truth_location": str((data / "ground_truth" / sid).relative_to(data.parent)),
        "descriptor_location": str((data / "analysis" / sid).relative_to(data.parent)) if n_desc is not None else None,
        "descriptor_count": n_desc, "git_commit": (md.get("git") or {}).get("commit"),
        "git_dirty": (md.get("git") or {}).get("dirty"), "generator_validation": gen_val,
        "descriptor_validation": json.loads(dval.read_text())["overall"] if dval.exists() else None,
        "bytes_on_disk": size,
    }


def build(data: Path, prefix: str = "") -> list[dict]:
    sids = sorted(p.name for p in (data / "metadata").glob(f"{prefix}*") if (p / "metadata.json").exists())
    return [session_entry(data, s) for s in sids]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(Path(__file__).resolve().parent.parent / "data"))
    ap.add_argument("--prefix", default="")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    data = Path(a.data)
    rows = build(data, a.prefix)
    out = Path(a.out) if a.out else data / "manifest.jsonl"
    with open(out, "w") as fh:
        for r in rows:
            fh.write(json.dumps({k: r[k] for k in MANIFEST_FIELDS}, sort_keys=False) + "\n")
    print(f"{len(rows)} sessions -> {out}")


if __name__ == "__main__":
    main()
