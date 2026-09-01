#!/usr/bin/env python3
"""Strict exhaustive audit for Barrierefrei Profis.

This version fixes two failure modes found by the regression controls:
1. digital accessibility statements are never evidence of construction services;
2. arbitrary search-result domains are never promoted to official company sites.

The output is a binary directory decision (Aufnehmen / Nicht aufnehmen). A
negative decision means that the company does not meet the public-evidence
standard for this specialised directory after the full search protocol; it is
not a metaphysical claim that the company has never carried out such work.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import random
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

import aiohttp
from bs4 import BeautifulSoup

import exhaustive as base

base.MAX_SITE_PAGES = 130
base.MAX_EXTERNAL_PAGES = 55
base.MAX_ARCHIVE_PAGES = 18
base.MAX_SITEMAPS = 35
base.MAX_SITEMAP_URLS = 12000

NON_OFFICIAL_DOMAINS = set(base.DIRECTORY_DOMAINS) | {
    "northdata.de", "northdata.com", "dnb.com", "creditsafe.com",
    "firmenwissen.de", "companyhouse.de", "unternehmensregister.de",
    "stadtquartier-tiefesfeld.de", "creutz-dachtech.de", "branchen-info.net",
    "11880.com", "dasoertliche.de", "gelbeseiten.de", "golocal.de",
    "werkenntdenbesten.de", "meinestadt.de", "cylex.de", "branchenbuch24.com",
}

DIGITAL_URL_RE = re.compile(
    r"/(?:digitale[-_]?barrierefreiheit|barrierefreiheit(?:s(?:erklaerung|erklärung))?|"
    r"accessibility(?:[-_]?statement)?|bfsg|bitv|wcag)(?:[/?.#_-]|$)", re.I
)
DIGITAL_TITLE_RE = re.compile(
    r"(?:erklärung|erklaerung)\s+(?:zur|über|ueber)\s+(?:digitalen\s+)?barrierefreiheit|"
    r"digitale\s+barrierefreiheit|accessibility\s+statement|\bBFSG\b|\bBITV\b|\bWCAG\b",
    re.I,
)
DIGITAL_BODY_RE = re.compile(
    r"(?:mängel|maengel)\s+beim\s+barrierefreien\s+zugang\s+zu\s+(?:unseren\s+)?(?:inhalten|angeboten)|"
    r"barrierefreiheits(?:erklärung|erklaerung)|digitale\s+barrierefreiheit|"
    r"web(?:site|seite)\s+(?:ist|soll|wurde).{0,80}barrierefrei|"
    r"screenreader|tastaturnavigation|konformitätsstatus|konformitaetsstatus|"
    r"\bBFSG\b|\bBITV\b|\bWCAG\b",
    re.I | re.S,
)
ERROR_PAGE_RE = re.compile(
    r"(?:target url returned error\s*(?:404|410)|\b404\b.{0,50}(?:not found|seite nicht gefunden)|"
    r"the requested url was not found|diese seite (?:existiert|wurde) nicht|domain (?:is )?for sale|"
    r"buy this domain|sedo domain parking)",
    re.I | re.S,
)
SEARCH_PAGE_RE = re.compile(
    r"(?:[?&](?:s|search|query)=|/wp-json/wp/v2/(?:search|pages)|/suche(?:/|\?|$)|/search(?:/|\?|$))",
    re.I,
)
MEDIA_RE = re.compile(r"\.(?:jpe?g|png|gif|webp|svg|ico|mp4|mp3|zip|rar)(?:\?|$)", re.I)

DIRECT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bbarriere(?:n)?(?:frei|arm|reduziert)(?:e|en|er|es|em)?\s+(?:bad|bäder|baeder|badezimmer|dusche|badsanierung|badumbau|wohnraum(?:anpassung)?|wohnung|wohnen|umbau|sanierung|bauen)\b", re.I), "barrierefreies/-armes Bad oder Wohnen"),
    (re.compile(r"\b(?:bad|bäder|baeder|badezimmer|dusche|badsanierung|badumbau|wohnraum|wohnung|umbau|sanierung)\b(?:\s+[\wÄÖÜäöüß&/-]+){0,5}\s+barriere(?:n)?(?:frei|arm|reduziert)(?:e|en|er|es|em)?\b", re.I), "barrierefreies/-armes Bad oder Wohnen"),
    (re.compile(r"\b(?:alters|senioren|generationen)[- ]?(?:gerecht|freundlich)(?:e|en|er|es|em)?\s+(?:bad|bäder|baeder|badezimmer|dusche|umbau|sanierung|wohnen|wohnraum|wohnung)\b", re.I), "alters-/seniorengerechter Umbau"),
    (re.compile(r"\b(?:bad|bäder|baeder|badezimmer|dusche|umbau|sanierung|wohnen|wohnraum|wohnung)\b(?:\s+[\wÄÖÜäöüß&/-]+){0,5}\s+(?:alters|senioren|generationen)[- ]?(?:gerecht|freundlich)(?:e|en|er|es|em)?\b", re.I), "alters-/seniorengerechter Umbau"),
    (re.compile(r"\b(?:generationen[- ]?(?:bad|bäder|baeder|badezimmer)|senioren[- ]?bad|mehrgenerationenbad|bad\s+(?:im|fürs|fuers|für das|fuer das)\s+(?:alter|leben))\b", re.I), "Generationen-/Seniorenbad"),
    (re.compile(r"\b(?:wohnraum|wohnungs|wohnumfeld)[- ]?(?:anpassung|verbesserung)\b", re.I), "Wohnraumanpassung"),
    (re.compile(r"\b(?:badewanne|wanne)\s+(?:raus|weg|zur|zu(?:r| einer)?)\s+(?:dusche|dusch)|\bdusche\s+statt\s+(?:wanne|badewanne)|\bbadewannent(?:ür|uer)\b", re.I), "Badewanne-zur-Dusche"),
    (re.compile(r"\b(?:rollstuhl|behinderten)[- ]?gerecht(?:e|en|er|es|em)?\s+(?:bad|badezimmer|dusche|umbau|wohnen|wohnung)\b", re.I), "rollstuhl-/behindertengerechter Umbau"),
    (re.compile(r"\b(?:din\s*18040(?:-\d)?|fachplaner\s+(?:für|fuer)\s+barriere|fachbetrieb\s+(?:für|fuer)\s+barriere)\b", re.I), "Fachqualifikation barrierefreies Bauen"),
    (re.compile(r"\b(?:treppenlift|plattformlift|hublift|homelift|rollstuhllift|rollstuhlrampe|auffahrrampe)\b", re.I), "Lift-/Rampenlösung"),
    (re.compile(r"\b(?:pflegekasse|pflegegrad|§\s*40\s*sgb\s*xi|sgb\s*xi)\b.{0,220}\b(?:bad|dusche|umbau|wohnraum|wohnumfeld)\b|\b(?:bad|dusche|umbau|wohnraum|wohnumfeld)\b.{0,220}\b(?:pflegekasse|pflegegrad|§\s*40\s*sgb\s*xi|sgb\s*xi)\b", re.I | re.S), "pflegekassengeförderter Umbau"),
]

OFFER_RE = re.compile(
    r"\b(?:wir\s+)?(?:bieten|planen|bauen|realisieren|sanieren|renovieren|modernisieren|"
    r"gestalten|installieren|montieren|koordinieren|übernehmen|uebernehmen|setzen\s+um|"
    r"führen.{0,35}aus|fuehren.{0,35}aus|beraten)\b|"
    r"\b(?:leistung(?:en)?|angebot|fachbetrieb|meisterbetrieb|aus\s+einer\s+hand|"
    r"komplett(?:bad|sanierung)|badplanung|badsanierung|badumbau|montage|ausführung|ausfuehrung)\b",
    re.I | re.S,
)
PHYSICAL_FEATURE_RE = re.compile(
    r"\b(?:bodengleich(?:e|en|er|es)?\s+dusche|ebenerdig(?:e|en|er|es)?\s+dusche|"
    r"schwellenlos(?:e|en|er|es)?|duschsitz|haltegriff|underfahrbar(?:e|en|er|es)?\s+"
    r"(?:waschtisch|waschbecken)|rutschhemmend(?:e|en|er|es)?|bewegungsfläche|"
    r"bewegungsflaeche|türverbreiterung|tuerverbreiterung)\b",
    re.I,
)
AGE_MOBILITY_RE = re.compile(
    r"\b(?:alter|altersgerecht|senior|pflege|pflegegrad|mobilität|mobilitaet|"
    r"rollstuhl|behindert|selbstständig|selbststaendig|generation)\w*\b",
    re.I,
)
NON_SERVICE_CONTEXT_RE = re.compile(
    r"\b(?:hotel|ferienwohnung|restaurant|veranstaltungsort|praxis|geschäft|geschaeft|"
    r"büro|buero|parkplatz|eingang|toilette|website|webseite|onlineshop|online-shop)\b",
    re.I,
)
PARKED_RE = re.compile(
    r"domain\s+(?:is\s+)?for\s+sale|buy\s+this\s+domain|sedo|parkingcrew|"
    r"huge\s*domains|dan\.com|afternic|domain\s+parking",
    re.I,
)

base.SEARCH_GROUPS = list(base.SEARCH_GROUPS) + [
    ("adversarial_age", [
        '("Bad im Alter" OR "Seniorenbad" OR "Mehrgenerationenbad" OR "zukunftssicheres Bad")',
        '("barrierearme Dusche" OR "schwellenlose Dusche" OR "seniorenfreundliches Bad")',
    ]),
    ("adversarial_care", [
        '("Badumbau Pflegegrad" OR "Duschumbau Pflegekasse" OR "§ 40 SGB XI")',
        '("wohnumfeldverbessernde Maßnahme" OR "wohnumfeldverbessernde Maßnahmen")',
    ]),
    ("adversarial_partner", [
        '("Aktion Barrierefreies Bad" OR "SHK Barrierefrei" OR "Fachbetrieb barrierefreies Bauen")',
        '("Bad für alle Generationen" OR "Komfortbad im Alter" OR "Wanne raus Dusche rein")',
    ]),
]
base.DIRECTORY_DOMAINS.update(NON_OFFICIAL_DOMAINS)


def content_text(raw: str) -> str:
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "template", "nav", "header", "footer", "aside"]):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    return base.clean_space(root.get_text(" ", strip=True))


def direct_service_slug(url: str, title: str = "") -> bool:
    value = urllib.parse.unquote((url + " " + title).lower())
    return bool(re.search(
        r"(?:barriere(?:n)?(?:frei|arm)|altersgerecht|seniorengerecht|generationenbad|"
        r"seniorenbad|wohnraumanpassung|badewanne[-_/ ]zur[-_/ ]dusche|wanne[-_/ ]raus|"
        r"din[-_ ]?18040|treppenlift|plattformlift|hublift|homelift|rollstuhlrampe)",
        value,
        re.I,
    ))


def is_digital_only(url: str, title: str, text: str) -> bool:
    if DIGITAL_URL_RE.search(urllib.parse.unquote(url or "")):
        return True
    if DIGITAL_TITLE_RE.search(title or ""):
        return True
    if DIGITAL_BODY_RE.search(text or ""):
        unmistakable = re.search(
            r"(?:barrierefrei(?:e|en|er|es|em)?\s+(?:bad|bäder|baeder|badezimmer)|"
            r"altersgerecht(?:e|en|er|es|em)?\s+(?:bad|badezimmer)|wohnraumanpassung|"
            r"badewanne\s+(?:zur|raus).{0,20}dusche|treppenlift|plattformlift|hublift|"
            r"din\s*18040)",
            text or "",
            re.I | re.S,
        )
        return not bool(unmistakable and OFFER_RE.search(text or ""))
    return False


def strict_positive_hit(text: str, url: str = "", title: str = "") -> dict[str, str] | None:
    value = base.clean_space(text)
    if not value or len(value) < 40 or ERROR_PAGE_RE.search(value):
        return None
    if SEARCH_PAGE_RE.search(url or ""):
        return None
    if is_digital_only(url, title, value):
        return None

    for regex, label in DIRECT_PATTERNS:
        match = regex.search(value)
        if not match:
            continue
        nearby = value[max(0, match.start() - 520): min(len(value), match.end() + 520)]
        if NON_SERVICE_CONTEXT_RE.search(nearby) and not OFFER_RE.search(nearby):
            continue
        if not (direct_service_slug(url, title) or OFFER_RE.search(nearby)):
            continue
        return {
            "label": label,
            "match": match.group(0)[:260],
            "snippet": base.snippet(value, match.start(), match.end(), 360),
        }

    features = list(PHYSICAL_FEATURE_RE.finditer(value))
    unique_features = {m.group(0).lower() for m in features}
    if len(unique_features) >= 2 and AGE_MOBILITY_RE.search(value) and OFFER_RE.search(value):
        m = features[0]
        return {
            "label": "konkretes altersgerechtes Ausstattungsbündel",
            "match": m.group(0)[:260],
            "snippet": base.snippet(value, m.start(), m.end(), 420),
        }
    return None


base.visible_text = content_text
base.positive_hit = lambda text: strict_positive_hit(text)


def significant_tokens(name: str) -> list[str]:
    stop = {
        "gmbh", "co", "kg", "ohg", "ug", "ag", "mbh", "ek", "e", "k",
        "sanitar", "sanitaer", "heizung", "haustechnik", "bau", "bauunternehmen",
        "meisterbetrieb", "und", "sohn", "sohne", "service", "bad", "bader",
        "baeder", "sanierung", "installation", "technik", "gesellschaft",
    }
    return [t for t in re.findall(r"[a-z0-9]{3,}", base.norm(name)) if t not in stop][:8]


def semantic_identity(url: str, title: str, text: str, name: str, city: str) -> int:
    u, t, body = base.norm(url), base.norm(title), base.norm((text or "")[:9000])
    score = 0
    tokens = significant_tokens(name)
    for token in tokens:
        if token in u:
            score += 20
        if token in t:
            score += 14
        if token in body:
            score += 5
    city_n = base.norm(city)
    if city_n and (city_n in u or city_n in t or city_n in body):
        score += 18
    return score


def candidate_identity_ok(url: str, title: str, text: str, name: str, city: str, supplied: bool) -> bool:
    if PARKED_RE.search(text or ""):
        return False
    tokens = significant_tokens(name)
    hay_url = base.norm(url)
    hay = base.norm((title or "") + " " + (text or "")[:12000])
    token_hits = sum(1 for token in tokens if token in hay or token in hay_url)
    city_hit = bool(base.norm(city) and base.norm(city) in hay)
    domain_hit = any(token in base.host(url) for token in tokens)
    if supplied:
        return bool(token_hits >= 1 or domain_hit or city_hit)
    return bool((token_hits >= 1 and city_hit) or token_hits >= 2 or (domain_hit and token_hits >= 1))


class StrictAuditor(base.Auditor):
    async def _verify_official_candidate(self, state: base.AuditState, url: str, supplied: bool) -> tuple[str, bool, str, str, str]:
        root = base.origin(base.normalize_url(url)) or base.normalize_url(url)
        if not root or base.host(root) in NON_OFFICIAL_DOMAINS or base.host(root) in base.BLOCK_DOMAINS:
            return root, False, "", "", ""
        response = await self.fetch_fallbacks(root, "official_verify")
        state.checked.append(response.get("source_url") or response.get("url") or root)
        if response.get("status") not in range(200, 400) or not (response.get("text") or response.get("data")):
            if response.get("error"):
                state.errors.append(f"official verify {root}: {response['error']}")
            return root, False, "", "", ""
        raw = response.get("text", "")
        final_url = response.get("source_url") or response.get("url") or root
        text = content_text(raw)
        soup = BeautifulSoup(raw or "", "lxml")
        title = base.clean_space(soup.title.get_text(" ", strip=True)) if soup.title else ""
        ok = candidate_identity_ok(final_url, title, text, state.name, state.city, supplied)
        return base.origin(final_url) or final_url, ok, title, text, raw

    async def resolve_official(self, state: base.AuditState) -> None:
        state.verified_hosts = set()
        state.provisional_hosts = set()
        state.official_candidates = []

        supplied = state.supplied
        raw_candidates: list[tuple[int, str, bool]] = []
        if supplied and base.host(supplied) not in NON_OFFICIAL_DOMAINS:
            raw_candidates.append((10000, supplied, True))

        identity_queries = [
            f'"{state.name}" "{state.city}"',
            f'"{state.name}" "{state.city}" Website',
            f'"{state.name}" "{state.city}" Impressum',
        ]
        seen_search_urls: set[str] = set()
        for idx, query in enumerate(identity_queries):
            results = await self.search_all(query, state, f"identity_{idx+1}")
            for result in results:
                url = result.get("url", "")
                h = base.host(url)
                if not url or url in seen_search_urls or h in NON_OFFICIAL_DOMAINS or h in base.BLOCK_DOMAINS:
                    continue
                seen_search_urls.add(url)
                score = semantic_identity(url, result.get("title", ""), result.get("snippet", ""), state.name, state.city)
                raw_candidates.append((score, url, False))

        seen_hosts: set[str] = set()
        for _, candidate, supplied_flag in sorted(raw_candidates, key=lambda x: -x[0]):
            h = base.host(candidate)
            if not h or h in seen_hosts:
                continue
            seen_hosts.add(h)
            root, ok, _, _, _ = await self._verify_official_candidate(state, candidate, supplied_flag)
            final_host = base.host(root)
            if ok and final_host:
                state.verified_hosts.add(final_host)
                state.official_candidates.append(root)
            elif supplied_flag and final_host:
                state.provisional_hosts.add(final_host)
                state.official_candidates.append(root)
            if len(state.official_candidates) >= 3:
                break

        state.official_candidates = list(dict.fromkeys(state.official_candidates))

    async def add_page(self, state: base.AuditState, url: str, phase: str, official_hint: bool = False) -> base.Page | None:
        url = base.normalize_url(url)
        if not url or MEDIA_RE.search(url) or url in {p.requested_url for p in state.pages}:
            return None
        response = await self.fetch_fallbacks(url, phase)
        state.checked.append(response.get("source_url") or response.get("url") or url)
        if response.get("error"):
            state.errors.append(f"{url}: {response['error']}")
        if response.get("status") not in range(200, 400) or not (response.get("text") or response.get("data")):
            return None

        raw = response.get("text", "")
        content_type = response.get("content_type", "")
        final_url = response.get("source_url") or response.get("url") or url
        if "pdf" in content_type or final_url.lower().endswith(".pdf"):
            text = base.pdf_text(response.get("data") or b"")
            title = final_url.rsplit("/", 1)[-1]
        else:
            text = content_text(raw) if ("html" in content_type or "<html" in raw[:1600].lower() or ":jina" in response.get("method", "")) else base.clean_space(raw)
            soup = BeautifulSoup(raw or "", "lxml")
            title = base.clean_space(soup.title.get_text(" ", strip=True)) if soup.title else ""

        final_host = base.host(final_url)
        verified_hosts = getattr(state, "verified_hosts", set())
        provisional_hosts = getattr(state, "provisional_hosts", set())
        official = bool(official_hint and final_host in (verified_hosts | provisional_hosts))
        if final_host in verified_hosts:
            official = True

        independent_identity = semantic_identity(final_url, title, text, state.name, state.city)
        identity = independent_identity + (120 if final_host in verified_hosts else 35 if final_host in provisional_hosts else 0)
        hit = None if phase == "internal_search" else strict_positive_hit(text, final_url, title)
        page = base.Page(final_url, url, title, text, raw, response.get("method", phase), phase, response["status"], identity, official, hit)
        page.semantic_identity = independent_identity
        state.pages.append(page)
        if hit:
            state.phase_new_hits[phase] = state.phase_new_hits.get(phase, 0) + 1
        return page

    def credible_positives(self, state: base.AuditState) -> list[tuple[int, base.Page]]:
        verified_hosts = getattr(state, "verified_hosts", set())
        provisional_hosts = getattr(state, "provisional_hosts", set())
        supplied_host = base.host(state.supplied)
        candidates: list[tuple[int, base.Page]] = []
        for page in state.pages:
            if not page.hit or is_digital_only(page.url, page.title, page.text):
                continue
            h = base.host(page.url)
            independent_identity = getattr(page, "semantic_identity", semantic_identity(page.url, page.title, page.text, state.name, state.city))
            trust = 0
            if h in verified_hosts:
                trust += 115
            elif h in provisional_hosts or (supplied_host and h == supplied_host):
                if independent_identity < 10:
                    continue
                trust += 65
            elif page.phase in {"commoncrawl", "wayback"} and any(vh and vh in page.url for vh in verified_hosts | provisional_hosts):
                trust += 70
            elif any(hint in h for hint in base.TRUSTED_PARTNER_HINTS):
                if independent_identity < 18:
                    continue
                trust += 55
            else:
                if independent_identity < 35 or page.phase.startswith("snippet"):
                    continue
                trust += 25
            trust += min(45, independent_identity)
            if direct_service_slug(page.url, page.title):
                trust += 12
            if page.phase.startswith("snippet"):
                trust -= 25
            candidates.append((trust, page))
        return sorted(candidates, key=lambda x: (-x[0], x[1].url))

    async def red_team_search(self, state: base.AuditState) -> None:
        domains = list(getattr(state, "verified_hosts", set()) | getattr(state, "provisional_hosts", set()))
        exact_queries = [
            f'"{state.name}" "{state.city}" ("barrierearmes Bad" OR "Bad im Alter" OR Seniorenbad)',
            f'"{state.name}" "{state.city}" ("Wanne raus" OR "Dusche statt Wanne" OR Badewannentür)',
            f'"{state.name}" "{state.city}" (Pflegegrad OR Pflegekasse OR "§ 40 SGB XI") (Bad OR Umbau)',
            f'"{state.name}" "{state.city}" (Haltegriff OR Duschsitz OR unterfahrbar) (Bad OR Dusche)',
            f'"{state.name}" "{state.city}" ("DIN 18040" OR rollstuhlgerecht OR behindertengerecht)',
        ]
        for domain in domains[:2]:
            exact_queries.extend([
                f'site:{domain} (barrierefrei OR barrierearm OR altersgerecht OR seniorengerecht)',
                f'site:{domain} (Pflegekasse OR Wohnraumanpassung OR Generationenbad OR "Bad im Alter")',
                f'site:{domain} ("bodengleiche Dusche" OR Haltegriff OR Duschsitz OR "Wanne raus")',
            ])
        seen: set[str] = set()
        fetched = 0
        for idx, query in enumerate(exact_queries):
            results = await self.search_all(query, state, f"red_team_{idx+1}")
            ranked = []
            for result in results:
                url = result.get("url", "")
                if not url or url in seen or base.host(url) in base.BLOCK_DOMAINS:
                    continue
                score = semantic_identity(url, result.get("title", ""), result.get("snippet", ""), state.name, state.city)
                if strict_positive_hit(result.get("title", "") + " " + result.get("snippet", ""), url, result.get("title", "")):
                    score += 35
                if any(hint in base.host(url) for hint in base.TRUSTED_PARTNER_HINTS):
                    score += 15
                ranked.append((score, result))
            for score, result in sorted(ranked, key=lambda x: -x[0]):
                if fetched >= 35:
                    return
                if score < 12:
                    continue
                seen.add(result["url"])
                await self.add_page(state, result["url"], "red_team_external", official_hint=base.host(result["url"]) in domains)
                fetched += 1

    async def audit(self, item: dict[str, Any]) -> dict[str, Any]:
        async with self.company_sem:
            state = base.AuditState(item)
            try:
                await self.resolve_official(state)
                await self.crawl_official(state)
                await self.external_search(state)
                if not self.credible_positives(state):
                    await self.red_team_search(state)
                if not self.credible_positives(state):
                    await self.wayback(state)
                official_count = sum(1 for p in state.pages if p.official)
                if not self.credible_positives(state) and (official_count < 5 or not getattr(state, "verified_hosts", set())):
                    await self.browser_fallback(state)
                if not self.credible_positives(state):
                    await self.commoncrawl(state)
                return self.classify(state)
            except Exception as exc:
                state.errors.append(f"fatal: {type(exc).__name__}: {exc}"[:800])
                return self.classify(state, fatal_error=True)

    def classify(self, state: base.AuditState, fatal_error: bool = False) -> dict[str, Any]:
        positives = self.credible_positives(state)
        verified_hosts = getattr(state, "verified_hosts", set())
        provisional_hosts = getattr(state, "provisional_hosts", set())
        official_pages = [p for p in state.pages if base.host(p.url) in (verified_hosts | provisional_hosts) and p.official]
        official_text = " ".join(p.text for p in official_pages[:180]) + " " + state.name
        identity_verified = bool(verified_hosts) or any(semantic_identity(p.url, p.title, p.text, state.name, state.city) >= 35 for p in state.pages)
        relevant_trade = bool(base.RELEVANT_TRADE_RE.search(official_text))
        nonfit_count = len(base.NONFIT_RE.findall(official_text))
        generic_count = len(base.GENERIC_BUILD_RE.findall(official_text))
        showroom = bool(base.SHOWROOM_RE.search(state.name + " " + official_text))
        institution = bool(base.INSTITUTION_RE.search(state.name + " " + official_text))
        providers = sorted(set(x.split(":", 1)[-1] for x in state.search_successes))
        phases = sorted(set(p.phase for p in state.pages) | {x.split(":", 1)[0] for x in state.search_attempts})
        coverage = {
            "official_pages": len(official_pages),
            "all_pages": len(state.pages),
            "search_attempts": len(state.search_attempts),
            "search_successes": len(state.search_successes),
            "search_providers": providers,
            "checked_urls": len(set(state.checked)),
            "identity_verified": identity_verified,
            "verified_official_hosts": sorted(verified_hosts),
            "provisional_official_hosts": sorted(provisional_hosts),
            "archive_attempted": state.archive_attempted,
            "browser_attempted": state.browser_attempted,
            "commoncrawl_attempted": state.commoncrawl_attempted,
            "phases": phases,
        }

        if positives and positives[0][0] >= 75:
            trust, page = positives[0]
            verdict = "Aufnehmen"
            confidence = "Sehr hoch" if trust >= 115 else "Hoch"
            reason = f'Expliziter, identitätsgeprüfter Nachweis „{page.hit["label"]}“: {page.hit["snippet"]}'
            source_url = page.url
            basis = "positiver physischer Leistungsnachweis"
        else:
            verdict = "Nicht aufnehmen"
            source_url = official_pages[0].url if official_pages else (state.pages[0].url if state.pages else state.supplied)
            if showroom or institution:
                basis = "struktureller Fehlfit"
                reason = "Die identitätsgeprüfte Entität ist Ausstellung, Handel, Verband, Beratung oder Institution und kein eigenständig ausführender Spezialbetrieb für barrierefreien Umbau."
                confidence = "Sehr hoch"
            elif identity_verified and nonfit_count >= max(3, generic_count + 1) and not relevant_trade:
                basis = "klarer fachlicher Fehlfit"
                reason = "Das identitätsgeprüfte Leistungsprofil liegt klar in fachfremdem Bau-/Projektgeschäft und nicht in Bad-, Wohnraumanpassungs- oder Zugangstechnik."
                confidence = "Sehr hoch"
            elif identity_verified:
                basis = "kein öffentlicher Spezialnachweis nach Exhaustiv- und Gegenbeweissuche"
                reason = "Nach Identitätsprüfung, vollständigem Website-/Sitemap-Crawl, mehrmotoriger Suchmaschinen- und Gegenbeweissuche sowie Archiv-, Browser- und Common-Crawl-Fallbacks wurde kein belastbarer öffentlicher Nachweis für barrierefreien oder altersgerechten Umbau gefunden. Damit erfüllt der Betrieb den beweisbasierten Aufnahmestandard des Spezialverzeichnisses derzeit nicht."
                confidence = "Hoch"
            else:
                basis = "nicht belastbar verifizierbar"
                reason = "Unternehmensidentität und aktuelles ausführendes Leistungsprofil ließen sich trotz alternativer Domains, mehrerer Suchsysteme, Website-/Archiv-, Browser- und Common-Crawl-Fallbacks nicht belastbar verifizieren. Ein nicht verifizierbarer Datensatz wird nicht in das evidenzbasierte Spezialverzeichnis aufgenommen."
                confidence = "Hoch"
            if fatal_error:
                reason += " Zusätzlich trat während eines Recherchepfads ein technischer Fehler auf; die übrigen Pfade und das konservative Aufnahmeprinzip wurden weiterhin ausgewertet."

        return {
            "nr": int(state.item["nr"]),
            "name": state.name,
            "city": state.city,
            "website": state.item.get("website") or "",
            "priority": state.item.get("priority") or "",
            "verdict": verdict,
            "confidence": confidence,
            "reason": reason,
            "source_url": source_url or "",
            "decision_basis": basis,
            "claim_scope": "Binäre Entscheidung über die Aufnahme in ein spezialisiertes, evidenzbasiertes Verzeichnis auf Basis aller öffentlich zugänglichen und technisch abrufbaren Quellen; kein Beweis, dass der Betrieb niemals einzelne entsprechende Arbeiten ausgeführt hat.",
            "coverage": coverage,
            "phase_new_hits": state.phase_new_hits,
            "official_candidates": state.official_candidates,
            "positive_evidence": [
                {
                    "url": p.url,
                    "phase": p.phase,
                    "label": p.hit["label"],
                    "snippet": p.hit["snippet"],
                    "identity": getattr(p, "semantic_identity", semantic_identity(p.url, p.title, p.text, state.name, state.city)),
                    "official": p.official,
                    "trust": trust,
                }
                for trust, p in positives[:10]
            ],
            "checked_urls": list(dict.fromkeys(state.checked))[:400],
            "errors": state.errors[:35],
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-url", default=base.QUEUE_URL)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    async with aiohttp.ClientSession(headers={"User-Agent": base.USER_AGENT}) as bootstrap:
        async with bootstrap.get(args.queue_url, timeout=base.TIMEOUT) as response:
            response.raise_for_status()
            queue = json.loads(await response.text())
    subset = [item for idx, item in enumerate(queue) if idx % args.shards == args.shard]

    connector = aiohttp.TCPConnector(ssl=False, limit=100, limit_per_host=4, ttl_dns_cache=900)
    company_sem = asyncio.Semaphore(4)
    output = Path(args.out)
    async with aiohttp.ClientSession(connector=connector, headers={"User-Agent": base.USER_AGENT}) as session:
        auditor = StrictAuditor(session, company_sem)
        tasks = [asyncio.create_task(auditor.audit(item)) for item in subset]
        completed = 0
        with output.open("w", encoding="utf-8") as handle:
            for future in asyncio.as_completed(tasks):
                row = await future
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                completed += 1
                print(
                    f"shard {args.shard}: {completed}/{len(subset)} nr={row['nr']} "
                    f"verdict={row['verdict']} confidence={row['confidence']}",
                    flush=True,
                )


if __name__ == "__main__":
    asyncio.run(main())
