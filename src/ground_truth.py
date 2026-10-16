"""Ground truth, stored separately from the observed CAN frames (data/ground_truth/<session>/).

frame_labels.csv     one row per captured frame (join key frame_uid). The label comes only from
                     the frame's provenance (which component created it), never from timing or a detector.
dropped_frames.csv   frames generated but never transmitted: drop_kind queue_drop (queue full) or
                     expired_drop (deadline policy), with label, attack_id and campaign_id.
attacks.csv          one row per configured attack: configuration, seed, and generated /
                     transmitted / dropped / captured counts.
attack_frames.csv    per transmitted attack frame: details (fuzzy mode, malfunction expected vs
                     malicious value, original payload of modified frames).
replay_map.csv       per replayed frame: link to the original frame and both timelines.
campaigns.csv        one row per campaign (from configuration via src/campaign_scheduler.py).
campaign_membership.csv  one row per (campaign, vehicle): attack_id, logical start/end, relative delay.
campaign_overlap.csv one row per vehicle pair inside a campaign: start delay and interval overlap.
All campaign times are on the logical SUMO/generation timeline; transmission and capture times
are never used for campaign ground truth.
"""

from __future__ import annotations

from .campaign_scheduler import pair_overlaps
from .recorder import fmt_time


def fmt_rel(ns: int) -> str:
    """Signed duration in seconds with ns precision."""
    return ("-" if ns < 0 else "") + fmt_time(abs(ns))

LABEL_COLUMNS = ["frame_uid", "session_id", "frame_label", "attack_type", "campaign_id",
                 "attack_id", "frame_source", "message_name"]
DROP_COLUMNS = ["session_id", "vehicle_id", "channel", "node", "timestamp", "sim_time_ns", "drop_time_ns",
                "drop_kind", "reason", "can_id", "dlc", "payload", "frame_label", "attack_type", "attack_id",
                "campaign_id", "frame_source", "message_name"]
ATTACK_COLUMNS = ["attack_id", "scenario_id", "session_id", "campaign_id", "attack_type", "vehicle_id",
                  "channel", "configured_start", "configured_end", "start_ns", "end_ns",
                  "first_generated_ns", "last_generated_ns", "requested_rate_hz", "generated",
                  "transmitted", "dropped", "queue_dropped", "expired_dropped", "captured", "skipped_inactive",
                  "observed_rate_hz",
                  "attack_seed", "params"]
ATTACK_FRAME_COLUMNS = ["frame_uid", "attack_id", "attack_type", "frame_source", "can_id", "message_name",
                        "fuzzy_mode", "signal", "expected_value", "malicious_value", "original_payload",
                        "payload"]
REPLAY_COLUMNS = ["replay_frame_uid", "attack_id", "original_frame_uid", "source_vehicle", "target_vehicle",
                  "can_id", "original_timestamp", "original_transmission_timestamp", "replay_timestamp",
                  "replay_transmission_timestamp", "original_payload", "replayed_payload"]
CAMPAIGN_COLUMNS = ["campaign_id", "scenario_id", "session_id", "attack_type", "coordinated", "delay_pattern",
                    "vehicle_count", "target_vehicles", "campaign_start", "campaign_end", "campaign_start_ns",
                    "campaign_end_ns", "campaign_duration_s", "min_inter_vehicle_delay_s",
                    "max_inter_vehicle_delay_s", "common_overlap_s", "campaign_seed", "params"]
MEMBERSHIP_COLUMNS = ["campaign_id", "scenario_id", "session_id", "vehicle_id", "attack_id", "attack_type",
                      "coordinated", "vehicle_attack_start", "vehicle_attack_end", "vehicle_attack_start_ns",
                      "vehicle_attack_end_ns", "relative_delay_s", "relative_delay_ns", "attack_duration_s"]
OVERLAP_COLUMNS = ["campaign_id", "session_id", "vehicle_a", "vehicle_b", "start_delay_s", "start_delay_ns",
                   "overlap_s", "overlap_ratio_min", "jaccard"]

SOURCE_LABEL = {"benign_generator": "benign", "attack_injection": "malicious", "attack_modification": "malicious"}


