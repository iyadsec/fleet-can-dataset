"""Attack engine: Flooding, Fuzzy, Malfunction, Replay.

NOT IMPLEMENTED in Milestone 1 by design: attacks are added only after the benign pipeline
is validated and Milestone 1 is approved. The orchestrator refuses to run if attacks are
configured.
"""


def build_attacks(attack_configs: list[dict]):
    if attack_configs:
        raise NotImplementedError("attack injection is not part of Milestone 1")
    return []
