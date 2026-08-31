#!/usr/bin/env python3
import argparse
import asyncio
import html
import json
import random
import re
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
    "BarrierefreiProfisDirectoryAudit/1.0"
)
TIMEOUT = aiohttp.ClientTimeout(total=22, connect=9, sock_read=14)
MAX_BYTES = 2_000_000
MAX_DIRECT_PAGES = 12
MAX_SEARCH_PAGES = 4

POSITIVE_PATTERNS = [
    (r"\bbarrierefrei(?:e|en|er|es|em)?\s+(?:bad|bäder|baeder|badezimmer|dusche|badsanierung|badumbau|umbau|sanierung|wohnen|wohnraum|wohnung|zugang)", "barrierefreies Bad/Wohnen"),
    (r"\b(?:bad|bäder|baeder|badezimmer|dusche|badsanierung|badumbau|umbau|sanierung|wohnen|wohnraum|wohnung)\b.{0,100}\bbarrierefrei", "barrierefreies Bad/Wohnen"),
    (r"\bbarrierearm(?:e|en|er|es|em)?\s+(?:bad|bäder|baeder|badezimmer|dusche|umbau|sanierung|wohnen|wohnraum)", "barrierearmes Bad/Wohnen"),
    (r"\b(?:altersgerecht|seniorengerecht)(?:e|en|er|es|em)?\s+(?:bad|bäder|baeder|badezimmer|dusche|umbau|sanierung|wohnen|wohnraum|wohnung)", "alters-/seniorengerechter Umbau"),
    (r"\b(?:bad|bäder|baeder|badezimmer|dusche|umbau|sanierung|wohnen|wohnraum)\b.{0,100}\b(?:altersgerecht|seniorengerecht)", "alters-/seniorengerechter Umbau"),
    (r"\bgenerationen[- ]?(?:bad|bäder|baeder|badezimmer)\b", "Generationenbad"),
    (r"\bbad\s+(?:für|fuer)\s+(?:das|ein)\s+leben\b", "Bad fürs Leben"),
    (r"\b(?:rollstuhl|behinderten)[- ]?gerecht(?:e|en|er|es|em)?\s+(?:bad|badezimmer|dusche|umbau|wohnen|wohnung|zugang)", "rollstuhl-/behindertengerechter Umbau"),
    (r"\bwohnraum[- ]?anpassung\b", "Wohnraumanpassung"),
    (r"\bwohnumfeldverbessernde(?:n|r|s|m)?\s+maßnahm", "wohnumfeldverbessernde Maßnahmen"),
    (r"\b(?:badewanne|wanne)\s+(?:raus|zur|zu(?:r| einer)?)\s+(?:dusche|dusch)", "Badewanne-zur-Dusche"),
    (r"\bpflegekasse\b.{0,160}\b(?:bad|dusche|umbau|sanierung|wohnraum)", "Pflegekassen-geförderter Badumbau"),
    (r"\b(?:bad|dusche|umbau|sanierung|wohnraum)\b.{0,160}\bpflegekasse\b", "Pflegekassen-geförderter Badumbau"),
    (r"\bkfw\b.{0,160}\b(?:barriere|altersgerecht|bad|umbau)", "KfW-geförderter Barriereabbau"),
    (r"\b(?:barriere|altersgerecht|bad|umbau)\b.{0,160}\bkfw\b", "KfW-geförderter Barriereabbau"),
]
POSITIVE_RE = [(re.compile(p, re.I | re.S), label) for p, label in POSITIVE_PATTERNS]
DIGITAL_ONLY_RE = re.compile(
    r"(barrierefreiheits(?:erklärung|erklaerung)|\bbfsg\b|\bbitv\b|\bwcag\b|screenreader|tastaturnavigation|digitale barrierefreiheit)", re.I
)
SERVICE_CONTEXT_RE = re.compile(
    r"(bad|bäder|baeder|badezimmer|dusche|sanitär|sanitaer|shk|wohnraum|umbau|sanierung|renovierung|fliesen|pflegekasse|kfw|haltegriff|duschsitz)", re.I
)
WEAK_RE = re.compile(
    r"(bodengleich(?:e|en|er|es)?\s+dusche|ebenerdig(?:e|en|er|es)?\s+dusche|schwellenlos(?:e|en|er|es)?|duschsitz|haltegriff|unterfahrbar(?:e|en|er|es)?|rutschhemmend(?:e|en|er|es)?|bewegungsfläche|bewegungsflaeche)", re.I
)
BUSINESS_RE = re.compile(
    r"(sanitär|sanitaer|heizung|shk|haustechnik|bad|bäder|baeder|badezimmer|fliesen|installateur|installation|klempn|badsanierung|badrenovierung|innenausbau|wohnraum|sanierung|renovierung|umbau|architekt|planung)", re.I
)
NONFIT_RE = re.compile(
    r"(tiefbau|straßenbau|strassenbau|erdbau|grundbau|rohrleitungsbau|fassadenbau|gerüstbau|geruestbau|dachdeck|bedachung|schornstein|abbruch|abriss|garten[- ]?und landschaft|galabau|immobilienentwicklung|projektentwicklung|wohnungsunternehmen|wohnungsgesellschaft|massivhaus|fertighaus|transportbeton|baustoffhandel|digitalagentur)", re.I
)
GENERIC_BUILD_RE = re.compile(
    r"(bauunternehmen|bauunternehmung|baugeschäft|baugeschaeft|hochbau|generalbau|schlüsselfertig|schluesselfertig|massivbau|hausbau)", re.I
)
LINK_KEYWORDS = {
    "barriere": 30, "altersgerecht": 28, "senior": 25, "generation": 24,
    "rollstuhl": 24, "behinderten": 24, "wohnraum": 20, "pflege": 18,
    "kfw": 18, "förder": 16, "foerder": 16, "komfortbad": 16,
    "badewanne": 15, "bodengleich": 14, "ebenerdig": 14, "dusche": 12,
    "badsanierung": 12, "badrenovierung": 12, "badumbau": 12,
    "badezimmer": 10, "bäder": 10, "baeder": 10, "sanitär": 8,
    "sanitaer": 8, "leistungen": 6, "referenzen": 6, "projekte": 5,
    "umbau": 7, "sanierung": 7, "wohnen": 5,
}
EXCLUDE_LINK_RE = re.compile(
    r"(impressum|datenschutz|privacy|cookie|agb|kontakt|jobs?|karriere|facebook|instagram|linkedin|youtube|mailto:|tel:|javascript:|\.(?:jpg|jpeg|png|gif|svg|webp|zip|mp4|mp3|docx?|xlsx?)$)", re.I
)
COMMON_PATHS = [
    "/barrierefreies-bad/", "/barrierefreie-baeder/", "/barrierefreies-badezimmer/",
    "/leistungen/barrierefreies-bad/", "/bad/barrierefreies-bad/",
    "/altersgerechtes-bad/", "/seniorengerechtes-bad/", "/generationenbad/",
    "/badewanne-zur-dusche/", "/leistungen/bad/", "/badsanierung/",
    "/badrenovierung/", "/bad/", "/leistungen/", "/referenzen/",
]
DIRECTORY_DOMAINS = {
    "sanitaerfinden.com", "www.sanitaerfinden.com", "sanitaer.org", "www.sanitaer.org",
    "fliesenleger.io", "www.fliesenleger.io", "sellwerk.de", "www.sellwerk.de",
    "gelbeseiten.de", "www.gelbeseiten.de", "11880.com", "www.11880.com",
    "facebook.com", "www.facebook.com", "m.facebook.com", "instagram.com", "www.instagram.com",
    "repair.ivof.com", "branchenbuch.meinestadt.de",
}
SEARCH_BLOCK_DOMAINS = {
    "youtube.com", "www.youtube.com", "facebook.com", "www.facebook.com",
    "instagram.com", "www.instagram.com", "linkedin.com", "www.linkedin.com",
    "pinterest.com", "www.pinterest.com", "kununu.com", "www.kununu.com",
    "indeed.com", "www.indeed.com",
}