def _label_fields(f) -> dict:
    if f.source not in SOURCE_LABEL:
        raise ValueError(f"frame source {f.source} has no ground-truth mapping")
    label = SOURCE_LABEL[f.source]
    if (label == "malicious") != bool(f.attack_id):
        raise ValueError(f"inconsistent provenance: source={f.source} attack_id={f.attack_id}")
    if label == "benign" and f.campaign_id:
        raise ValueError("benign frame carries a campaign_id")
    return {"frame_label": label, "attack_type": f.attack_type or "None", "campaign_id": f.campaign_id or "None",
            "attack_id": f.attack_id or "None", "frame_source": f.source, "message_name": f.message}


def label_row(frame_uid: str, frame, session_id: str) -> dict:
    return {"frame_uid": frame_uid, "session_id": session_id, **_label_fields(frame)}


def frame_label_rows(label_seeds: list[dict], session_id: str) -> list[dict]:
    return [label_row(r["frame_uid"], r["frame"], session_id) for r in label_seeds]


def dropped_rows(drops, session_id: str) -> list[dict]:
    rows = []
    for d in drops:
        f = d.frame
        rows.append({"session_id": session_id, "vehicle_id": f.vehicle_id, "channel": f.channel, "node": d.node,
                     "timestamp": fmt_time(f.sim_time_ns), "sim_time_ns": f.sim_time_ns, "drop_time_ns": d.drop_ns,
                     "drop_kind": d.kind, "reason": d.reason, "can_id": f"0x{f.can_id:03X}", "dlc": f.dlc,
                     "payload": f.data.hex().upper(), **_label_fields(f)})
    return rows


class AttackTally:
    """Per-attack counters from captured (paired) frames, accumulated one frame at a time (generator 1.3.0)."""

    def __init__(self):
        self.captured: dict[str, int] = {}
        self.gen_min: dict[str, int] = {}
        self.gen_max: dict[str, int] = {}

    def add(self, frame) -> None:
        a = frame.attack_id
        if a:
            self.captured[a] = self.captured.get(a, 0) + 1
            t = frame.sim_time_ns
            self.gen_min[a] = min(self.gen_min.get(a, t), t)
            self.gen_max[a] = max(self.gen_max.get(a, t), t)


def attack_rows(engine, pairs, drops, scenario_id: str, session_id: str) -> list[dict]:
    tally = AttackTally()
    for rec, _ in pairs:
        tally.add(rec.frame)
    return attack_rows_from_tally(engine, tally, drops, scenario_id, session_id)


def attack_rows_from_tally(engine, tally: AttackTally, drops, scenario_id: str, session_id: str) -> list[dict]:
    drop_count, qdrop, xdrop = {}, {}, {}
    gmin, gmax = dict(tally.gen_min), dict(tally.gen_max)
    for d in drops:
        a = d.frame.attack_id
        if a:
            drop_count[a] = drop_count.get(a, 0) + 1
            kind = qdrop if d.kind == "queue_drop" else xdrop
            kind[a] = kind.get(a, 0) + 1
            t = d.frame.sim_time_ns
            gmin[a] = min(gmin.get(a, t), t)
            gmax[a] = max(gmax.get(a, t), t)
    rows = []
    for a in engine.attacks:
        dur_s = (a.end_ns - a.start_ns) / 1e9
        cap = tally.captured.get(a.attack_id, 0)
        rows.append({
            "attack_id": a.attack_id, "scenario_id": scenario_id, "session_id": session_id,
            "campaign_id": a.campaign_id or "None",
            "attack_type": a.attack_type, "vehicle_id": a.vehicle_id, "channel": a.channel,
            "configured_start": fmt_time(a.start_ns), "configured_end": fmt_time(a.end_ns),
            "start_ns": a.start_ns, "end_ns": a.end_ns,
            "first_generated_ns": gmin.get(a.attack_id, ""), "last_generated_ns": gmax.get(a.attack_id, ""),
            "requested_rate_hz": "" if a.requested_rate_hz is None else f"{a.requested_rate_hz:.3f}",
            "generated": a.generated, "transmitted": engine.transmitted.get(a.attack_id, 0),
            "dropped": drop_count.get(a.attack_id, 0),
            "queue_dropped": qdrop.get(a.attack_id, 0), "expired_dropped": xdrop.get(a.attack_id, 0),
            "captured": cap, "skipped_inactive": a.skipped_inactive,
            "observed_rate_hz": f"{cap / dur_s:.3f}",
            "attack_seed": a.seed, "params": a.params_json,
        })
    return rows


