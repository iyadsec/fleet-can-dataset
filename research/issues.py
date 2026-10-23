"""Issue register, corrections log, unresolved issues and the G0 assessment table (validation/), from the results.

Categories: A genuine data defect | B representation/export issue | C evaluation-methodology issue |
D expected synthetic-data limitation. Numbers are read from results/ and validation/ at run time.
Usage: .venv/bin/python -m research.issues
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research.common import RELEASE, atomic_csv          # noqa: E402

RES, VAL = RELEASE / "results", RELEASE / "validation"


def w(df, name, d=VAL):
    atomic_csv(pa.Table.from_pandas(df.astype(str), preserve_index=False), d / name)


def main():
    af = pd.read_csv(RES / "attack_family_summary.csv").set_index("attack_family")
    rp = pd.read_csv(RES / "replay_activation_timing.csv")
    fp = pd.read_csv(RES / "benign_false_positive_statistics.csv")
    pooled = fp[fp.grouping == "pooled"].iloc[0]
    first = fp[(fp.grouping == "window_position") & (fp.window_position == "first")].iloc[0]
    camp = pd.read_csv(RES / "campaign_instances.csv")
    sim = camp[(camp.coordinated.astype(str).str.lower() == "true") & (camp.temporal_pattern == "simultaneous")]
    bs = camp[camp.temporal_pattern == "bounded_spread_0p5s"]
    bs_agree = bs.window_order_agreeing_pairs.sum() / bs.window_order_pairs.sum()
    avail = rp[rp.classification == "source_frame_availability_delay"]
    exp = rp[rp.classification == "expected_replay_behavior"]
    vib = pd.read_csv(RES / "vehicle_identity_bias.csv")
    g0r = vib[(vib.analysis == "attack_rate_by_vehicle_id") & (vib.route_group == "G0")].iloc[0]
    g0 = pd.read_csv(RES / "G0_route_bias_statistics.csv")
    g0b = g0[(g0.analysis == "benign_vehicle_metric") & (g0.comparison == "G0_vs_N20") & (g0.profile != "all")]
    max_smd = g0b.standardized_mean_difference.abs().max()
    min_p = g0b.mann_whitney_p.min()
    gtv = pd.read_csv(VAL / "source_ground_truth_validation.csv")
    sch = pd.read_csv(VAL / "source_schema_validation.csv")

    I = []

    def add(i, sev, cat, title, affected, evidence, impact, action, risk, status):
        I.append({"issue_id": i, "severity": sev, "category": cat, "title": title, "affected_sessions": affected,
                  "evidence": evidence, "scientific_impact": impact, "action_taken": action, "remaining_risk": risk,
                  "resolution_status": status})

    add("FR-01", "high", "B", "Leakage-prone identifier and host-clock columns in source raw frames",
        "all 835", "source raw/can_frames contains scenario_id, session_id, frame_uid (encode N, scenario, family, K, timing) and capture_time_ns (host wall clock, correlated with generation order)",
        "a detector reading the raw table naively could learn scenario/session identity instead of CAN behaviour",
        "release observation files contain only frame_index, timestamp_ns, vehicle_id, can_id, dlc, data (835/835 verified); non-observable fields moved to metadata/frame_bus_timing.parquet; adapter allow-list + 22 leakage tests",
        "users who bypass the adapter and read metadata files", "resolved")
    add("FR-02", "high", "C", "Prohibited metadata must not reach detector features, graph construction or campaign logic",
        "all 835", "review finding P1; descriptor, model-input and graph-input guards implemented in research/adapter.py and detection/features.py",
        "label/metadata leakage would invalidate FLEET-GUARD and ROBUST-FLEET-GUARD results",
        "allow-lists for observations, model input (exactly 24 features) and graph inputs; injected-field tests; dataset-wide scan 835/835",
        "experiment code must use the adapter; profile may be used only to route to per-profile models", "resolved (safeguard in place)")
    add("FR-03", "medium", "C", "Absolute simulation time and fixed warm-up are potential shortcuts",
        "all attack sessions", "every attack starts at >= 10.0 s after a benign warm-up; session lengths are pattern-specific",
        "a detector using absolute timestamp_ns or time-since-start could separate attack from benign periods without CAN evidence",
        "documented; the 24-D descriptor uses only intra-window time differences; descriptor_timestamp_ns is for cross-vehicle alignment only",
        "detectors that add absolute-time features", "documented; evaluation decision")
    add("FR-04", "medium", "D", "Limited benign variability (few IDs, strict periodicity, constant bytes)",
        "all 835", "4-5 benign CAN IDs per profile; per-ID period CV small (±1 % jitter); benign rate std across vehicles ~0.03 frames/s; payload bytes 4-6 constant 0 in profiles A and B",
        "anomaly detection is easier than on real traffic; absolute performance does not transfer",
        "characterised in results/benign_traffic_statistics.csv, payload_statistics.csv, can_id_statistics.csv; no data change",
        "overstated detector performance if not reported", "documented (limitation)")
    add("FR-05", "medium", "C", "Benign false-positive level is fixed by construction of the original evidence constants",
        "20 S0 sessions (8 held out)", f"pooled held-out FPR at strong 0.80 = {pooled.fpr_strong_ge_0_80:.4f} (95 % CI {pooled.fpr_strong_95ci_low:.4f}-{pooled.fpr_strong_95ci_high:.4f}) vs exchangeable reference 0.20; weak-or-strong {pooled.fpr_weak_or_strong_ge_0_55:.4f} vs 0.45",
        "the percentile-normalised score makes ~20 % benign windows 'strong' regardless of the data; this is detector behaviour, not a dataset defect; no evidence of train/held-out domain shift",
        "reported; no threshold tuning", "fleet-level layers must be designed for this local false-alarm rate", "documented")
    add("FR-06", "medium", "C", "First descriptor window of each trace is almost always anomalous (ECU start-up)",
        "all 835", f"held-out benign first windows: FPR {first.fpr_strong_ge_0_80:.3f} (n = {int(first.held_out_windows)}); windows 2-5: see benign_false_positive_statistics.csv",
        "start-up windows (staggered message phases after ECU switch-on U[0,1] s) produce systematic local alerts; predictable artefact of the synthetic start-up",
        "documented; window_index and switch-on times available for exclusion/stratification", "inflated alert counts if start-up windows are not handled", "open: evaluation decision required")
    add("FR-07", "high", "C", "Original detection-threshold selection needs labelled attack validation data",
        "all attack sessions", "original P1 rule 'validation FPR <= 5 % -> highest recall' requires positives in the validation partition",
        "using attack labels for thresholds before the main evaluation must follow a pre-registered, leakage-free protocol",
        "not computed; only label-free original P2 constants (0.55/0.80) used in the sanity check", "none if the protocol is approved before evaluation",
        "open: approval required")
    add("FR-08", "high", "C", "Window-label rule: original majority rule leaves malfunction without positives",
        "195 malfunction sessions; all replay sessions", f"malfunction max attack frames per window = {int(af.loc['malfunction', 'max_attack_frames_in_window_max'])} (< 50): 0 of {int(af.loc['malfunction', 'instances'])} instances have a majority window; replay reaches the majority only through the tie rule (max {int(af.loc['replay', 'max_attack_frames_in_window_max'])} of 100)",
        "the original rule makes malfunction undetectable by definition; 'any malicious frame' changes positives and FPR semantics",
        "both labels stored separately (label_majority_original, label_any_malicious); no new rule invented", "inconsistent results across papers if the rule is not fixed", "open: approval required")
    add("FR-09", "medium", "D", "Replay partial-overlap: first member's replay window starts before source ECU activation",
        f"{avail.session_id.nunique()} sessions / {len(avail)} instances", f"replay window [0, 10 s) while the source ECU switches on at U[0,1] s; observable onset {avail.configured_to_observable_onset_ms.min():.1f}-{avail.configured_to_observable_onset_ms.max():.1f} ms after configured start; generator first injection = predicted start + (first eligible source frame - w0) + 0.25 ms exactly",
        "configured and observable onset differ by up to ~1 s for these members; labels are correct (frames carry replay ground truth only from the first injected frame)",
        "classified 'source_frame_availability_delay'; derived observable onset stored separately; configured times unchanged",
        "temporal-correlation evaluation must use observable onset", "explained (expected generator behaviour)")
    add("FR-10", "low", "D", "Replay observable onset delays in all other instances",
        f"{exp.session_id.nunique()} sessions / {len(exp)} instances", f"delay {exp.configured_to_observable_onset_ms.min():.2f}-{exp.configured_to_observable_onset_ms.max():.2f} ms = phase of the first source message after window start + 0.25 ms offset + bus time",
        "negligible relative to window span (0.28-0.37 s)", "classified 'expected_replay_behavior'", "none", "explained")
    add("FR-11", "medium", "D", "Temporal resolution of descriptors limits observable campaign timing",
        "S3 simultaneous, bounded_spread_0p5s, probe", f"100-frame windows span 0.28 s (A/C) and 0.37 s (B); simultaneous campaigns show descriptor-level onset spread median {sim.derived_window_onset_spread_s.median()*1000:.0f} ms (max {sim.derived_window_onset_spread_s.max()*1000:.0f} ms) instead of 0; bounded_spread_0p5s at K <= 100 (gaps >= 5 ms): window-level order agreement {bs_agree:.3f}; 0.1 s probe: order preserved 15/15",
        "configured differences below ~one window stride are not resolvable at descriptor level; claims about fine-grained timing must respect this",
        "quantified in campaign_timing.csv / temporal_observability.csv; no timestamp change", "over-interpretation of sub-window timing", "documented")
    add("FR-12", "high", "C", "G0 vehicle-identity structure (first-K members, 2-digit IDs)",
        "25 G0 sessions", f"G0 attacked vehicles are always vehicle_01..05 (attack rate per ID: max {g0r.max_vehicle_rate:.2f}, min {g0r.min_vehicle_rate:.2f}; chi-square p = {g0r.chi_square_uniformity_p:.1e}); IDs have 2 digits vs 3 elsewhere; N = 10, K = 5, S0/S3 only",
        "vehicle identity or ID format perfectly encodes the attack role inside G0; any identity-aware component or pooled per-ID statistic would leak",
        "identity excluded from all feature paths by the adapter; recommendation: evaluate G0 separately for fleet-level campaign metrics",
        "pooling G0 with P1/P2 in campaign-level metrics", "open: methodological approval (recommendation given)")
    add("FR-13", "low", "D", "G0 route source vs N >= 20: no measurable benign CAN difference",
        "25 G0 + 245 N20 sessions", f"per-profile benign metrics G0 vs N = 20 non-attacked vehicles: max |SMD| = {max_smd:.2f}, min Mann-Whitney p = {min_p:.2f}",
        "route source does not alter benign CAN characteristics at the measured level; differences in pooled statistics come from profile/scenario composition",
        "documented in P2_G0_bias.md", "none for benign/local analyses", "assessed")
    add("FR-14", "low", "D", "Non-saturating flooding and additive malfunction", "flooding 205, malfunction 195 sessions",
        f"flooding bus utilisation during attack max {af.loc['flooding', 'bus_utilization_during_attack_max']:.3f}; 0 dropped/expired frames in all sessions; malfunction 100 frames/s alongside the genuine message",
        "results do not transfer to saturating DoS or replacement-type spoofing", "documented in ATTACK_DEFINITIONS.md", "misinterpretation", "documented")
    add("FR-15", "low", "D", "Idealised logical CAN bus", "all 835", "no error frames, retransmissions, bus-off, bit-level effects or gateways; one bus per vehicle; 0 drops",
        "frame-loss and error conditions are not represented", "documented", "none", "documented")
    add("FR-16", "low", "D", "Generation split across two runner commits", "116 / 719 sessions",
        "f2128775 (116) and 7b659631 (719); identical generation code; reproducibility verified in M10", "none expected; covariate available in session_summary.csv (generator_commit)",
        "documented", "none", "documented")
    add("FR-17", "low", "B", "Ground-truth numeric fields stored as text in the source", "all 835",
        "source storage rule writes mixed/empty-capable columns as strings (e.g. start_ns, requested_rate_hz)", "parsing ambiguity for users",
        "original tables kept verbatim; typed derived table attack_instances.* with integer/float columns", "none", "resolved")
    add("FR-18", "low", "B", "Full CSV export stored gzip-compressed", "all 835",
        "plain CSV ~26.6 GB would not fit the 30 GB free disk with the other outputs", "none (lossless; standard tools read .csv.gz)",
        "approved by the dataset owner (2026-10-23); read-back equality verified for every session", "none", "resolved")
    add("FR-19", "info", "B", "raw scenario_id collapses S3-mixed / S3-probe to 'S3'", "45 sessions",
        "source scenario_id values: S0, S1, S2, S3 only; the matrix scenario (S3-mixed, S3-probe) is in session.json", "none (field is not detector-facing)",
        "metadata carries the matrix scenario; documented", "none", "documented")
    issues = pd.DataFrame(I)
    issues["genuine_data_defect"] = issues.category == "A"
    w(issues, "issue_register.csv")
    w(issues, "data_quality_issues.csv", RES)
    corr = pd.DataFrame([
        {"correction_id": "C-01", "issue_id": "FR-01", "type": "representation (B)", "change": "detector-safe observation schema; identifiers and host clock moved to metadata/frame_bus_timing.parquet; frame_uid replaced by frame_index (derivable, verified)", "source_data_changed": False, "reproducible_by": "research/session_export.py"},
        {"correction_id": "C-02", "issue_id": "FR-01", "type": "representation (B)", "change": "timestamp_ns := source tx_end_ns (observable bus time); can_id/dlc/payload unchanged; payload renamed data", "source_data_changed": False, "reproducible_by": "research/session_export.py"},
        {"correction_id": "C-03", "issue_id": "FR-17", "type": "representation (B)", "change": "typed derived attack_instances table; originals kept verbatim", "source_data_changed": False, "reproducible_by": "research/session_export.py"},
        {"correction_id": "C-04", "issue_id": "FR-18", "type": "format (B)", "change": "CSV export gzip-compressed (owner-approved)", "source_data_changed": False, "reproducible_by": "research/common.py atomic_csv"},
        {"correction_id": "C-05", "issue_id": "FR-02", "type": "safeguard (C)", "change": "detector adapter + allow-lists + leakage tests", "source_data_changed": False, "reproducible_by": "research/adapter.py, tests/test_metadata_leakage.py"},
        {"correction_id": "C-06", "issue_id": "-", "type": "join keys (B)", "change": "frame_index / replay_frame_index / original_frame_index added to ground-truth tables", "source_data_changed": False, "reproducible_by": "research/session_export.py"}])
    w(corr, "corrections_log.csv")
    unres = issues[issues.resolution_status.str.startswith("open")]
    w(unres, "unresolved_issues.csv")
    g0a = pd.DataFrame([
        {"item": "benign CAN traffic (per profile, G0 vs N = 20)", "result": f"max |SMD| {max_smd:.2f}; min p {min_p:.2f}", "assessment": "no route-source effect detected", "pooling": "poolable for benign/local analyses"},
        {"item": "benign descriptors (per profile, G0 vs P1/P2)", "result": "max |SMD| %.2f" % g0[g0.analysis == "benign_descriptor_feature"].standardized_mean_difference.abs().max(), "assessment": "small differences", "pooling": "poolable"},
        {"item": "fleet size", "result": "G0 N = 10 only; P1/P2 N = 20/50/100", "assessment": "N confounded with G0", "pooling": "stratify by N"},
        {"item": "scenario / timing composition", "result": "G0: S0 20 %, S3 80 % (simultaneous, small; K = 5)", "assessment": "narrower than P1/P2", "pooling": "compare within matched cells only"},
        {"item": "attack assignment / vehicle identity", "result": f"members always vehicle_01..05; chi-square p {g0r.chi_square_uniformity_p:.1e}", "assessment": "identity encodes attack role", "pooling": "evaluate separately"},
        {"item": "vehicle-ID format", "result": "2-digit (G0) vs 3-digit (P1/P2)", "assessment": "format reveals G0 membership", "pooling": "never use identity strings"},
        {"item": "P1/P2 identity bias", "result": "chi-square uniformity p >= 0.96 and |Spearman| <= 0.04 for every N; member positions uniform (KS p >= 0.19)", "assessment": "no identity bias", "pooling": "poolable"},
        {"item": "detector features include vehicle identifiers", "result": "no: 835/835 descriptor files contain only keys + 24 features; model_matrix rejects identity columns", "assessment": "pass", "pooling": "-"},
        {"item": "RECOMMENDATION", "result": "G0 should be evaluated separately for fleet-level campaign metrics; it may be pooled for benign-traffic and local-detector (per-window) analyses", "assessment": "methodological approval required", "pooling": "separate"}])
    w(g0a, "P2_G0_bias_assessment.csv")
    print(issues[["issue_id", "severity", "category", "resolution_status"]].to_string())
    print("schema pass", int(sch["pass"].sum()), "gt pass", int(gtv["pass"].sum()))


if __name__ == "__main__":
    main()
