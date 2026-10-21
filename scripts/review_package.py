"""Independent scientific review package for fleetcan-sim-scale-1.0 (READ-ONLY on the frozen dataset).

  select   deterministic representative sessions (first configuration in frozen-matrix order matching each
           criterion, repetition R1) -> session_selection.csv
  samples  bounded raw CAN samples (original rows, unaltered) + separate ground truth for the selected sessions
  tables   aggregate work/session_stats.jsonl (scripts/review_stats.py) into the review CSVs and summary.json
  figures  descriptive plots

Usage: .venv/bin/python scripts/review_package.py {select|samples|tables|figures} --out DIR
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))
NS = 1_000_000_000

CRITERIA = [   # (selection_id, criterion description, matcher on frozen-matrix configuration)
    ("01_S0_N20", "S0 benign, N=20", dict(scenario="S0", N=20)),
    ("02_S0_N100", "S0 benign, N=100", dict(scenario="S0", N=100)),
    ("03_S1_flooding_N50", "S1 isolated flooding, N=50", dict(scenario="S1", attack="flooding", N=50)),
    ("04_S1_malfunction_N100", "S1 isolated malfunction, N=100", dict(scenario="S1", attack="malfunction", N=100)),
    ("05_S1_replay_N20", "S1 isolated replay, N=20", dict(scenario="S1", attack="replay", N=20)),
    ("06_S2_near_fuzzy_N20", "S2 independent, near-simultaneous, fuzzy, N=20", dict(scenario="S2", attack="fuzzy", N=20, independent_timing="near_simultaneous")),
    ("07_S2_staggered_N50", "S2 independent, 0.5 s staggered, N=50 (first family in matrix order)", dict(scenario="S2", N=50, independent_timing="staggered_0.5s")),
    ("08_S2_separated_N100", "S2 independent, 10 s separated, N=100 (first family in matrix order)", dict(scenario="S2", N=100, independent_timing="separated_10s")),
    ("09_S3_sim_flooding_K100_N100", "S3 simultaneous flooding, K=100, N=100", dict(scenario="S3", attack="flooding", N=100, K=100, temporal_pattern="simultaneous")),
    ("10_S3_sim_fuzzy_K5_N20", "S3 simultaneous fuzzy, K=5, N=20", dict(scenario="S3", attack="fuzzy", N=20, K=5, temporal_pattern="simultaneous")),
    ("11_S3_small_K10_N50", "S3 0.5 s staggered, K=10, N=50 (first family)", dict(scenario="S3", N=50, K=10, temporal_pattern="small")),
    ("12_S3_medium_K5_N20", "S3 2 s staggered, K=5, N=20 (first family)", dict(scenario="S3", N=20, K=5, temporal_pattern="medium")),
    ("13_S3_large_K5_N100", "S3 10 s staggered, K=5, N=100 (first family)", dict(scenario="S3", N=100, K=5, temporal_pattern="large")),
    ("14_S3_partial_K5_N50", "S3 partial overlap, K=5, N=50 (first family)", dict(scenario="S3", N=50, K=5, temporal_pattern="partial")),
    ("15_mixed_N100", "S3 mixed (coordinated + independent), N=100 (first family)", dict(scenario="S3-mixed", N=100)),
    ("16_mixed_N50", "S3 mixed, N=50 (first family)", dict(scenario="S3-mixed", N=50)),
    ("17_S3_replay_small_K20_N20", "S3 replay campaign, 0.5 s staggered, K=20, N=20", dict(scenario="S3", attack="replay", N=20, K=20, temporal_pattern="small")),
    ("18_bounded_2s_K50_N100", "S3 bounded spread 2 s, K=50, N=100 (first family)", dict(scenario="S3", N=100, K=50, temporal_pattern="bounded_spread_2s")),
    ("19_bounded_0p5s_K100_N100", "S3 bounded spread 0.5 s, K=100, N=100 (first family)", dict(scenario="S3", N=100, K=100, temporal_pattern="bounded_spread_0p5s")),
    ("20_probe_N20", "S3 replay 0.1 s temporal probe, K=2, N=20", dict(scenario="S3-probe", N=20)),
    ("21_G0_small", "G0 route-source bridge, N=10, 0.5 s staggered (first family)", dict(phase="G0", temporal_pattern="small")),
]


def matrix():
    import run_m10_generation as R
    doc, sha = R.load_matrix(R.EXPECTED_SHA)
    return doc


def select(out: Path):
    doc = matrix()
    rows = []
    for sel_id, desc, crit in CRITERIA:
        c = next((c for c in doc["configurations"] if all(c.get(k) == v for k, v in crit.items())), None)
        if c is None:
            raise SystemExit(f"no configuration for {sel_id}")
        sid = c["session_ids"][0]
        meta = json.loads((DATA / "metadata" / sid / "metadata.json").read_text())
        t = meta["tags"]
        rows.append({"selection_id": sel_id, "criterion": desc, "rule": "first configuration in frozen-matrix order matching "
                     + json.dumps(crit) + ", repetition R1", "session_id": sid, "config_id": c["config_id"], "seed": t["seed"],
                     "N": c["N"], "K": c["K"], "scenario": c["scenario"], "attack": c["attack"],
                     "timing": c["temporal_pattern"] if c["scenario"] != "S2" else c["independent_timing"],
                     "N_A": t["N_A"], "N_B": t["N_B"], "N_C": t["N_C"], "K_A": t["K_A"], "K_B": t["K_B"], "K_C": t["K_C"],
                     "duration_s": c["duration_s"]})
    pd.DataFrame(rows).to_csv(out / "session_selection.csv", index=False)
    print(pd.DataFrame(rows)[["selection_id", "session_id"]].to_string(index=False))


def samples(out: Path):
    sel = pd.read_csv(out / "session_selection.csv")
    base = out / "samples"
    if base.exists():
        shutil.rmtree(base)
    man = []
    for r in sel.itertuples():
        sid = r.session_id
        d = base / f"{r.selection_id}__{sid}"
        (d / "raw").mkdir(parents=True)
        (d / "ground_truth").mkdir()
        meta = json.loads((DATA / "metadata" / sid / "metadata.json").read_text())
        prof = {v["vehicle_id"]: v["profile"][-1] for v in meta["fleet"]["vehicles"]}
        att = pq.read_table(DATA / "ground_truth" / sid / "attacks.parquet").to_pandas()
        fr_all = pq.read_table(DATA / "raw" / sid / "can_frames.parquet").to_pandas()
        lab_all = pq.read_table(DATA / "ground_truth" / sid / "frame_labels.parquet").to_pandas()
        # vehicles: up to 3 attacked (earliest configured start, then id) + up to 2 non-attacked of different profiles
        attv = (att.assign(s=att.start_ns.astype(np.int64)).sort_values(["s", "vehicle_id"]).vehicle_id.drop_duplicates().tolist())
        pick = attv[:3]
        used = {prof[v] for v in pick}
        non = [v for v in sorted(prof) if v not in set(attv)]
        others = []
        for p in ["A", "B", "C"]:
            cand = [v for v in non if prof[v] == p]
            if cand and (p not in used or not pick):
                others.append(cand[0])
        for v in non:
            if len(others) >= (3 if not pick else 2):
                break
            if v not in others:
                others.append(v)
        others = others[: (3 if not pick else 2)]
        vehicles = pick + others
        # segments (observable tx_end): benign before warm-up end, attack onset boundary, attack end boundary
        if len(att):
            ts = int(att.start_ns.astype(np.int64).min())
            te = int(att[att.vehicle_id.isin(pick)].end_ns.astype(np.int64).max())
        else:
            ts, te = 20 * NS, 20 * NS
        segs = [("benign_pre_attack", 9 * NS, int(9.5 * NS)), ("attack_onset_boundary", ts - NS // 2, ts + NS),
                ("attack_end_boundary", te - NS // 2, te + NS // 2)] if len(att) else \
               [("benign_a", 9 * NS, int(9.5 * NS)), ("benign_b", 20 * NS, int(20.5 * NS))]
        t = fr_all.tx_end_ns.to_numpy()
        mask = np.zeros(len(fr_all), dtype=bool)
        for _, a, b in segs:
            mask |= (t >= a) & (t < b)
        mask &= fr_all.vehicle_id.isin(vehicles).to_numpy()
        fr = fr_all[mask]
        fr.to_csv(d / "raw" / "can_frames_sample.csv", index=False)
        uids = set(fr.frame_uid)
        lab = lab_all[lab_all.frame_uid.isin(uids)]
        lab.to_csv(d / "ground_truth" / "frame_labels_sample.csv", index=False)
        files = [("raw/can_frames_sample.csv", "subset", len(fr)), ("ground_truth/frame_labels_sample.csv", "subset (labels of sampled frames)", len(lab))]
        for tname in ("attacks", "campaigns", "campaign_membership", "campaign_overlap", "dropped_frames"):
            tb = pq.read_table(DATA / "ground_truth" / sid / f"{tname}.parquet").to_pandas()
            tb.to_csv(d / "ground_truth" / f"{tname}.csv", index=False)
            files.append((f"ground_truth/{tname}.csv", "complete", len(tb)))
        af = pq.read_table(DATA / "ground_truth" / sid / "attack_frames.parquet").to_pandas()
        af = af[af.frame_uid.isin(uids)]
        af.to_csv(d / "ground_truth" / "attack_frames_sample.csv", index=False)
        files.append(("ground_truth/attack_frames_sample.csv", "subset (attack frames among sampled frames)", len(af)))
        rm = pq.read_table(DATA / "ground_truth" / sid / "replay_map.parquet").to_pandas()
        rm = rm[rm.replay_frame_uid.isin(uids)]
        rm.to_csv(d / "ground_truth" / "replay_map_sample.csv", index=False)
        files.append(("ground_truth/replay_map_sample.csv", "subset (links of sampled replay frames)", len(rm)))
        if len(rm):
            orig = fr_all[fr_all.frame_uid.isin(set(rm.original_frame_uid))]
            orig.to_csv(d / "raw" / "replay_original_frames_sample.csv", index=False)
            files.append(("raw/replay_original_frames_sample.csv", "subset (raw original frames referenced by sampled replay frames)", len(orig)))
        shutil.copy2(DATA / "metadata" / sid / "metadata.json", d / "metadata.json")
        shutil.copy2(DATA / "metadata" / sid / "bus_stats.json", d / "bus_stats.json")
        files += [("metadata.json", "complete (provenance; evaluation only)", 1), ("bus_stats.json", "complete", 1)]
        seg_txt = "; ".join(f"{n} [{a / NS:.3f}, {b / NS:.3f}) s" for n, a, b in segs)
        (d / "SAMPLE_INFO.md").write_text(
            f"# {r.selection_id}: {sid}\n\nCriterion: {r.criterion}\n\nSelection rule: {r.rule}\n\n"
            f"Vehicles sampled: {', '.join(f'{v} (profile {prof[v]}, {'attacked' if v in set(attv) else 'not attacked'})' for v in vehicles)}\n\n"
            f"Time segments (on observable bus time tx_end_ns): {seg_txt}\n\n"
            f"Raw frames: original rows of raw/{sid}/can_frames (all columns, values unaltered), filtered by vehicle and time "
            f"segment only. This is a SUBSET: {len(fr):,} of {len(fr_all):,} frames of the session.\n\n"
            "| file | completeness | rows |\n|---|---|---|\n" + "".join(f"| {f} | {c} | {n} |\n" for f, c, n in files)
            + "\nGround truth is evaluation-only and must not be used as detector input.\n")
        for f, c, n in files:
            man.append({"selection_id": r.selection_id, "session_id": sid, "file": f"samples/{d.name}/{f}", "completeness": c, "rows": n})
        man.append({"selection_id": r.selection_id, "session_id": sid, "file": "(session total)", "completeness": "frames in full session",
                    "rows": len(fr_all)})
        print(r.selection_id, sid, len(fr), "sampled of", len(fr_all), flush=True)
    pd.DataFrame(man).to_csv(out / "samples" / "sample_manifest.csv", index=False)


def tables(out: Path):
    recs = [json.loads(l) for l in (out / "work" / "session_stats.jsonl").read_text().splitlines() if l.strip()]
    errs = [r for r in recs if "error" in r]
    recs = sorted([r for r in recs if "error" not in r], key=lambda r: r["session_id"])
    S = {}
    # session_statistics.csv
    cols = ["session_id", "config_id", "repetition", "seed", "phase", "N", "K", "scenario", "attack", "timing", "independent_timing",
            "duration_s", "frames", "vehicles", "vehicles_with_malicious_frames", "attacks", "coordinated_campaigns", "independent_campaigns",
            "benign_rate_fps_mean", "bus_utilization_mean", "bus_utilization_max", "bus_utilization_peak_bin", "queue_drops", "expired_drops",
            "dropped_benign", "unique_ids_benign", "unique_ids_malicious", "zero_byte_fraction_benign", "zero_byte_fraction_malicious",
            "benign_period_jitter_cv_median", "benign_period_jitter_cv_p99", "windows", "windows_with_malicious", "windows_all_malicious",
            "benign_window_s_median", "malicious_window_s_median", "N_A", "N_B", "N_C", "K_A", "K_B", "K_C"]
    ss = pd.DataFrame([{c: r.get(c) for c in cols} for r in recs])
    ss["onset_delay_ms_median"] = [float(np.median(r["onset_delay_ms"])) if r["onset_delay_ms"] else None for r in recs]
    ss["onset_delay_ms_max"] = [float(np.max(r["onset_delay_ms"])) if r["onset_delay_ms"] else None for r in recs]
    ss.to_csv(out / "session_statistics.csv", index=False)
    # ground_truth_validation.csv
    gv = []
    for r in recs:
        row = {"session_id": r["session_id"], **r["checks"]}
        if "replay" in r:
            rp = r["replay"]
            row.update(replay_links=rp["links"], replay_missing_frames=rp["missing_frames"],
                       replay_same_id_payload_bus=rp["same_id_payload_bus"], replay_shift_max_dev_ns=rp["shift_max_dev_ns"],
                       replay_originals_before_start=rp["originals_before_start"])
        if "malfunction_value_250_fraction" in r:
            row["malfunction_value_250_fraction"] = r["malfunction_value_250_fraction"]
        row["S2_all_independent"] = (r["coordinated_campaigns"] == 0 and r["independent_campaigns"] >= 2) if r["scenario"] == "S2" else None
        row["S3_one_coordinated"] = (r["coordinated_campaigns"] == 1) if r["scenario"].startswith("S3") else None
        gv.append(row)
    gvd = pd.DataFrame(gv)
    bool_cols = ["labels_aligned", "frame_uid_unique", "attack_type_matches_session"]
    zero_cols = ["id_11bit_violations", "dlc_range_violations", "payload_length_violations", "frame_bits_violations",
                 "malicious_on_non_attacked_vehicle", "malicious_outside_attack_window", "benign_frames_with_attack_id"]
    ok = gvd[bool_cols].all(axis=1) & (gvd[zero_cols] == 0).all(axis=1)
    if "replay_links" in gvd:
        rr = gvd.replay_links.notna()
        ok &= ~rr | ((gvd.replay_missing_frames == 0) & (gvd.replay_same_id_payload_bus == gvd.replay_links)
                     & (gvd.replay_shift_max_dev_ns == 0) & (gvd.replay_originals_before_start == gvd.replay_links))
    ok &= gvd.S2_all_independent.fillna(True).astype(bool) & gvd.S3_one_coordinated.fillna(True).astype(bool)
    gvd["all_checks_pass"] = ok
    gvd.to_csv(out / "ground_truth_validation.csv", index=False)
    # attack_statistics.csv (one row per attack) + family summary
    ar = pd.DataFrame([a | {"N": r["N"], "K": r["K"], "scenario": r["scenario"], "timing": r["timing"]} for r in recs for a in r["attack_rows"]])
    ar.to_csv(out / "attack_statistics.csv", index=False)
    fam = ar.groupby("attack_type").agg(attacks=("attack_id", "count"), requested_rate_hz=("requested_rate_hz", "median"),
                                        observed_rate_hz_median=("observed_rate_hz", "median"), observed_rate_hz_min=("observed_rate_hz", "min"),
                                        observed_rate_hz_max=("observed_rate_hz", "max"), duration_s=("configured_duration_s", "median"),
                                        generated=("generated", "sum"), queue_dropped=("queue_dropped", "sum"), expired_dropped=("expired_dropped", "sum"),
                                        unique_ids_median=("unique_can_ids", "median"), unique_ids_max=("unique_can_ids", "max"),
                                        onset_ms_median=("onset_delay_ms", "median"), onset_ms_p99=("onset_delay_ms", lambda x: x.quantile(0.99)),
                                        onset_ms_max=("onset_delay_ms", "max"))
    fam.to_csv(out / "attack_family_summary.csv")
    # campaign_timing_statistics.csv
    ct = []
    for r in recs:
        for c in r["campaigns"]:
            dev = [o - cfg for o, cfg in zip(c["observed_first_frame_rel_s"], c["configured_delays_s"]) if o is not None]
            ct.append({"session_id": r["session_id"], "N": r["N"], "K": r["K"], "scenario": r["scenario"], "attack": r["attack"],
                       "timing": r["timing"] if r["scenario"] != "S2" else r["independent_timing"], "campaign_id": c["campaign_id"],
                       "coordinated": c["coordinated"], "members": c["members"], "member_profiles": c["member_profiles"],
                       "configured_spread_s": c["configured_spread_s"],
                       "observed_minus_configured_onset_s_median": float(np.median(dev)) if dev else None,
                       "observed_minus_configured_onset_s_max": float(np.max(dev)) if dev else None,
                       "observed_onset_order_matches_configured": bool(all(np.diff([o for o in c["observed_first_frame_rel_s"] if o is not None]) >= -1e-3))
                       if len(c["observed_first_frame_rel_s"]) > 1 else None})
    ctd = pd.DataFrame(ct)
    ctd.to_csv(out / "campaign_timing_statistics.csv", index=False)
    # vehicle_profile_statistics.csv
    parts = [json.loads(l) for l in (out / "work" / "profile_parts.jsonl").read_text().splitlines() if l.strip()]
    pr = []
    for p in "ABC":
        P = [x for x in parts if x["profile"] == p]
        ids = Counter()
        per = defaultdict(list)
        dl = Counter()
        bm = np.zeros(8)
        fr = sum(x["frames"] for x in P)
        for x in P:
            ids.update(x["id_counts"])
            for k, v in x["id_period_ms"].items():
                per[k].append(v)
            dl.update({int(k): v for k, v in x["dlc"].items()})
            bm += np.array(x["byte_mean"]) * x["frames"]
        vf = [f for x in P for f in x["vehicle_frames"]]
        vs = sum(x["vehicle_seconds"] for x in P)
        for k in sorted(ids):
            pr.append({"profile": p, "can_id": k, "benign_frames": ids[k], "share_of_profile_frames": ids[k] / fr,
                       "median_period_ms": float(np.median(per[k])) if per[k] else None})
        pr.append({"profile": p, "can_id": "ALL", "benign_frames": fr, "share_of_profile_frames": 1.0, "median_period_ms": None,
                   "benign_frames_per_vehicle_second": fr / vs, "vehicle_instances": len(vf), "distinct_can_ids": len(ids),
                   "dlc_distribution": json.dumps({k: round(v / fr, 4) for k, v in sorted(dl.items())}),
                   "byte_mean_by_position": json.dumps((bm / fr).round(2).tolist())})
    pd.DataFrame(pr).to_csv(out / "vehicle_profile_statistics.csv", index=False)
    # summary
    S["sessions_analysed"] = len(recs)
    S["errors"] = len(errs)
    S["frames_total"] = int(ss.frames.sum())
    S["all_checks_pass_sessions"] = int(gvd.all_checks_pass.sum())
    S["check_totals"] = {c: int(gvd[c].sum()) for c in zero_cols}
    S["replay"] = {"sessions": int(gvd.replay_links.notna().sum()), "links": int(gvd.replay_links.sum()),
                   "missing": int(gvd.replay_missing_frames.sum()), "shift_max_dev_ns": int(gvd.replay_shift_max_dev_ns.max())}
    S["malfunction_value_250_min_fraction"] = float(gvd.malfunction_value_250_fraction.min())
    S["benign_rate_fps"] = ss.benign_rate_fps_mean.describe().round(3).to_dict()
    S["jitter_cv_median"] = float(ss.benign_period_jitter_cv_median.median())
    S["jitter_cv_p99_max"] = float(ss.benign_period_jitter_cv_p99.max())
    S["bus_util"] = {"mean_of_mean": float(ss.bus_utilization_mean.mean()), "max": float(ss.bus_utilization_max.max()),
                     "peak_bin_max": float(ss.bus_utilization_peak_bin.max())}
    S["drops"] = {"queue": int(ss.queue_drops.sum()), "expired": int(ss.expired_drops.sum()), "benign_dropped": int(ss.dropped_benign.sum()),
                  "sessions_with_drops": int((ss.queue_drops + ss.expired_drops > 0).sum())}
    S["windows"] = {"total": int(ss.windows.sum()), "with_malicious": int(ss.windows_with_malicious.sum()),
                    "all_malicious": int(ss.windows_all_malicious.sum()), "benign_window_s_median": float(ss.benign_window_s_median.median()),
                    "malicious_window_s_median": float(ss.malicious_window_s_median.median())}
    S["attack_families"] = json.loads(fam.round(3).to_json(orient="index"))
    S["timing"] = ctd.groupby("timing").agg(campaigns=("campaign_id", "count"), dev_med=("observed_minus_configured_onset_s_median", "median"),
                                            dev_max=("observed_minus_configured_onset_s_max", "max"),
                                            order_ok=("observed_onset_order_matches_configured", lambda x: float(pd.Series(x).dropna().mean()) if pd.Series(x).notna().any() else None)).round(4).to_dict(orient="index")
    S["attack_window_util"] = {}
    for fam_name in ("flooding", "fuzzy", "malfunction", "replay"):
        w = [x for r in recs if r["attack"] == fam_name for x in r["attack_window_util"]]
        if w:
            S["attack_window_util"][fam_name] = {"baseline_median": float(np.median([x["baseline"] for x in w])),
                                                 "during_median": float(np.median([x["during"] for x in w])),
                                                 "during_max": float(np.max([x["during"] for x in w])),
                                                 "benign_dropped_during_total": int(np.sum([x["benign_dropped_during"] for x in w])),
                                                 "benign_queue_delay_p99_us_median": float(np.median([x["benign_queue_delay_p99_us_during"] for x in w]))}
    dlc_m = defaultdict(Counter)
    for r in recs:
        if r["attack"] != "none":
            dlc_m[r["attack"]].update({int(k): v for k, v in r["dlc_malicious"].items()})
    S["dlc_malicious"] = {k: {d: round(c / sum(v.values()), 4) for d, c in sorted(v.items())} for k, v in dlc_m.items()}
    S["attack_start_s_values"] = sorted({round(float(a["onset_delay_ms"] or 0), 0) for a in []})
    (out / "work" / "summary.json").write_text(json.dumps(S, indent=1, default=float))
    print(json.dumps({k: S[k] for k in ("sessions_analysed", "errors", "frames_total", "all_checks_pass_sessions")}))


def figures(out: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fd = out / "figures"
    fd.mkdir(exist_ok=True)
    recs = [json.loads(l) for l in (out / "work" / "session_stats.jsonl").read_text().splitlines() if l.strip()]
    recs = [r for r in recs if "error" not in r]
    col = {"A": "#4C72B0", "B": "#DD8452", "C": "#55A868", "flooding": "#C44E52", "fuzzy": "#8172B3", "malfunction": "#937860",
           "replay": "#DA8BC3", "none": "#8C8C8C"}
    # 1 benign frames per second per vehicle by profile
    vr = defaultdict(list)
    for r in recs:
        for v in r["vehicle_stats"]:
            vr[v["profile"]].append(v["benign_rate_fps"])
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.boxplot([vr[p] for p in "ABC"], tick_labels=[f"profile {p}" for p in "ABC"], showfliers=False)
    ax.set_ylabel("benign frames / s per vehicle")
    ax.set_title("Benign CAN frame rate per vehicle (all 835 sessions)")
    fig.tight_layout(); fig.savefig(fd / "01_benign_rate_by_profile.png", dpi=130); plt.close(fig)
    # 2 per-ID benign share by profile
    prs = pd.read_csv(out / "vehicle_profile_statistics.csv")
    prs = prs[prs.can_id != "ALL"]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.5), sharey=True)
    for ax, p in zip(axes, "ABC"):
        q = prs[prs.profile == p].sort_values("can_id")
        ax.bar(q.can_id, q.share_of_profile_frames, color=col[p])
        ax.set_title(f"profile {p}: {len(q)} CAN IDs"); ax.tick_params(axis="x", rotation=90, labelsize=7)
    axes[0].set_ylabel("share of benign frames")
    fig.suptitle("Benign CAN-ID distribution by profile"); fig.tight_layout(); fig.savefig(fd / "02_can_id_distribution_by_profile.png", dpi=130); plt.close(fig)
    # 3 DLC distribution benign vs malicious per family
    dl = defaultdict(Counter)
    for r in recs:
        dl["benign"].update({int(k): v for k, v in r["dlc_benign"].items()})
        if r["attack"] != "none":
            dl[r["attack"]].update({int(k): v for k, v in r["dlc_malicious"].items()})
    fig, ax = plt.subplots(figsize=(7, 3.5))
    keys = ["benign", "flooding", "fuzzy", "malfunction", "replay"]
    w = 0.16
    for i, k in enumerate(keys):
        tot = sum(dl[k].values())
        ax.bar(np.arange(9) + (i - 2) * w, [dl[k].get(x, 0) / tot for x in range(9)], width=w, label=k, color=col.get(k, "#8C8C8C"))
    ax.set_xticks(range(9)); ax.set_xlabel("DLC"); ax.set_ylabel("share of frames"); ax.legend(fontsize=7)
    ax.set_title("DLC distribution: benign vs malicious frames by attack family")
    fig.tight_layout(); fig.savefig(fd / "03_dlc_distribution.png", dpi=130); plt.close(fig)
    # 4 bus utilisation baseline vs during attack
    fig, ax = plt.subplots(figsize=(6, 3.5))
    data, labels = [], []
    for k in ["flooding", "fuzzy", "malfunction", "replay"]:
        w_ = [x for r in recs if r["attack"] == k for x in r["attack_window_util"]]
        data += [[x["baseline"] for x in w_], [x["during"] for x in w_]]
        labels += [f"{k}\nbefore", f"{k}\nduring"]
    ax.boxplot(data, tick_labels=labels, showfliers=False); ax.tick_params(axis="x", labelsize=7)
    ax.set_ylabel("bus utilisation (attacked vehicle)"); ax.set_title("Bus utilisation 5 s before vs during each attack")
    fig.tight_layout(); fig.savefig(fd / "04_bus_utilisation_attack.png", dpi=130); plt.close(fig)
    # 5 onset delay
    fig, ax = plt.subplots(figsize=(6, 3.5))
    for k in ["flooding", "fuzzy", "malfunction", "replay"]:
        v = [x for r in recs if r["attack"] == k for x in r["onset_delay_ms"]]
        ax.hist(np.clip(v, 0, 1200), bins=60, histtype="step", label=f"{k} (n={len(v)})", color=col[k])
    ax.set_yscale("log"); ax.set_xlabel("first malicious frame on bus - configured attack start (ms)"); ax.set_ylabel("attacks")
    ax.legend(fontsize=7); ax.set_title("Observable attack onset delay")
    fig.tight_layout(); fig.savefig(fd / "05_onset_delay.png", dpi=130); plt.close(fig)
    # 6 configured vs observed member delay
    fig, ax = plt.subplots(figsize=(5, 4.5))
    for r in recs:
        if not r["scenario"].startswith("S3"):
            continue
        for c in r["campaigns"]:
            if c["coordinated"] != "true":
                continue
            xs = [a for a, o in zip(c["configured_delays_s"], c["observed_first_frame_rel_s"]) if o is not None]
            ys = [o for o in c["observed_first_frame_rel_s"] if o is not None]
            ax.scatter(xs, ys, s=2, color=col[r["attack"]], alpha=0.4)
    ax.plot([0, 50], [0, 50], "k--", lw=0.6)
    ax.set_xlabel("configured member delay (s)"); ax.set_ylabel("observed first malicious frame (s, rel. campaign start)")
    ax.set_title("Coordinated campaigns: configured vs observed member timing", fontsize=9)
    fig.tight_layout(); fig.savefig(fd / "06_configured_vs_observed_delay.png", dpi=130); plt.close(fig)
    # 7 100-frame window duration
    bw = [v["benign_window_s_median"] for r in recs for v in r["vehicle_stats"] if v["benign_window_s_median"]]
    mw = [v["malicious_window_s_median"] for r in recs for v in r["vehicle_stats"] if v["malicious_window_s_median"]]
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.hist(bw, bins=60, alpha=0.6, label="benign windows (median per vehicle)")
    ax.hist(mw, bins=60, alpha=0.6, label="windows containing malicious frames")
    for x, lab in ((0.1, "0.1 s probe"), (0.5, "0.5 s stagger")):
        ax.axvline(x, color="k", ls="--", lw=0.7); ax.text(x, ax.get_ylim()[1] * 0.9, lab, fontsize=7, rotation=90, va="top")
    ax.set_xlabel("time span of a 100-frame window (s)"); ax.set_ylabel("vehicles"); ax.legend(fontsize=7)
    ax.set_title("Temporal resolution of FLEET-GUARD 100-frame windows")
    fig.tight_layout(); fig.savefig(fd / "07_window_duration.png", dpi=130); plt.close(fig)
    # 8 payload byte means
    fig, ax = plt.subplots(figsize=(6, 3.5))
    bm = defaultdict(list)
    for r in recs:
        bm["benign"].append(r["byte_mean_benign"])
        if r["byte_mean_malicious"]:
            bm[r["attack"]].append(r["byte_mean_malicious"])
    for k in keys:
        ax.plot(range(8), np.mean(bm[k], axis=0), marker="o", label=k, color=col.get(k, "#8C8C8C"))
    ax.set_xlabel("payload byte position (missing positions = 0)"); ax.set_ylabel("mean byte value"); ax.legend(fontsize=7)
    ax.set_title("Mean payload byte value by position")
    fig.tight_layout(); fig.savefig(fd / "08_payload_byte_means.png", dpi=130); plt.close(fig)
    # 9 malicious-window share by attack and K
    fig, ax = plt.subplots(figsize=(6, 3.5))
    for k in ["flooding", "fuzzy", "malfunction", "replay"]:
        q = [(r["K"] or 1, r["windows_with_malicious"] / r["windows"]) for r in recs if r["attack"] == k and r["scenario"].startswith("S3")]
        if q:
            g = pd.DataFrame(q, columns=["K", "f"]).groupby("K").f.median()
            ax.plot(g.index, g.values, marker="o", label=k, color=col[k])
    ax.set_xscale("log"); ax.set_xlabel("campaign size K"); ax.set_ylabel("share of 100-frame windows with malicious frames")
    ax.legend(fontsize=7); ax.set_title("Window-level attack prevalence (S3, median over sessions)")
    fig.tight_layout(); fig.savefig(fd / "09_window_prevalence_by_K.png", dpi=130); plt.close(fig)
    print("figures:", sorted(p.name for p in fd.iterdir()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["select", "samples", "tables", "figures"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    if out.resolve().is_relative_to(DATA.resolve()):
        sys.exit("refusing to write inside data/")
    out.mkdir(parents=True, exist_ok=True)
    {"select": select, "samples": samples, "tables": tables, "figures": figures}[a.cmd](out)


if __name__ == "__main__":
    main()
