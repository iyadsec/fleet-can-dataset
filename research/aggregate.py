"""Dataset-wide results and validation tables for fleetcan-sim-research-1.0 (from WORK/sessions/* of all 835 sessions).

Usage: .venv/bin/python -m research.aggregate
Writes RELEASE/results/*.csv and RELEASE/validation/source_*.csv, P1_temporal_consistency.csv, P2_*.csv.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.descriptor_schema import FEATURE_COLUMNS                         # noqa: E402
from research.common import (EXPECTED_CONFIGS, EXPECTED_FRAMES, EXPECTED_SESSIONS, RELEASE, SRC, SRC_FINAL, WORK,  # noqa: E402
                             atomic_csv, load_manifest)

RES = RELEASE / "results"
VAL = RELEASE / "validation"


def w(df: pd.DataFrame, path: Path):
    df = df.copy()
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].map(lambda x: "" if x is None or (isinstance(x, float) and np.isnan(x)) else x).astype(str)
    atomic_csv(pa.Table.from_pandas(df, preserve_index=False), path)
    return df


def load(name: str, sids) -> pd.DataFrame:
    parts = []
    for s in sids:
        p = WORK / "sessions" / s / f"{name}.parquet"
        if p.exists():
            parts.append(pd.read_parquet(p))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def pooled(n, mean, std):
    """Exact pooled mean/std (population) from group counts, means and population stds."""
    n, mean, std = (np.asarray(x, float) for x in (n, mean, std))
    ok = np.isfinite(n) & np.isfinite(mean) & np.isfinite(std) & (n > 0)
    n, mean, std = n[ok], mean[ok], std[ok]
    if n.sum() == 0:
        return np.nan, np.nan
    mu = (n * mean).sum() / n.sum()
    var = (n * (std ** 2 + (mean - mu) ** 2)).sum() / n.sum()
    return float(mu), float(np.sqrt(var))


def describe(x, prefix):
    x = pd.to_numeric(pd.Series(x), errors="coerce").dropna().to_numpy(float)
    if not x.size:
        return {f"{prefix}_{k}": None for k in ("mean", "std", "min", "p50", "max")}
    return {f"{prefix}_mean": x.mean(), f"{prefix}_std": x.std(), f"{prefix}_min": x.min(),
            f"{prefix}_p50": float(np.median(x)), f"{prefix}_max": x.max()}


def main():
    RES.mkdir(parents=True, exist_ok=True)
    VAL.mkdir(parents=True, exist_ok=True)
    recs = load_manifest()
    meta = {r["session_id"]: r for r in recs}
    sids = sorted(meta)
    summ = {s: json.loads((WORK / "sessions" / s / "summary.json").read_text()) for s in sids
            if (WORK / "sessions" / s / "DONE").exists()}
    missing = sorted(set(sids) - set(summ))
    if missing and os.environ.get("FLEETCAN_PARTIAL") != "1":
        raise SystemExit(f"{len(missing)} sessions not exported: {missing[:5]}")
    if missing:                                          # development only: aggregate the sessions exported so far
        sids = sorted(summ)
        meta = {s: meta[s] for s in sids}
    rg = lambda s: "G0" if meta[s]["phase"] == "G0" else f"P{meta[s]['phase']}"         # noqa: E731

    veh = load("vehicles", sids)
    att = load("attacks", sids)
    camp = load("campaigns", sids)
    rep = load("replay", sids)
    cid = load("canid", sids)
    tb = load("timebins", sids)
    ds = load("desc_stats", sids)
    for df in (veh, att, camp, rep):
        df["route_group"] = df.session_id.map(rg)
        df["N"] = df.session_id.map(lambda s: meta[s]["N"])
        df["scenario"] = df.session_id.map(lambda s: meta[s]["scenario"])
        df["temporal_pattern"] = df.session_id.map(lambda s: meta[s]["temporal_pattern"])
    veh["session_attack_family"] = veh.session_id.map(lambda s: meta[s]["attack"])

    # ================================================================== validation: source
    chk = pd.DataFrame([{**v["checks"]} for v in summ.values()]).sort_values("session_id")
    sch_cols = ["session_id", "raw_rows", "raw_schema_ok", "raw_schema", "label_schema_ok", "raw_null_values",
                "frame_uid_equals_session_and_index", "session_id_constant", "scenario_id_values",
                "timestamp_text_derivable", "transmission_timestamp_text_derivable", "queue_delay_text_derivable",
                "capture_offset_empty", "channel_matches_vehicle", "vehicles_in_frames_equal_fleet",
                "can_id_format_violations", "can_id_over_11bit", "dlc_out_of_range", "payload_length_mismatch",
                "payload_non_hex", "frame_bits_out_of_bounds", "tx_duration_equals_bits", "tx_end_globally_nondecreasing",
                "tx_end_global_ties", "capture_time_nondecreasing", "per_vehicle_row_order_is_time_order",
                "per_vehicle_timestamp_ties", "duplicate_observation_rows", "unmatched_captures",
                "parquet_roundtrip_equal", "csv_roundtrip_equal", "observation_rows_written"]
    sch = chk[sch_cols].copy()
    zero_cols = ["raw_null_values", "can_id_format_violations", "can_id_over_11bit", "dlc_out_of_range",
                 "payload_length_mismatch", "payload_non_hex", "frame_bits_out_of_bounds", "per_vehicle_timestamp_ties",
                 "duplicate_observation_rows", "unmatched_captures"]
    true_cols = [c for c in sch_cols if sch[c].dtype == bool]
    sch["pass"] = sch[true_cols].all(axis=1) & (sch[zero_cols] == 0).all(axis=1) & (sch.raw_rows == sch.observation_rows_written)
    w(sch, VAL / "source_schema_validation.csv")

    gt_cols = ["session_id", "labels_aligned", "frame_label_values", "malicious_frames", "malicious_without_attack_id",
               "benign_with_attack_id", "benign_frame_source_not_generator", "malicious_frame_source_values",
               "malicious_on_non_attacked_vehicle", "malicious_outside_configured_interval_or_vehicle",
               "unlabelled_attack_frames", "attack_captured_equals_labelled", "attack_generated_equals_transmitted_plus_dropped",
               "dropped_frames_rows", "dropped_rows_equal_bus_drops", "attack_family_matches_config", "attacks",
               "campaigns", "coordinated_campaigns", "membership_consistent", "every_attack_in_a_campaign_table",
               "attack_frames_foreign_uid", "replay_map_foreign_uid"]
    gtv = chk[gt_cols].copy()
    gz = ["malicious_without_attack_id", "benign_with_attack_id", "benign_frame_source_not_generator",
          "malicious_on_non_attacked_vehicle", "malicious_outside_configured_interval_or_vehicle", "unlabelled_attack_frames",
          "attack_frames_foreign_uid", "replay_map_foreign_uid"]
    gtrue = [c for c in gt_cols if gtv[c].dtype == bool]
    gtv["pass"] = gtv[gtrue].all(axis=1) & (gtv[gz] == 0).all(axis=1)
    w(gtv, VAL / "source_ground_truth_validation.csv")
    w(gtv.rename(columns={"pass": "all_checks_pass"}), RES / "ground_truth_checks.csv")

    # manifest / completeness
    man_rows = []
    for s in sids:
        r = meta[s]
        man_rows.append({"session_id": s, "configuration_id": r["configuration_id"], "repetition": r["repetition"],
                         "phase": r["phase"], "frames": summ[s]["frames"],
                         **{f"{d}_present": (SRC / d / s).is_dir() for d in ("raw", "ground_truth", "metadata", "validation")},
                         "generator_validation": json.loads((SRC / "validation" / s / "validation_report.json").read_text())["overall"],
                         "matrix_sha256_ok": r["matrix_sha256"] == "9fc005de3f9b712045c7eec5c610043a375ab2dad362fff5b54a092a17a1e735"})
    man = pd.DataFrame(man_rows)
    cfg_reps = man.groupby("configuration_id").repetition.apply(lambda x: sorted(x) == [1, 2, 3, 4, 5])
    man["config_has_5_repetitions"] = man.configuration_id.map(cfg_reps)
    man["pass"] = man[[c for c in man.columns if c.endswith("_present")]].all(axis=1) & (man.generator_validation == "PASS") \
        & man.matrix_sha256_ok & man.config_has_5_repetitions
    w(man, VAL / "source_manifest_validation.csv")
    totals = {"sessions": len(man), "unique_sessions": man.session_id.nunique(), "configurations": man.configuration_id.nunique(),
              "frames": int(man.frames.sum())}

    # integrity (sha256sum -c log of the frozen SHA256SUMS + anchors)
    log = (WORK / "source_sha256_check.txt").read_text().splitlines()
    ok_lines = [l for l in log if l.endswith(": OK")]
    bad_lines = [l for l in log if ":" in l and not l.endswith(": OK") and not l.startswith("EXIT")]
    integ = pd.DataFrame([{"path": l.rsplit(": ", 1)[0], "status": l.rsplit(": ", 1)[1]} for l in ok_lines + bad_lines])
    anchors = json.loads((SRC_FINAL / "FREEZE.json").read_text())
    from research.common import sha256_file
    a_rows = [
        {"path": "final/fleetcan-sim-scale-1.0/SHA256SUMS (vs FREEZE.json sha256sums_sha256)",
         "status": "OK" if sha256_file(SRC_FINAL / "SHA256SUMS") == anchors["sha256sums_sha256"] else "FAILED"},
        {"path": "final/fleetcan-sim-scale-1.0/manifest.jsonl (vs FREEZE.json manifest_sha256)",
         "status": "OK" if sha256_file(SRC_FINAL / "manifest.jsonl") == anchors["manifest_sha256"] else "FAILED"},
        {"path": "configs/fleetcan_sim_scale_1_0.yaml (vs FREEZE.json matrix_sha256)",
         "status": "OK" if sha256_file(SRC.parent / "configs" / "fleetcan_sim_scale_1_0.yaml") == anchors["matrix_sha256"] else "FAILED"}]
    integ = pd.concat([pd.DataFrame(a_rows), integ], ignore_index=True)
    integ["check"] = ["anchor"] * len(a_rows) + ["file_sha256"] * (len(integ) - len(a_rows))
    w(integ[["check", "path", "status"]], VAL / "source_integrity.csv")
    integrity = {"files_listed": len((SRC_FINAL / "SHA256SUMS").read_text().splitlines()), "files_ok": len(ok_lines),
                 "files_failed": len(bad_lines), "anchors_ok": all(r["status"] == "OK" for r in a_rows),
                 "exit": [l for l in log if l.startswith("EXIT")]}

    # replay + campaign source validation
    if len(rep):
        rv = rep[["session_id", "attack_id", "vehicle_id", "source_vehicle", "replay_links", "eligible_source_frames",
                  "links_same_id_payload_vehicle", "links_time_shift_exact", "originals_benign",
                  "replayed_frames_labelled_malicious", "links_equal_eligible_source_set", "originals_before_start"]].copy()
        rv["predicted_first_injection_matches_generator"] = rep.predicted_first_injection_ns == rep.source_first_generated_ns
        rv["pass"] = rv[[c for c in rv.columns if rv[c].dtype == bool]].all(axis=1) & (rv.originals_before_start == rv.replay_links)
        w(rv, VAL / "source_replay_validation.csv")
    cv = camp[["session_id", "campaign_id", "coordinated", "attack_family", "campaign_size", "members",
               "members_without_observable_frames"]].copy()
    sess_mem_ok = chk.set_index("session_id").membership_consistent
    cv["session_membership_consistent"] = cv.session_id.map(sess_mem_ok)
    cv["size_equals_members"] = cv.campaign_size == cv.members
    cv["pass"] = cv.session_membership_consistent & cv.size_equals_members & (cv.members_without_observable_frames == 0)
    w(cv, VAL / "source_campaign_validation.csv")

    # ================================================================== results: summaries
    sess_rows = []
    for s in sids:
        r, c = meta[s], summ[s]["checks"]
        v = veh[veh.session_id == s]
        sess_rows.append({"session_id": s, "configuration_id": r["configuration_id"], "repetition": r["repetition"],
                          "route_group": rg(s), "scenario": r["scenario"], "attack_family": r["attack"], "N": r["N"],
                          "K": r["K"], "temporal_pattern": r["temporal_pattern"], "independent_timing": r["independent_timing"],
                          "seed": r["seed"], "generator_commit": r["git_commit"][:7], "duration_s": r["duration_s"],
                          "frames": summ[s]["frames"], "malicious_frames": summ[s]["malicious_frames"],
                          "malicious_fraction": summ[s]["malicious_frames"] / summ[s]["frames"],
                          "vehicles": len(v), "attacked_vehicles": int(v.attacked.sum()), "attacks": summ[s]["attacks"],
                          "campaigns": summ[s]["campaigns"], "coordinated_campaigns": c["coordinated_campaigns"],
                          "descriptor_windows": summ[s]["descriptor_windows"],
                          "windows_with_malicious": int(v.windows_with_malicious.sum()),
                          "windows_majority_malicious": int(v.windows_majority_malicious.sum()),
                          "mean_vehicle_fps": float(v.fps_over_session.mean()),
                          "max_bus_utilization_bin": float(v.bus_utilization_max_bin.max()),
                          "bus_dropped": int(v.bus_dropped.sum()),
                          "schema_checks_pass": bool(sch.set_index("session_id").loc[s, "pass"]),
                          "ground_truth_checks_pass": bool(gtv.set_index("session_id").loc[s, "pass"]),
                          "raw_parquet": f"data/raw_parquet/{s}/can_frames.parquet", "raw_csv": f"data/raw_csv/{s}/can_frames.csv.gz",
                          "ground_truth": f"data/ground_truth/{s}/", "metadata": f"data/metadata/{s}/",
                          "descriptors": f"data/descriptors/{s}/descriptors.parquet"})
    sess = pd.DataFrame(sess_rows)
    w(sess, RES / "session_summary.csv")
    w(veh.sort_values(["session_id", "vehicle_id"]), RES / "vehicle_summary.csv")

    def group_summary(by):
        rows = []
        for k, g in sess.groupby(by):
            rows.append({by: k, "sessions": len(g), "configurations": g.configuration_id.nunique(), "frames": int(g.frames.sum()),
                         "malicious_frames": int(g.malicious_frames.sum()), "vehicles": int(g.vehicles.sum()),
                         "attacked_vehicles": int(g.attacked_vehicles.sum()), "attacks": int(g.attacks.sum()),
                         "campaigns": int(g.campaigns.sum()), "coordinated_campaigns": int(g.coordinated_campaigns.sum()),
                         "descriptor_windows": int(g.descriptor_windows.sum()),
                         "windows_with_malicious": int(g.windows_with_malicious.sum()),
                         "windows_majority_malicious": int(g.windows_majority_malicious.sum()),
                         "attack_families": ";".join(sorted(g.attack_family.unique())),
                         "fleet_sizes": ";".join(map(str, sorted(g.N.unique()))),
                         "temporal_patterns": ";".join(sorted(g.temporal_pattern.unique()))})
        return pd.DataFrame(rows)
    w(group_summary("scenario"), RES / "scenario_summary.csv")
    w(group_summary("N"), RES / "fleet_size_summary.csv")

    # ================================================================== profiles / benign traffic
    vb = veh.copy()
    prof_rows = []
    for p, g in vb.groupby("profile"):
        mu_iat, sd_iat = pooled(g.benign_iat_ms_n, g.benign_iat_ms_mean, g.benign_iat_ms_std)
        c = cid[cid.profile == p]
        prof_rows.append({"profile": p, "vehicles": len(g), "sessions": g.session_id.nunique(), "frames": int(g.frames.sum()),
                          "benign_frames": int(g.benign_frames.sum()), "malicious_frames": int(g.malicious_frames.sum()),
                          "attacked_vehicle_instances": int(g.attacked.sum()),
                          "benign_can_ids": ";".join(sorted(c.can_id.unique())), "benign_can_id_count": c.can_id.nunique(),
                          **describe(g.benign_fps_active, "benign_fps"), **describe(g.benign_can_id_entropy_bits, "benign_id_entropy_bits"),
                          "benign_iat_ms_pooled_mean": mu_iat, "benign_iat_ms_pooled_std": sd_iat,
                          **describe(g.benign_dlc_mean, "benign_dlc_mean"), **describe(g.benign_mean_byte_std, "benign_mean_byte_std"),
                          **describe(g.benign_constant_byte_positions, "constant_byte_positions"),
                          **describe(g.benign_distinct_payloads, "distinct_payloads_per_vehicle"),
                          "repeated_payload_fraction": float(g.benign_repeated_payload_frames.sum() / g.benign_frames.sum()),
                          **describe(g.benign_window_span_s_median, "benign_window_span_s")})
    w(pd.DataFrame(prof_rows), RES / "vehicle_profile_summary.csv")

    # CAN-ID statistics (benign per profile x ID; attack per family)
    id_rows = []
    for (p, i), g in cid.groupby(["profile", "can_id"]):
        mu, sd = pooled(g.period_ms_n, g.period_ms_mean, g.period_ms_std)
        id_rows.append({"frame_class": "benign", "profile": p, "attack_family": "", "can_id": i, "vehicles": len(g),
                        "frames": int(g.frames.sum()), "dlc_values": ";".join(sorted(set(";".join(g.dlc_values).split(";")))),
                        "period_ms_mean": mu, "period_ms_std": sd, "period_ms_min": g.period_ms_min.min(),
                        "period_ms_median_of_vehicle_medians": g.period_ms_p50.median(), "period_ms_max": g.period_ms_max.max(),
                        "distinct_payloads_per_vehicle_mean": g.distinct_payloads.mean(),
                        "repeated_payload_fraction": g.repeated_payload_frames.sum() / g.frames.sum(),
                        "mean_byte_std": g.mean_byte_std.mean()})
    for f, g in att.groupby("attack_family"):
        id_rows.append({"frame_class": "malicious", "profile": "", "attack_family": f, "can_id": "(per instance)",
                        "vehicles": g.groupby(["session_id", "vehicle_id"]).ngroups, "frames": int(g.derived_malicious_frames.sum()),
                        "unique_can_ids_per_instance_mean": g.derived_unique_can_ids.mean(),
                        "unique_can_ids_per_instance_max": g.derived_unique_can_ids.max(),
                        "dlc_values_per_instance_mean": g.derived_dlc_values.mean(),
                        "distinct_payloads_per_instance_mean": g.derived_distinct_payloads.mean()})
    w(pd.DataFrame(id_rows), RES / "can_id_statistics.csv")
    (RES / "detail").mkdir(exist_ok=True)
    w(cid, RES / "detail" / "vehicle_can_id_statistics.csv.gz")

    # DLC / payload histograms
    dlc_acc, byte_acc = defaultdict(lambda: np.zeros(9, np.int64)), defaultdict(lambda: np.zeros((8, 256), np.int64))
    for s in sids:
        h = json.loads((WORK / "sessions" / s / "hist.json").read_text())
        fam = meta[s]["attack"]
        for k, v in h["dlc"].items():
            p, cls = k.split("|")
            dlc_acc[(p, cls, fam if cls == "malicious" else "")] += np.asarray(v, np.int64)
        for p, v in h["byte"].items():
            byte_acc[p] += np.asarray(v, np.int64)
    dl_rows = []
    for (p, cls, fam), v in sorted(dlc_acc.items()):
        for d in range(9):
            dl_rows.append({"profile": p, "frame_class": cls, "attack_family": fam, "dlc": d, "frames": int(v[d]),
                            "fraction": float(v[d] / v.sum()) if v.sum() else 0.0})
    w(pd.DataFrame(dl_rows), RES / "dlc_statistics.csv")
    pl_rows = []
    vals = np.arange(256)
    for p, m in sorted(byte_acc.items()):
        for i in range(8):
            h = m[i]
            nn = h.sum()
            mean = float((h * vals).sum() / nn) if nn else None
            std = float(np.sqrt((h * (vals - mean) ** 2).sum() / nn)) if nn else None
            pr = h[h > 0] / nn if nn else np.array([])
            pl_rows.append({"profile": p, "frame_class": "benign", "byte_position": i, "frames_with_byte": int(nn),
                            "mean": mean, "std": std, "min": int(vals[h > 0].min()) if nn else None,
                            "max": int(vals[h > 0].max()) if nn else None, "distinct_values": int((h > 0).sum()),
                            "entropy_bits": float(-(pr * np.log2(pr)).sum()) if nn else None,
                            "fraction_zero": float(h[0] / nn) if nn else None})
    w(pd.DataFrame(pl_rows), RES / "payload_statistics.csv")

    # inter-arrival
    ia_rows = []
    for scope, pref in (("all_frames", "iat_ms"), ("benign_frames", "benign_iat_ms")):
        for keys, g in [(("all",), vb)] + [((p,), gg) for p, gg in vb.groupby("profile")]:
            mu, sd = pooled(g[f"{pref}_n"], g[f"{pref}_mean"], g[f"{pref}_std"])
            ia_rows.append({"scope": scope, "profile": keys[0], "vehicles": len(g), "gaps": int(g[f"{pref}_n"].sum()),
                            "pooled_mean_ms": mu, "pooled_std_ms": sd, "min_ms": g[f"{pref}_min"].min(),
                            "median_of_vehicle_medians_ms": g[f"{pref}_p50"].median(), "max_ms": g[f"{pref}_max"].max()})
    for (p, i), g in cid.groupby(["profile", "can_id"]):
        mu, sd = pooled(g.period_ms_n, g.period_ms_mean, g.period_ms_std)
        ia_rows.append({"scope": "benign_per_can_id_period", "profile": p, "can_id": i, "vehicles": len(g),
                        "gaps": int(g.period_ms_n.sum()), "pooled_mean_ms": mu, "pooled_std_ms": sd,
                        "min_ms": g.period_ms_min.min(), "median_of_vehicle_medians_ms": g.period_ms_p50.median(),
                        "max_ms": g.period_ms_max.max(), "coefficient_of_variation": sd / mu if mu else None})
    w(pd.DataFrame(ia_rows), RES / "interarrival_statistics.csv")

    # benign traffic characterisation (grouped)
    bt_rows = []
    groupings = [("all", None), ("profile", "profile"), ("route_group", "route_group"), ("fleet_size", "N"),
                 ("scenario", "scenario"), ("attacked_vehicle", "attacked"), ("profile_x_route_group", ["profile", "route_group"]),
                 ("profile_x_fleet_size", ["profile", "N"])]
    for name, by in groupings:
        groups = [("all", vb)] if by is None else list(vb.groupby(by))
        for k, g in groups:
            mu, sd = pooled(g.benign_iat_ms_n, g.benign_iat_ms_mean, g.benign_iat_ms_std)
            bt_rows.append({"grouping": name, "group": "|".join(map(str, k)) if isinstance(k, tuple) else str(k),
                            "vehicles": len(g), "sessions": g.session_id.nunique(), "benign_frames": int(g.benign_frames.sum()),
                            **describe(g.benign_fps_active, "benign_fps"),
                            **describe(g.unique_benign_can_ids, "unique_benign_can_ids"),
                            **describe(g.benign_can_id_entropy_bits, "can_id_entropy_bits"),
                            **describe(g.benign_most_common_id_ratio, "most_common_id_ratio"),
                            "benign_iat_ms_pooled_mean": mu, "benign_iat_ms_pooled_std": sd,
                            **describe(g.benign_dlc_mean, "dlc_mean"), **describe(g.benign_mean_byte_std, "mean_byte_std"),
                            **describe(g.benign_constant_byte_positions, "constant_byte_positions"),
                            **describe(g.benign_distinct_payloads / g.benign_frames, "distinct_payload_ratio"),
                            "repeated_payload_fraction": float(g.benign_repeated_payload_frames.sum() / g.benign_frames.sum()),
                            **describe(g.warmup_benign_frames, "warmup_benign_frames"),
                            **describe(g.switch_on_ns / 1e9, "ecu_switch_on_s"),
                            **describe(g.mean_speed_mps, "mean_speed_mps")})
    w(pd.DataFrame(bt_rows), RES / "benign_traffic_statistics.csv")
    # benign traffic over simulation time: benign frames per vehicle-second, per profile
    nveh = vb.groupby(["session_id", "profile"]).size().rename("vehicles").reset_index()
    t2 = tb.merge(nveh, on=["session_id", "profile"])
    t2["fps_per_vehicle"] = t2.benign_frames / t2.vehicles
    t2["route_group"] = t2.session_id.map(rg)
    tot = t2.groupby(["profile", "route_group", "second"]).agg(sessions=("session_id", "nunique"),
                                                               fps_per_vehicle_mean=("fps_per_vehicle", "mean"),
                                                               fps_per_vehicle_std=("fps_per_vehicle", "std")).reset_index()
    w(tot, RES / "benign_traffic_over_time.csv")
    s0 = t2[t2.session_id.map(lambda s: meta[s]["scenario"] == "S0") & (t2.second < 40)]
    w(s0.groupby(["profile", "route_group", "second"]).agg(sessions=("session_id", "nunique"),
                                                          fps_per_vehicle_mean=("fps_per_vehicle", "mean"),
                                                          fps_per_vehicle_std=("fps_per_vehicle", "std")).reset_index(),
      RES / "benign_traffic_over_time_S0.csv")

    # ================================================================== attacks / campaigns / temporal
    att_out = att.sort_values(["session_id", "attack_id"])
    w(att_out, RES / "attack_instances.csv")
    af_rows = []
    for f, g in att.groupby("attack_family"):
        af_rows.append({"attack_family": f, "instances": len(g), "sessions": g.session_id.nunique(),
                        "vehicles_attacked": g.groupby(["session_id", "vehicle_id"]).ngroups,
                        "malicious_frames": int(g.derived_malicious_frames.sum()),
                        "instances_without_observable_frames": int((g.derived_malicious_frames == 0).sum()),
                        "dropped": int(g.dropped.sum()), "skipped_inactive": int(g.skipped_inactive.sum()),
                        **describe(g.requested_rate_hz, "requested_rate_hz"), **describe(g.derived_injection_rate_hz, "injection_rate_hz"),
                        **describe(g.configured_duration_s, "configured_duration_s"),
                        **describe(g.derived_onset_delay_ms, "onset_delay_ms"), **describe(g.derived_end_delay_ms, "end_delay_ms"),
                        **describe(g.derived_first_window_ge1_delay_ms, "first_window_ge1_delay_ms"),
                        **describe(g.derived_first_window_ge50_delay_ms, "first_majority_window_delay_ms"),
                        "instances_with_majority_window": int((g.derived_majority_windows > 0).sum()),
                        **describe(g.derived_max_attack_frames_in_window, "max_attack_frames_in_window"),
                        **describe(g.derived_unique_can_ids, "unique_can_ids"),
                        **describe(g.derived_bus_utilization_during_attack, "bus_utilization_during_attack"),
                        **describe(g.derived_benign_fps_during_attack / g.derived_benign_fps_before_attack, "benign_rate_ratio_during_vs_before"),
                        **describe(g.derived_malicious_queue_delay_us_mean, "malicious_queue_delay_us")})
    w(pd.DataFrame(af_rows), RES / "attack_family_summary.csv")
    w(camp.sort_values(["session_id", "campaign_id"]), RES / "campaign_instances.csv")
    ct_rows = []
    camp["frame_order_agreement"] = camp.frame_order_agreeing_pairs / camp.ordered_pairs.replace(0, np.nan)
    camp["window_order_agreement"] = camp.window_order_agreeing_pairs / camp.window_order_pairs.replace(0, np.nan)
    for k, g in camp.groupby(["scenario", "coordinated", "temporal_pattern", "attack_family", "campaign_size"]):
        ct_rows.append({"scenario": k[0], "coordinated": k[1], "temporal_pattern": k[2], "attack_family": k[3],
                        "campaign_size": k[4], "campaigns": len(g), "sessions": g.session_id.nunique(),
                        **describe(g.configured_onset_spread_s, "configured_spread_s"),
                        **describe(g.derived_observable_onset_spread_s, "observable_spread_s"),
                        **describe(g.derived_window_onset_spread_s, "window_spread_s"),
                        "configured_min_positive_gap_s": g.configured_min_positive_gap_s.min(),
                        "ordered_pairs": int(g.ordered_pairs.sum()),
                        "frame_order_agreement": g.frame_order_agreeing_pairs.sum() / max(1, g.ordered_pairs.sum()),
                        "window_order_agreement": g.window_order_agreeing_pairs.sum() / max(1, g.window_order_pairs.sum()),
                        "members_without_observable_frames": int(g.members_without_observable_frames.sum()),
                        "members_without_majority_window": int(g.members_without_majority_window.sum()),
                        **describe(g.derived_max_onset_lag_ms, "max_onset_lag_ms"),
                        **describe(g.derived_max_window_lag_ms, "max_window_lag_ms")})
    w(pd.DataFrame(ct_rows), RES / "campaign_timing.csv")
    # temporal observability per attack instance
    span = veh.set_index(["session_id", "vehicle_id"]).benign_window_span_s_median
    to = att[["session_id", "attack_id", "attack_family", "vehicle_id", "profile", "campaign_id", "scenario", "temporal_pattern",
              "N", "route_group", "configured_start_ns", "configured_end_ns", "derived_first_malicious_timestamp_ns",
              "derived_last_malicious_timestamp_ns", "derived_onset_delay_ms", "derived_end_delay_ms",
              "derived_first_window_ge1_index", "derived_first_window_ge1_end_ns", "derived_first_window_ge1_delay_ms",
              "derived_first_window_ge5_end_ns", "derived_first_window_ge5_delay_ms",
              "derived_first_window_ge50_end_ns", "derived_first_window_ge50_delay_ms",
              "derived_last_window_end_ns", "derived_windows_with_attack_frames", "derived_majority_windows"]].copy()
    to["benign_window_span_s"] = [span.get((a, b)) for a, b in zip(to.session_id, to.vehicle_id)]
    w(to.sort_values(["session_id", "attack_id"]), RES / "temporal_observability.csv")
    # descriptor timing (per session/vehicle window cadence)
    dt = veh[["session_id", "vehicle_id", "profile", "frames", "windows", "benign_window_span_s_median", "first_timestamp_ns",
              "last_timestamp_ns", "switch_on_ns", "timestamp_order_violations"]].copy()
    dt["expected_windows"] = np.where(dt.frames >= 100, (dt.frames - 100) // 50 + 1, 0)
    dt["window_count_ok"] = dt.windows == dt.expected_windows
    dt["benign_window_stride_s_approx"] = dt.benign_window_span_s_median * 50 / 99
    w(dt, RES / "descriptor_timing.csv")
    # temporal consistency validation (per session)
    tc = chk[["session_id", "descriptor_windows", "expected_windows", "descriptor_window_count_ok", "descriptor_label_rows_match",
              "descriptor_timestamp_is_last_frame", "descriptor_window_monotone", "per_vehicle_row_order_is_time_order",
              "per_vehicle_timestamp_ties", "tx_end_globally_nondecreasing", "tx_end_global_ties"]].copy()
    tc["timestamp_basis"] = "timestamp_ns = source tx_end_ns (observable bus end-of-frame)"
    tc["pass"] = tc.descriptor_window_count_ok & tc.descriptor_label_rows_match & tc.descriptor_timestamp_is_last_frame \
        & tc.descriptor_window_monotone & (tc.per_vehicle_timestamp_ties == 0)
    w(tc, VAL / "P1_temporal_consistency.csv")

    # ================================================================== replay
    if len(rep):
        rt = rep[["session_id", "attack_id", "campaign_id", "vehicle_id", "source_vehicle", "profile", "scenario", "temporal_pattern",
                  "N", "route_group", "configured_start_ns", "replay_window_start_ns", "replay_window_end_ns", "time_offset_ns",
                  "source_first_sumo_state_ns", "source_switch_on_ns", "source_ecu_activation_ns", "ecu_active_at_window_start",
                  "source_first_frame_ns", "first_eligible_source_frame_index", "first_eligible_source_frame_ns",
                  "predicted_first_injection_ns", "source_first_generated_ns", "first_injected_generation_ns",
                  "first_observable_replay_frame_index", "first_observable_replay_frame_ns", "availability_delay_ms",
                  "bus_delay_ms", "configured_to_observable_onset_ms", "classification"]]
        w(rt.sort_values(["session_id", "attack_id"]), RES / "replay_activation_timing.csv")
        rl = rep[["session_id", "attack_id", "vehicle_id", "source_vehicle", "replay_links", "eligible_source_frames",
                  "links_same_id_payload_vehicle", "links_time_shift_exact", "originals_benign",
                  "replayed_frames_labelled_malicious", "links_equal_eligible_source_set", "originals_before_start"]]
        w(rl.sort_values(["session_id", "attack_id"]), RES / "replay_link_validation.csv")
        p2 = rep[["session_id", "attack_id", "temporal_pattern", "configured_to_observable_onset_ms", "availability_delay_ms",
                  "ecu_active_at_window_start", "classification"]].copy()
        p2["pass"] = p2.classification.isin(["expected_replay_behavior", "source_frame_availability_delay"])
        w(p2.sort_values(["session_id", "attack_id"]), VAL / "P2_replay_validation.csv")

    # ================================================================== bus
    bus_rows = []
    for k, g in veh.groupby(["session_attack_family", "attacked", "profile"]):
        bus_rows.append({"session_attack_family": k[0], "attacked_vehicle": k[1], "profile": k[2], "vehicles": len(g),
                         **describe(g.bus_utilization_overall, "utilization_overall"),
                         **describe(g.bus_utilization_max_bin, "utilization_max_100ms_bin"),
                         **describe(g.queue_delay_us_mean, "queue_delay_us_mean"), "queue_delay_us_max": g.queue_delay_us_max.max(),
                         **describe(g.queued_frame_fraction, "queued_frame_fraction"),
                         "dropped_frames": int(g.bus_dropped.sum()), "queue_drops": int(g.bus_queue_drops.sum()),
                         "expired_drops": int(g.bus_expired_drops.sum()),
                         "model": "logical CAN bus model (src/bus_model.py), 500 kbit/s, exact stuffing; not a physical-layer simulation"})
    w(pd.DataFrame(bus_rows), RES / "bus_statistics.csv")

    # ================================================================== descriptor statistics
    d_rows = []
    for (p, cls, f), g in ds.groupby(["profile", "window_class", "feature"]):
        n = g.n.sum()
        mu = g["sum"].sum() / n
        var = max(0.0, g.sumsq.sum() / n - mu * mu)
        d_rows.append({"profile": p, "window_class": cls, "feature": f, "feature_index": FEATURE_COLUMNS.index(f),
                       "windows": int(n), "mean": mu, "std": np.sqrt(var), "min": g["min"].min(), "max": g["max"].max()})
    w(pd.DataFrame(d_rows).sort_values(["profile", "window_class", "feature_index"]), RES / "descriptor_statistics.csv")

    # ================================================================== G0 and vehicle identity
    g0_rows = []
    metrics = ["benign_fps_active", "unique_benign_can_ids", "benign_can_id_entropy_bits", "benign_iat_ms_mean",
               "benign_mean_byte_std", "benign_constant_byte_positions", "benign_dlc_mean", "mean_speed_mps",
               "switch_on_ns", "bus_utilization_overall", "benign_window_span_s_median"]
    nonattacked = veh[~veh.attacked]
    for p in ["A", "B", "C", "all"]:
        sel = nonattacked if p == "all" else nonattacked[nonattacked.profile == p]
        for comp, other in (("G0_vs_P1_P2_all_N", sel[sel.route_group != "G0"]), ("G0_vs_N20", sel[(sel.route_group != "G0") & (sel.N == 20)])):
            a = sel[sel.route_group == "G0"]
            for m in metrics:
                x, y = a[m].dropna().astype(float), other[m].dropna().astype(float)
                sp = np.sqrt((x.var(ddof=1) + y.var(ddof=1)) / 2) if len(x) > 1 and len(y) > 1 else np.nan
                mw = stats.mannwhitneyu(x, y) if len(x) and len(y) and (x.nunique() > 1 or y.nunique() > 1) else None
                g0_rows.append({"analysis": "benign_vehicle_metric", "comparison": comp, "profile": p, "metric": m,
                                "G0_vehicles": len(x), "other_vehicles": len(y), "G0_mean": x.mean(), "other_mean": y.mean(),
                                "difference": x.mean() - y.mean(), "standardized_mean_difference": (x.mean() - y.mean()) / sp if sp else np.nan,
                                "mann_whitney_p": mw.pvalue if mw is not None else None})
    # composition differences
    for col in ("scenario", "attack_family", "temporal_pattern", "N"):
        for grp, g in sess.assign(G0=sess.route_group == "G0").groupby("G0"):
            for val, n in g[col].value_counts().items():
                g0_rows.append({"analysis": f"composition_{col}", "comparison": "G0" if grp else "P1_P2", "metric": str(val),
                                "G0_vehicles": None, "other_vehicles": None, "G0_mean": n / len(g) if grp else None,
                                "other_mean": None if grp else n / len(g)})
    # descriptor-level benign windows: G0 vs others (from per-session sums)
    ds2 = ds[ds.window_class == "benign_only"].copy()
    ds2["G0"] = ds2.session_id.map(lambda s: rg(s) == "G0")
    for (p, f), g in ds2.groupby(["profile", "feature"]):
        vals = {}
        for flag, h in g.groupby("G0"):
            n = h.n.sum()
            mu = h["sum"].sum() / n
            vals[flag] = (n, mu, np.sqrt(max(0.0, h.sumsq.sum() / n - mu * mu)))
        if True in vals and False in vals:
            sp = np.sqrt((vals[True][2] ** 2 + vals[False][2] ** 2) / 2)
            g0_rows.append({"analysis": "benign_descriptor_feature", "comparison": "G0_vs_P1_P2_all_N", "profile": p, "metric": f,
                            "G0_vehicles": int(vals[True][0]), "other_vehicles": int(vals[False][0]),
                            "G0_mean": vals[True][1], "other_mean": vals[False][1], "difference": vals[True][1] - vals[False][1],
                            "standardized_mean_difference": (vals[True][1] - vals[False][1]) / sp if sp else 0.0})
    g0 = pd.DataFrame(g0_rows)
    w(g0, RES / "G0_route_bias_statistics.csv")

    vid_rows = []
    veh["vehicle_number"] = veh.vehicle_id.str.extract(r"(\d+)$")[0].astype(int)
    veh["id_digits"] = veh.vehicle_id.str.extract(r"_(\d+)$")[0].str.len()
    for (grp, N), g in veh.groupby(["route_group", "N"]):
        att_s = g[g.session_attack_family != "none"]
        if not len(att_s):
            continue
        by_v = att_s.groupby("vehicle_number").attacked.agg(["sum", "count"])
        exp = by_v["sum"].sum() / by_v["count"].sum()
        chi = stats.chisquare(by_v["sum"], by_v["count"] * exp) if exp > 0 else None
        rho = stats.spearmanr(att_s.vehicle_number, att_s.attacked.astype(int))
        top = by_v.assign(rate=by_v["sum"] / by_v["count"]).sort_values("rate", ascending=False)
        vid_rows.append({"analysis": "attack_rate_by_vehicle_id", "route_group": grp, "N": N, "sessions": att_s.session_id.nunique(),
                         "vehicle_ids": len(by_v), "id_format_digits": ";".join(map(str, sorted(g.id_digits.unique()))),
                         "overall_attack_rate": exp, "min_vehicle_rate": top.rate.min(), "max_vehicle_rate": top.rate.max(),
                         "top_vehicle_ids": ";".join(f"{i}:{r:.2f}" for i, r in top.rate.head(5).items()),
                         "chi_square_uniformity_p": chi.pvalue if chi is not None else None,
                         "spearman_vehicle_number_vs_attacked": rho.statistic, "spearman_p": rho.pvalue})
    # coordinated membership vs vehicle number (S3 campaigns)
    mem_rows = []
    for s in sids:
        m = pd.read_parquet(RELEASE / "data" / "ground_truth" / s / "campaign_membership.parquet") if meta[s]["K"] else None
        if m is None or not len(m):
            continue
        cc = pd.read_parquet(RELEASE / "data" / "ground_truth" / s / "campaigns.parquet")
        coord = set(cc.campaign_id[cc.coordinated.astype(str).str.lower() == "true"])
        for r in m.itertuples():
            mem_rows.append({"session_id": s, "route_group": rg(s), "N": meta[s]["N"], "vehicle_number": int(re.search(r"(\d+)$", r.vehicle_id).group(1)),
                             "coordinated": r.campaign_id in coord, "member_selection": meta[s].get("route_source")})
    mem = pd.DataFrame(mem_rows)
    for (grp, N, co), g in mem.groupby(["route_group", "N", "coordinated"]):
        frac = g.vehicle_number / N
        vid_rows.append({"analysis": "campaign_member_position", "route_group": grp, "N": N, "coordinated": co,
                         "members": len(g), "sessions": g.session_id.nunique(),
                         "member_position_mean": frac.mean(), "member_position_uniform_expectation": (N + 1) / (2 * N),
                         "ks_uniform_p": stats.kstest((g.vehicle_number - 0.5) / N, "uniform").pvalue,
                         "distinct_member_ids": g.vehicle_number.nunique(),
                         "member_ids_le_K_fraction": None})
    w(pd.DataFrame(vid_rows), RES / "vehicle_identity_bias.csv")

    # ================================================================== dataset summary
    ds_rows = [("source_dataset", "fleetcan-sim-scale-1.0"), ("release", "fleetcan-sim-research-1.0"),
               ("sessions", totals["sessions"]), ("unique_sessions", totals["unique_sessions"]),
               ("configurations", totals["configurations"]),
               ("repetitions_per_configuration", ";".join(map(str, sorted(man.groupby("configuration_id").size().unique())))),
               ("can_frames", totals["frames"]), ("expected_can_frames", EXPECTED_FRAMES),
               ("frames_accounted_for", totals["frames"] == EXPECTED_FRAMES and int(sch.observation_rows_written.sum()) == EXPECTED_FRAMES),
               ("benign_frames", int(veh.benign_frames.sum())), ("malicious_frames", int(veh.malicious_frames.sum())),
               ("vehicle_traces", len(veh)), ("attack_instances", len(att)),
               ("attack_instances_with_observable_frames", int((att.derived_malicious_frames > 0).sum())),
               ("campaigns_total", len(camp)), ("campaigns_coordinated", int((camp.coordinated == "true").sum())),
               ("campaigns_independent", int((camp.coordinated != "true").sum())),
               ("replay_instances", len(rep)), ("descriptor_windows", int(sess.descriptor_windows.sum())),
               ("windows_with_malicious_frames", int(sess.windows_with_malicious.sum())),
               ("windows_majority_malicious", int(sess.windows_majority_malicious.sum())),
               ("source_files_sha256_ok", f"{integrity['files_ok']}/{integrity['files_listed']}"),
               ("source_anchor_checks_ok", integrity["anchors_ok"]),
               ("schema_validation_pass_sessions", int(sch["pass"].sum())), ("ground_truth_validation_pass_sessions", int(gtv["pass"].sum())),
               ("manifest_validation_pass_sessions", int(man["pass"].sum())),
               ("temporal_consistency_pass_sessions", int(tc["pass"].sum()))]
    for f, g in att.groupby("attack_family"):
        ds_rows.append((f"attack_instances_{f}", len(g)))
    for sc, g in sess.groupby("scenario"):
        ds_rows.append((f"sessions_{sc}", len(g)))
    for n_, g in sess.groupby("N"):
        ds_rows.append((f"sessions_N{n_}", len(g)))
    w(pd.DataFrame(ds_rows, columns=["metric", "value"]).astype(str), RES / "dataset_summary.csv")
    out = dict(ds_rows)
    out["integrity"] = integrity
    (WORK / "aggregate_summary.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