def attack_frame_row(frame_uid: str, f) -> dict:
    ref = f.ref or {}
    return {"frame_uid": frame_uid, "attack_id": f.attack_id, "attack_type": f.attack_type,
            "frame_source": f.source, "can_id": f"0x{f.can_id:03X}", "message_name": f.message,
            "fuzzy_mode": ref.get("fuzzy_mode", ""), "signal": ref.get("signal", ""),
            "expected_value": ref.get("expected_value", ""), "malicious_value": ref.get("malicious_value", ""),
            "original_payload": ref.get("original_payload", ""), "payload": f.data.hex().upper()}


def replay_row(frame_uid: str, f, tx_end_ns: int, original_uid: str) -> dict:
    ref = f.ref
    return {"replay_frame_uid": frame_uid, "attack_id": f.attack_id, "original_frame_uid": original_uid,
            "source_vehicle": ref["source_vehicle"], "target_vehicle": ref["target_vehicle"],
            "can_id": f"0x{f.can_id:03X}", "original_timestamp": fmt_time(ref["original_sim_time_ns"]),
            "original_transmission_timestamp": fmt_time(ref["original_tx_end_ns"]),
            "replay_timestamp": fmt_time(f.sim_time_ns), "replay_transmission_timestamp": fmt_time(tx_end_ns),
            "original_payload": ref["original_payload"], "replayed_payload": f.data.hex().upper()}


def attack_frame_rows(pairs, uid_of) -> tuple[list[dict], list[dict]]:
    detail, replay = [], []
    for rec, cap in pairs:
        f = rec.frame
        if not f.attack_id:
            continue
        uid = uid_of[id(f)]
        detail.append(attack_frame_row(uid, f))
        if f.attack_type == "replay":
            replay.append(replay_row(uid, f, rec.tx.end_ns, uid_of.get(id(f.ref["original_frame"]), "")))
    return detail, replay


def campaign_rows(campaigns, scenario_id: str, session_id: str):
    """Return (campaign rows, membership rows, overlap rows) from scheduled Campaign objects."""
    import json
    rows, members, overlaps = [], [], []
    for c in campaigns:
        d = c.inter_vehicle_delays_ns()
        rows.append({
            "campaign_id": c.campaign_id, "scenario_id": scenario_id, "session_id": session_id,
            "attack_type": c.attack_type, "coordinated": str(c.coordinated).lower(), "delay_pattern": c.delay_pattern,
            "vehicle_count": len(c.members), "target_vehicles": ";".join(m.vehicle_id for m in c.members),
            "campaign_start": fmt_time(c.start_ns), "campaign_end": fmt_time(c.end_ns),
            "campaign_start_ns": c.start_ns, "campaign_end_ns": c.end_ns,
            "campaign_duration_s": fmt_rel(c.end_ns - c.start_ns),
            "min_inter_vehicle_delay_s": fmt_rel(min(d)) if d else "", "max_inter_vehicle_delay_s": fmt_rel(max(d)) if d else "",
            "common_overlap_s": fmt_rel(c.common_overlap_ns()), "campaign_seed": c.seed,
            "params": json.dumps(c.params, sort_keys=True, default=str)})
        for m in c.members:
            members.append({
                "campaign_id": c.campaign_id, "scenario_id": scenario_id, "session_id": session_id,
                "vehicle_id": m.vehicle_id, "attack_id": m.attack_id, "attack_type": c.attack_type,
                "coordinated": str(c.coordinated).lower(),
                "vehicle_attack_start": fmt_time(m.start_ns), "vehicle_attack_end": fmt_time(m.end_ns),
                "vehicle_attack_start_ns": m.start_ns, "vehicle_attack_end_ns": m.end_ns,
                "relative_delay_s": fmt_rel(m.relative_delay_ns), "relative_delay_ns": m.relative_delay_ns,
                "attack_duration_s": fmt_rel(m.end_ns - m.start_ns)})
        for o in pair_overlaps(c):
            overlaps.append({"campaign_id": c.campaign_id, "session_id": session_id, "vehicle_a": o["vehicle_a"],
                             "vehicle_b": o["vehicle_b"], "start_delay_s": fmt_rel(o["start_delay_ns"]),
                             "start_delay_ns": o["start_delay_ns"], "overlap_s": fmt_rel(o["overlap_ns"]),
                             "overlap_ratio_min": f"{o['overlap_ratio_min']:.6f}", "jaccard": f"{o['jaccard']:.6f}"})
    return rows, members, overlaps
