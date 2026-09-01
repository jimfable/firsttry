#!/usr/bin/env python3
"""Authoritative v8 evaluator: supplied-domain lock and visitor-access rejection."""
from __future__ import annotations

import re

import exhaustive as base
import exhaustive_v4 as v4
import exhaustive_v5 as v5

EXTRA_COMMON_PATHS = [
    "/unsere-leistungen/",
    "/unsere-leistungen/barrierefreies-bad/",
    "/unsere-leistungen/barrierefreie-baeder/",
    "/unsere-leistungen/altersgerechtes-bad/",
    "/unsere-leistungen/seniorengerechtes-bad/",
    "/unsere-leistungen/badewanne-zur-dusche/",
    "/unsere-leistungen/foerderantraege/",
    "/unsere-leistungen/förderantraege/",
    "/service/barrierefreies-bad/",
    "/ratgeber/badwissen/barrierefreie-baeder/",
    "/ratgeber/barrierefreies-bad/",
    "/blog/barrierefreies-bad/",
    "/leistungen/badewanne-zur-dusche/",
    "/leistungen/wohnraumanpassung/",
]
for path in EXTRA_COMMON_PATHS:
    if path not in base.COMMON_PATHS:
        base.COMMON_PATHS.append(path)

VISITOR_ACCESS_RE = re.compile(
    r"(?:rollstuhlgerecht(?:er|e|en)?|barrierefrei(?:er|e|en)?)\s+"
    r"(?:parkplatz|zugang|eingang|erreichbar|zugänglich|zugaenglich)|"
    r"(?:parkplatz|zugang|eingang|geschäftsräume|geschaeftsraeume|standort|filiale|ausstellung)"
    r".{0,120}(?:rollstuhlgerecht|barrierefrei)",
    re.I | re.S,
)
PHYSICAL_SERVICE_RE = re.compile(
    r"(?:bad|badezimmer|dusche|badewanne|wanne|umbau|sanier|renov|modernis|"
    r"wohnraumanpass|wohnumfeld|haltegriff|duschsitz|waschtisch|türverbreiter|"
    r"tuerverbreiter|treppenlift|plattformlift|hublift|homelift|rampe|montage|einbau)",
    re.I,
)

ORIGINAL_V4_HIT = v4.strict_positive_hit_v4


def strict_positive_hit_v8(text: str, url: str = "", title: str = ""):
    hit = ORIGINAL_V4_HIT(text, url, title)
    if not hit:
        return None
    snippet = hit.get("snippet", "")
    if VISITOR_ACCESS_RE.search(snippet) and not PHYSICAL_SERVICE_RE.search(snippet):
        return None
    return hit


# Patch inherited site, browser, archive and Common Crawl evidence evaluation.
v4.strict_positive_hit_v4 = strict_positive_hit_v8
v5.strict_positive_hit = strict_positive_hit_v8
base.positive_hit = lambda text: strict_positive_hit_v8(text)


class AuthoritativeAuditor(v5.AuthoritativeAuditor):
    async def resolve_official(self, state: base.AuditState) -> None:
        await super().resolve_official(state)
        supplied_host = base.host(state.supplied)
        accepted = supplied_host and supplied_host in (
            getattr(state, "verified_hosts", set()) | getattr(state, "provisional_hosts", set())
        )
        if accepted:
            state.verified_hosts = {h for h in getattr(state, "verified_hosts", set()) if h == supplied_host}
            state.provisional_hosts = {h for h in getattr(state, "provisional_hosts", set()) if h == supplied_host}
            state.official_candidates = [u for u in state.official_candidates if base.host(u) == supplied_host]
            if not state.official_candidates:
                state.official_candidates = [base.origin(state.supplied) or state.supplied]

    def credible_positives(self, state: base.AuditState):
        output = []
        supplied_host = base.host(state.supplied)
        for trust, page in super().credible_positives(state):
            snippet = page.hit.get("snippet", "")
            if VISITOR_ACCESS_RE.search(snippet) and not PHYSICAL_SERVICE_RE.search(snippet):
                continue
            page_host = base.host(page.url)
            if supplied_host and page_host != supplied_host:
                if not any(hint in page_host for hint in base.TRUSTED_PARTNER_HINTS):
                    continue
            output.append((trust, page))
        return output


StrictAuditor = AuthoritativeAuditor
strict_positive_hit = strict_positive_hit_v8
