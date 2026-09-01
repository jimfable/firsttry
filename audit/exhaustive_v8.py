#!/usr/bin/env python3
"""Authoritative v8 evaluator with identity lock and exhaustive physical-service detection."""
from __future__ import annotations

import asyncio
import re

import exhaustive as base
import exhaustive_v3 as v3
import exhaustive_v4 as v4
import exhaustive_v5 as v5

# Keep enough room for the official navigation, sitemap and the high-value
# direct probes below. The prior 130-page cap could be consumed before a
# relevant but unusually named page was reached.
base.MAX_SITE_PAGES = max(base.MAX_SITE_PAGES, 175)

PRIORITY_PATHS = [
    "/ratgeber/badwissen/",
    "/ratgeber/badwissen/bad-sanieren/",
    "/ratgeber/badwissen/barrierefreie-baeder/",
    "/ratgeber/barrierefreies-bad/",
    "/blog/barrierefreies-bad/",
    "/barrierefreies-bad/",
    "/barrierefreie-baeder/",
    "/barrierefreies-badezimmer/",
    "/barrierearmes-bad/",
    "/barrierearme-dusche/",
    "/altersgerechtes-bad/",
    "/seniorengerechtes-bad/",
    "/generationenbad/",
    "/badewanne-zur-dusche/",
    "/wohnraumanpassung/",
    "/leistungen/bad/",
    "/leistungen/bad/barrierefreies-bad/",
    "/leistungen/bad/foerderung-bad/",
    "/leistungen/barrierefreies-bad/",
    "/leistungen/barrierefreie-baeder/",
    "/leistungen/altersgerechtes-bad/",
    "/leistungen/seniorengerechtes-bad/",
    "/leistungen/badewanne-zur-dusche/",
    "/leistungen/wohnraumanpassung/",
    "/unsere-leistungen/",
    "/unsere-leistungen/barrierefreies-bad/",
    "/unsere-leistungen/barrierefreie-baeder/",
    "/unsere-leistungen/altersgerechtes-bad/",
    "/unsere-leistungen/seniorengerechtes-bad/",
    "/unsere-leistungen/badewanne-zur-dusche/",
    "/unsere-leistungen/foerderantraege/",
    "/service/barrierefreies-bad/",
]
for path in PRIORITY_PATHS:
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
    r"(?:bad|bäder|baeder|badezimmer|dusche|badewanne|wanne|umbau|sanier|renov|modernis|"
    r"wohnraumanpass|wohnumfeld|haltegriff|duschsitz|waschtisch|waschbecken|türverbreiter|"
    r"tuerverbreiter|treppenlift|plattformlift|hublift|homelift|rampe|montage|einbau)",
    re.I,
)
EXTENDED_BARRIER_RE = re.compile(
    r"\b(?:"
    r"barriere(?:n)?(?:frei|arm|ärmer|aermer|reduziert|reduzierend)(?:e|en|er|es|em)?"
    r"|barrieren(?:abbau|reduzierung)?"
    r")\b",
    re.I,
)
BAD_CONTEXT_RE = re.compile(
    r"\b(?:bad|bäder|baeder|badezimmer|dusche|badewanne|wanne|sanitär|sanitaer|"
    r"umbau|sanierung|modernisierung|wohnraum|wohnumfeld)\w*\b",
    re.I,
)
EXECUTION_RE = re.compile(
    r"\b(?:"
    r"wir\s+(?:bieten|planen|bauen|realisieren|sanieren|renovieren|modernisieren|"
    r"gestalten|installieren|montieren|koordinieren|übernehmen|uebernehmen|unterstützen|unterstuetzen)"
    r"|(?:unterstütz|unterstuetz)\w*.{0,90}(?:umsetz|ausführ|ausfuehr|sanier|umbau)"
    r"|setz\w*.{0,50}\bum\b|führ\w*.{0,50}\baus\b|fuehr\w*.{0,50}\baus\b"
    r"|übernehm\w*|uebernehm\w*|realisier\w*|installier\w*|montier\w*"
    r"|fachgerecht\w*.{0,70}(?:umsetz|ausführ|ausfuehr)"
    r"|(?:planung|beratung|ausführung|ausfuehrung|montage|umbau|sanierung)\s+aus\s+einer\s+hand"
    r")\b",
    re.I | re.S,
)
FEATURE_PATTERNS = [
    re.compile(r"\b(?:boden|niveau)gleich\w*\s+dusche|\bebenerdig\w*\s+dusche", re.I),
    re.compile(r"\bhaltegriff\w*|\bstützklappgriff\w*|\bstuetzklappgriff\w*", re.I),
    re.compile(r"\bduschsitz\w*|\bduschklappsitz\w*", re.I),
    re.compile(r"\bunterfahrbar\w*\s+(?:waschtisch|waschbecken)", re.I),
    re.compile(r"\bbewegungsfl(?:ä|ae)che\w*", re.I),
    re.compile(r"\brutsch(?:fest|hemmend|sicher)\w*", re.I),
    re.compile(r"\bschwellenlos\w*|\bstufenlos\w*", re.I),
]
CARE_AGE_RE = re.compile(
    r"\b(?:alter|altersgerecht|senior|pflegegrad|pflegekasse|mobilität|mobilitaet|"
    r"rollstuhl|behindert|generation|wohnumfeldverbesser)\w*\b",
    re.I,
)

