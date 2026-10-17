"""Milestone 8.1: memory-scalability runs and equivalence checks (generator 1.3.0). Engineering tests only.

  run NAME... [--chunk-rows K] [--suffix S]   run config/m81/experiments/NAME.yaml (or config/m8/experiments/NAME.yaml)
                                              with resource sampling of the orchestrator AND the analysis process
  regress SID...                              re-run frozen fleetcan-sim-1.1 configurations as M81_REG_<sid>
  compare A B                                 tables + descriptors + labels + bus_stats + metadata + validation report
  configs                                     write config/m81/ (longer N=100 tests)

Rows -> results/m8_1_memory_scalability/memory_metrics.csv; comparisons -> results/m8_1_memory_scalability/compare/.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from compare_sessions import compare  # noqa: E402
from m8_scaling import EXPERIMENT, FLEET, TICK, _mb, _sample, camp, vid  # noqa: E402
from src import storage  # noqa: E402

DATA = ROOT / "data"
CFG = ROOT / "config" / "m81"
OUT = ROOT / "results" / "m8_1_memory_scalability"
PY = str(ROOT / ".venv" / "bin" / "python")
FIELDS = ["session_id", "fleet_size", "duration_s", "seed", "scenario", "attack_type", "campaign_size", "chunk_rows",
          "wall_time_s", "real_time_factor", "cpu_mean_percent", "cpu_peak_percent", "memory_start_mb", "memory_peak_mb",
          "memory_end_mb", "analysis_peak_mb", "analysis_wall_s", "swap_used_delta_mb", "fd_peak", "thread_peak",
          "frames_generated", "frames_transmitted", "frames_captured", "queue_drops", "expired_drops", "capture_loss",
          "flow_control_timeouts", "descriptor_count", "temp_storage_peak_mb", "output_size_mb", "validation_status"]


def swap_used_mb() -> float:
    m = {l.split(":")[0]: int(l.split()[1]) for l in Path("/proc/meminfo").read_text().splitlines()}
    return (m["SwapTotal"] - m["SwapFree"]) / 1024


def _sampled(cmd: list[str], stage: Path | None = None) -> tuple[subprocess.CompletedProcess, list[dict], float]:
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    samples, tmp_peak, t0 = [], 0.0, time.perf_counter()
    while p.poll() is None:
        s = _sample(p.pid)
        if stage is not None and stage.exists():
            tmp_peak = max(tmp_peak, _mb(stage))
        s["tmp_mb"] = tmp_peak
        samples.append(s)
        time.sleep(0.25)
    out = p.stdout.read()
    return subprocess.CompletedProcess(cmd, p.returncode, out), [s for s in samples if s["procs"]], time.perf_counter() - t0


def run_one(cfg_path: Path, sid: str, chunk_rows: int | None = None) -> dict:
    swap0 = swap_used_mb()
    cmd = [PY, "-m", "src.orchestrator", "--config", str(cfg_path), "--session-id", sid]
    if chunk_rows:
        cmd += ["--chunk-rows", str(chunk_rows)]
    g, smp, _ = _sampled(cmd, DATA / ".incomplete" / sid)
    (OUT / "logs").mkdir(parents=True, exist_ok=True)
    (OUT / "logs" / f"{sid}.log").write_text(g.stdout)
    if g.returncode != 0:
        raise RuntimeError(f"{sid}: orchestrator exit {g.returncode}\n{g.stdout[-3000:]}")
    a, asmp, a_wall = _sampled([PY, "-m", "analysis.run_session", sid])
    (OUT / "logs" / f"{sid}.analysis.log").write_text(a.stdout)
    swap1 = max(swap_used_mb(), swap0)
    md = json.loads((DATA / "metadata" / sid / "metadata.json").read_text())
    val = json.loads((DATA / "validation" / sid / "validation_report.json").read_text())
    dval = json.loads((DATA / "analysis" / sid / "descriptor_validation.json").read_text())
    cpu = [100.0 * (b["cpu_ticks"] - x["cpu_ticks"]) / TICK / (b["t"] - x["t"]) for x, b in zip(smp, smp[1:]) if b["t"] > x["t"]]
    bt, tot = md["can"]["bus_totals"], md["transport_stats"]["total"]
    sim_s = md["simulation"]["sim_end_ns"] / 1e9
    wall = md["simulation"]["execution"]["wall_seconds"]
    tags = md.get("tags", {})
    ndesc = len(pd.read_parquet(DATA / "analysis" / sid / "descriptors.parquet", columns=["descriptor_id"]))
    row = {"session_id": sid, "fleet_size": md["fleet"]["vehicle_count"], "duration_s": sim_s, "seed": md["random_seed"],
           "scenario": md["scenario_id"], "attack_type": tags.get("attack"), "campaign_size": tags.get("campaign_size"),
           "chunk_rows": md["simulation"]["execution"].get("recorder", {}).get("chunk_rows"),
           "wall_time_s": wall, "real_time_factor": round(sim_s / wall, 4),
           "cpu_mean_percent": round(float(np.mean(cpu)), 1) if cpu else None, "cpu_peak_percent": round(float(np.max(cpu)), 1) if cpu else None,
           "memory_start_mb": round(smp[0]["rss_kb"] / 1024, 1), "memory_peak_mb": round(max(s["rss_kb"] for s in smp) / 1024, 1),
           "memory_end_mb": round(smp[-1]["rss_kb"] / 1024, 1),
           "analysis_peak_mb": round(max(s["rss_kb"] for s in asmp) / 1024, 1) if asmp else None, "analysis_wall_s": round(a_wall, 1),
           "swap_used_delta_mb": round(swap1 - swap0, 1), "fd_peak": max(s["fds"] for s in smp), "thread_peak": max(s["threads"] for s in smp),
           "frames_generated": sum(v["generated"] for v in bt.values()), "frames_transmitted": sum(v["transmitted"] for v in bt.values()),
           "frames_captured": tot.get("captured"), "queue_drops": sum(v["queue_dropped"] for v in bt.values()),
           "expired_drops": sum(v["expired_dropped"] for v in bt.values()),
           "capture_loss": sum(v["transmitted"] for v in bt.values()) - int(tot.get("captured", 0)),
           "flow_control_timeouts": md["simulation"]["execution"]["flow_control_timeouts"], "descriptor_count": ndesc,
           "temp_storage_peak_mb": round(max(s["tmp_mb"] for s in smp), 1),
           "output_size_mb": round(sum(_mb(DATA / d / sid) for d in ("raw", "ground_truth", "analysis", "metadata", "validation")), 2),
           "validation_status": f"generator={val['overall']};descriptor={dval['overall']};analysis_exit={a.returncode}"}
    f = OUT / "memory_metrics.csv"
    new = not f.exists()
    with open(f, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    print(json.dumps(row), flush=True)
    return row


# ------------------------------------------------------------------ equivalence
VOLATILE_META = {"session_id", "versions", "git", "wall_clock", "config_sha256", "software", "description", "experiment_id"}


def _flat(d, prefix=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flat(v, f"{prefix}.{k}" if prefix else k))
    else:
        out[prefix] = d
    return out


def compare_full(a: str, b: str) -> dict:
    tables = compare(DATA, a, b)
    labels = {}
    for t in ("descriptor_labels",):
        ta, tb = (storage.read_table(DATA / "analysis" / s / t) for s in (a, b))
        cols = [c for c in ta.columns if c not in ("descriptor_id", "session_id")]
        labels[t] = "IDENTICAL" if ta[cols].reset_index(drop=True).equals(tb[cols].reset_index(drop=True)) else "DIFFERENT"
    ba, bb = (json.loads((DATA / "metadata" / s / "bus_stats.json").read_text()) for s in (a, b))
    ma, mb = (json.loads((DATA / "metadata" / s / "metadata.json").read_text()) for s in (a, b))
    fa, fb = _flat({k: v for k, v in ma.items() if k not in VOLATILE_META}), _flat({k: v for k, v in mb.items() if k not in VOLATILE_META})
    meta_diff = sorted(k for k in set(fa) | set(fb) if fa.get(k) != fb.get(k))
    # simulation.sumo_config / sumo_command: path of the per-session generated scenario directory (contains the session id);
    # the route content itself is compared through simulation.route_generation (route_sha256, per-vehicle assignment)
    expected_meta = [k for k in meta_diff if k.startswith(("simulation.execution.", "time_base.wall_anchor", "simulation.sumo_command",
                                                           "simulation.sumo_config", "dataset_version"))]
    va, vb = (json.loads((DATA / "validation" / s / "validation_report.json").read_text()) for s in (a, b))
    skip = {"V25", "V27"}                                  # git commit / generator version (expected to differ)
    import re

    def logical(c, sid):                                   # capture-layer (host wall clock) numbers are not research timing
        d = c["detail"].replace(sid, "<sid>")
        if c["id"] == "C05":
            d = re.sub(r"deviate up to [0-9.]+ ms", "deviate up to <capture> ms", d)
        return (c["id"], c["status"], d)

    def info_logical(info):
        info = json.loads(json.dumps(info))
        for rows in (info.get("campaign_delay_by_layer") or {}).values():
            for r in rows:
                r.pop("first_capture_delay_s", None)
        info.pop("capture_offset_us", None)
        return info
    ca = [logical(c, a) for c in va["checks"] if c["id"] not in skip]
    cb = [logical(c, b) for c in vb["checks"] if c["id"] not in skip]
    da, db = (json.loads((DATA / "analysis" / s / "descriptor_validation.json").read_text()) for s in (a, b))
    out = {"a": a, "b": b, "tables": tables, "labels": labels, "bus_stats_identical": ba == bb,
           "metadata_differences": meta_diff, "metadata_unexpected_differences": [k for k in meta_diff if k not in expected_meta],
           "validation_checks_identical": ca == cb, "validation_info_identical": info_logical(va["info"]) == info_logical(vb["info"]),
           "capture_layer_differences": [c["id"] for c, d in zip(va["checks"], vb["checks"]) if c["id"] == "C05" and c["detail"] != d["detail"]]
                                        + (["info.campaign_delay_by_layer.first_capture_delay_s"] if va["info"].get("campaign_delay_by_layer") != vb["info"].get("campaign_delay_by_layer") else []),
           "validation_overall": [va["overall"], vb["overall"]],
           "descriptor_validation_identical": [(c["id"], c["status"], c["detail"]) for c in da["checks"]] == [(c["id"], c["status"], c["detail"]) for c in db["checks"]]}
    out["identical"] = (all(v.startswith("IDENTICAL") for v in tables.values()) and all(v == "IDENTICAL" for v in labels.values())
                        and out["bus_stats_identical"] and not out["metadata_unexpected_differences"] and out["validation_checks_identical"]
                        and out["validation_info_identical"] and out["descriptor_validation_identical"])
    (OUT / "compare").mkdir(parents=True, exist_ok=True)
    (OUT / "compare" / f"{a}__vs__{b}.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: out[k] for k in ("a", "b", "identical", "bus_stats_identical", "metadata_unexpected_differences",
                                          "validation_checks_identical", "validation_info_identical", "descriptor_validation_identical")}))
    if not out["identical"]:
        print(json.dumps(out, indent=1, default=str))
    return out


def verify_long(sid: str, group_size: int = 5) -> dict:
    """Independent bounded verification of a long session (no use of src/validator.py): frame UIDs dense and
    unique; IDs, profiles, switch-on and route re-derived from the seed; every ECU release time re-derived from its
    jitter stream (per vehicle group); descriptors of first / middle / last vehicle (and campaign members)
    recomputed from raw frames; campaign membership delays exact."""
    import yaml
    from final_requirements_audit import FEATURES, descriptor_features, expected_profiles, expected_switch_on, sub_seed
    from src import route_generator, session_groups
    md = json.loads((DATA / "metadata" / sid / "metadata.json").read_text())
    seed, veh = md["random_seed"], md["fleet"]["vehicles"]
    vids = [v["vehicle_id"] for v in veh]
    issues = []

    def need(c, m):
        if not c:
            issues.append(m)
    need(vids == [f"vehicle_{i:03d}" for i in range(1, len(vids) + 1)], "vehicle ids")
    need([v["profile"] for v in veh] == expected_profiles(seed, len(vids)), "profiles")
    need([int(v["switch_on_ns"]) for v in veh] == expected_switch_on(seed, len(vids)), "switch-on")
    rg = md["simulation"].get("route_generation")
    if rg:
        _, info = route_generator.generate(ROOT / "sumo/network/grid4x4.net.xml", vids, sub_seed(seed, "route_generation"),
                                           md["simulation"]["duration_s_configured"])
        need(info["route_sha256"] == rg["route_sha256"], "route not reproducible")
    raw, gt = DATA / "raw" / sid, DATA / "ground_truth" / sid
    n, bad_uid = 0, 0
    for b in session_groups.iter_column_batches(raw / "can_frames", ["frame_uid"]):
        want = [f"{sid}-F{i:09d}" for i in range(n, n + len(b))]
        bad_uid += int((b.frame_uid.to_numpy() != np.array(want)).sum())
        n += len(b)
    need(bad_uid == 0, f"{bad_uid} frame UIDs not dense/ordered")
    P = yaml.safe_load((ROOT / "config/profiles/profiles_abc.yaml").read_text())["profiles"]
    msgs = {p: {m["name"]: m for m in P[p]["messages"]} for p in P}
    prof = {v["vehicle_id"]: v["profile"] for v in veh}
    so = {v["vehicle_id"]: int(v["switch_on_ns"]) for v in veh}
    jseed = sub_seed(seed, "ecu_jitter")
    memb = storage.read_table(gt / "campaign_membership")
    recon_v = sorted({vids[0], vids[len(vids) // 2], vids[-1], *memb.vehicle_id})
    mism = streams = n_desc = 0
    err = 0.0
    d_all = storage.read_table(DATA / "analysis" / sid / "descriptors")
    d_all = d_all[d_all.vehicle_id.isin(recon_v)]
    for grp in session_groups.vehicle_groups(vids, group_size):
        f = session_groups.read_filtered(raw / "can_frames", "vehicle_id", grp, columns=["frame_uid", "vehicle_id", "sim_time_ns", "tx_end_ns", "can_id", "dlc", "payload"])
        lab = session_groups.read_filtered(gt / "frame_labels", "frame_uid", set(f.frame_uid), columns=["frame_uid", "frame_label", "message_name"])
        st = session_groups.read_filtered(raw / "vehicle_states", "vehicle_id", grp, columns=["vehicle_id", "sim_time_ns"])
        f = f.merge(lab, on="frame_uid")
        for c in ("sim_time_ns", "tx_end_ns", "dlc"):
            f[c] = f[c].astype("int64")
        t0 = st.assign(t=st.sim_time_ns.astype("int64")).groupby("vehicle_id").t.min()
        for (v, m), g in f[f.frame_label == "benign"].groupby(["vehicle_id", "message_name"]):
            spec = msgs[prof[v]][m]
            Pn, off = round(spec["period_ms"] * 1e6), round(spec["offset_ms"] * 1e6)
            t = np.sort(g.sim_time_ns.to_numpy())
            nominal = int(t0[v]) + so[v] + off + np.arange(len(t)) * Pn
            rel = nominal + np.round(np.random.default_rng(sub_seed(jseed, f"ecu_jitter/{v}/{m}")).uniform(-0.01, 0.01, size=len(t)) * Pn).astype(np.int64)
            rel[0] = max(int(t0[v]), rel[0])
            mism += int((rel != t).sum())
            streams += 1
        d = d_all
        for v in [x for x in grp if x in recon_v]:
            g = f[f.vehicle_id == v].sort_values(["tx_end_ns", "frame_uid"]).reset_index(drop=True)
            g["cid"] = g.can_id.map(lambda x: int(x, 0))
            for row in d[d.vehicle_id == v].itertuples():
                k = int(row.window_index)
                w = g.iloc[k * 50: k * 50 + 100]
                x = descriptor_features(w.cid.to_numpy(), w.dlc.to_numpy(), list(w.payload), w.tx_end_ns.to_numpy())
                sv = np.array([float(getattr(row, c)) for c in FEATURES])
                err = max(err, float(np.max(np.abs(x - sv) / np.maximum(1.0, np.abs(sv)))))
                need(w.frame_uid.iloc[0] == row.first_frame_uid and w.frame_uid.iloc[-1] == row.last_frame_uid, f"{row.descriptor_id} span")
                n_desc += 1
        del f, lab, st
        session_groups.release_memory()
    need(mism == 0, f"{mism} ECU release times differ from seed re-derivation")
    need(err < 1e-9, f"descriptor reconstruction error {err}")
    camp = {}
    if len(memb):
        for cid, g in memb.groupby("campaign_id"):
            camp[cid] = {"members": len(g), "delays_s": sorted(int(x) / 1e9 for x in g.relative_delay_ns)}
    res = {"session_id": sid, "ok": not issues, "issues": issues[:10], "frames": n, "ecu_streams": streams, "release_mismatches": mism,
           "reconstructed_descriptors": n_desc, "reconstructed_vehicles": recon_v, "recon_max_rel_error": err, "campaigns": camp}
    (OUT / "verify").mkdir(parents=True, exist_ok=True)
    (OUT / "verify" / f"{sid}.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("session_id", "ok", "issues", "frames", "ecu_streams", "release_mismatches", "reconstructed_descriptors", "recon_max_rel_error", "campaigns")}))
    return res


def compare_stream(a: str, b: str) -> dict:
    """Bounded comparison of two (long) sessions: big tables compared batch by batch in file order (frame UIDs
    compared by index), small tables / descriptors / bus_stats / validation compared whole."""
    from compare_sessions import GT_TABLES, LOGICAL_FRAME
    from src import session_groups
    res = {}

    def batches(sid, sub, name, cols):
        return session_groups.iter_column_batches(DATA / sub / sid / name, cols)

    def big(sub, name, cols, uid_col=None):
        n, same = 0, True
        for x, y in zip(batches(a, sub, name, cols), batches(b, sub, name, cols)):
            if uid_col:
                x[uid_col] = x[uid_col].str.replace(a, "", regex=False)
                y[uid_col] = y[uid_col].str.replace(b, "", regex=False)
            same &= x.reset_index(drop=True).equals(y.reset_index(drop=True))
            n += len(x)
        na, nb = (session_groups.num_rows(DATA / sub / s / name) for s in (a, b))
        return f"IDENTICAL ({n} rows)" if same and na == nb == n else f"DIFFERENT (rows {na}/{nb})"
    res["can_frames"] = big("raw", "can_frames", ["frame_uid"] + LOGICAL_FRAME, "frame_uid")
    res["vehicle_states"] = big("raw", "vehicle_states", [c for c in storage.read_columns(DATA / "raw" / a / "vehicle_states") if c != "session_id"])
    res["frame_labels"] = big("ground_truth", "frame_labels", [c for c in storage.read_columns(DATA / "ground_truth" / a / "frame_labels") if c != "session_id"], "frame_uid")
    for t in GT_TABLES:
        if t == "frame_labels":
            continue
        x, y = (storage.read_table(DATA / "ground_truth" / s / t) for s in (a, b))
        for c in [c for c in x.columns if "uid" in c or c == "session_id"]:
            x[c] = x[c].str.replace(a, "", regex=False)
            y[c] = y[c].str.replace(b, "", regex=False)
        res[t] = f"IDENTICAL ({len(x)} rows)" if x.equals(y) else "DIFFERENT"
    for t in ("descriptors", "descriptor_labels"):
        x, y = (storage.read_table(DATA / "analysis" / s / t) for s in (a, b))
        cols = [c for c in x.columns if c not in ("descriptor_id", "session_id", "first_frame_uid", "last_frame_uid")]
        same = x[cols].equals(y[cols])
        if "first_frame_uid" in x:
            same &= x.first_frame_uid.str.replace(a, "", regex=False).equals(y.first_frame_uid.str.replace(b, "", regex=False))
        res[t] = f"IDENTICAL ({len(x)} rows)" if same else "DIFFERENT"
    ba, bb = (json.loads((DATA / "metadata" / s / "bus_stats.json").read_text()) for s in (a, b))
    res["bus_stats"] = "IDENTICAL" if ba == bb else "DIFFERENT"
    va, vb = (json.loads((DATA / "validation" / s / "validation_report.json").read_text()) for s in (a, b))
    res["validation_checks"] = "IDENTICAL" if [(c["id"], c["status"], c["detail"].replace(a, "<sid>")) for c in va["checks"] if c["id"] not in ("V25", "V27")] == \
        [(c["id"], c["status"], c["detail"].replace(b, "<sid>")) for c in vb["checks"] if c["id"] not in ("V25", "V27")] else "DIFFERENT"
    out = {"a": a, "b": b, "tables": res, "identical": all(v.startswith("IDENTICAL") for v in res.values())}
    (OUT / "compare").mkdir(parents=True, exist_ok=True)
    (OUT / "compare" / f"{a}__vs__{b}.stream.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out))
    return out


def write_configs() -> None:
    """Long N=100 engineering tests (not research configurations). Same frozen fleet/CAN/attack settings as M8;
    the 40 s run reuses the M8 seed so its output can be compared with the generator 1.2.0 session M8_S0_N100."""
    d = CFG / "experiments"
    d.mkdir(parents=True, exist_ok=True)
    exps = [dict(eid=f"M81_S0_N100_D{dur:03d}", seed=80100, scen="S0", dur=float(dur), attack="none", size=0, pattern="none")
            for dur in (80, 120, 180)]
    exps.append(dict(eid="M81_CAMP_N100_fuzzy_s10_large", seed=81101, scen="S3", dur=10 + 90 + 5 + 5, attack="fuzzy", size=10,
                     pattern="large", body=camp("C_M81_FUZZ", "fuzzy", [vid(i) for i in range(1, 11)], 10.0, [10.0 * i for i in range(10)])))
    for e in exps:
        campaigns = "config/campaigns.yaml"
        if e.get("body"):
            (d / f"{e['eid']}.campaigns.yaml").write_text("campaigns:\n" + e["body"])
            campaigns = f"config/m81/experiments/{e['eid']}.campaigns.yaml"
        txt = EXPERIMENT.format(eid=e["eid"], dv="m81-memory-test", scen=e["scen"], desc=e["eid"], seed=e["seed"], n=100,
                                attack=e["attack"], size=e["size"], pattern=e["pattern"], dur=e["dur"],
                                attacks="config/attacks.yaml", campaigns=campaigns)
        (d / f"{e['eid']}.yaml").write_text(txt.replace("# Milestone 8 scalability test", "# Milestone 8.1 memory test").replace("milestone: M8,", "milestone: M8.1,"))
    print([e["eid"] for e in exps])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "regress", "compare", "compare-stream", "configs", "verify-long"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--suffix", default="")
    ap.add_argument("--chunk-rows", type=int, default=None)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.cmd == "run":
        for name in a.args:
            p = CFG / "experiments" / f"{name}.yaml"
            run_one(p if p.exists() else ROOT / "config" / "m8" / "experiments" / f"{name}.yaml", name + a.suffix, a.chunk_rows)
    elif a.cmd == "regress":
        spec = {r["session_id"]: r for r in json.loads((ROOT / "config/final/matrix/matrix_spec.json").read_text())["runs"]}
        (CFG / "regression").mkdir(parents=True, exist_ok=True)
        for sid in a.args:
            src = (ROOT / spec[sid]["experiment_config"]).read_text()
            assert "dataset_version: fleetcan-sim-1.1\n" in src
            p = CFG / "regression" / f"{sid}.yaml"
            p.write_text(src.replace("dataset_version: fleetcan-sim-1.1\n", "dataset_version: m81-regression-test\n"))
            run_one(p, "M81_REG_" + sid + a.suffix, a.chunk_rows)
    elif a.cmd == "compare":
        compare_full(a.args[0], a.args[1])
    elif a.cmd == "configs":
        write_configs()
    elif a.cmd == "compare-stream":
        compare_stream(a.args[0], a.args[1])
    elif a.cmd == "verify-long":
        for sid in a.args:
            verify_long(sid)
