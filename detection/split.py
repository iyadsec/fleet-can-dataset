"""Deterministic session-level split (docs/m7_local_if_protocol.md §2).

Unit = generated session (all its descriptors share one split). Per configuration the five repetitions are
permuted with SeedSequence([seed, crc32(config_id)]):
  S0 (benign) configurations  -> 3 train / 1 validation / 1 test
  attack configurations       -> 0 train / 2 validation / 3 test
"""

from __future__ import annotations

import zlib
from collections import defaultdict

import numpy as np

SPLIT_SEED = 42                      # original FLEET-GUARD random_state
SPLITS = ("train", "validation", "test")
BENIGN_ALLOC = ("train", "train", "train", "validation", "test")
ATTACK_ALLOC = ("validation", "validation", "test", "test", "test")


def _permutation(config_id: str, n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(np.random.SeedSequence([seed, zlib.crc32(config_id.encode())]))
    return rng.permutation(n)


def build_split(runs: list[dict], seed: int = SPLIT_SEED) -> dict[str, str]:
    """runs: matrix_spec.json 'runs' records. Returns {session_id: split}."""
    by_cfg: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_cfg[r["config_id"]].append(r)
    out: dict[str, str] = {}
    for cid in sorted(by_cfg):
        reps = sorted(by_cfg[cid], key=lambda r: r["repetition"])
        alloc = BENIGN_ALLOC if reps[0]["attack"] == "none" else ATTACK_ALLOC
        if len(reps) != len(alloc):
            raise ValueError(f"{cid}: expected {len(alloc)} repetitions, got {len(reps)}")
        for slot, idx in enumerate(_permutation(cid, len(reps), seed)):
            out[reps[idx]["session_id"]] = alloc[slot]
    return out


def check_split(split: dict[str, str], runs: list[dict]) -> None:
    """Invariants: every session in exactly one split; training sessions are benign S0 only."""
    sids = [r["session_id"] for r in runs]
    if sorted(split) != sorted(sids):
        raise AssertionError("split does not cover exactly the matrix sessions")
    if set(split.values()) - set(SPLITS):
        raise AssertionError("unknown split name")
    attack = {r["session_id"]: r["attack"] for r in runs}
    if any(split[s] == "train" and attack[s] != "none" for s in split):
        raise AssertionError("attack session assigned to train")
