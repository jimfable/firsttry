#!/usr/bin/env python3
"""Authoritative v8 evaluator with identity lock and exhaustive physical-service detection."""
from __future__ import annotations

import asyncio
import re
import time

import exhaustive as base
import exhaustive_v3 as v3
import exhaustive_v4 as v4
import exhaustive_v5 as v5

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

# Browser rendering is expensive. These twelve paths cover the dominant URL
# families and are used only when static official-site coverage is sparse.
BROWSER_PRIORITY_PATHS = [
    "/unsere-leistungen/foerderantraege/",
    "/leistungen/bad/foerderung-bad/",
    "/ratgeber/badwissen/bad-sanieren/",
    "/barrierefreies-bad/",
    "/barrierefreie-baeder/",
    "/barrierefreies-badezimmer/",
    "/altersgerechtes-bad/",
    "/seniorengerechtes-bad/",
    "/generationenbad/",
    "/badewanne-zur-dusche/",
    "/wohnraumanpassung/",
    "/badsanierung/",
]

VISITOR_ACCESS_RE = re.compile(
    r"(?:rollstuhlgerecht(?:er|e|en)?|barrierefrei(?:er|e|en)?)\s+"
    r"(?:parkplatz|zugang|eingang|erreichbar|zugänglich|zugaenglich)|"
    r"(?:parkplatz|zugang|eingang|geschäftsräume|geschaeftsraeume|standort|filiale|ausstellung)"
    r".{0,120}(?:rollstuhlgerecht|barrierefrei)",
    re.I | re.S,
)
PHYSICAL_SERVICE_RE = re.compile(
    r"(?:\bbad\b|\bbad(?:ewanne|zimmer|sanier|umbau|renov|modernis|planung|gestaltung|ausstattung|lösung|loesung)\w*|"
    r"bäder|baeder|dusche|umbau|sanier|renov|modernis|wohnraumanpass|wohnumfeld|haltegriff|"
    r"duschsitz|waschtisch|waschbecken|türverbreiter|tuerverbreiter|treppenlift|plattformlift|"
    r"hublift|homelift|rampe|montage|einbau)",
    re.I,
)
EXTENDED_BARRIER_RE = re.compile(
    r"\b(?:"
    r"barriere(?:n)?(?:frei|arm|ärmer|aermer|reduziert|reduzierend)(?:e|en|er|es|em)?"
    r"|barrieren(?:abbau|reduzierung)?"
    r")\b",
    re.I,
)
# Avoid matching street names such as “Badstüberstraße”. Every occurrence of
# “Bad” is either a standalone word or one of the domain-specific compounds.
BAD_CONTEXT_RE = re.compile(
    r"(?:"
    r"\bbad\b|\bbad(?:ewanne|zimmer|sanier|umbau|renov|modernis|planung|gestaltung|ausstattung|lösung|loesung)\w*"
    r"|\bbäder\w*|\bbaeder\w*|\bdusche\w*|\bsanitär\w*|\bsanitaer\w*"
    r"|\bumbau\w*|\bsanierung\w*|\bmodernisierung\w*|\bwohnraum\w*|\bwohnumfeld\w*"
    r")",
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
SOCIAL_SERVICE_RE = re.compile(
    r"(?:ambulant\s+betreutes\s+wohnen|betreutes\s+wohnen|assistenzleistung|eingliederungshilfe|"
    r"sozialgesetzbuch\s*(?:ix|9)|anfrage\s+auf\s+aufnahme|wohngemeinschaft|teamleitung)",
    re.I,
)
CONSTRUCTION_ADAPTATION_RE = re.compile(
    r"(?:umbau|sanier|renov|modernis|montage|einbau|bauen|realisier|installier|badewanne|"
    r"dusche|haltegriff|duschsitz|waschtisch|türverbreit|tuerverbreit|rampe|lift|aufzug)",
    re.I,
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
    digital_url_or_title = bool(v3.DIGITAL_URL_RE.search(decoded) or v3.DIGITAL_TITLE_RE.search(title or ""))
    if digital_url_or_title and not (BAD_CONTEXT_RE.search(value) and EXECUTION_RE.search(value)):
        return None

    for match in EXTENDED_BARRIER_RE.finditer(value):
        nearby = value[max(0, match.start() - 750): min(len(value), match.end() + 750)]
        if not BAD_CONTEXT_RE.search(nearby):
            continue
        if SOCIAL_SERVICE_RE.search(nearby) and not CONSTRUCTION_ADAPTATION_RE.search(nearby):
            continue
        if VISITOR_ACCESS_RE.search(nearby) and not PHYSICAL_SERVICE_RE.search(nearby):
            continue
        if v4.PROJECT_ONLY_RE.search(nearby) and not v4.RETROFIT_RE.search(nearby):
            continue
        service_path = bool(
            v3.direct_service_slug(url, title)
            or re.search(r"/(?:leistungen|unsere-leistungen|service)/", url or "", re.I)
        )
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
        if SOCIAL_SERVICE_RE.search(nearby) and not CONSTRUCTION_ADAPTATION_RE.search(nearby):
            return None
        if not (v4.PROJECT_ONLY_RE.search(nearby) and not v4.RETROFIT_RE.search(nearby)):
            return {
                "label": "konkretes alters-/pflegegerechtes Ausstattungs- und Ausführungsbündel",
                "match": match.group(0)[:260],
                "snippet": base.snippet(value, match.start(), match.end(), 460),
            }
    return None


def strict_positive_hit_v8(text: str, url: str = "", title: str = ""):
    hit = _extended_physical_hit(text, url, title) or ORIGINAL_V4_HIT(text, url, title)
    if not hit:
        return None
    snippet = hit.get("snippet", "")
    if SOCIAL_SERVICE_RE.search(snippet) and not CONSTRUCTION_ADAPTATION_RE.search(snippet):
        return None
    if VISITOR_ACCESS_RE.search(snippet) and not PHYSICAL_SERVICE_RE.search(snippet):
        return None
    return hit


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
        for candidate in state.official_candidates[:3]:
            site_root = base.origin(candidate) or candidate
            urls = [base.normalize_url(site_root + path) for path in PRIORITY_PATHS]
            for start in range(0, len(urls), 8):
                await asyncio.gather(*(
                    self.add_page(state, url, "priority_probe", official_hint=True)
                    for url in urls[start:start + 8]
                ))
        await super().crawl_official(state)

    async def browser_fallback(self, state: base.AuditState) -> None:
        await super().browser_fallback(state)
        if self.credible_positives(state):
            return
        official_count = sum(1 for page in state.pages if page.official)
        if official_count >= 3 or not (self.chrome and self.chromedriver):
            return
        candidate = (state.official_candidates[:1] or ([state.supplied] if state.supplied else []))
        if not candidate:
            return
        root = base.origin(candidate[0]) or candidate[0]
        targets = [base.normalize_url(root + path) for path in BROWSER_PRIORITY_PATHS]
        loop = asyncio.get_running_loop()

        def run_same_origin_fetch(home: str, urls: list[str]):
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
            driver.set_page_load_timeout(30)
            driver.set_script_timeout(12)
            output = []
            try:
                driver.get(home)
                time.sleep(1.5)
                for url in urls:
                    script = """
                    const url=arguments[0], done=arguments[arguments.length-1];
                    const controller=new AbortController();
                    const timer=setTimeout(()=>controller.abort(),8000);
                    fetch(url,{credentials:'include',redirect:'follow',signal:controller.signal})
                      .then(async r=>{const t=await r.text();clearTimeout(timer);done({ok:r.ok,status:r.status,url:r.url,text:t});})
                      .catch(e=>{clearTimeout(timer);done({ok:false,status:0,url:url,text:'',error:String(e)});});
                    """
                    try:
                        result = driver.execute_async_script(script, url) or {}
                    except Exception as exc:
                        result = {"ok": False, "status": 0, "url": url, "text": "", "error": f"{type(exc).__name__}: {exc}"}
                    output.append(result)
            finally:
                driver.quit()
            return output

        try:
            fetched = await loop.run_in_executor(None, run_same_origin_fetch, root, targets)
        except Exception as exc:
            state.errors.append(f"priority browser fetch: {type(exc).__name__}: {exc}"[:500])
            return
        for result in fetched:
            url = base.normalize_url(result.get("url") or "")
            state.checked.append((url or root) + " [browser same-origin priority]")
            raw = result.get("text") or ""
            if not result.get("ok") or not raw:
                continue
            text = base.visible_text(raw)
            soup = base.BeautifulSoup(raw, "lxml")
            title = base.clean_space(soup.title.get_text(" ", strip=True)) if soup.title else ""
            ident = base.identity_score(url, title, text, state.name, state.city, base.host(state.supplied))
            hit = strict_positive_hit_v8(text, url, title)
            page = base.Page(url, url, title, text, raw, "selenium_fetch", "browser_priority", int(result.get("status") or 200), ident, True, hit)
            state.pages.append(page)
            if hit:
                state.phase_new_hits["browser_priority"] = state.phase_new_hits.get("browser_priority", 0) + 1
                if self.credible_positives(state):
                    break

    def credible_positives(self, state: base.AuditState):
        output = []
        supplied_host = base.host(state.supplied)
        for trust, page in super().credible_positives(state):
            snippet = page.hit.get("snippet", "")
            if SOCIAL_SERVICE_RE.search(snippet) and not CONSTRUCTION_ADAPTATION_RE.search(snippet):
                continue
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
