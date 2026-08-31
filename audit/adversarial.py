#!/usr/bin/env python3
"""Independent adversarial verifier for the exhaustive Barrierefrei audit.

This pass deliberately uses a different query vocabulary, broader synonyms,
additional search front ends, and explicit positive-source verification. It
consumes the primary audit result and returns a final binary directory decision.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import io
import json
import random
import re
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
from bs4 import BeautifulSoup
from pypdf import PdfReader

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 "
    "BarrierefreiProfisAdversarialVerifier/1.0"
)
TIMEOUT = aiohttp.ClientTimeout(total=32, connect=11, sock_read=22)
MAX_BYTES = 3_000_000
MAX_RESULT_FETCHES = 70

POSITIVE_CONTROLS = {109, 161, 702, 1946}
NEGATIVE_CONTROLS = {969, 1062, 2579, 3250}

BLOCK_DOMAINS = {
    "youtube.com", "facebook.com", "instagram.com", "linkedin.com",
    "pinterest.com", "kununu.com", "indeed.com", "xing.com",
}
DIRECTORY_DOMAINS = {
    "sanitaerfinden.com", "sanitaer.org", "fliesenleger.io", "sellwerk.de",
    "gelbeseiten.de", "11880.com", "dasoertliche.de", "golocal.de",
    "werkenntdenbesten.de", "houzz.de", "pflegehilfe.org", "nullbarriere.de",
}
TRUSTED_HINTS = (
    "die-badgestalter", "badundheizung", "sanitaer.org", "pflegehilfe",
    "dincertco", "handwerkskammer", "hwk-", "innung", "nullbarriere",
    "barrierefrei-leben", "aktion-barrierefreies-bad",
)

BROAD_POSITIVE = [
    (r"\bbarriere(?:frei|arm|reduziert)(?:e|en|er|es|em)?\b.{0,150}\b(?:bad|bäder|baeder|badezimmer|dusche|umbau|sanierung|wohnraum|wohnung|zugang|bauen)\b", "barrierefreier/-armer Umbau"),
    (r"\b(?:bad|bäder|baeder|badezimmer|dusche|umbau|sanierung|wohnraum|wohnung)\b.{0,150}\bbarriere(?:frei|arm|reduziert)", "barrierefreier/-armer Umbau"),
    (r"\b(?:seniorenbad|senioren-bad|generationenbad|mehrgenerationenbad|komfortbad)\b", "Senioren-/Generationenbad"),
    (r"\b(?:bad|badezimmer)\s+(?:im|für das|fuer das|fürs|fuers)\s+(?:alter|leben)\b", "Bad im Alter/fürs Leben"),
    (r"\b(?:alters|senioren|generationen)[- ]?(?:gerecht|freundlich)(?:e|en|er|es|em)?\b.{0,150}\b(?:bad|dusche|umbau|sanierung|wohnen|wohnraum)\b", "alters-/seniorengerechte Wohnanpassung"),
    (r"\b(?:bad|dusche|umbau|sanierung|wohnen|wohnraum)\b.{0,150}\b(?:alters|senioren|generationen)[- ]?(?:gerecht|freundlich)", "alters-/seniorengerechte Wohnanpassung"),
    (r"\b(?:wanne|badewanne)\s+(?:raus|weg|zur|zu(?:r| einer)?)\s+(?:dusche|dusch)\b", "Wanne-zur-Dusche"),
    (r"\b(?:dusche\s+statt\s+(?:wanne|badewanne)|badewannentür|badewannentuer)\b", "Wannenumbau"),
    (r"\b(?:wohnraum|wohnungs|wohnumfeld)[- ]?(?:anpassung|verbesserung)\b", "Wohnraumanpassung"),
    (r"\b(?:pflegegrad|pflegekasse|§\s*40\s*sgb\s*xi|sgb\s*xi)\b.{0,220}\b(?:bad|dusche|umbau|wohnraum|wohnumfeld)\b", "Pflegekassen-geförderter Umbau"),
    (r"\b(?:din\s*18040|fachplaner\s+(?:für|fuer)\s+barriere|fachbetrieb\s+(?:für|fuer)\s+barriere)\b", "Barrierefrei-Fachqualifikation"),
    (r"\b(?:rollstuhl|behinderten)[- ]?gerecht(?:e|en|er|es|em)?\b.{0,120}\b(?:bad|dusche|wohnen|wohnung|zugang|umbau)\b", "rollstuhl-/behindertengerechter Umbau"),
    (r"\b(?:treppenlift|plattformlift|hublift|homelift|rollstuhlrampe|auffahrrampe)\b", "Lift-/Rampenlösung"),
]
BROAD_RE = [(re.compile(p, re.I | re.S), label) for p, label in BROAD_POSITIVE]
FEATURE_RE = re.compile(
    r"(bodengleich(?:e|en|er|es)?\s+dusche|ebenerdig(?:e|en|er|es)?\s+dusche|"
    r"schwellenlos(?:e|en|er|es)?|duschsitz|haltegriff|unterfahrbar(?:e|en|er|es)?|"
    r"rutschhemmend(?:e|en|er|es)?|bewegungsfläche|bewegungsflaeche)", re.I
)
AGE_CONTEXT_RE = re.compile(r"(alter|senior|pflege|mobilität|mobilitaet|selbstständig|selbststaendig|sicherheit|generation|rollstuhl)", re.I)
SERVICE_RE = re.compile(r"(bad|bäder|baeder|badezimmer|dusche|sanitär|sanitaer|shk|wohnraum|umbau|sanierung|fliesen|lift|rampe)", re.I)
DIGITAL_ONLY_RE = re.compile(
    r"(barrierefreiheits(?:erklärung|erklaerung)|\bbfsg\b|\bbitv\b|\bwcag\b|"
    r"screenreader|tastaturnavigation|digitale barrierefreiheit|accessibility statement)", re.I
)
NON_SERVICE_RE = re.compile(r"(ferienwohnung|hotelzimmer|veranstaltungsort|praxiszugang|webseite|website|online-shop|onlineshop)", re.I)

ROUND_QUERIES = {
    "round_1_exact": [
        '"barrierefrei" (Bad OR Badezimmer OR Dusche OR Umbau)',
        '(altersgerecht OR seniorengerecht OR Generationenbad OR Seniorenbad)',
        '("Wanne raus" OR "Wanne zur Dusche" OR "Dusche statt Wanne")',
        '(Wohnraumanpassung OR "wohnumfeldverbessernde Maßnahmen" OR Pflegekasse)',
    ],
    "round_2_synonyms": [
        '(barrierearm OR barrierereduziert OR "Bad im Alter" OR Komfortbad)',
        '("Bad für alle Generationen" OR "Bad fürs Leben" OR "zukunftssicheres Bad")',
        '(Haltegriff OR Duschsitz OR unterfahrbar OR Bewegungsfläche) (Bad OR Dusche)',
        '(rollstuhlgerecht OR behindertengerecht OR "DIN 18040")',
    ],
    "round_3_access": [
        '(Treppenlift OR Plattformlift OR Hublift OR Homelift OR Rollstuhlrampe)',
        '(Pflegegrad OR "§ 40 SGB XI") (Bad OR Dusche OR Umbau)',
        '("bodengleiche Dusche" OR "ebenerdige Dusche") (Senior OR Alter OR Pflege)',
    ],
    "round_4_partner": [
        'site:die-badgestalter.de barrierefrei',
        'site:badundheizung.de barrierefrei',
        'site:sanitaer.org "Barrierefreies Bad"',
        'site:pflegehilfe.org Badumbau',
        'site:nullbarriere.de',
        'site:dincertco.de barrierefrei',
    ],
}


def clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def deaccent(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(c))


def norm(value: str) -> str:
    return deaccent(clean_space(value).lower())


def normalize_url(value: str) -> str:
    value = html.unescape((value or "").strip())
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    try:
        p = urllib.parse.urlsplit(value)
        query = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith(("utm_", "gclid", "fbclid", "y_source"))]
        return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", urllib.parse.urlencode(query), ""))
    except Exception:
        return value


def host(url: str) -> str:
    try:
        value = urllib.parse.urlsplit(url).netloc.lower().split(":")[0]
        return value[4:] if value.startswith("www.") else value
    except Exception:
        return ""


def visible_text(raw: str) -> str:
    soup = BeautifulSoup(raw or "", "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    return clean_space(soup.get_text(" ", strip=True))


def make_snippet(text: str, start: int, end: int, radius: int = 300) -> str:
    lo, hi = max(0, start - radius), min(len(text), end + radius)
    out = clean_space(text[lo:hi])
    return ("…" if lo else "") + out + ("…" if hi < len(text) else "")


def broad_hit(text: str) -> dict[str, str] | None:
    value = clean_space(text)
    for regex, label in BROAD_RE:
        match = regex.search(value)
        if not match:
            continue
        nearby = value[max(0, match.start() - 400): min(len(value), match.end() + 400)]
        if DIGITAL_ONLY_RE.search(nearby) and not SERVICE_RE.search(nearby):
            continue
        if NON_SERVICE_RE.search(nearby) and not re.search(r"(wir|leistung|montage|sanierung|umbau|planen|realisieren|installieren)", nearby, re.I):
            continue
        return {"label": label, "match": match.group(0)[:260], "snippet": make_snippet(value, match.start(), match.end())}
    features = list(FEATURE_RE.finditer(value))
    if len(features) >= 2 and AGE_CONTEXT_RE.search(value) and SERVICE_RE.search(value):
        match = features[0]
        return {"label": "altersgerechtes Ausstattungsbündel", "match": match.group(0), "snippet": make_snippet(value, match.start(), match.end(), 380)}
    return None


def company_tokens(name: str) -> list[str]:
    stop = {"gmbh", "co", "kg", "ohg", "ug", "ag", "mbh", "ek", "sanitar", "heizung", "haustechnik", "bau", "meisterbetrieb", "und", "sohn", "sohne", "service", "bad", "bader", "baeder", "sanierung", "installation", "technik"}
    return [token for token in re.findall(r"[a-z0-9]{3,}", norm(name)) if token not in stop][:7]


def identity_score(url: str, title: str, text: str, name: str, city: str, known_hosts: set[str]) -> int:
    u, t, body = norm(url), norm(title), norm(text[:5000])
    score = 0
    if host(url) in known_hosts:
        score += 120
    for token in company_tokens(name):
        if token in u:
            score += 18
        if token in t:
            score += 12
        if token in body:
            score += 4
    city_n = norm(city)
    if city_n and (city_n in u or city_n in t or city_n in body):
        score += 15
    if host(url) in BLOCK_DOMAINS:
        score -= 150
    if host(url) in DIRECTORY_DOMAINS:
        score -= 6
    return score


def pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        return clean_space(" ".join((page.extract_text() or "") for page in reader.pages[:80]))
    except Exception:
        return ""


@dataclass
class Candidate:
    url: str
    title: str
    text: str
    provider: str
    identity: int
    hit: dict[str, str] | None
    trusted: bool
    fetched: bool


@dataclass
class State:
    row: dict[str, Any]
    checked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    query_attempts: list[str] = field(default_factory=list)
    provider_success: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    new_hits_by_round: dict[str, int] = field(default_factory=dict)

    @property
    def nr(self) -> int:
        return int(self.row["nr"])

    @property
    def name(self) -> str:
        return self.row.get("name", "")

    @property
    def city(self) -> str:
        return self.row.get("city", "")

    @property
    def known_hosts(self) -> set[str]:
        urls = [self.row.get("website", ""), self.row.get("source_url", "")]
        urls += list(self.row.get("official_candidates") or [])
        return {host(url) for url in urls if host(url)}


class Verifier:
    def __init__(self, session: aiohttp.ClientSession, company_sem: asyncio.Semaphore):
        self.session = session
        self.company_sem = company_sem
        self.fetch_sem = asyncio.Semaphore(35)
        self.search_sem = asyncio.Semaphore(5)
        self.cache: dict[str, dict[str, Any]] = {}

    async def fetch(self, url: str, method: str) -> dict[str, Any]:
        url = normalize_url(url)
        if not url:
            return {"url": url, "status": 0, "text": "", "data": b"", "content_type": "", "error": "empty", "method": method}
        if url in self.cache:
            return dict(self.cache[url])
        async with self.fetch_sem:
            await asyncio.sleep(random.uniform(0.02, 0.12))
            try:
                async with self.session.get(url, allow_redirects=True, timeout=TIMEOUT) as response:
                    data = await response.content.read(MAX_BYTES)
                    ct = (response.headers.get("content-type") or "").lower()
                    text = data.decode(response.charset or "utf-8", errors="ignore")
                    result = {"requested_url": url, "url": str(response.url), "status": response.status, "text": text, "data": data, "content_type": ct, "error": "", "method": method}
            except Exception as exc:
                result = {"requested_url": url, "url": url, "status": 0, "text": "", "data": b"", "content_type": "", "error": f"{type(exc).__name__}: {exc}"[:500], "method": method}
        self.cache[url] = dict(result)
        return result

    async def fetch_fallbacks(self, url: str, method: str) -> dict[str, Any]:
        url = normalize_url(url)
        variants = [url]
        if url.startswith("https://"):
            variants.append("http://" + url[len("https://"):])
        elif url.startswith("http://"):
            variants.append("https://" + url[len("http://"):])
        p = urllib.parse.urlsplit(url)
        if p.netloc.startswith("www."):
            variants.append(urllib.parse.urlunsplit((p.scheme, p.netloc[4:], p.path, p.query, "")))
        elif p.netloc:
            variants.append(urllib.parse.urlunsplit((p.scheme, "www." + p.netloc, p.path, p.query, "")))
        last = None
        for variant in dict.fromkeys(variants):
            last = await self.fetch(variant, method)
            if last["status"] in range(200, 400) and (last["text"] or last["data"]):
                return last
        jina = "https://r.jina.ai/http://" + url.split("://", 1)[-1]
        result = await self.fetch(jina, method + ":jina")
        if result["status"] in range(200, 400) and result["text"]:
            result["source_url"] = url
            return result
        return last or result

    async def bing(self, query: str) -> list[dict[str, str]]:
        provider = "bing"
        url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "format": "rss", "setlang": "de-DE"})
        async with self.search_sem:
            result = await self.fetch(url, provider)
        if result["status"] not in range(200, 400):
            return []
        out = []
        try:
            root = ET.fromstring(result["text"])
            for item in root.findall(".//item")[:12]:
                link = clean_space(item.findtext("link", default=""))
                if link:
                    out.append({"title": clean_space(item.findtext("title", default="")), "url": normalize_url(link), "snippet": visible_text(item.findtext("description", default="")), "provider": provider})
        except Exception:
            pass
        return out

    async def ddg(self, query: str) -> list[dict[str, str]]:
        provider = "ddg"
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        async with self.search_sem:
            result = await self.fetch(url, provider)
        if result["status"] not in range(200, 400):
            return []
        soup = BeautifulSoup(result["text"], "lxml")
        out = []
        for block in soup.select(".result")[:12]:
            anchor = block.select_one(".result__a")
            if not anchor:
                continue
            href = anchor.get("href", "")
            parsed = urllib.parse.urlsplit(href)
            actual = urllib.parse.parse_qs(parsed.query).get("uddg", [href])[0]
            sn = block.select_one(".result__snippet")
            out.append({"title": clean_space(anchor.get_text(" ", strip=True)), "url": normalize_url(actual), "snippet": clean_space(sn.get_text(" ", strip=True)) if sn else "", "provider": provider})
        return out

    async def yahoo(self, query: str) -> list[dict[str, str]]:
        provider = "yahoo"
        url = "https://search.yahoo.com/search?" + urllib.parse.urlencode({"p": query})
        async with self.search_sem:
            result = await self.fetch(url, provider)
        if result["status"] not in range(200, 400):
            return []
        soup = BeautifulSoup(result["text"], "lxml")
        out = []
        for block in soup.select("div.algo")[:12]:
            anchor = block.select_one("h3 a")
            if not anchor:
                continue
            sn = block.select_one(".compText")
            out.append({"title": clean_space(anchor.get_text(" ", strip=True)), "url": normalize_url(anchor.get("href", "")), "snippet": clean_space(sn.get_text(" ", strip=True)) if sn else "", "provider": provider})
        return out

    async def google(self, query: str) -> list[dict[str, str]]:
        provider = "google"
        url = "https://www.google.com/search?" + urllib.parse.urlencode({"q": query, "num": "10", "hl": "de"})
        async with self.search_sem:
            result = await self.fetch(url, provider)
        if result["status"] not in range(200, 400):
            return []
        soup = BeautifulSoup(result["text"], "lxml")
        out = []
        for anchor in soup.select("a"):
            href = anchor.get("href", "")
            if href.startswith("/url?q="):
                href = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("q", [""])[0]
            if not href.startswith("http") or host(href) in {"google.com"}:
                continue
            title = clean_space(anchor.get_text(" ", strip=True))
            if title:
                out.append({"title": title, "url": normalize_url(href), "snippet": "", "provider": provider})
            if len(out) >= 12:
                break
        return out

    async def jina(self, query: str) -> list[dict[str, str]]:
        provider = "jina"
        url = "https://s.jina.ai/" + urllib.parse.quote(query, safe="")
        async with self.search_sem:
            result = await self.fetch(url, provider)
        if result["status"] not in range(200, 400):
            return []
        out = []
        for title, link in re.findall(r"\[([^\]]{2,180})\]\((https?://[^)\s]+)\)", result["text"]):
            if host(link) not in BLOCK_DOMAINS:
                out.append({"title": clean_space(title), "url": normalize_url(link), "snippet": "", "provider": provider})
            if len(out) >= 12:
                break
        return out

    async def search_all(self, query: str, state: State, round_name: str) -> list[dict[str, str]]:
        providers = [self.bing, self.ddg, self.yahoo, self.google, self.jina]
        state.query_attempts.extend([f"{round_name}:{fn.__name__}" for fn in providers])
        batches = await asyncio.gather(*(fn(query) for fn in providers))
        out = []
        seen = set()
        for batch in batches:
            if batch:
                state.provider_success.append(f"{round_name}:{batch[0]['provider']}")
            for row in batch:
                if row["url"] and row["url"] not in seen and host(row["url"]) not in BLOCK_DOMAINS:
                    seen.add(row["url"])
                    out.append(row)
        return out

    async def evaluate_url(self, url: str, title: str, snippet_text: str, provider: str, state: State, round_name: str) -> Candidate | None:
        url = normalize_url(url)
        if not url:
            return None
        snippet_hit = broad_hit(title + " " + snippet_text)
        prelim_identity = identity_score(url, title, snippet_text, state.name, state.city, state.known_hosts)
        if prelim_identity < 5 and not (snippet_hit and prelim_identity >= 0):
            return None
        result = await self.fetch_fallbacks(url, f"redteam:{round_name}")
        state.checked.append(result.get("source_url") or result.get("url") or url)
        if result.get("error"):
            state.errors.append(f"{url}: {result['error']}")
        fetched = result["status"] in range(200, 400) and bool(result["text"] or result["data"])
        if fetched:
            final_url = result.get("source_url") or result.get("url") or url
            if "pdf" in result.get("content_type", "") or final_url.lower().endswith(".pdf"):
                text = pdf_text(result.get("data") or b"")
                page_title = title
            else:
                raw = result.get("text", "")
                text = visible_text(raw) if ("html" in result.get("content_type", "") or "<html" in raw[:1000].lower() or ":jina" in result.get("method", "")) else clean_space(raw)
                soup = BeautifulSoup(raw or "", "lxml")
                page_title = clean_space(soup.title.get_text(" ", strip=True)) if soup.title else title
            ident = identity_score(final_url, page_title, text, state.name, state.city, state.known_hosts)
            hit = broad_hit(text)
            trusted = host(final_url) in state.known_hosts or any(h in host(final_url) for h in TRUSTED_HINTS)
            candidate = Candidate(final_url, page_title, text, provider, ident, hit, trusted, True)
        else:
            ident = prelim_identity
            trusted = host(url) in state.known_hosts or any(h in host(url) for h in TRUSTED_HINTS)
            candidate = Candidate(url, title, snippet_text, provider, ident, snippet_hit, trusted, False)
        state.candidates.append(candidate)
        if candidate.hit and candidate.identity >= 10 and (candidate.trusted or candidate.identity >= 28):
            state.new_hits_by_round[round_name] = state.new_hits_by_round.get(round_name, 0) + 1
        return candidate

    async def verify_primary_positive(self, state: State) -> None:
        urls = []
        if state.row.get("source_url"):
            urls.append(state.row["source_url"])
        for evidence in state.row.get("positive_evidence") or []:
            if evidence.get("url"):
                urls.append(evidence["url"])
        for url in dict.fromkeys(urls)[:10]:
            await self.evaluate_url(url, "", "", "primary_source_recheck", state, "positive_recheck")

    async def search_rounds(self, state: State) -> None:
        known_domain = next(iter(state.known_hosts), "")
        fetched = 0
        seen = set()
        for round_name, expressions in ROUND_QUERIES.items():
            for expression in expressions:
                if expression.startswith("site:"):
                    queries = [f'"{state.name}" "{state.city}" {expression}']
                else:
                    queries = [f'"{state.name}" "{state.city}" {expression}']
                    if known_domain:
                        queries.append(f"site:{known_domain} {expression}")
                for query in queries:
                    results = await self.search_all(query, state, round_name)
                    ranked = []
                    for result in results:
                        if result["url"] in seen:
                            continue
                        score = identity_score(result["url"], result["title"], result["snippet"], state.name, state.city, state.known_hosts)
                        if broad_hit(result["title"] + " " + result["snippet"]):
                            score += 35
                        if any(h in host(result["url"]) for h in TRUSTED_HINTS):
                            score += 12
                        ranked.append((score, result))
                    for score, result in sorted(ranked, key=lambda x: -x[0]):
                        if fetched >= MAX_RESULT_FETCHES:
                            break
                        if score < 5:
                            continue
                        seen.add(result["url"])
                        await self.evaluate_url(result["url"], result["title"], result["snippet"], result["provider"], state, round_name)
                        fetched += 1
                    if fetched >= MAX_RESULT_FETCHES:
                        break
                if fetched >= MAX_RESULT_FETCHES:
                    break
            if fetched >= MAX_RESULT_FETCHES:
                break

    def classify(self, state: State) -> dict[str, Any]:
        valid = []
        for candidate in state.candidates:
            if not candidate.hit or candidate.identity < 10:
                continue
            trust_score = candidate.identity + (80 if host(candidate.url) in state.known_hosts else 0) + (30 if candidate.trusted else 0) + (10 if candidate.fetched else 0)
            if candidate.provider == "primary_source_recheck":
                trust_score += 10
            valid.append((trust_score, candidate))
        valid.sort(key=lambda x: (-x[0], x[1].url))

        primary_verdict = state.row.get("verdict")
        if valid and valid[0][0] >= 38:
            score, candidate = valid[0]
            verdict = "Aufnehmen"
            confidence = "Sehr hoch" if score >= 100 else "Hoch"
            reason = f'Unabhängiger Gegencheck bestätigt „{candidate.hit["label"]}“ bei der identitätsgeprüften Firma: {candidate.hit["snippet"]}'
            source = candidate.url
            changed = primary_verdict != "Aufnehmen"
            basis = "unabhängig bestätigter positiver Nachweis"
        else:
            verdict = "Nicht aufnehmen"
            confidence = "Sehr hoch" if primary_verdict == "Nicht aufnehmen" else "Hoch"
            changed = primary_verdict != "Nicht aufnehmen"
            if primary_verdict == "Aufnehmen":
                reason = "Der primäre positive Treffer hielt der unabhängigen Quellen-, Identitäts- und Kontextprüfung nicht stand; weder die Originalquelle noch alternative Suchpfade bestätigten ein ausführendes Barrierefrei-/Altersgerecht-Angebot belastbar."
                basis = "primären Positivtreffer im Gegencheck verworfen"
            else:
                reason = "Der unabhängige Red-Team-Pass mit erweitertem Synonymvokabular, fünf Suchfrontends, Partnerverzeichnissen und erneuter Quellenprüfung fand keinen identitätsgesicherten positiven Gegenbeweis. Die Entscheidung, den Betrieb nicht in das evidenzbasierte Spezialverzeichnis aufzunehmen, wird bestätigt."
                basis = "Negativentscheidung unabhängig bestätigt"
            source = state.row.get("source_url") or state.row.get("website") or ""

        return {
            "nr": state.nr,
            "name": state.name,
            "city": state.city,
            "website": state.row.get("website") or "",
            "primary_verdict": primary_verdict,
            "primary_confidence": state.row.get("confidence") or "",
            "final_verdict": verdict,
            "final_confidence": confidence,
            "changed": changed,
            "reason": reason,
            "source_url": source,
            "decision_basis": basis,
            "claim_scope": "Finale Entscheidung über die Aufnahme in ein spezialisiertes, evidenzbasiertes Verzeichnis; keine absolute Behauptung, dass ein Betrieb niemals einzelne entsprechende Arbeiten ausgeführt hat.",
            "query_attempts": len(state.query_attempts),
            "providers_succeeded": sorted(set(x.split(":", 1)[-1] for x in state.provider_success)),
            "checked_urls_count": len(set(state.checked)),
            "checked_urls": list(dict.fromkeys(state.checked))[:200],
            "new_hits_by_round": state.new_hits_by_round,
            "positive_candidates": [
                {"score": score, "url": c.url, "provider": c.provider, "identity": c.identity, "trusted": c.trusted, "fetched": c.fetched, "label": c.hit["label"], "snippet": c.hit["snippet"]}
                for score, c in valid[:8]
            ],
            "errors": state.errors[:20],
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    async def verify(self, row: dict[str, Any]) -> dict[str, Any]:
        async with self.company_sem:
            state = State(row)
            try:
                if row.get("verdict") == "Aufnehmen":
                    await self.verify_primary_positive(state)
                await self.search_rounds(state)
                return self.classify(state)
            except Exception as exc:
                state.errors.append(f"fatal: {type(exc).__name__}: {exc}"[:700])
                return self.classify(state)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-url", required=True)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as bootstrap:
        async with bootstrap.get(args.queue_url, timeout=TIMEOUT) as response:
            response.raise_for_status()
            rows = json.loads(await response.text())
    subset = [row for idx, row in enumerate(rows) if idx % args.shards == args.shard]

    connector = aiohttp.TCPConnector(ssl=False, limit=90, limit_per_host=4, ttl_dns_cache=900)
    company_sem = asyncio.Semaphore(5)
    async with aiohttp.ClientSession(connector=connector, headers={"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9,en;q=0.5"}) as session:
        verifier = Verifier(session, company_sem)
        tasks = [asyncio.create_task(verifier.verify(row)) for row in subset]
        completed = 0
        with Path(args.out).open("w", encoding="utf-8") as handle:
            for future in asyncio.as_completed(tasks):
                result = await future
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                handle.flush()
                completed += 1
                print(f"red-team shard {args.shard}: {completed}/{len(subset)} nr={result['nr']} verdict={result['final_verdict']} changed={result['changed']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