ORIGINAL_V4_HIT = v4.strict_positive_hit_v4


def _extended_physical_hit(text: str, url: str = "", title: str = ""):
    value = base.clean_space(text)
    if not value or len(value) < 40 or v4.ERROR_OR_PARKED_RE.search(value):
        return None
    decoded = (url + " " + title).lower()
    if v4.SEARCH_PAGE_RE.search(url or ""):
        return None

    # A genuine digital-accessibility page remains excluded. A footer link to
    # such a statement must not veto a page that contains a physical service.
    digital_url_or_title = bool(v3.DIGITAL_URL_RE.search(decoded) or v3.DIGITAL_TITLE_RE.search(title or ""))
    if digital_url_or_title and not (BAD_CONTEXT_RE.search(value) and EXECUTION_RE.search(value)):
        return None

    barriers = list(EXTENDED_BARRIER_RE.finditer(value))
    for match in barriers:
        nearby = value[max(0, match.start() - 750): min(len(value), match.end() + 750)]
        if not BAD_CONTEXT_RE.search(nearby):
            continue
        if VISITOR_ACCESS_RE.search(nearby) and not PHYSICAL_SERVICE_RE.search(nearby):
            continue
        if v4.PROJECT_ONLY_RE.search(nearby) and not v4.RETROFIT_RE.search(nearby):
            continue
        service_path = bool(v3.direct_service_slug(url, title) or re.search(r"/(?:leistungen|unsere-leistungen|service)/", url or "", re.I))
        if not (service_path or v3.OFFER_RE.search(nearby) or v4.STRONG_OFFER_RE.search(nearby) or EXECUTION_RE.search(nearby)):
            continue
        return {
            "label": "barrierefreier/-armer bzw. barrierereduzierender Bad- oder Wohnumbau",
            "match": match.group(0)[:260],
            "snippet": base.snippet(value, match.start(), match.end(), 430),
        }

    feature_hits = [(idx, regex.search(value)) for idx, regex in enumerate(FEATURE_PATTERNS)]
    feature_hits = [(idx, match) for idx, match in feature_hits if match]
    if len(feature_hits) >= 2 and CARE_AGE_RE.search(value) and (
        v3.OFFER_RE.search(value) or v4.STRONG_OFFER_RE.search(value) or EXECUTION_RE.search(value)
    ):
        match = feature_hits[0][1]
        nearby = value[max(0, match.start() - 900): min(len(value), match.end() + 900)]
        if not (v4.PROJECT_ONLY_RE.search(nearby) and not v4.RETROFIT_RE.search(nearby)):
            return {
                "label": "konkretes alters-/pflegegerechtes Ausstattungs- und Ausführungsbündel",
                "match": match.group(0)[:260],
                "snippet": base.snippet(value, match.start(), match.end(), 460),
            }
    return None


def strict_positive_hit_v8(text: str, url: str = "", title: str = ""):
    # The broad physical detector runs first so that a digital-accessibility
    # footer cannot make a genuine bathroom-service page disappear.
    hit = _extended_physical_hit(text, url, title) or ORIGINAL_V4_HIT(text, url, title)
    if not hit:
        return None
    snippet = hit.get("snippet", "")
    if VISITOR_ACCESS_RE.search(snippet) and not PHYSICAL_SERVICE_RE.search(snippet):
        return None
    return hit


# Patch the symbol actually resolved by StrictAuditor.add_page as well as all
# inherited browser/archive/Common-Crawl paths. The missing v3 assignment was
# the wiring bug that made earlier v8 detector changes ineffective.
v3.strict_positive_hit = strict_positive_hit_v8
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

    async def crawl_official(self, state: base.AuditState) -> None:
        # Fetch the highest-value pages before a large sitemap can consume the
        # site-page budget. add_page is deduplicating, so super() can safely run
        # its complete navigation/sitemap crawl afterwards.
        for candidate in state.official_candidates[:3]:
            site_root = base.origin(candidate) or candidate
            urls = [base.normalize_url(site_root + path) for path in PRIORITY_PATHS]
            for start in range(0, len(urls), 8):
                await asyncio.gather(*(
                    self.add_page(state, url, "priority_probe", official_hint=True)
                    for url in urls[start:start + 8]
                ))
        await super().crawl_official(state)

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
