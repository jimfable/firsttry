#!/usr/bin/env python3
"""Authoritative strict evaluator for the 1,041 Barrierefrei-Profis candidates.

Version 4 adds two hard safeguards discovered by the adversarial controls:
* a supplied company domain can no longer be displaced by a merely similar
  company name from search results;
* old project/reference pages and bare accessible-new-build claims are not
  evidence of a current accessible-remodelling service.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

import exhaustive as base
import exhaustive_v3 as v3

CURRENT_YEAR = datetime.now(timezone.utc).year
MIN_ARCHIVE_EVIDENCE_YEAR = CURRENT_YEAR - 2

NON_OFFICIAL_DOMAINS = set(v3.NON_OFFICIAL_DOMAINS) | {
    "barrierefrei-profis.de", "www.barrierefrei-profis.de",
    "aroundhome.de", "www.aroundhome.de",
    "creditreform.de", "www.creditreform.de", "firmeneintrag.creditreform.de",
    "cylex.de", "www.cylex.de", "web2.cylex.de",
    "firmania.de", "www.firmania.de",
    "handelsregister.ai", "www.handelsregister.ai",
    "sanierungsverzeichnis.de", "www.sanierungsverzeichnis.de",
    "branchen-info.net", "www.branchen-info.net",
    "stadtquartier-tiefesfeld.de", "www.stadtquartier-tiefesfeld.de",
    "creutz-dachtech.de", "www.creutz-dachtech.de",
    "maierusa.com", "www.maierusa.com", "maieramerica.com", "www.maieramerica.com",
}
base.DIRECTORY_DOMAINS.update(NON_OFFICIAL_DOMAINS)
v3.NON_OFFICIAL_DOMAINS.update(NON_OFFICIAL_DOMAINS)

# The directory is for providers of accessible conversion/adaptation, not merely
# developers of one accessible new-build project.
DIRECT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bbarriere(?:n)?(?:frei|arm|reduziert)(?:e|en|er|es|em)?\s+(?:bad|bäder|baeder|badezimmer|dusche|badsanierung|badumbau|umbau|sanierung|wohnraum(?:anpassung)?|zugang)\b", re.I), "barrierefreies/-armes Bad oder Umbau"),
    (re.compile(r"\b(?:bad|bäder|baeder|badezimmer|dusche|badsanierung|badumbau|umbau|sanierung|wohnraum(?:anpassung)?)\b(?:\s+[\wÄÖÜäöüß&/-]+){0,6}\s+barriere(?:n)?(?:frei|arm|reduziert)(?:e|en|er|es|em)?\b", re.I), "barrierefreies/-armes Bad oder Umbau"),
    (re.compile(r"\b(?:alters|senioren|generationen)[- ]?(?:gerecht|freundlich)(?:e|en|er|es|em)?\s+(?:bad|bäder|baeder|badezimmer|dusche|umbau|sanierung|wohnraum(?:anpassung)?)\b", re.I), "alters-/seniorengerechter Umbau"),
    (re.compile(r"\b(?:bad|bäder|baeder|badezimmer|dusche|umbau|sanierung|wohnraum(?:anpassung)?)\b(?:\s+[\wÄÖÜäöüß&/-]+){0,6}\s+(?:alters|senioren|generationen)[- ]?(?:gerecht|freundlich)(?:e|en|er|es|em)?\b", re.I), "alters-/seniorengerechter Umbau"),
    (re.compile(r"\b(?:generationen[- ]?(?:bad|bäder|baeder|badezimmer)|senioren[- ]?bad|mehrgenerationenbad|bad\s+(?:im|fürs|fuers|für das|fuer das)\s+(?:alter|leben))\b", re.I), "Generationen-/Seniorenbad"),
    (re.compile(r"\b(?:wohnraum|wohnungs|wohnumfeld)[- ]?(?:anpassung|verbesserung)\b", re.I), "Wohnraumanpassung"),
    (re.compile(r"\b(?:badewanne|wanne)\s+(?:raus|weg|zur|zu(?:r| einer)?)\s+(?:dusche|dusch)|\bdusche\s+statt\s+(?:wanne|badewanne)|\bbadewannent(?:ür|uer)\b", re.I), "Badewanne-zur-Dusche"),
    (re.compile(r"\b(?:rollstuhl|behinderten)[- ]?gerecht(?:e|en|er|es|em)?\s+(?:bad|badezimmer|dusche|umbau|sanierung|wohnraum)\b", re.I), "rollstuhl-/behindertengerechter Umbau"),
    (re.compile(r"\b(?:din\s*18040(?:-\d)?|fachplaner\s+(?:für|fuer)\s+barriere|fachbetrieb\s+(?:für|fuer)\s+barriere)\b", re.I), "Fachqualifikation barrierefreies Bauen"),
    (re.compile(r"\b(?:treppenlift|plattformlift|hublift|homelift|rollstuhllift|rollstuhlrampe|auffahrrampe)\b", re.I), "Lift-/Rampenlösung"),
    (re.compile(r"\b(?:pflegekasse|pflegegrad|§\s*40\s*sgb\s*xi|sgb\s*xi)\b.{0,240}\b(?:bad|dusche|umbau|sanierung|wohnraum|wohnumfeld)\b|\b(?:bad|dusche|umbau|sanierung|wohnraum|wohnumfeld)\b.{0,240}\b(?:pflegekasse|pflegegrad|§\s*40\s*sgb\s*xi|sgb\s*xi)\b", re.I | re.S), "pflegekassengeförderter Umbau"),
]

PROJECT_ONLY_RE = re.compile(
    r"\b(?:baugebiet|bungalow(?:siedlung)?|reihenhaus|doppelhaus|einfamilienhaus|"
    r"mehrfamilienhaus|wohnanlage|neubauprojekt|projektentwicklung|musterhaus|"
    r"schlüsselfertig|schluesselfertig|referenzprojekt|bauvorhaben|objektbau)\b",
    re.I,
)
RETROFIT_RE = re.compile(
    r"\b(?:umbau|sanierung|renovierung|modernisierung|nachrüstung|nachruestung|"
    r"wohnraumanpassung|bestands(?:bau|gebäude|gebaeude)|badewanne|wanne\s+raus|"
    r"dusche\s+statt|treppenlift|plattformlift|hublift|rampe)\b",
    re.I,
)
STRONG_OFFER_RE = re.compile(
    r"\b(?:wir\s+(?:bieten|planen|bauen|realisieren|sanieren|renovieren|modernisieren|"
    r"installieren|montieren|koordinieren|übernehmen|uebernehmen)|"
    r"(?:planung|beratung|ausführung|ausfuehrung|montage|umbau|sanierung)\s+aus\s+einer\s+hand|"
    r"jetzt\s+(?:beraten|angebot|anfragen|kontakt)|für\s+sie\s+(?:planen|bauen|realisieren))\b",
    re.I | re.S,
)
ERROR_OR_PARKED_RE = re.compile(
    r"(?:target url returned error\s*(?:404|410)|\b404\b.{0,60}(?:not found|seite nicht gefunden)|"
    r"the requested url was not found|domain (?:is )?for sale|buy this domain|sedo|"
    r"parkingcrew|afternic|huge\s*domains)",
    re.I | re.S,
)
SEARCH_PAGE_RE = re.compile(r"(?:[?&](?:s|search|query)=|/wp-json/wp/v2/(?:search|pages)|/suche(?:/|\?|$)|/search(?:/|\?|$))", re.I)


def strict_positive_hit_v4(text: str, url: str = "", title: str = "") -> dict[str, str] | None:
    value = base.clean_space(text)
    if not value or len(value) < 40 or ERROR_OR_PARKED_RE.search(value):
        return None
    if SEARCH_PAGE_RE.search(url or "") or v3.is_digital_only(url, title, value):
        return None

    for regex, label in DIRECT_PATTERNS:
        match = regex.search(value)
        if not match:
            continue
        nearby = value[max(0, match.start() - 650): min(len(value), match.end() + 650)]
        if v3.NON_SERVICE_CONTEXT_RE.search(nearby) and not v3.OFFER_RE.search(nearby):
            continue
        if PROJECT_ONLY_RE.search(nearby) and not RETROFIT_RE.search(nearby):
            # A single accessible housing/project reference is not evidence of a
            # current conversion service.
            continue
        if not (v3.direct_service_slug(url, title) or v3.OFFER_RE.search(nearby) or STRONG_OFFER_RE.search(nearby)):
            continue
        return {
            "label": label,
            "match": match.group(0)[:260],
            "snippet": base.snippet(value, match.start(), match.end(), 380),
        }

    features = list(v3.PHYSICAL_FEATURE_RE.finditer(value))
    unique_features = {m.group(0).lower() for m in features}
    if len(unique_features) >= 2 and v3.AGE_MOBILITY_RE.search(value) and (v3.OFFER_RE.search(value) or STRONG_OFFER_RE.search(value)):
        m = features[0]
        nearby = value[max(0, m.start() - 650): min(len(value), m.end() + 650)]
        if not (PROJECT_ONLY_RE.search(nearby) and not RETROFIT_RE.search(nearby)):
            return {
                "label": "konkretes altersgerechtes Ausstattungsbündel",
                "match": m.group(0)[:260],
                "snippet": base.snippet(value, m.start(), m.end(), 440),
            }
    return None


# Patch every inherited path, including browser and Common Crawl processing.
v3.strict_positive_hit = strict_positive_hit_v4
base.positive_hit = lambda text: strict_positive_hit_v4(text)


def compact_host(url: str) -> str:
    return re.sub(r"[^a-z0-9]", "", base.registrableish_host(base.host(url)))


def distinctive_tokens(name: str) -> list[str]:
    generic = {
        "bauprojekt", "bauprojekte", "bau", "betrieb", "betriebs", "gmbh", "gruppe",
        "sanitaer", "sanitar", "heizung", "haustechnik", "meisterbetrieb", "service",
        "bad", "badumbau", "badsanierung", "technik", "planung", "und", "co", "kg",
    }
    return [t for t in v3.significant_tokens(name) if len(t) >= 4 and t not in generic]


def domain_token_hits(url: str, name: str) -> int:
    h = compact_host(url)
    return sum(1 for token in distinctive_tokens(name) if token in h)


def search_candidate_identity_ok(url: str, title: str, snippet: str, name: str, city: str) -> bool:
    h = base.host(url)
    if not h or h in NON_OFFICIAL_DOMAINS or h in base.BLOCK_DOMAINS:
        return False
    tokens = distinctive_tokens(name)
    host_hits = domain_token_hits(url, name)
    hay = base.norm((title or "") + " " + (snippet or ""))
    body_hits = sum(1 for token in tokens if token in hay)
    city_hit = bool(base.norm(city) and base.norm(city) in hay)
    # A generic surname alone is insufficient: maierusa.com and similar false
    # identities must not displace the supplied business domain.
    return bool(host_hits >= 2 or (host_hits >= 1 and body_hits >= 1 and city_hit) or (body_hits >= 2 and city_hit))


def wayback_capture_year(url: str) -> int | None:
    match = re.search(r"/web/(\d{4})\d{10}(?:id_)?/", url or "")
    return int(match.group(1)) if match else None


class AuthoritativeAuditor(v3.StrictAuditor):
    async def resolve_official(self, state: base.AuditState) -> None:
        state.verified_hosts = set()
        state.provisional_hosts = set()
        state.official_candidates = []

        supplied = state.supplied
        supplied_alive = False
        if supplied and base.host(supplied) not in NON_OFFICIAL_DOMAINS:
            root, ok, _title, text, _raw = await self._verify_official_candidate(state, supplied, True)
            root = base.origin(root) or base.origin(supplied) or supplied
            supplied_alive = bool(text and not ERROR_OR_PARKED_RE.search(text))
            supplied_host = base.host(root)
            if supplied_host:
                state.official_candidates.append(root)
                # A supplied domain with a distinctive company token is trusted
                # as official even when a JS shell prevents body-name matching.
                if ok or domain_token_hits(root, state.name) >= 1:
                    state.verified_hosts.add(supplied_host)
                else:
                    state.provisional_hosts.add(supplied_host)

        # Search for an alternative official site only when no usable supplied
        # site exists, or as a tightly gated secondary identity candidate.
        queries = [
            f'"{state.name}" "{state.city}"',
            f'"{state.name}" "{state.city}" Website',
            f'"{state.name}" "{state.city}" Impressum',
        ]
        raw_candidates: list[tuple[int, str]] = []
        seen: set[str] = set()
        for idx, query in enumerate(queries):
            for result in await self.search_all(query, state, f"identity_{idx+1}"):
                url = result.get("url", "")
                if not url or url in seen:
                    continue
                seen.add(url)
                if not search_candidate_identity_ok(url, result.get("title", ""), result.get("snippet", ""), state.name, state.city):
                    continue
                score = v3.semantic_identity(url, result.get("title", ""), result.get("snippet", ""), state.name, state.city)
                score += 30 * domain_token_hits(url, state.name)
                raw_candidates.append((score, url))

        max_candidates = 1 if supplied_alive else 2
        added = 0
        for _score, candidate in sorted(raw_candidates, key=lambda x: (-x[0], x[1])):
            if added >= max_candidates:
                break
            if any(base.same_site(candidate, existing) for existing in state.official_candidates):
                continue
            root, ok, _title, text, _raw = await self._verify_official_candidate(state, candidate, False)
            if not ok or not text or ERROR_OR_PARKED_RE.search(text):
                continue
            final_host = base.host(root)
            if not final_host or final_host in NON_OFFICIAL_DOMAINS:
                continue
            state.verified_hosts.add(final_host)
            state.official_candidates.append(base.origin(root) or root)
            added += 1

        state.official_candidates = list(dict.fromkeys(state.official_candidates))[:3]

    async def crawl_official(self, state: base.AuditState) -> None:
        # Same exhaustive crawl as the base implementation, but always anchor
        # sitemaps/common paths to the candidate domain, not to a reader proxy.
        for candidate in state.official_candidates[:3]:
            if len(state.pages) >= base.MAX_SITE_PAGES:
                break
            home = await self.add_page(state, candidate, "official_home", official_hint=True)
            if not home:
                continue
            site_root = base.origin(candidate) or base.origin(home.url) or candidate
            sitemap = await self.sitemap_urls(state, site_root)
            candidate_map: dict[str, int] = {}
            for score, url, _anchor in base.extract_links(home.raw, site_root):
                candidate_map[url] = max(score, candidate_map.get(url, -999))
            # Jina reader returns Markdown; recover its internal links too.
            for _label, link in re.findall(r"\[([^\]]{1,180})\]\((https?://[^)\s]+)\)", home.raw or ""):
                link = base.normalize_url(html.unescape(link))
                if base.same_site(site_root, link):
                    candidate_map[link] = max(base.link_score(link), candidate_map.get(link, -999))
            for url in sitemap:
                candidate_map[url] = max(base.link_score(url), candidate_map.get(url, -999))
            for path in base.COMMON_PATHS:
                url = base.normalize_url(site_root + path)
                candidate_map[url] = max(base.link_score(url), candidate_map.get(url, -999))

            if sitemap and len(sitemap) <= 240:
                selected = list(dict.fromkeys(sitemap + list(candidate_map)))
            else:
                shallow = [u for u in candidate_map if len([p for p in urllib.parse.urlsplit(u).path.split("/") if p]) <= 3]
                ranked = [u for u, _ in sorted(candidate_map.items(), key=lambda kv: (-kv[1], len(kv[0])))]
                selected = list(dict.fromkeys(ranked[:96] + shallow[:36]))
            remaining = max(0, base.MAX_SITE_PAGES - len(state.pages))
            selected = [u for u in selected if u != home.requested_url][:remaining]
            for start in range(0, len(selected), 10):
                await asyncio.gather(*(self.add_page(state, u, "official_site", official_hint=True) for u in selected[start:start + 10]))

            terms = [
                "barrierefrei", "barrierearm", "altersgerecht", "seniorengerecht",
                "Generationenbad", "Wohnraumanpassung", "Pflegekasse", "Pflegegrad",
                "bodengleiche Dusche", "Wanne raus", "Bad im Alter",
            ]
            internal_urls: list[str] = []
            for term in terms:
                q = urllib.parse.quote(term)
                internal_urls.extend([
                    site_root + f"/wp-json/wp/v2/search?search={q}&per_page=100",
                    site_root + f"/wp-json/wp/v2/pages?search={q}&per_page=100",
                    site_root + f"/?s={q}",
                ])
            for internal_url in internal_urls:
                if len(state.pages) >= base.MAX_SITE_PAGES:
                    break
                page = await self.add_page(state, internal_url, "internal_search", official_hint=True)
                if page and page.raw.lstrip().startswith(("[", "{")):
                    try:
                        payload = json.loads(page.raw)
                        if isinstance(payload, list):
                            for obj in payload[:30]:
                                if isinstance(obj, dict):
                                    link = obj.get("url") or obj.get("link")
                                    if link:
                                        await self.add_page(state, link, "wp_result", official_hint=True)
                    except Exception:
                        pass

    def credible_positives(self, state: base.AuditState) -> list[tuple[int, base.Page]]:
        candidates = super().credible_positives(state)
        filtered: list[tuple[int, base.Page]] = []
        for trust, page in candidates:
            if page.phase == "wayback":
                year = wayback_capture_year(page.url)
                if not year or year < MIN_ARCHIVE_EVIDENCE_YEAR:
                    continue
            # Archive/project reference copy must still describe conversion or
            # an ongoing offered service, not only a completed accessible build.
            if page.phase in {"wayback", "commoncrawl"} and PROJECT_ONLY_RE.search(page.text):
                if not RETROFIT_RE.search(page.text) and not STRONG_OFFER_RE.search(page.text):
                    continue
            filtered.append((trust, page))
        return filtered


# Public alias for runners/workflows.
StrictAuditor = AuthoritativeAuditor
