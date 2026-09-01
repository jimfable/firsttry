#!/usr/bin/env python3
"""Small deterministic patch layer over the authoritative v4 evaluator."""
from __future__ import annotations

import re

import exhaustive as base
import exhaustive_v4 as v4


def compact_host(url: str) -> str:
    value = (base.host(url) or "").lower().strip(".")
    if value.startswith("www."):
        value = value[4:]
    return re.sub(r"[^a-z0-9]", "", value)


# v4's identity helpers resolve compact_host dynamically in the v4 module.
v4.compact_host = compact_host

AuthoritativeAuditor = v4.AuthoritativeAuditor
StrictAuditor = AuthoritativeAuditor
strict_positive_hit = v4.strict_positive_hit_v4
