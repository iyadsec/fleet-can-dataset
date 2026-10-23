"""Detector-safe data adapter (re-export of research.adapter). Import this module for every detector-side input.

    import sys; sys.path.insert(0, "<release>/scripts")
    from detector_adapter import load_observations, descriptors, model_input, graph_inputs
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research.adapter import *  # noqa: F401,F403,E402
from research.adapter import (ALLOWED_OBSERVATION_COLUMNS, PROHIBITED_FIELDS, descriptors, graph_inputs,  # noqa: F401,E402
                              load_observations, model_input, sanitize_observations, temporal_edges)
