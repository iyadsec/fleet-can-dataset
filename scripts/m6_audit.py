"""Milestone 6 audit of the generated fleetcan-sim-1.1 dataset (descriptive + validation only; no detection).

Checks the dataset against config/final/matrix/matrix_spec.json and reports: validation PASS/WARN/FAIL,
matrix completeness, profile coverage, temporal ground truth, attack coverage, dataset statistics, storage.
Writes data/final/fleetcan-sim-1.1/audit.json and prints Markdown tables.

Usage: .venv/bin/python scripts/m6_audit.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import storage  # noqa: E402

DATA = ROOT / "data"
FINAL = DATA / "final" / "fleetcan-sim-1.1"
SPEC = json.loads((ROOT / "config" / "final" / "matrix" / "matrix_spec.json").read_text())
STEP = {"simultaneous": 0.0, "small": 0.5, "medium": 2.0, "large": 10.0, "partial": 5.0, "probe": 0.1}


def table(rows):
    df = pd.DataFrame(rows)
    return "\n".join(["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
                     + ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)])


def dir_bytes(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def main():
    runs = SPEC["runs"]
    out = {"problems": []}
    rows = []
    for r in runs:
        sid = r["session_id"]
        rec = dict(r)
        md_p = DATA / "metadata" / sid / "metadata.json"
        if not md_p.exists():
            rec["missing"] = True
            rows.append(rec)
            continue
        md = json.loads(md_p.read_text())
        val = json.loads((DATA / "validation" / sid / "validation_report.json").read_text())
        dval_p = DATA / "analysis" / sid / "descriptor_validation.json"
        dval = json.loads(dval_p.read_text()) if dval_p.exists() else {"overall": "MISSING", "checks": []}
        warns = [c["id"] for c in val["checks"] if c["status"] == "WARN"] + [c["id"] for c in dval["checks"] if c["status"] == "WARN"]
        fails = [c["id"] for c in val["checks"] if c["status"] == "FAIL"] + [c["id"] for c in dval["checks"] if c["status"] == "FAIL"]
        lab = storage.read_table(DATA / "ground_truth" / sid / "frame_labels", columns=["frame_label"])
        dl = storage.read_table(DATA / "analysis" / sid / "descriptor_labels", columns=["window_class"])
        att = storage.read_table(DATA / "ground_truth" / sid / "attacks")
        memb = storage.read_table(DATA / "ground_truth" / sid / "campaign_membership")
        rep = storage.read_table(DATA / "ground_truth" / sid / "replay_map")
        drops = storage.read_table(DATA / "ground_truth" / sid / "dropped_frames", columns=["drop_kind", "frame_label"])
        prof = {v["vehicle_id"]: v["profile"][-1] for v in md["fleet"]["vehicles"]}
        n_mal = int((lab.frame_label == "malicious").sum())
        rec.update({
            "missing": False, "seed_ok": md["random_seed"] == r["seed"], "versions": md["versions"],
            "git_commit": md["git"]["commit"], "git_dirty": md["git"]["dirty"],
            "generator_validation": val["overall"], "descriptor_validation": dval["overall"], "warns": warns, "fails": fails,
            "profiles": "".join(prof[v] for v in sorted(prof)), "frames": len(lab), "malicious": n_mal, "benign": len(lab) - n_mal,
            "drops_queue": int((drops.drop_kind == "queue_drop").sum()), "drops_expired": int((drops.drop_kind == "expired_drop").sum()),
            "descriptors": len(dl), **{f"win_{k}": int((dl.window_class == k).sum()) for k in ("benign_only", "mixed", "malicious_only")},
            "vehicle_seconds": md["fleet"]["vehicle_count"] * md["simulation"]["sim_end_ns"] / 1e9,
            "attacked_vehicles": sorted(set(att.vehicle_id)), "attacked_profiles": [prof[v] for v in sorted(set(att.vehicle_id))],
            "membership": [(m.campaign_id, m.vehicle_id, int(m.relative_delay_ns)) for m in memb.itertuples()],
            "replay_same_vehicle": bool((rep.source_vehicle == rep.target_vehicle).all()) if len(rep) else None,
            "replay_rows": len(rep), "malfunction_check": next((c["status"] for c in val["checks"] if c["id"] == "A06"), None),
            "replay_check": next((c["status"] for c in val["checks"] if c["id"] == "A07"), None),
            "campaign_check": next((c["status"] for c in val["checks"] if c["id"] == "C03"), None),
            "bytes": {k: dir_bytes(DATA / k / sid) for k in ("raw", "ground_truth", "metadata", "validation", "analysis")},
        })
        rows.append(rec)
    df = pd.DataFrame(rows)
    present = df[~df.missing]

    # ---- validation counts
    status = present.apply(lambda r: "FAIL" if (r.generator_validation != "PASS" or r.descriptor_validation != "PASS" or r.fails)
                           else ("WARN" if r.warns else "PASS"), axis=1)
    out["validation"] = {"PASS": int((status == "PASS").sum()), "WARN": int((status == "WARN").sum()),
                         "FAIL": int((status == "FAIL").sum()), "MISSING": int(df.missing.sum())}
    out["warnings"] = dict(Counter(w for ws in present.warns for w in ws))
    out["failures"] = [{"session": r.session_id, "config": r.config_id, "seed": r.seed, "fails": r.fails,
                        "generator": r.generator_validation, "descriptor": r.descriptor_validation}
                       for r in present.itertuples() if status[r.Index] == "FAIL"]
    print("## Validation\n", out["validation"], "\nwarnings:", out["warnings"], "\nfailures:", out["failures"])

    # ---- matrix completeness
    sids_on_disk = {p.name for p in (DATA / "metadata").glob("FC11_*")}
    expected = {r["session_id"] for r in runs}
    cfg_reps = present.groupby("config_id").repetition.apply(lambda x: sorted(x))
    comp = {
        "configurations_expected": SPEC["configurations"], "configurations_present": int(present.config_id.nunique()),
        "sessions_expected": len(runs), "sessions_present": len(present),
        "all_configs_have_5_reps": bool(all(v == [1, 2, 3, 4, 5] for v in cfg_reps)),
        "duplicate_config_seed": int(present.duplicated(["config_id", "seed"]).sum()),
        "unexpected_fc11_sessions": sorted(sids_on_disk - expected), "missing_sessions": sorted(expected - sids_on_disk),
        "fleet_sizes": sorted(present.fleet_size.unique().tolist()), "attacks": sorted(present.attack.unique().tolist()),
        "scenarios": sorted(present.scenario.unique().tolist()), "patterns": sorted(present.pattern.unique().tolist()),
        "campaign_sizes_s3": sorted(present[present.scenario.str.startswith("S3")].campaign_size.unique().tolist()),
        "seeds_match_spec": bool(present.seed_ok.all()),
        "versions": sorted({json.dumps(v, sort_keys=True) for v in present.versions}),
        "git_commits": sorted(set(present.git_commit)), "any_dirty": bool(present.git_dirty.any()),
    }
    out["completeness"] = comp
    print("\n## Matrix completeness\n", json.dumps(comp, indent=1))

    # ---- profile coverage
    pc = {}
    for n, g in present.groupby("fleet_size"):
        if n == 2:
            pc[n] = dict(Counter("+".join(sorted(p)) for p in g.profiles))
        else:
            pc[n] = {"sessions": len(g), "profile_count_patterns": dict(Counter(
                "A%dB%dC%d" % (p.count("A"), p.count("B"), p.count("C")) for p in g.profiles)),
                "total_vehicles": dict(Counter("".join(g.profiles)))}
    out["profile_coverage"] = pc
    print("\n## Profile coverage\n", json.dumps(pc, indent=1))

    # ---- temporal ground truth
    tg = defaultdict(lambda: {"sessions": 0, "member_delays_ok": 0, "members": 0})
    for r in present.itertuples():
        if r.scenario.startswith("S3"):
            key = r.pattern if r.scenario != "S3-mixed" else "mixed(small)"
            coord = [m for m in r.membership if not m[0].endswith(("IND1", "IND2"))]
            want = [round(STEP[r.pattern if r.scenario != "S3-mixed" else "small"] * i * 1e9) for i in range(len(coord))]
            got = [m[2] for m in sorted(coord, key=lambda m: m[1])]
            tg[key]["sessions"] += 1
            tg[key]["members"] += len(got)
            tg[key]["member_delays_ok"] += int(got == want and r.campaign_check == "PASS")
            tg[key]["delays_s"] = sorted({d / 1e9 for d in got})[:4]
    out["temporal"] = dict(tg)
    print("\n## Temporal ground truth (S3)\n", table([{"condition": k, **v} for k, v in tg.items()]))

    # ---- attack coverage
    ac = []
    for f, g in present[present.attack != "none"].groupby("attack"):
        ac.append({"attack": f, "sessions": len(g), "attacked_vehicles": int(sum(len(x) for x in g.attacked_vehicles)),
                   "malicious_frames": int(g.malicious.sum()),
                   "campaign_size_dist": dict(Counter(int(x) for x in g.campaign_size)),
                   "attacked_profiles": dict(Counter(p for ps in g.attacked_profiles for p in ps)),
                   "A06_malfunction": dict(Counter(g.malfunction_check)) if f == "malfunction" else "-",
                   "A07_replay": dict(Counter(g.replay_check)) if f == "replay" else "-",
                   "replay_same_vehicle": bool(g.replay_same_vehicle.dropna().all()) if f == "replay" else "-"})
    out["attack_coverage"] = ac
    print("\n## Attack coverage\n", table(ac))

    # ---- statistics
    def stats(by):
        res = []
        for k, g in present.groupby(by):
            res.append({by: k, "sessions": len(g), "vehicle_traces": int(g.fleet_size.sum()),
                        "vehicle_seconds": round(g.vehicle_seconds.sum()), "frames": int(g.frames.sum()),
                        "benign_frames": int(g.benign.sum()), "malicious_frames": int(g.malicious.sum()),
                        "queue_drops": int(g.drops_queue.sum()), "expired_drops": int(g.drops_expired.sum()),
                        "descriptors": int(g.descriptors.sum()), "benign_only_windows": int(g.win_benign_only.sum()),
                        "mixed_windows": int(g.win_mixed.sum()), "malicious_only_windows": int(g.win_malicious_only.sum()),
                        "MB": round(sum(sum(b.values()) for b in g.bytes) / 1e6, 1)})
        return res
    present = present.assign(dataset="fleetcan-sim-1.1")
    out["statistics"] = {"total": stats("dataset")[0], "by_fleet": stats("fleet_size"), "by_scenario": stats("scenario"),
                         "by_attack": stats("attack")}
    for k in ("total", "by_fleet", "by_scenario", "by_attack"):
        v = out["statistics"][k]
        print(f"\n## Statistics {k}\n", table(v if isinstance(v, list) else [v]))

    # ---- storage
    b = pd.DataFrame(list(present.bytes))
    final_bytes = dir_bytes(FINAL)
    out["storage"] = {k: round(int(b[k].sum()) / 1e6, 1) for k in b.columns}
    out["storage"]["final_dir_MB"] = round(final_bytes / 1e6, 2)
    out["storage"]["total_MB"] = round(int(b.values.sum()) / 1e6 + final_bytes / 1e6, 1)
    print("\n## Storage (MB)\n", out["storage"])
    FINAL.mkdir(parents=True, exist_ok=True)
    (FINAL / "audit.json").write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