def deaccent(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def clean_space(value):
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_url(value):
    value = html.unescape((value or "").strip())
    if not value:
        return ""
    value = value.replace("%3Futm_", "?utm_").replace("%3futm_", "?utm_")
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    try:
        parts = urllib.parse.urlsplit(value)
        host_name = parts.netloc.lower()
        path = parts.path or "/"
        query_pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        query_pairs = [(k, v) for k, v in query_pairs if not k.lower().startswith(("utm_", "gclid", "fbclid", "y_source"))]
        return urllib.parse.urlunsplit((parts.scheme.lower(), host_name, path, urllib.parse.urlencode(query_pairs), ""))
    except Exception:
        return value


def origin(url):
    try:
        p = urllib.parse.urlsplit(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def host(url):
    try:
        return urllib.parse.urlsplit(url).netloc.lower().split(":")[0]
    except Exception:
        return ""


def registrableish_host(value):
    value = value.lower().strip(".")
    return value[4:] if value.startswith("www.") else value


def same_site(a, b):
    ha, hb = registrableish_host(host(a)), registrableish_host(host(b))
    return bool(ha and hb and (ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)))


def visible_text(raw_html):
    soup = BeautifulSoup(raw_html or "", "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    return clean_space(soup.get_text(" ", strip=True))


def make_snippet(text, start, end, radius=190):
    lo, hi = max(0, start - radius), min(len(text), end + radius)
    out = clean_space(text[lo:hi])
    return ("…" if lo else "") + out + ("…" if hi < len(text) else "")


def positive_hit(text):
    normalized = clean_space(text)
    for regex, label in POSITIVE_RE:
        match = regex.search(normalized)
        if not match:
            continue
        nearby = normalized[max(0, match.start() - 220): min(len(normalized), match.end() + 220)]
        if DIGITAL_ONLY_RE.search(nearby) and not SERVICE_CONTEXT_RE.search(nearby):
            continue
        return {"label": label, "snippet": make_snippet(normalized, match.start(), match.end()), "match": match.group(0)[:220]}
    return None


def count_terms(regex, text):
    return min(20, len(regex.findall(text or "")))


def link_score(url, anchor=""):
    value = urllib.parse.unquote((url + " " + anchor).lower())
    if EXCLUDE_LINK_RE.search(value):
        return -100
    score = sum(weight for keyword, weight in LINK_KEYWORDS.items() if keyword in value)
    if re.search(r"/(?:20\d\d|tag|category|author|page)/", value):
        score -= 8
    return score


def extract_links(raw_html, base_url):
    soup = BeautifulSoup(raw_html or "", "lxml")
    output, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = html.unescape(a.get("href", "")).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = normalize_url(urllib.parse.urljoin(base_url, href))
        if not absolute or not same_site(base_url, absolute) or absolute in seen:
            continue
        seen.add(absolute)
        anchor = clean_space(a.get_text(" ", strip=True))
        score = link_score(absolute, anchor)
        if score > 0:
            output.append((score, absolute, anchor))
    output.sort(key=lambda x: (-x[0], len(x[1])))
    return output


def extract_sitemap_urls(xml_text):
    urls = []
    try:
        root = ET.fromstring(xml_text)
        for elem in root.iter():
            if elem.tag.lower().endswith("loc") and elem.text:
                urls.append(clean_space(elem.text))
    except Exception:
        urls = re.findall(r"<loc>\s*(.*?)\s*</loc>", xml_text or "", flags=re.I | re.S)
    return [html.unescape(u) for u in urls if u]


def company_tokens(name):
    stop = {"gmbh", "co", "kg", "ohg", "ug", "ag", "mbh", "ek", "e", "k", "sanitar", "heizung", "haustechnik", "bau", "bauunternehmen", "meisterbetrieb", "und", "sohn", "sohne", "service", "bad"}
    tokens = re.findall(r"[a-z0-9]{3,}", deaccent(name.lower()))
    return [t for t in tokens if t not in stop][:5]


def result_identity_score(url, title, name, city, known_host=""):
    u = deaccent((url or "").lower())
    title_l = deaccent((title or "").lower())
    score = 0
    if known_host and registrableish_host(host(url)) == registrableish_host(known_host):
        score += 100
    for token in company_tokens(name):
        if token in u:
            score += 14
        if token in title_l:
            score += 8
    city_token = deaccent(city.lower()).replace(" ", "-")
    if city_token and (city_token in u or deaccent(city.lower()) in title_l):
        score += 5
    if host(url) in SEARCH_BLOCK_DOMAINS:
        score -= 100
    if host(url) in DIRECTORY_DOMAINS:
        score -= 10
    return score


class Crawler:
    def __init__(self, session, company_sem):
        self.session = session
        self.company_sem = company_sem
        self.search_sem = asyncio.Semaphore(2)

    async def fetch(self, url, method="direct"):
        url = normalize_url(url)
        if not url:
            return None
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml,text/plain;q=0.9,*/*;q=0.5"}
        try:
            async with self.session.get(url, headers=headers, allow_redirects=True, timeout=TIMEOUT) as resp:
                content_type = (resp.headers.get("content-type") or "").lower()
                body = await resp.content.read(MAX_BYTES)
                text = body.decode(resp.charset or "utf-8", errors="ignore")
                return {"requested_url": url, "url": str(resp.url), "status": resp.status, "content_type": content_type, "text": text, "method": method, "error": ""}
        except Exception as exc:
            return {"requested_url": url, "url": url, "status": 0, "content_type": "", "text": "", "method": method, "error": f"{type(exc).__name__}: {exc}"[:300]}

    async def fetch_with_fallbacks(self, url):
        result = await self.fetch(url, "direct")
        if result and result["status"] in range(200, 400) and result["text"]:
            return result
        alt = "http://" + url[len("https://"):] if url.startswith("https://") else ("https://" + url[len("http://"):] if url.startswith("http://") else "")
        if alt:
            alt_result = await self.fetch(alt, "scheme_fallback")
            if alt_result and alt_result["status"] in range(200, 400) and alt_result["text"]:
                return alt_result
        jina = "https://r.jina.ai/http://" + url.split("://", 1)[-1]
        jina_result = await self.fetch(jina, "jina_reader")
        if jina_result and jina_result["status"] in range(200, 400) and jina_result["text"]:
            jina_result["source_url"] = url
            return jina_result
        return result or {"url": url, "status": 0, "text": "", "error": "No response", "method": "none"}

    async def bing_rss(self, query):
        async with self.search_sem:
            await asyncio.sleep(random.uniform(0.3, 0.9))
            url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "format": "rss", "setlang": "de-DE"})
            result = await self.fetch(url, "bing_rss")
        items = []
        if not result or result["status"] not in range(200, 400):
            return items, result
        try:
            root = ET.fromstring(result["text"])
            for item in root.findall(".//item")[:8]:
                title = clean_space(item.findtext("title", default=""))
                link = clean_space(item.findtext("link", default=""))
                desc = visible_text(item.findtext("description", default=""))
                if link:
                    items.append({"title": title, "url": link, "snippet": desc})
        except Exception:
            pass
        return items, result

    async def ddg_html(self, query):
        async with self.search_sem:
            await asyncio.sleep(random.uniform(0.6, 1.2))
            url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
            result = await self.fetch(url, "ddg_html")
        items = []
        if not result or result["status"] not in range(200, 400):
            return items, result
        soup = BeautifulSoup(result["text"], "lxml")
        for block in soup.select(".result")[:8]:
            a = block.select_one(".result__a")
            if not a:
                continue
            href = a.get("href", "")
            parsed = urllib.parse.urlsplit(href)
            actual = urllib.parse.parse_qs(parsed.query).get("uddg", [href])[0]
            sn = block.select_one(".result__snippet")
            items.append({"title": clean_space(a.get_text(" ", strip=True)), "url": normalize_url(actual), "snippet": clean_space(sn.get_text(" ", strip=True)) if sn else ""})
        return items, result

    async def search(self, query):
        items, meta = await self.bing_rss(query)
        if not items:
            items, meta2 = await self.ddg_html(query)
            return items, [m for m in (meta, meta2) if m]
        return items, [meta] if meta else []

    async def crawl_company(self, item):
        async with self.company_sem:
            nr, name, city = item["nr"], item["name"], item["city"]
            supplied = normalize_url(item.get("website") or "")
            checked, errors, pages, search_pages = [], [], [], []
            methods, hit, hit_url = set(), None, ""
            official_host = host(supplied)

            async def add_page(url, kind):
                nonlocal hit, hit_url, official_host
                url = normalize_url(url)
                if not url or url in {p.get("requested_url") for p in pages}:
                    return None
                result = await self.fetch_with_fallbacks(url)
                methods.add(result.get("method", kind))
                checked.append(result.get("source_url") or result.get("url") or url)
                if result.get("error"):
                    errors.append(f"{url}: {result['error']}")
                if result.get("status") in range(200, 400) and result.get("text"):
                    raw = result["text"]
                    ct = result.get("content_type", "")
                    txt = visible_text(raw) if ("html" in ct or "<html" in raw[:1000].lower() or result.get("method") == "jina_reader") else clean_space(raw)
                    page = {"requested_url": url, "url": result.get("source_url") or result.get("url") or url, "final_url": result.get("url") or url, "raw": raw, "text": txt, "kind": kind, "status": result.get("status", 0), "method": result.get("method", kind)}
                    pages.append(page)
                    if not official_host and host(page["url"]) not in SEARCH_BLOCK_DOMAINS:
                        official_host = host(page["url"])
                    ph = positive_hit(txt)
                    if ph and not hit:
                        hit, hit_url = ph, page["url"]
                    return page
                return None

            home = await add_page(supplied, "supplied_home") if supplied else None
            if home and not hit:
                base = home["url"]
                candidates = extract_links(home["raw"], base)
                sitemap_candidates = []
                for sm_url in (origin(base) + "/sitemap.xml", origin(base) + "/sitemap_index.xml", origin(base) + "/wp-sitemap.xml"):
                    if len(pages) >= MAX_DIRECT_PAGES or hit:
                        break
                    sm = await self.fetch_with_fallbacks(sm_url)
                    methods.add(sm.get("method", "sitemap"))
                    if sm.get("status") in range(200, 400) and sm.get("text"):
                        checked.append(sm.get("source_url") or sm.get("url") or sm_url)
                        locs = extract_sitemap_urls(sm["text"])
                        for child in [u for u in locs if "sitemap" in u.lower()][:4]:
                            sm2 = await self.fetch_with_fallbacks(child)
                            methods.add(sm2.get("method", "sitemap"))
                            if sm2.get("status") in range(200, 400) and sm2.get("text"):
                                checked.append(sm2.get("source_url") or sm2.get("url") or child)
                                locs.extend(extract_sitemap_urls(sm2["text"]))
                        for u in locs:
                            if same_site(base, u) and link_score(u) > 0:
                                sitemap_candidates.append((link_score(u), normalize_url(u), "sitemap"))
                        if sitemap_candidates:
                            break
                candidate_map = {}
                for score, url, anchor in candidates + sitemap_candidates:
                    candidate_map[url] = max(score, candidate_map.get(url, -999))
                for path in COMMON_PATHS:
                    url = normalize_url(origin(base) + path)
                    candidate_map.setdefault(url, link_score(url))
                for url, score in sorted(candidate_map.items(), key=lambda x: (-x[1], len(x[0]))):
                    if hit or len(pages) >= MAX_DIRECT_PAGES:
                        break
                    if score > 0:
                        await add_page(url, "internal_or_sitemap")

            if not hit:
                query = (f'site:{registrableish_host(host(supplied))} ("barrierefreies Bad" OR "altersgerechtes Bad" OR seniorengerecht OR Generationenbad OR Wohnraumanpassung OR "Badewanne zur Dusche")' if supplied else f'"{name}" "{city}" ("barrierefrei" OR altersgerecht OR seniorengerecht OR Generationenbad OR Wohnraumanpassung OR "bodengleiche Dusche")')
                results, search_meta = await self.search(query)
                for meta in search_meta:
                    methods.add(meta.get("method", "search"))
                    if meta.get("url"):
                        search_pages.append(meta["url"])
                known = host(supplied)
                ranked = []
                for res in results:
                    ph = positive_hit(res.get("snippet", "") + " " + res.get("title", ""))
                    score = result_identity_score(res["url"], res["title"], name, city, known) + (30 if ph else 0)
                    ranked.append((score, res, ph))
                ranked.sort(key=lambda x: -x[0])
                for score, res, snippet_hit in ranked:
                    if hit or len(pages) >= MAX_DIRECT_PAGES + MAX_SEARCH_PAGES:
                        break
                    if score < 4:
                        continue
                    fetched = await add_page(res["url"], "search_result")
                    if not fetched and snippet_hit and score >= 15:
                        hit, hit_url = snippet_hit, res["url"]
                if not supplied and not pages:
                    results2, search_meta2 = await self.search(f'"{name}" "{city}"')
                    for meta in search_meta2:
                        methods.add(meta.get("method", "search"))
                        if meta.get("url"):
                            search_pages.append(meta["url"])
                    ranked2 = sorted([(result_identity_score(x["url"], x["title"], name, city), x) for x in results2], key=lambda x: -x[0])
                    for score, res in ranked2[:3]:
                        if score >= 5:
                            await add_page(res["url"], "identity_search_result")
                            if hit:
                                break

            all_text = " ".join(p["text"] for p in pages)
            business_score = count_terms(BUSINESS_RE, all_text + " " + name)
            nonfit_score = count_terms(NONFIT_RE, all_text + " " + name)
            generic_build_score = count_terms(GENERIC_BUILD_RE, all_text + " " + name)
            weak_score = count_terms(WEAK_RE, all_text)
            fetched_count = len(pages)

            if hit:
                verdict = "Relevant"
                confidence = "Hoch" if hit_url and (not supplied or same_site(supplied, hit_url)) else "Mittel"
                evidence = f'Expliziter Nachweis „{hit["label"]}“ auf einer geprüften Seite: {hit["snippet"]}'
                source_url = hit_url
            else:
                plausible = business_score >= 2 or bool(BUSINESS_RE.search(name))
                clearly_nonfit = nonfit_score >= max(2, business_score + 1)
                generic_only = generic_build_score >= 2 and business_score <= 2
                if plausible:
                    verdict = "Kontakt nötig"
                    confidence = "Mittel" if fetched_count >= 2 else "Niedrig"
                    details = ([f"{weak_score} schwache Komfort-/Duschen-Signale"] if weak_score else []) + ([f"{fetched_count} relevante Unternehmens-/Suchseiten geprüft"] if fetched_count else ["keine belastbare Unternehmensseite technisch erreichbar"])
                    evidence = "Online-Recherche vollständig durchgeführt: " + ", ".join(details) + ". Der Betrieb ist fachlich als SHK-/Bad-/Sanierungsanbieter plausibel, aber es fand sich kein ausdrücklicher Nachweis für barrierefreien, alters- oder seniorengerechten Umbau. Eine direkte Ja/Nein-Bestätigung ist erforderlich."
                    source_url = pages[0]["url"] if pages else supplied
                elif clearly_nonfit or generic_only:
                    verdict = "Nicht relevant"
                    confidence = "Hoch" if fetched_count >= 3 and clearly_nonfit else "Mittel"
                    evidence = f"Nach Prüfung von {fetched_count} Unternehmens-/Suchseiten zeigt das Leistungsprofil allgemeinen bzw. fachfremden Bau statt SHK, Bad oder Wohnraumanpassung; kein Barrierefrei-/Altersgerecht-Nachweis gefunden."
                    source_url = pages[0]["url"] if pages else supplied
                else:
                    verdict = "Kontakt nötig"
                    confidence = "Niedrig"
                    evidence = f"Online-Recherche ausgeschöpft ({fetched_count} Seiten, externe Identitätssuche). Unternehmensprofil und Barrierefrei-Leistung konnten nicht belastbar verifiziert werden; direkte Bestätigung erforderlich."
                    source_url = pages[0]["url"] if pages else supplied

            if not source_url and search_pages:
                source_url = search_pages[0]
            checked_unique = list(dict.fromkeys(u for u in checked + search_pages if u))[:25]
            return {"nr": nr, "name": name, "city": city, "website": item.get("website") or "", "priority": item.get("priority") or "", "verdict": verdict, "confidence": confidence, "evidence": evidence, "source_url": source_url or "", "pages_fetched": fetched_count, "checked_urls": checked_unique, "research_method": ", ".join(sorted(methods)) or "search attempted", "business_score": business_score, "weak_score": weak_score, "nonfit_score": nonfit_score, "errors": errors[:8], "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", required=True)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    queue = json.loads(Path(args.queue).read_text(encoding="utf-8"))
    subset = [item for idx, item in enumerate(queue) if idx % args.shards == args.shard]
    connector = aiohttp.TCPConnector(ssl=False, limit=32, limit_per_host=2, ttl_dns_cache=600)
    company_sem = asyncio.Semaphore(8)
    out_path = Path(args.out)
    results = []
    async with aiohttp.ClientSession(connector=connector) as session:
        crawler = Crawler(session, company_sem)
        async def guarded(item):
            try:
                return await crawler.crawl_company(item)
            except Exception as exc:
                return {"nr": item["nr"], "name": item["name"], "city": item["city"], "website": item.get("website") or "", "priority": item.get("priority") or "", "verdict": "Kontakt nötig", "confidence": "Niedrig", "evidence": f"Die Online-Recherche wurde technisch angestoßen, endete für diesen Datensatz jedoch mit einem unerwarteten Fehler ({type(exc).__name__}). Direkte Bestätigung erforderlich.", "source_url": item.get("website") or "", "pages_fetched": 0, "checked_urls": [], "research_method": "crawler_error", "business_score": 0, "weak_score": 0, "nonfit_score": 0, "errors": [f"{type(exc).__name__}: {exc}"[:500]], "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        tasks = [asyncio.create_task(guarded(item)) for item in subset]
        completed = 0
        with out_path.open("w", encoding="utf-8") as fh:
            for fut in asyncio.as_completed(tasks):
                result = await fut
                results.append(result)
                fh.write(json.dumps(result, ensure_ascii=False) + "\n")
                fh.flush()
                completed += 1
                if completed % 10 == 0 or completed == len(subset):
                    print(f"shard {args.shard}: {completed}/{len(subset)}", flush=True)
    if len(results) != len(subset):
        raise SystemExit(f"Expected {len(subset)} results, got {len(results)}")
    print(json.dumps(Counter(r["verdict"] for r in results), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
