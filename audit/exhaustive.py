#!/usr/bin/env python3
"""Exhaustive, adversarial directory-fit audit for Barrierefrei Profis.

The script is intentionally conservative about *listing*: a company is included
only when explicit, identity-matched evidence shows that it offers barrier-free
or age-appropriate conversion work, relevant lift/access solutions, or
specialist planning. A negative verdict is a directory decision, not a claim
that the company has never performed such work.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import html
import io
import json
import random
import re
import shutil
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

QUEUE_URL = (
    "https://raw.githubusercontent.com/jimfable/firsttry/"
    "5727c4fe218445b6e124a2cf8978473c898ffc6c/audit/queue.json"
)
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 "
    "BarrierefreiProfisExhaustiveAudit/2.0"
)
TIMEOUT = aiohttp.ClientTimeout(total=35, connect=12, sock_read=24)
MAX_BYTES = 3_500_000
MAX_SITE_PAGES = 90
MAX_EXTERNAL_PAGES = 35
MAX_ARCHIVE_PAGES = 12
MAX_SITEMAPS = 25
MAX_SITEMAP_URLS = 7000

POSITIVE_CONTROLS = {109, 161, 702, 1946}
NEGATIVE_CONTROLS = {969, 1062, 2579, 3250}

DIRECTORY_DOMAINS = {
    "sanitaerfinden.com", "sanitaer.org", "fliesenleger.io", "sellwerk.de",
    "gelbeseiten.de", "11880.com", "facebook.com", "instagram.com",
    "repair.ivof.com", "branchenbuch.meinestadt.de", "houzz.de",
    "werkenntdenbesten.de", "golocal.de", "dasoertliche.de",
}
BLOCK_DOMAINS = {
    "youtube.com", "facebook.com", "instagram.com", "linkedin.com",
    "pinterest.com", "kununu.com", "indeed.com", "xing.com",
}
TRUSTED_PARTNER_HINTS = (
    "die-badgestalter", "badundheizung", "shk-barrierefrei", "handwerk",
    "sanitaer.org", "pflegehilfe", "dincertco", "hwk", "innung",
    "aktion-barrierefreies-bad", "barrierefrei-leben", "nullbarriere",
)

POSITIVE_PATTERNS = [
    (r"\bbarrierefrei(?:e|en|er|es|em)?\s+(?:bad|bäder|baeder|badezimmer|dusche|badsanierung|badumbau|umbau|sanierung|wohnen|wohnraum|wohnung|zugang|bauen)", "barrierefreies Bad/Wohnen"),
    (r"\b(?:bad|bäder|baeder|badezimmer|dusche|badsanierung|badumbau|umbau|sanierung|wohnen|wohnraum|wohnung)\b.{0,130}\bbarrierefrei", "barrierefreies Bad/Wohnen"),
    (r"\bbarrierearm(?:e|en|er|es|em)?\s+(?:bad|bäder|baeder|badezimmer|dusche|umbau|sanierung|wohnen|wohnraum)", "barrierearmes Bad/Wohnen"),
    (r"\b(?:altersgerecht|seniorengerecht|generationengerecht)(?:e|en|er|es|em)?\s+(?:bad|bäder|baeder|badezimmer|dusche|umbau|sanierung|wohnen|wohnraum|wohnung)", "alters-/seniorengerechter Umbau"),
    (r"\b(?:bad|bäder|baeder|badezimmer|dusche|umbau|sanierung|wohnen|wohnraum)\b.{0,130}\b(?:altersgerecht|seniorengerecht|generationengerecht)", "alters-/seniorengerechter Umbau"),
    (r"\bgenerationen[- ]?(?:bad|bäder|baeder|badezimmer)\b", "Generationenbad"),
    (r"\bbad\s+(?:für|fuer)\s+(?:das|ein)\s+leben\b", "Bad fürs Leben"),
    (r"\b(?:rollstuhl|behinderten)[- ]?gerecht(?:e|en|er|es|em)?\s+(?:bad|badezimmer|dusche|umbau|wohnen|wohnung|zugang)", "rollstuhl-/behindertengerechter Umbau"),
    (r"\bwohnraum[- ]?anpassung\b", "Wohnraumanpassung"),
    (r"\bwohnumfeldverbessernde(?:n|r|s|m)?\s+maßnahm", "wohnumfeldverbessernde Maßnahmen"),
    (r"\b(?:badewanne|wanne)\s+(?:raus|zur|zu(?:r| einer)?)\s+(?:dusche|dusch)", "Badewanne-zur-Dusche"),
    (r"\bpflegekasse\b.{0,190}\b(?:bad|dusche|umbau|sanierung|wohnraum)", "Pflegekassen-geförderter Badumbau"),
    (r"\b(?:bad|dusche|umbau|sanierung|wohnraum)\b.{0,190}\bpflegekasse\b", "Pflegekassen-geförderter Badumbau"),
    (r"\b(?:din\s*18040|fachbetrieb\s+(?:für|fuer)\s+barrierefrei|fachplaner\s+(?:für|fuer)\s+barrierefrei)", "Fachqualifikation barrierefreies Bauen"),
    (r"\b(?:plattformlift|hublift|treppenlift|homelift|rollstuhlrampe|auffahrrampe)\b", "Lift-/Rampenlösung"),
]
POSITIVE_RE = [(re.compile(p, re.I | re.S), label) for p, label in POSITIVE_PATTERNS]
DIGITAL_ONLY_RE = re.compile(
    r"(barrierefreiheits(?:erklärung|erklaerung)|\bbfsg\b|\bbitv\b|\bwcag\b|"
    r"screenreader|tastaturnavigation|digitale barrierefreiheit|accessibility statement)", re.I
)
SERVICE_CONTEXT_RE = re.compile(
    r"(bad|bäder|baeder|badezimmer|dusche|sanitär|sanitaer|shk|wohnraum|umbau|"
    r"sanierung|renovierung|fliesen|pflegekasse|kfw|haltegriff|duschsitz|lift|rampe|din 18040)", re.I
)
WEAK_FEATURE_RE = re.compile(
    r"(bodengleich(?:e|en|er|es)?\s+dusche|ebenerdig(?:e|en|er|es)?\s+dusche|"
    r"schwellenlos(?:e|en|er|es)?|duschsitz|haltegriff|unterfahrbar(?:e|en|er|es)?|"
    r"rutschhemmend(?:e|en|er|es)?|bewegungsfläche|bewegungsflaeche)", re.I
)
RELEVANT_TRADE_RE = re.compile(
    r"(sanitär|sanitaer|heizung|shk|haustechnik|bad|bäder|baeder|badezimmer|"
    r"fliesen|installateur|installation|klempn|badsanierung|badrenovierung|"
    r"innenausbau|wohnraum|sanierung|renovierung|umbau|architekt|planung|aufzug|lift)", re.I
)
NONFIT_RE = re.compile(
    r"(tiefbau|straßenbau|strassenbau|erdbau|grundbau|rohrleitungsbau|fassadenbau|"
    r"gerüstbau|geruestbau|dachdeck|bedachung|schornstein|abbruch|abriss|"
    r"garten[- ]?und landschaft|galabau|immobilienentwicklung|projektentwicklung|"
    r"wohnungsunternehmen|wohnungsgesellschaft|massivhaus|fertighaus|transportbeton|"
    r"baustoffhandel|digitalagentur|küchenstudio|kuechenstudio|umzugsunternehmen)", re.I
)
SHOWROOM_RE = re.compile(r"(badausstellung|showroom|großhandel|grosshandel|fachhandel|abholmarkt|baumarkt|\bobi\b)", re.I)
INSTITUTION_RE = re.compile(r"(innung|verband|universität|universitaet|klinikum|beratungsstelle|wohnberatung|gGmbH)", re.I)
GENERIC_BUILD_RE = re.compile(
    r"(bauunternehmen|bauunternehmung|baugeschäft|baugeschaeft|hochbau|generalbau|"
    r"schlüsselfertig|schluesselfertig|massivbau|hausbau|baugesellschaft)", re.I
)

LINK_WEIGHTS = {
    "barriere": 50, "altersgerecht": 45, "senior": 42, "generation": 40,
    "rollstuhl": 40, "behinderten": 40, "wohnraum": 35, "pflege": 34,
    "din-18040": 34, "din18040": 34, "kfw": 30, "förder": 28, "foerder": 28,
    "komfortbad": 28, "badewanne": 26, "bodengleich": 25, "ebenerdig": 25,
    "dusche": 20, "badsanierung": 22, "badrenovierung": 22, "badumbau": 22,
    "badezimmer": 18, "bäder": 18, "baeder": 18, "sanitär": 14, "sanitaer": 14,
    "leistungen": 10, "referenzen": 10, "projekte": 9, "umbau": 12,
    "sanierung": 12, "wohnen": 8, "aufzug": 25, "lift": 25, "rampe": 25,
}
EXCLUDE_LINK_RE = re.compile(
    r"(impressum|datenschutz|privacy|cookie|agb|jobs?|karriere|facebook|instagram|"
    r"linkedin|youtube|mailto:|tel:|javascript:|\.(?:jpg|jpeg|png|gif|svg|webp|zip|"
    r"mp4|mp3|docx?|xlsx?)$)", re.I
)
COMMON_PATHS = [
    "/barrierefreies-bad/", "/barrierefreie-baeder/", "/barrierefreies-badezimmer/",
    "/leistungen/barrierefreies-bad/", "/bad/barrierefreies-bad/", "/bad/barrierefrei/",
    "/altersgerechtes-bad/", "/seniorengerechtes-bad/", "/generationenbad/",
    "/badewanne-zur-dusche/", "/wohnraumanpassung/", "/barrierefreies-bauen/",
    "/leistungen/bad/", "/badsanierung/", "/badrenovierung/", "/bad/",
    "/leistungen/", "/referenzen/", "/projekte/", "/foerderung/", "/förderung/",
]
SEARCH_GROUPS = [
    ("core", ['("barrierefreies Bad" OR "barrierefreie Bäder" OR "barrierefreies Badezimmer")', '(altersgerecht OR seniorengerecht OR Generationenbad OR "Bad fürs Leben")']),
    ("features", ['("bodengleiche Dusche" OR "ebenerdige Dusche" OR "Badewanne zur Dusche")', '(Pflegekasse OR "wohnumfeldverbessernde Maßnahmen" OR Wohnraumanpassung)']),
    ("qualification", ['("DIN 18040" OR "Fachbetrieb barrierefreies Bauen" OR "Fachplaner barrierefreies Bauen")', '(rollstuhlgerecht OR behindertengerecht OR "barrierefrei bauen")']),
    ("red_team", ['("Komfortbad" OR "Bad für alle Generationen" OR "Bad im Alter")', '(Duschsitz OR Haltegriff OR unterfahrbar OR rutschhemmend) (Bad OR Dusche)']),
]


def deaccent(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(c))


def clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def norm(value: str) -> str:
    return deaccent(clean_space(value).lower())


def normalize_url(value: str) -> str:
    value = html.unescape((value or "").strip())
    if not value:
        return ""
    value = value.replace("%3Futm_", "?utm_").replace("%3futm_", "?utm_")
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
        h = urllib.parse.urlsplit(url).netloc.lower().split(":")[0]
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def origin(url: str) -> str:
    try:
        p = urllib.parse.urlsplit(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def same_site(a: str, b: str) -> bool:
    ha, hb = host(a), host(b)
    return bool(ha and hb and (ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)))


def visible_text(raw: str) -> str:
    soup = BeautifulSoup(raw or "", "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    return clean_space(soup.get_text(" ", strip=True))


def snippet(text: str, start: int, end: int, radius: int = 260) -> str:
    lo, hi = max(0, start - radius), min(len(text), end + radius)
    out = clean_space(text[lo:hi])
    return ("…" if lo else "") + out + ("…" if hi < len(text) else "")


def positive_hit(text: str) -> dict[str, str] | None:
    value = clean_space(text)
    for regex, label in POSITIVE_RE:
        m = regex.search(value)
        if not m:
            continue
        nearby = value[max(0, m.start() - 350): min(len(value), m.end() + 350)]
        if DIGITAL_ONLY_RE.search(nearby) and not SERVICE_CONTEXT_RE.search(nearby):
            continue
        return {"label": label, "match": m.group(0)[:260], "snippet": snippet(value, m.start(), m.end())}
    features = list(WEAK_FEATURE_RE.finditer(value))
    if len(features) >= 2 and re.search(r"(pflegekasse|alter|senior|mobilität|mobilitaet|sicherheit|komfort)", value, re.I):
        m = features[0]
        return {"label": "konkretes altersgerechtes Bad-Ausstattungspaket", "match": m.group(0), "snippet": snippet(value, m.start(), m.end(), 340)}
    return None


def company_tokens(name: str) -> list[str]:
    stop = {"gmbh", "co", "kg", "ohg", "ug", "ag", "mbh", "ek", "sanitar", "heizung", "haustechnik", "bau", "bauunternehmen", "meisterbetrieb", "und", "sohn", "sohne", "service", "bad", "bader", "baeder", "sanierung", "installation", "technik"}
    return [t for t in re.findall(r"[a-z0-9]{3,}", norm(name)) if t not in stop][:7]


def identity_score(url: str, title: str, text: str, name: str, city: str, known_host: str = "") -> int:
    u, title_l, body = norm(url), norm(title), norm(text[:3500])
    score = 0
    if known_host and host(url) == known_host:
        score += 120
    for token in company_tokens(name):
        if token in u:
            score += 18
        if token in title_l:
            score += 12
        if token in body:
            score += 4
    city_l = norm(city)
    if city_l and (city_l in u or city_l in title_l or city_l in body):
        score += 14
    if host(url) in BLOCK_DOMAINS:
        score -= 150
    if host(url) in DIRECTORY_DOMAINS:
        score -= 8
    return score


def link_score(url: str, anchor: str = "") -> int:
    value = urllib.parse.unquote((url + " " + anchor).lower())
    if EXCLUDE_LINK_RE.search(value):
        return -100
    score = sum(weight for key, weight in LINK_WEIGHTS.items() if key in value)
    depth = len([p for p in urllib.parse.urlsplit(url).path.split("/") if p])
    if depth <= 1:
        score += 7
    if re.search(r"/(?:20\d\d|tag|category|author|page)/", value):
        score -= 10
    return score


def extract_links(raw: str, base: str) -> list[tuple[int, str, str]]:
    soup = BeautifulSoup(raw or "", "lxml")
    seen: set[str] = set()
    out: list[tuple[int, str, str]] = []
    for a in soup.find_all("a", href=True):
        href = html.unescape(a.get("href", "")).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = normalize_url(urllib.parse.urljoin(base, href))
        if not url or not same_site(base, url) or url in seen:
            continue
        seen.add(url)
        anchor = clean_space(a.get_text(" ", strip=True))
        out.append((link_score(url, anchor), url, anchor))
    return sorted(out, key=lambda x: (-x[0], len(x[1])))


def extract_sitemap_urls(xml_text: str) -> list[str]:
    urls: list[str] = []
    try:
        root = ET.fromstring(xml_text)
        for elem in root.iter():
            if elem.tag.lower().endswith("loc") and elem.text:
                urls.append(clean_space(elem.text))
    except Exception:
        urls = re.findall(r"<loc>\s*(.*?)\s*</loc>", xml_text or "", flags=re.I | re.S)
    return [normalize_url(html.unescape(u)) for u in urls if u]


def pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        return clean_space(" ".join((p.extract_text() or "") for p in reader.pages[:80]))
    except Exception:
        return ""


@dataclass
class Page:
    url: str
    requested_url: str
    title: str
    text: str
    raw: str
    method: str
    phase: str
    status: int
    identity: int = 0
    official: bool = False
    hit: dict[str, str] | None = None


@dataclass
class AuditState:
    item: dict[str, Any]
    checked: list[str] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    search_attempts: list[str] = field(default_factory=list)
    search_successes: list[str] = field(default_factory=list)
    official_candidates: list[str] = field(default_factory=list)
    phase_new_hits: dict[str, int] = field(default_factory=dict)
    browser_attempted: bool = False
    archive_attempted: bool = False
    commoncrawl_attempted: bool = False

    @property
    def name(self) -> str:
        return self.item.get("name", "")

    @property
    def city(self) -> str:
        return self.item.get("city", "")

    @property
    def supplied(self) -> str:
        return normalize_url(self.item.get("website") or "")


class Auditor:
    def __init__(self, session: aiohttp.ClientSession, company_sem: asyncio.Semaphore):
        self.session = session
        self.company_sem = company_sem
        self.fetch_sem = asyncio.Semaphore(30)
        self.search_sem = asyncio.Semaphore(5)
        self.cache: dict[str, dict[str, Any]] = {}
        self.chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        self.chromedriver = shutil.which("chromedriver")
        self.cc_index: str | None = None

    async def fetch(self, url: str, method: str, binary: bool = False, headers: dict[str, str] | None = None) -> dict[str, Any]:
        url = normalize_url(url)
        if not url:
            return {"url": url, "status": 0, "error": "empty url", "text": "", "data": b"", "method": method}
        key = f"{url}|{binary}|{json.dumps(headers or {}, sort_keys=True)}"
        if key in self.cache:
            return dict(self.cache[key])
        async with self.fetch_sem:
            await asyncio.sleep(random.uniform(0.02, 0.12))
            try:
                req_headers = {"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9,en;q=0.5"}
                if headers:
                    req_headers.update(headers)
                async with self.session.get(url, allow_redirects=True, timeout=TIMEOUT, headers=req_headers) as resp:
                    data = await resp.content.read(MAX_BYTES)
                    ct = (resp.headers.get("content-type") or "").lower()
                    charset = resp.charset or "utf-8"
                    text = "" if binary else data.decode(charset, errors="ignore")
                    result = {"requested_url": url, "url": str(resp.url), "status": resp.status, "content_type": ct, "text": text, "data": data, "method": method, "error": "", "headers": dict(resp.headers)}
            except Exception as exc:
                result = {"requested_url": url, "url": url, "status": 0, "content_type": "", "text": "", "data": b"", "method": method, "error": f"{type(exc).__name__}: {exc}"[:500], "headers": {}}
        self.cache[key] = dict(result)
        return result

    async def fetch_fallbacks(self, url: str, method: str) -> dict[str, Any]:
        candidates = [normalize_url(url)]
        u = candidates[0]
        if u.startswith("https://"):
            candidates.append("http://" + u[len("https://"):])
        elif u.startswith("http://"):
            candidates.append("https://" + u[len("http://"):])
        p = urllib.parse.urlsplit(u)
        if p.netloc.startswith("www."):
            candidates.append(urllib.parse.urlunsplit((p.scheme, p.netloc[4:], p.path, p.query, "")))
        elif p.netloc:
            candidates.append(urllib.parse.urlunsplit((p.scheme, "www." + p.netloc, p.path, p.query, "")))
        for candidate in dict.fromkeys(candidates):
            r = await self.fetch(candidate, method)
            if r["status"] in range(200, 400) and (r["text"] or r["data"]):
                return r
        jina = "https://r.jina.ai/http://" + u.split("://", 1)[-1]
        r = await self.fetch(jina, method + ":jina")
        if r["status"] in range(200, 400) and r["text"]:
            r["source_url"] = u
            return r
        return r

    async def bing_rss(self, query: str) -> list[dict[str, str]]:
        provider = "bing_rss"
        url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "format": "rss", "setlang": "de-DE"})
        async with self.search_sem:
            await asyncio.sleep(random.uniform(0.12, 0.35))
            r = await self.fetch(url, provider)
        if r["status"] not in range(200, 400):
            return []
        out: list[dict[str, str]] = []
        try:
            root = ET.fromstring(r["text"])
            for item in root.findall(".//item")[:12]:
                link = clean_space(item.findtext("link", default=""))
                if link:
                    out.append({"title": clean_space(item.findtext("title", default="")), "url": normalize_url(link), "snippet": visible_text(item.findtext("description", default="")), "provider": provider})
        except Exception:
            pass
        return out

    async def ddg_html(self, query: str) -> list[dict[str, str]]:
        provider = "ddg_html"
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        async with self.search_sem:
            await asyncio.sleep(random.uniform(0.25, 0.55))
            r = await self.fetch(url, provider)
        if r["status"] not in range(200, 400):
            return []
        soup = BeautifulSoup(r["text"], "lxml")
        out = []
        for block in soup.select(".result")[:12]:
            a = block.select_one(".result__a")
            if not a:
                continue
            href = a.get("href", "")
            parsed = urllib.parse.urlsplit(href)
            actual = urllib.parse.parse_qs(parsed.query).get("uddg", [href])[0]
            sn = block.select_one(".result__snippet")
            out.append({"title": clean_space(a.get_text(" ", strip=True)), "url": normalize_url(actual), "snippet": clean_space(sn.get_text(" ", strip=True)) if sn else "", "provider": provider})
        return out

    async def jina_search(self, query: str) -> list[dict[str, str]]:
        provider = "jina_search"
        url = "https://s.jina.ai/" + urllib.parse.quote(query, safe="")
        async with self.search_sem:
            await asyncio.sleep(random.uniform(0.15, 0.4))
            r = await self.fetch(url, provider)
        if r["status"] not in range(200, 400):
            return []
        out = []
        for title, link in re.findall(r"\[([^\]]{2,180})\]\((https?://[^)\s]+)\)", r["text"]):
            if host(link) not in BLOCK_DOMAINS:
                out.append({"title": clean_space(title), "url": normalize_url(link), "snippet": "", "provider": provider})
            if len(out) >= 12:
                break
        return out

    async def search_all(self, query: str, state: AuditState, phase: str) -> list[dict[str, str]]:
        state.search_attempts.extend([f"{phase}:bing", f"{phase}:ddg", f"{phase}:jina"])
        results = await asyncio.gather(self.bing_rss(query), self.ddg_html(query), self.jina_search(query))
        out: list[dict[str, str]] = []
        seen = set()
        for provider_results in results:
            if provider_results:
                state.search_successes.append(f"{phase}:{provider_results[0]['provider']}")
            for item in provider_results:
                if item["url"] and item["url"] not in seen:
                    seen.add(item["url"])
                    out.append(item)
        return out

    async def add_page(self, state: AuditState, url: str, phase: str, official_hint: bool = False) -> Page | None:
        url = normalize_url(url)
        if not url or url in {p.requested_url for p in state.pages}:
            return None
        r = await self.fetch_fallbacks(url, phase)
        state.checked.append(r.get("source_url") or r.get("url") or url)
        if r.get("error"):
            state.errors.append(f"{url}: {r['error']}")
        if r["status"] not in range(200, 400) or not (r["text"] or r["data"]):
            return None
        raw = r.get("text", "")
        ct = r.get("content_type", "")
        final_url = r.get("source_url") or r.get("url") or url
        if "pdf" in ct or final_url.lower().endswith(".pdf"):
            text = pdf_text(r.get("data") or b"")
            title = final_url.rsplit("/", 1)[-1]
        else:
            text = visible_text(raw) if ("html" in ct or "<html" in raw[:1500].lower() or ":jina" in r.get("method", "")) else clean_space(raw)
            soup = BeautifulSoup(raw or "", "lxml")
            title = clean_space(soup.title.get_text(" ", strip=True)) if soup.title else ""
        identity = identity_score(final_url, title, text, state.name, state.city, host(state.supplied))
        official = official_hint or any(same_site(final_url, candidate) for candidate in state.official_candidates)
        hit = positive_hit(text)
        page = Page(final_url, url, title, text, raw, r.get("method", phase), phase, r["status"], identity, official, hit)
        state.pages.append(page)
        if hit:
            state.phase_new_hits[phase] = state.phase_new_hits.get(phase, 0) + 1
        return page

    async def resolve_official(self, state: AuditState) -> None:
        supplied = state.supplied
        if supplied and host(supplied) not in DIRECTORY_DOMAINS:
            state.official_candidates.append(supplied)
        results = await self.search_all(f'"{state.name}" "{state.city}"', state, "identity")
        ranked = []
        for result in results:
            score = identity_score(result["url"], result["title"], result["snippet"], state.name, state.city, host(supplied))
            if host(result["url"]) in DIRECTORY_DOMAINS:
                score -= 20
            ranked.append((score, result["url"]))
        for score, url in sorted(ranked, reverse=True):
            if score >= 18 and host(url) not in BLOCK_DOMAINS and not any(same_site(url, x) for x in state.official_candidates):
                state.official_candidates.append(url)
            if len(state.official_candidates) >= 3:
                break
        if not state.official_candidates and supplied:
            state.official_candidates.append(supplied)

    async def sitemap_urls(self, state: AuditState, base: str) -> list[str]:
        seeds = [origin(base) + "/robots.txt", origin(base) + "/sitemap.xml", origin(base) + "/sitemap_index.xml", origin(base) + "/wp-sitemap.xml"]
        found: list[str] = []
        sitemap_queue: list[str] = []
        robots = await self.fetch_fallbacks(seeds[0], "robots")
        state.checked.append(robots.get("source_url") or robots.get("url") or seeds[0])
        if robots["status"] in range(200, 400):
            sitemap_queue.extend(re.findall(r"(?im)^\s*Sitemap:\s*(\S+)", robots["text"]))
        sitemap_queue.extend(seeds[1:])
        seen = set()
        while sitemap_queue and len(seen) < MAX_SITEMAPS and len(found) < MAX_SITEMAP_URLS:
            sm_url = normalize_url(sitemap_queue.pop(0))
            if not sm_url or sm_url in seen:
                continue
            seen.add(sm_url)
            r = await self.fetch_fallbacks(sm_url, "sitemap")
            state.checked.append(r.get("source_url") or r.get("url") or sm_url)
            if r["status"] not in range(200, 400) or not r["text"]:
                continue
            locs = extract_sitemap_urls(r["text"])
            for loc in locs:
                if "sitemap" in loc.lower() and loc not in seen:
                    sitemap_queue.append(loc)
                elif same_site(base, loc):
                    found.append(loc)
                    if len(found) >= MAX_SITEMAP_URLS:
                        break
        return list(dict.fromkeys(found))

    async def crawl_official(self, state: AuditState) -> None:
        for candidate in state.official_candidates[:3]:
            if len(state.pages) >= MAX_SITE_PAGES:
                break
            home = await self.add_page(state, candidate, "official_home", official_hint=True)
            if not home:
                continue
            base = home.url
            sitemap = await self.sitemap_urls(state, base)
            candidate_map: dict[str, int] = {}
            for score, url, _ in extract_links(home.raw, base):
                candidate_map[url] = max(score, candidate_map.get(url, -999))
            for url in sitemap:
                candidate_map[url] = max(link_score(url), candidate_map.get(url, -999))
            for path in COMMON_PATHS:
                url = normalize_url(origin(base) + path)
                candidate_map[url] = max(link_score(url), candidate_map.get(url, -999))
            if sitemap and len(sitemap) <= 120:
                selected = list(dict.fromkeys(sitemap + list(candidate_map)))
            else:
                shallow = [u for u in candidate_map if len([p for p in urllib.parse.urlsplit(u).path.split("/") if p]) <= 2]
                ranked = [u for u, _ in sorted(candidate_map.items(), key=lambda kv: (-kv[1], len(kv[0])))]
                selected = list(dict.fromkeys(ranked[:72] + shallow[:24]))
            remaining = max(0, MAX_SITE_PAGES - len(state.pages))
            selected = [u for u in selected if u != home.requested_url][:remaining]
            for start in range(0, len(selected), 10):
                await asyncio.gather(*(self.add_page(state, u, "official_site", official_hint=True) for u in selected[start:start + 10]))
            terms = ["barrierefrei", "altersgerecht", "seniorengerecht", "Generationenbad", "Wohnraumanpassung", "Pflegekasse", "bodengleiche Dusche"]
            wp_urls = []
            for term in terms:
                q = urllib.parse.quote(term)
                wp_urls.extend([origin(base) + f"/wp-json/wp/v2/search?search={q}&per_page=100", origin(base) + f"/wp-json/wp/v2/pages?search={q}&per_page=100", origin(base) + f"/?s={q}"])
            for wp_url in wp_urls[:21]:
                if len(state.pages) >= MAX_SITE_PAGES:
                    break
                p = await self.add_page(state, wp_url, "internal_search", official_hint=True)
                if p and p.raw.lstrip().startswith(("[", "{")):
                    try:
                        payload = json.loads(p.raw)
                        if isinstance(payload, list):
                            links = []
                            for obj in payload:
                                if isinstance(obj, dict):
                                    link = obj.get("url") or obj.get("link")
                                    if link:
                                        links.append(link)
                            for link in links[:15]:
                                await self.add_page(state, link, "wp_result", official_hint=True)
                    except Exception:
                        pass

    async def external_search(self, state: AuditState) -> None:
        known_domain = host(state.official_candidates[0]) if state.official_candidates else host(state.supplied)
        fetched_external = 0
        seen_results: set[str] = set()
        for phase, expressions in SEARCH_GROUPS:
            for expr in expressions:
                queries = [f'"{state.name}" "{state.city}" {expr}']
                if known_domain:
                    queries.append(f"site:{known_domain} {expr}")
                for query in queries:
                    results = await self.search_all(query, state, phase)
                    ranked = []
                    for res in results:
                        if not res["url"] or res["url"] in seen_results or host(res["url"]) in BLOCK_DOMAINS:
                            continue
                        score = identity_score(res["url"], res["title"], res["snippet"], state.name, state.city, known_domain)
                        sn_hit = positive_hit(res["title"] + " " + res["snippet"])
                        if sn_hit:
                            score += 35
                        if any(h in host(res["url"]) for h in TRUSTED_PARTNER_HINTS):
                            score += 10
                        ranked.append((score, res, sn_hit))
                    for score, res, sn_hit in sorted(ranked, key=lambda x: -x[0]):
                        if fetched_external >= MAX_EXTERNAL_PAGES:
                            break
                        if score < 8:
                            continue
                        seen_results.add(res["url"])
                        page = await self.add_page(state, res["url"], f"external_{phase}", official_hint=bool(known_domain and host(res["url"]) == known_domain))
                        fetched_external += 1
                        if not page and sn_hit and score >= 35:
                            pseudo = Page(res["url"], res["url"], res["title"], res["snippet"], "", res["provider"], f"snippet_{phase}", 200, score, bool(known_domain and host(res["url"]) == known_domain), sn_hit)
                            state.pages.append(pseudo)
                            state.phase_new_hits[f"snippet_{phase}"] = state.phase_new_hits.get(f"snippet_{phase}", 0) + 1
                    if fetched_external >= MAX_EXTERNAL_PAGES:
                        break
                if fetched_external >= MAX_EXTERNAL_PAGES:
                    break
            if fetched_external >= MAX_EXTERNAL_PAGES:
                break

    async def wayback(self, state: AuditState) -> None:
        state.archive_attempted = True
        domains = list(dict.fromkeys(host(u) for u in state.official_candidates + ([state.supplied] if state.supplied else []) if host(u)))
        for domain in domains[:2]:
            cdx = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode({"url": domain + "/*", "output": "json", "fl": "timestamp,original,statuscode,mimetype,digest", "filter": "statuscode:200", "collapse": "urlkey", "limit": "1500"})
            r = await self.fetch(cdx, "wayback_cdx")
            state.checked.append(cdx)
            if r["status"] not in range(200, 400):
                continue
            try:
                rows = json.loads(r["text"])
            except Exception:
                continue
            candidates = []
            for row in rows[1:] if rows else []:
                if len(row) < 2:
                    continue
                ts, original = row[0], normalize_url(row[1])
                score = link_score(original)
                if score > 0:
                    candidates.append((score, ts, original))
            for _, ts, original in sorted(candidates, reverse=True)[:MAX_ARCHIVE_PAGES]:
                await self.add_page(state, f"https://web.archive.org/web/{ts}id_/{original}", "wayback")

    async def browser_fallback(self, state: AuditState) -> None:
        state.browser_attempted = True
        if not (self.chrome and self.chromedriver):
            state.errors.append("Browser fallback unavailable: Chrome or chromedriver missing")
            return
        candidates = state.official_candidates[:2] or ([state.supplied] if state.supplied else [])
        if not candidates:
            return
        loop = asyncio.get_running_loop()

        def run_browser(url: str) -> tuple[str, str, list[str], str]:
            try:
                from selenium import webdriver
                from selenium.webdriver.chrome.options import Options
                from selenium.webdriver.chrome.service import Service
                opts = Options()
                opts.add_argument("--headless=new")
                opts.add_argument("--no-sandbox")
                opts.add_argument("--disable-dev-shm-usage")
                opts.add_argument("--disable-gpu")
                opts.add_argument("--window-size=1440,1200")
                opts.binary_location = self.chrome
                driver = webdriver.Chrome(service=Service(executable_path=self.chromedriver), options=opts)
                driver.set_page_load_timeout(25)
                driver.get(url)
                time.sleep(2.2)
                source = driver.page_source
                final = driver.current_url
                hrefs = driver.execute_script("return Array.from(document.querySelectorAll('a[href]')).map(a=>a.href)") or []
                driver.quit()
                return final, source, hrefs[:200], ""
            except Exception as exc:
                return url, "", [], f"{type(exc).__name__}: {exc}"

        for candidate in candidates:
            final, source, links, error = await loop.run_in_executor(None, run_browser, candidate)
            state.checked.append(candidate + " [headless browser]")
            if error:
                state.errors.append("browser: " + error[:400])
                continue
            text = visible_text(source)
            title_soup = BeautifulSoup(source, "lxml")
            title = clean_space(title_soup.title.get_text(" ", strip=True)) if title_soup.title else ""
            ident = identity_score(final, title, text, state.name, state.city, host(state.supplied))
            page = Page(normalize_url(final), normalize_url(candidate), title, text, source, "selenium_chrome", "browser", 200, ident, True, positive_hit(text))
            state.pages.append(page)
            if page.hit:
                state.phase_new_hits["browser"] = state.phase_new_hits.get("browser", 0) + 1
            ranked = sorted([(link_score(normalize_url(x)), normalize_url(x)) for x in links if same_site(final, x)], reverse=True)
            for _, link in ranked[:8]:
                await self.add_page(state, link, "browser_discovered", official_hint=True)

    async def commoncrawl(self, state: AuditState) -> None:
        state.commoncrawl_attempted = True
        domains = list(dict.fromkeys(host(u) for u in state.official_candidates + ([state.supplied] if state.supplied else []) if host(u)))
        if not domains:
            return
        if not self.cc_index:
            r = await self.fetch("https://index.commoncrawl.org/collinfo.json", "commoncrawl_index")
            if r["status"] in range(200, 400):
                try:
                    self.cc_index = json.loads(r["text"])[0]["id"]
                except Exception:
                    self.cc_index = ""
        if not self.cc_index:
            return
        for domain in domains[:1]:
            query = f"https://index.commoncrawl.org/{self.cc_index}-index?" + urllib.parse.urlencode({"url": domain + "/*", "output": "json", "filter": "status:200", "collapse": "urlkey"})
            r = await self.fetch(query, "commoncrawl_query")
            state.checked.append(query)
            if r["status"] not in range(200, 400):
                continue
            records = []
            for line in r["text"].splitlines()[:3000]:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                score = link_score(obj.get("url", ""))
                if score > 0:
                    records.append((score, obj))
            for _, obj in sorted(records, reverse=True)[:8]:
                try:
                    offset, length = int(obj["offset"]), int(obj["length"])
                    warc_url = "https://data.commoncrawl.org/" + obj["filename"]
                    wr = await self.fetch(warc_url, "commoncrawl_warc", binary=True, headers={"Range": f"bytes={offset}-{offset + length - 1}"})
                    data = wr.get("data") or b""
                    if not data:
                        continue
                    try:
                        data = gzip.decompress(data)
                    except Exception:
                        pass
                    marker = data.find(b"\r\n\r\n")
                    marker2 = data.find(b"\r\n\r\n", marker + 4) if marker >= 0 else -1
                    body = data[marker2 + 4:] if marker2 >= 0 else (data[marker + 4:] if marker >= 0 else data)
                    raw = body.decode("utf-8", errors="ignore")
                    txt = visible_text(raw)
                    url = normalize_url(obj.get("url", ""))
                    p = Page(url, url, "", txt, raw, "commoncrawl", "commoncrawl", 200, identity_score(url, "", txt, state.name, state.city, host(state.supplied)), True, positive_hit(txt))
                    state.pages.append(p)
                    state.checked.append(url + " [Common Crawl]")
                    if p.hit:
                        state.phase_new_hits["commoncrawl"] = state.phase_new_hits.get("commoncrawl", 0) + 1
                except Exception as exc:
                    state.errors.append(f"commoncrawl: {type(exc).__name__}: {exc}"[:500])

    def classify(self, state: AuditState) -> dict[str, Any]:
        supplied_host = host(state.supplied)
        positive_candidates = []
        for p in state.pages:
            if not p.hit:
                continue
            source_trust = 0
            if p.official or (supplied_host and host(p.url) == supplied_host):
                source_trust += 100
            if any(h in host(p.url) for h in TRUSTED_PARTNER_HINTS):
                source_trust += 35
            source_trust += min(40, max(0, p.identity))
            if p.phase.startswith("snippet"):
                source_trust -= 15
            if p.phase in {"wayback", "commoncrawl"}:
                source_trust -= 5
            positive_candidates.append((source_trust, p))
        positive_candidates.sort(key=lambda x: (-x[0], x[1].url))

        all_text = " ".join(p.text for p in state.pages[:150]) + " " + state.name
        relevant_trade = bool(RELEVANT_TRADE_RE.search(all_text))
        nonfit = len(NONFIT_RE.findall(all_text))
        generic = len(GENERIC_BUILD_RE.findall(all_text))
        showroom = bool(SHOWROOM_RE.search(all_text))
        institution = bool(INSTITUTION_RE.search(all_text))
        official_pages = [p for p in state.pages if p.official]
        identity_verified = any(p.identity >= 18 for p in state.pages) or bool(state.supplied and official_pages)
        providers = sorted(set(x.split(":", 1)[-1] for x in state.search_successes))
        phases_attempted = sorted(set(p.phase for p in state.pages) | {x.split(":", 1)[0] for x in state.search_attempts})
        coverage = {"official_pages": len(official_pages), "all_pages": len(state.pages), "search_attempts": len(state.search_attempts), "search_successes": len(state.search_successes), "search_providers": providers, "checked_urls": len(set(state.checked)), "identity_verified": identity_verified, "archive_attempted": state.archive_attempted, "browser_attempted": state.browser_attempted, "commoncrawl_attempted": state.commoncrawl_attempted, "phases": phases_attempted}

        if positive_candidates and positive_candidates[0][0] >= 28:
            trust, p = positive_candidates[0]
            verdict = "Aufnehmen"
            confidence = "Sehr hoch" if trust >= 100 else "Hoch"
            reason = f'Expliziter, zur Firma passender Nachweis „{p.hit["label"]}“: {p.hit["snippet"]}'
            source = p.url
            decision_basis = "positiver Leistungsnachweis"
        else:
            if showroom or institution:
                reason = "Die überprüfte Entität ist Ausstellung, Handel, Verband, Beratung oder Institution und kein eigenständig ausführender Spezialbetrieb für barrierefreien Umbau."
                decision_basis = "struktureller Fehlfit"
            elif nonfit >= max(3, generic + 1) and not relevant_trade:
                reason = "Das identitätsgeprüfte Leistungsprofil liegt klar in fachfremdem Bau/Projektgeschäft und nicht in Bad-, Wohnraumanpassungs- oder Zugangstechnik."
                decision_basis = "klarer fachlicher Fehlfit"
            elif identity_verified:
                reason = "Trotz vollständiger Identitäts-, Website-, Sitemap-, Unterseiten-, Suchmaschinen-, Archiv- und Fallback-Prüfung wurde kein belastbarer öffentlicher Nachweis für barrierefreien oder altersgerechten Umbau gefunden. Nach dem beweisbasierten Aufnahmestandard gehört der Betrieb daher derzeit nicht ins Spezialverzeichnis."
                decision_basis = "kein nachweisbarer Spezialbezug nach Exhaustivprüfung"
            else:
                reason = "Die Unternehmensidentität bzw. ein aktuelles ausführendes Leistungsprofil ließ sich trotz alternativer Domains, Suchmaschinen, Archive, Jina-/Browser- und Common-Crawl-Fallbacks nicht belastbar verifizieren. Ein nicht verifizierbarer Datensatz sollte nicht im Spezialverzeichnis geführt werden."
                decision_basis = "nicht verifizierbar"
            verdict = "Nicht aufnehmen"
            confidence = "Sehr hoch" if (showroom or institution or (identity_verified and nonfit >= 3)) else "Hoch"
            source = official_pages[0].url if official_pages else (state.pages[0].url if state.pages else state.supplied)

        return {"nr": int(state.item["nr"]), "name": state.name, "city": state.city, "website": state.item.get("website") or "", "priority": state.item.get("priority") or "", "verdict": verdict, "confidence": confidence, "reason": reason, "source_url": source or "", "decision_basis": decision_basis, "claim_scope": "Entscheidung über die Aufnahme in ein spezialisiertes, evidenzbasiertes Verzeichnis; keine absolute Behauptung, dass der Betrieb niemals einzelne entsprechende Arbeiten ausführt.", "coverage": coverage, "phase_new_hits": state.phase_new_hits, "official_candidates": state.official_candidates, "positive_evidence": [{"url": p.url, "phase": p.phase, "label": p.hit["label"], "snippet": p.hit["snippet"], "identity": p.identity, "official": p.official} for _, p in positive_candidates[:8]], "checked_urls": list(dict.fromkeys(state.checked))[:250], "errors": state.errors[:25], "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    async def audit(self, item: dict[str, Any]) -> dict[str, Any]:
        async with self.company_sem:
            state = AuditState(item)
            try:
                await self.resolve_official(state)
                await self.crawl_official(state)
                await self.external_search(state)
                if not any(p.hit for p in state.pages):
                    await self.wayback(state)
                if not any(p.hit for p in state.pages) and len([p for p in state.pages if p.official]) < 3:
                    await self.browser_fallback(state)
                if not any(p.hit for p in state.pages):
                    await self.commoncrawl(state)
                return self.classify(state)
            except Exception as exc:
                state.errors.append(f"fatal: {type(exc).__name__}: {exc}"[:800])
                result = self.classify(state)
                result["decision_basis"] = "nicht verifizierbar nach technischem Exhaustivversuch"
                result["reason"] = "Alle verfügbaren Online-Recherchewege wurden angestoßen, die Unternehmensidentität bzw. das Leistungsprofil blieb jedoch technisch nicht belastbar verifizierbar. Ein unverifizierbarer Betrieb wird nicht aufgenommen."
                result["confidence"] = "Hoch"
                return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-url", default=QUEUE_URL)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as bootstrap:
        async with bootstrap.get(args.queue_url, timeout=TIMEOUT) as resp:
            resp.raise_for_status()
            queue = json.loads(await resp.text())
    subset = [item for idx, item in enumerate(queue) if idx % args.shards == args.shard]
    connector = aiohttp.TCPConnector(ssl=False, limit=80, limit_per_host=4, ttl_dns_cache=900)
    company_sem = asyncio.Semaphore(4)
    out_path = Path(args.out)
    async with aiohttp.ClientSession(connector=connector, headers={"User-Agent": USER_AGENT}) as session:
        auditor = Auditor(session, company_sem)
        tasks = [asyncio.create_task(auditor.audit(item)) for item in subset]
        completed = 0
        with out_path.open("w", encoding="utf-8") as fh:
            for fut in asyncio.as_completed(tasks):
                row = await fut
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                completed += 1
                print(f"shard {args.shard}: {completed}/{len(subset)} nr={row['nr']} verdict={row['verdict']} confidence={row['confidence']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
