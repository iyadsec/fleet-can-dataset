"""Ground truth, stored separately from the observed CAN frames (data/ground_truth/<session>/).

frame_labels.csv     one row per captured frame (join key frame_uid). The label comes only from
                     the frame's provenance (which component created it), never from timing or a detector.
dropped_frames.csv   frames generated but never transmitted (bus model queue overflow).
attacks.csv          one row per configured attack: configuration, seed, and generated /
                     transmitted / dropped / captured counts.
attack_frames.csv    per transmitted attack frame: details (fuzzy mode, malfunction expected vs
                     malicious value, original payload of modified frames).
replay_map.csv       per replayed frame: link to the original frame and both timelines.
campaigns.csv        built only from config/campaigns.yaml. Empty in Milestones 1-2.
"""

from __future__ import annotations

from .recorder import fmt_time

LABEL_COLUMNS = ["frame_uid", "session_id", "frame_label", "attack_type", "campaign_id",
                 "attack_id", "frame_source", "message_name"]
DROP_COLUMNS = ["session_id", "vehicle_id", "channel", "node", "timestamp", "sim_time_ns", "drop_time_ns",
                "reason", "can_id", "dlc", "payload", "frame_label", "attack_type", "attack_id",
                "frame_source", "message_name"]
ATTACK_COLUMNS = ["attack_id", "scenario_id", "session_id", "campaign_id", "attack_type", "vehicle_id",
                  "channel", "configured_start", "configured_end", "start_ns", "end_ns",
                  "first_generated_ns", "last_generated_ns", "requested_rate_hz", "generated",
                  "transmitted", "dropped", "captured", "skipped_inactive", "observed_rate_hz",
                  "attack_seed", "params"]
ATTACK_FRAME_COLUMNS = ["frame_uid", "attack_id", "attack_type", "frame_source", "can_id", "message_name",
                        "fuzzy_mode", "signal", "expected_value", "malicious_value", "original_payload",
                        "payload"]
REPLAY_COLUMNS = ["replay_frame_uid", "attack_id", "original_frame_uid", "source_vehicle", "target_vehicle",
                  "can_id", "original_timestamp", "original_transmission_timestamp", "replay_timestamp",
                  "replay_transmission_timestamp", "original_payload", "replayed_payload"]
CAMPAIGN_COLUMNS = ["campaign_id", "scenario_id", "session_id", "attack_type", "vehicle_id",
                    "campaign_start", "campaign_end", "vehicle_attack_start", "vehicle_attack_end",
                    "relative_delay", "coordinated", "delay_pattern"]

SOURCE_LABEL = {"benign_generator": "benign", "attack_injection": "malicious", "attack_modification": "malicious"}


def _label_fields(f) -> dict:
    if f.source not in SOURCE_LABEL:
        raise ValueError(f"frame source {f.source} has no ground-truth mapping")
    label = SOURCE_LABEL[f.source]
    if (label == "malicious") != bool(f.attack_id):
        raise ValueError(f"inconsistent provenance: source={f.source} attack_id={f.attack_id}")
    return {"frame_label": label, "attack_type": f.attack_type or "None", "campaign_id": "None",
            "attack_id": f.attack_id or "None", "frame_source": f.source, "message_name": f.message}


def frame_label_rows(label_seeds: list[dict], session_id: str) -> list[dict]:
    return [{"frame_uid": r["frame_uid"], "session_id": session_id, **_label_fields(r["frame"])}
            for r in label_seeds]


def dropped_rows(drops, session_id: str) -> list[dict]:
    rows = []
    for d in drops:
        f = d.frame
        rows.append({"session_id": session_id, "vehicle_id": f.vehicle_id, "channel": f.channel, "node": d.node,
                     "timestamp": fmt_time(f.sim_time_ns), "sim_time_ns": f.sim_time_ns, "drop_time_ns": d.drop_ns,
                     "reason": d.reason, "can_id": f"0x{f.can_id:03X}", "dlc": f.dlc, "payload": f.data.hex().upper(),
                     **{k: v for k, v in _label_fields(f).items() if k != "campaign_id"}})
    return rows


