"""Campaign scheduler (simultaneous, staggered, random-delay, partial-overlap, independent).

NOT IMPLEMENTED in Milestone 1 by design. Campaign membership will come only from
config/campaigns.yaml and is never inferred from timing.
"""


def build_schedule(campaign_configs: list[dict], seed: int):
    if campaign_configs:
        raise NotImplementedError("campaign scheduling is not part of Milestone 1")
    return []