def attack_rows(engine, pairs, drops, scenario_id: str, session_id: str) -> list[dict]:
    cap_count, tx_count, drop_count = {}, {}, {}
    gen_times: dict[str, list[int]] = {}
    for rec, _ in pairs:
        a = rec.frame.attack_id
        if a:
            cap_count[a] = cap_count.get(a, 0) + 1
            gen_times.setdefault(a, []).append(rec.frame.sim_time_ns)
    for d in drops:
        a = d.frame.attack_id
        if a:
            drop_count[a] = drop_count.get(a, 0) + 1
            gen_times.setdefault(a, []).append(d.frame.sim_time_ns)
    for a in engine.attacks:
        tx_count[a.attack_id] = engine.transmitted.get(a.attack_id, 0)
    rows = []
    for a in engine.attacks:
        g = gen_times.get(a.attack_id, [])
        dur_s = (a.end_ns - a.start_ns) / 1e9
        rows.append({
            "attack_id": a.attack_id, "scenario_id": scenario_id, "session_id": session_id, "campaign_id": "None",
            "attack_type": a.attack_type, "vehicle_id": a.vehicle_id, "channel": a.channel,
            "configured_start": fmt_time(a.start_ns), "configured_end": fmt_time(a.end_ns),
            "start_ns": a.start_ns, "end_ns": a.end_ns,
            "first_generated_ns": min(g) if g else "", "last_generated_ns": max(g) if g else "",
            "requested_rate_hz": "" if a.requested_rate_hz is None else f"{a.requested_rate_hz:.3f}",
            "generated": a.generated, "transmitted": tx_count[a.attack_id], "dropped": drop_count.get(a.attack_id, 0),
            "captured": cap_count.get(a.attack_id, 0), "skipped_inactive": a.skipped_inactive,
            "observed_rate_hz": f"{cap_count.get(a.attack_id, 0) / dur_s:.3f}",
            "attack_seed": a.seed, "params": a.params_json,
        })
    return rows


def attack_frame_rows(pairs, uid_of) -> tuple[list[dict], list[dict]]:
    detail, replay = [], []
    for rec, cap in pairs:
        f = rec.frame
        if not f.attack_id:
            continue
        ref = f.ref or {}
        uid = uid_of[id(f)]
        detail.append({"frame_uid": uid, "attack_id": f.attack_id, "attack_type": f.attack_type,
                       "frame_source": f.source, "can_id": f"0x{f.can_id:03X}", "message_name": f.message,
                       "fuzzy_mode": ref.get("fuzzy_mode", ""), "signal": ref.get("signal", ""),
                       "expected_value": ref.get("expected_value", ""), "malicious_value": ref.get("malicious_value", ""),
                       "original_payload": ref.get("original_payload", ""), "payload": f.data.hex().upper()})
        if f.attack_type == "replay":
            o = ref["original_frame"]
            replay.append({"replay_frame_uid": uid, "attack_id": f.attack_id,
                           "original_frame_uid": uid_of.get(id(o), ""),
                           "source_vehicle": ref["source_vehicle"], "target_vehicle": ref["target_vehicle"],
                           "can_id": f"0x{f.can_id:03X}", "original_timestamp": fmt_time(ref["original_sim_time_ns"]),
                           "original_transmission_timestamp": fmt_time(ref["original_tx_end_ns"]),
                           "replay_timestamp": fmt_time(f.sim_time_ns),
                           "replay_transmission_timestamp": fmt_time(rec.tx.end_ns),
                           "original_payload": ref["original_payload"], "replayed_payload": f.data.hex().upper()})
    return detail, replay


def campaign_rows(campaigns: list[dict], scenario_id: str, session_id: str) -> list[dict]:
    if campaigns:
        raise NotImplementedError("campaigns are implemented after Milestone 2 approval")
    return []
