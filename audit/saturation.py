#!/usr/bin/env python3
"""Third-pass saturation challenge for still-negative Barrierefrei decisions.

This pass does not trust a missing keyword on the current website. It searches
URL indexes, archives, alternate official domains and long-tail formulations.
Every item ends with the same binary directory decision.
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

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36 BarrierefreiProfisSaturation/1.0"
TIMEOUT = aiohttp.ClientTimeout(total=32, connect=11, sock_read=22)
MAX_BYTES = 3_000_000
MAX_FETCH = 55

POSITIVE_CONTROLS = {109, 161, 702, 1946}
NEGATIVE_CONTROLS = {969, 1062, 2579, 3250}
BLOCK = {"youtube.com", "facebook.com", "instagram.com", "linkedin.com", "pinterest.com", "kununu.com", "indeed.com", "xing.com"}
TRUSTED = ("die-badgestalter", "badundheizung", "sanitaer.org", "pflegehilfe", "dincertco", "handwerkskammer", "innung", "nullbarriere", "barrierefrei-leben")

PHRASES = [
    '"Bad ohne Barrieren"', '"barrierearmes Badezimmer"', '"barrierereduziertes Bad"',
    '"Bad im Alter"', '"Seniorenbad"', '"Mehrgenerationenbad"',
    '"Bad für jede Lebensphase"', '"Bad für alle Lebenslagen"', '"zukunftssicheres Bad"',
    '"Wanne raus Dusche rein"', '"Badewanne raus Dusche rein"', '"Dusche statt Badewanne"',
    '"Badewannentür"', '"Duschsitz" Haltegriff', '"unterfahrbarer Waschtisch"',
    '"Pflegekassenzuschuss" Bad', '"Pflegegrad" Badumbau', '"§ 40 SGB XI"',
    '"wohnumfeldverbessernde Maßnahme"', '"selbstständig im Bad"',
    '"rollstuhlgerechtes Bad"', '"behindertengerechtes Bad"', '"DIN 18040"',
]
URL_TERMS = ["barriere", "senior", "alter", "generation", "pflege", "wanne", "dusche", "rollstuhl", "din-18040", "komfortbad"]

DIGITAL = re.compile(r"(barrierefreiheits(?:erklärung|erklaerung)|\bbfsg\b|\bbitv\b|\bwcag\b|screenreader|tastaturnavigation|digitale barrierefreiheit)", re.I)
SERVICE = re.compile(r"(bad|bäder|baeder|badezimmer|dusche|sanitär|sanitaer|wohnraum|umbau|sanierung|fliesen|lift|rampe)", re.I)
ACTION = re.compile(r"(wir|leistung|bieten|planen|realisieren|bauen|umbau|sanieren|montage|installieren|ausführen|ausfuehren)", re.I)
HIT_PATTERNS = [
    (r"\b(?:bad|bäder|baeder|badezimmer|dusche|wohnraum|umbau|sanierung)\b.{0,180}\b(?:barrierefrei|barrierearm|barrierereduziert|altersgerecht|seniorengerecht|generationengerecht)\b", "barriere-/altersgerechter Umbau"),
    (r"\b(?:barrierefrei|barrierearm|barrierereduziert|altersgerecht|seniorengerecht|generationengerecht)\b.{0,180}\b(?:bad|bäder|baeder|badezimmer|dusche|wohnraum|umbau|sanierung)\b", "barriere-/altersgerechter Umbau"),
    (r"\b(?:seniorenbad|mehrgenerationenbad|generationenbad|komfortbad|bad\s+im\s+alter)\b", "Senioren-/Generationenbad"),
    (r"\b(?:wanne|badewanne)\s+(?:raus|weg|zur|zu(?:r| einer)?)\s+(?:dusche|dusch)\b", "Wanne-zur-Dusche"),
    (r"\b(?:dusche\s+statt\s+(?:wanne|badewanne)|badewannentür|badewannentuer)\b", "Wannenumbau"),
    (r"\b(?:pflegekassenzuschuss|pflegekasse|pflegegrad|§\s*40\s*sgb\s*xi)\b.{0,220}\b(?:bad|dusche|umbau|wohnraum)\b", "Pflegekassen-Umbau"),
    (r"\b(?:wohnraum|wohnumfeld)[- ]?(?:anpassung|verbesserung)\b", "Wohnraumanpassung"),
    (r"\b(?:rollstuhl|behinderten)[- ]?gerecht(?:e|en|er|es|em)?\b.{0,130}\b(?:bad|dusche|wohnen|umbau|zugang)\b", "rollstuhl-/behindertengerechter Umbau"),
    (r"\b(?:treppenlift|plattformlift|hublift|homelift|rollstuhlrampe|auffahrrampe)\b", "Lift-/Rampenlösung"),
]
HIT_RE = [(re.compile(p, re.I | re.S), label) for p, label in HIT_PATTERNS]
FEATURE = re.compile(r"(bodengleich(?:e|en)?\s+dusche|ebenerdig(?:e|en)?\s+dusche|duschsitz|haltegriff|unterfahrbar(?:e|en)?|bewegungsfläche|bewegungsflaeche|rutschhemmend(?:e|en)?)", re.I)
AGE = re.compile(r"(alter|senior|pflege|mobilität|mobilitaet|rollstuhl|generation|selbstständig|selbststaendig)", re.I)


def clean(v: str) -> str:
    return re.sub(r"\s+", " ", v or "").strip()


def norm(v: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", clean(v).lower()) if not unicodedata.combining(c))


def nurl(v: str) -> str:
    v = html.unescape((v or "").strip())
    if not v:
        return ""
    if not re.match(r"^https?://", v, re.I):
        v = "https://" + v
    try:
        p = urllib.parse.urlsplit(v)
        return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))
    except Exception:
        return v


def host(v: str) -> str:
    try:
        h = urllib.parse.urlsplit(v).netloc.lower().split(":")[0]
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def visible(raw: str) -> str:
    soup = BeautifulSoup(raw or "", "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    return clean(soup.get_text(" ", strip=True))


def snippet(text: str, start: int, end: int, radius: int = 320) -> str:
    lo, hi = max(0, start-radius), min(len(text), end+radius)
    return ("…" if lo else "") + clean(text[lo:hi]) + ("…" if hi < len(text) else "")


def hit(text: str) -> dict[str, str] | None:
    value = clean(text)
    for regex, label in HIT_RE:
        m = regex.search(value)
        if not m:
            continue
        around = value[max(0, m.start()-420):min(len(value), m.end()+420)]
        if DIGITAL.search(around) and not SERVICE.search(around):
            continue
        if not ACTION.search(around) and re.search(r"(projekt|gebäude|gebaeude|hotel|praxis|veranstaltung)", around, re.I):
            continue
        return {"label": label, "snippet": snippet(value, m.start(), m.end())}
    fs = list(FEATURE.finditer(value))
    if len(fs) >= 3 and AGE.search(value) and ACTION.search(value):
        m = fs[0]
        return {"label": "altersgerechtes Ausstattungspaket", "snippet": snippet(value, m.start(), m.end(), 400)}
    return None


def tokens(name: str) -> list[str]:
    stop = {"gmbh","co","kg","ohg","ug","ag","mbh","sanitar","heizung","haustechnik","bau","meisterbetrieb","service","bad","technik","und"}
    return [t for t in re.findall(r"[a-z0-9]{3,}", norm(name)) if t not in stop][:7]


def identity(url: str, title: str, text: str, name: str, city: str, domains: set[str]) -> int:
    u, t, b = norm(url), norm(title), norm(text[:5000])
    score = 110 if host(url) in domains else 0
    for tok in tokens(name):
        score += 18 if tok in u else 0
        score += 12 if tok in t else 0
        score += 4 if tok in b else 0
    c = norm(city)
    score += 15 if c and (c in u or c in t or c in b) else 0
    score -= 150 if host(url) in BLOCK else 0
    return score


def pdftext(data: bytes) -> str:
    try:
        return clean(" ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages[:80]))
    except Exception:
        return ""


@dataclass
class State:
    row: dict[str, Any]
    checked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    query_attempts: int = 0
    provider_success: set[str] = field(default_factory=set)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    archive_attempted: bool = False
    url_index_attempted: bool = False

    @property
    def name(self): return self.row.get("name", "")
    @property
    def city(self): return self.row.get("city", "")
    @property
    def domains(self):
        urls = [self.row.get("website", ""), self.row.get("source_url", "")]
        for c in self.row.get("positive_candidates") or []:
            urls.append(c.get("url", ""))
        return {host(x) for x in urls if host(x)}


class Saturator:
    def __init__(self, session: aiohttp.ClientSession, sem: asyncio.Semaphore):
        self.session = session
        self.company_sem = sem
        self.fetch_sem = asyncio.Semaphore(35)
        self.search_sem = asyncio.Semaphore(5)
        self.cache = {}

    async def fetch(self, url: str, method: str, headers=None):
        url = nurl(url)
        key = (url, json.dumps(headers or {}, sort_keys=True))
        if key in self.cache:
            return dict(self.cache[key])
        async with self.fetch_sem:
            await asyncio.sleep(random.uniform(.02, .1))
            try:
                h = {"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=.9,en;q=.5"}
                h.update(headers or {})
                async with self.session.get(url, allow_redirects=True, timeout=TIMEOUT, headers=h) as r:
                    data = await r.content.read(MAX_BYTES)
                    out = {"url": str(r.url), "requested": url, "status": r.status, "ct": (r.headers.get("content-type") or "").lower(), "text": data.decode(r.charset or "utf-8", errors="ignore"), "data": data, "error": "", "method": method}
            except Exception as exc:
                out = {"url": url, "requested": url, "status": 0, "ct": "", "text": "", "data": b"", "error": f"{type(exc).__name__}: {exc}"[:500], "method": method}
        self.cache[key] = dict(out)
        return out

    async def fallback(self, url: str, method: str):
        url = nurl(url)
        variants = [url]
        if url.startswith("https://"): variants.append("http://" + url[8:])
        elif url.startswith("http://"): variants.append("https://" + url[7:])
        for v in dict.fromkeys(variants):
            r = await self.fetch(v, method)
            if r["status"] in range(200,400) and (r["text"] or r["data"]): return r
        j = await self.fetch("https://r.jina.ai/http://" + url.split("://",1)[-1], method+":jina")
        if j["status"] in range(200,400) and j["text"]:
            j["source"] = url
            return j
        return j

    async def search_bing(self, q):
        url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q":q,"format":"rss","setlang":"de-DE"})
        r = await self.fetch(url,"bing")
        out=[]
        if r["status"] in range(200,400):
            try:
                root=ET.fromstring(r["text"])
                for it in root.findall(".//item")[:12]:
                    link=clean(it.findtext("link",default=""))
                    if link: out.append({"url":nurl(link),"title":clean(it.findtext("title",default="")),"snippet":visible(it.findtext("description",default="")),"provider":"bing"})
            except Exception: pass
        return out

    async def search_ddg(self,q):
        r=await self.fetch("https://html.duckduckgo.com/html/?"+urllib.parse.urlencode({"q":q}),"ddg")
        out=[]
        if r["status"] in range(200,400):
            soup=BeautifulSoup(r["text"],"lxml")
            for b in soup.select(".result")[:12]:
                a=b.select_one(".result__a")
                if not a: continue
                href=a.get("href",""); actual=urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("uddg",[href])[0]
                sn=b.select_one(".result__snippet")
                out.append({"url":nurl(actual),"title":clean(a.get_text(" ",strip=True)),"snippet":clean(sn.get_text(" ",strip=True)) if sn else "","provider":"ddg"})
        return out

    async def search_jina(self,q):
        r=await self.fetch("https://s.jina.ai/"+urllib.parse.quote(q,safe=""),"jina")
        out=[]
        if r["status"] in range(200,400):
            for title,url in re.findall(r"\[([^\]]{2,180})\]\((https?://[^)\s]+)\)",r["text"]):
                if host(url) not in BLOCK: out.append({"url":nurl(url),"title":clean(title),"snippet":"","provider":"jina"})
                if len(out)>=12: break
        return out

    async def search_mojeek(self,q):
        r=await self.fetch("https://www.mojeek.com/search?"+urllib.parse.urlencode({"q":q}),"mojeek")
        out=[]
        if r["status"] in range(200,400):
            soup=BeautifulSoup(r["text"],"lxml")
            for b in soup.select("li.result")[:12]:
                a=b.select_one("a.ob") or b.select_one("h2 a")
                if not a: continue
                sn=b.select_one("p.s")
                out.append({"url":nurl(a.get("href","")),"title":clean(a.get_text(" ",strip=True)),"snippet":clean(sn.get_text(" ",strip=True)) if sn else "","provider":"mojeek"})
        return out

    async def search(self,q,state):
        state.query_attempts += 4
        batches=await asyncio.gather(self.search_bing(q),self.search_ddg(q),self.search_jina(q),self.search_mojeek(q))
        out=[]; seen=set()
        for batch in batches:
            if batch: state.provider_success.add(batch[0]["provider"])
            for x in batch:
                if x["url"] and x["url"] not in seen and host(x["url"]) not in BLOCK:
                    seen.add(x["url"]); out.append(x)
        return out

    async def evaluate(self,x,state,phase):
        pre=identity(x["url"],x.get("title",""),x.get("snippet",""),state.name,state.city,state.domains)
        sh=hit(x.get("title","")+" "+x.get("snippet",""))
        if pre<5 and not (sh and pre>=0): return
        r=await self.fallback(x["url"],phase); state.checked.append(r.get("source") or r.get("url") or x["url"])
        if r.get("error"): state.errors.append(f"{x['url']}: {r['error']}")
        if r["status"] in range(200,400) and (r["text"] or r["data"]):
            source=r.get("source") or r.get("url") or x["url"]
            if "pdf" in r.get("ct","") or source.lower().endswith(".pdf"): txt=pdftext(r.get("data") or b"")
            else: txt=visible(r["text"]) if ("html" in r.get("ct","") or "<html" in r["text"][:1000].lower() or ":jina" in r.get("method","")) else clean(r["text"])
            ident=identity(source,x.get("title",""),txt,state.name,state.city,state.domains); h=hit(txt); fetched=True
        else:
            source=x["url"]; txt=x.get("snippet",""); ident=pre; h=sh; fetched=False
        trusted=host(source) in state.domains or any(t in host(source) for t in TRUSTED)
        if h and ident>=10 and (trusted or ident>=28):
            score=ident+(70 if host(source) in state.domains else 0)+(25 if trusted else 0)+(10 if fetched else 0)
            state.evidence.append({"score":score,"url":source,"phase":phase,"label":h["label"],"snippet":h["snippet"],"identity":ident,"trusted":trusted,"fetched":fetched})

    async def archive_url_index(self,state):
        state.archive_attempted=True; state.url_index_attempted=True
        for domain in list(state.domains)[:2]:
            cdx="https://web.archive.org/cdx/search/cdx?"+urllib.parse.urlencode({"url":domain+"/*","output":"json","fl":"timestamp,original,statuscode","filter":"statuscode:200","collapse":"urlkey","limit":"2500"})
            r=await self.fetch(cdx,"wayback-cdx"); state.checked.append(cdx)
            if r["status"] not in range(200,400): continue
            try: rows=json.loads(r["text"])
            except Exception: continue
            cand=[]
            for row in rows[1:] if rows else []:
                if len(row)<2: continue
                ts,url=row[0],nurl(row[1]); low=url.lower()
                score=sum(1 for term in URL_TERMS if term in low)
                if score: cand.append((score,ts,url))
            for _,ts,url in sorted(cand,reverse=True)[:15]:
                await self.evaluate({"url":f"https://web.archive.org/web/{ts}id_/{url}","title":"","snippet":"","provider":"wayback"},state,"wayback-saturation")

    async def process(self,row):
        if row.get("final_verdict")=="Aufnehmen":
            return {**row,"saturation_verdict":"Aufnehmen","saturation_confidence":row.get("final_confidence","Hoch"),"saturation_changed":False,"saturation_reason":"Bereits in zwei vorherigen Prüfungen positiv belegt; kein Negativ-Sättigungslauf erforderlich.","saturation_source":row.get("source_url","")}
        async with self.company_sem:
            state=State(row); seen=set(); fetched=0
            try:
                domain=next(iter(state.domains),"")
                for phrase in PHRASES:
                    queries=[f'"{state.name}" "{state.city}" {phrase}']
                    if domain: queries.append(f"site:{domain} {phrase}")
                    for q in queries:
                        for x in await self.search(q,state):
                            if fetched>=MAX_FETCH: break
                            if x["url"] in seen: continue
                            seen.add(x["url"]); await self.evaluate(x,state,"long-tail-search"); fetched+=1
                        if fetched>=MAX_FETCH: break
                    if fetched>=MAX_FETCH: break
                await self.archive_url_index(state)
            except Exception as exc:
                state.errors.append(f"fatal: {type(exc).__name__}: {exc}"[:700])
            state.evidence.sort(key=lambda x:(-x["score"],x["url"]))
            if state.evidence and state.evidence[0]["score"]>=38:
                ev=state.evidence[0]
                verdict="Aufnehmen"; conf="Sehr hoch" if ev["score"]>=100 else "Hoch"; changed=True
                reason=f'Der dritte Sättigungsdurchlauf fand den zuvor übersehenen, identitätsgeprüften Nachweis „{ev["label"]}“: {ev["snippet"]}'
                source=ev["url"]
            else:
                verdict="Nicht aufnehmen"; conf="Sehr hoch"; changed=False
                reason="Auch der dritte, long-tail- und archivbasierte Sättigungsdurchlauf fand keinen identitätsgesicherten positiven Gegenbeweis. Damit ist die evidenzbasierte Nichtaufnahme nach drei voneinander getrennten Prüfwegen bestätigt."
                source=row.get("source_url") or row.get("website") or ""
            return {**row,"saturation_verdict":verdict,"saturation_confidence":conf,"saturation_changed":changed,"saturation_reason":reason,"saturation_source":source,"saturation_query_attempts":state.query_attempts,"saturation_providers":sorted(state.provider_success),"saturation_checked_urls_count":len(set(state.checked)),"saturation_checked_urls":list(dict.fromkeys(state.checked))[:180],"saturation_evidence":state.evidence[:8],"saturation_archive_attempted":state.archive_attempted,"saturation_url_index_attempted":state.url_index_attempted,"saturation_errors":state.errors[:20],"saturation_completed_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}


async def main():
    p=argparse.ArgumentParser(); p.add_argument("--queue",required=True); p.add_argument("--shard",type=int,required=True); p.add_argument("--shards",type=int,required=True); p.add_argument("--out",required=True); a=p.parse_args()
    rows=json.loads(Path(a.queue).read_text(encoding="utf-8")); subset=[r for i,r in enumerate(rows) if i%a.shards==a.shard]
    connector=aiohttp.TCPConnector(ssl=False,limit=90,limit_per_host=4,ttl_dns_cache=900)
    async with aiohttp.ClientSession(connector=connector,headers={"User-Agent":USER_AGENT}) as session:
        sat=Saturator(session,asyncio.Semaphore(5)); tasks=[asyncio.create_task(sat.process(r)) for r in subset]
        with Path(a.out).open("w",encoding="utf-8") as h:
            done=0
            for fut in asyncio.as_completed(tasks):
                r=await fut; h.write(json.dumps(r,ensure_ascii=False)+"\n");h.flush();done+=1
                print(f"saturation shard {a.shard}: {done}/{len(subset)} nr={r['nr']} verdict={r['saturation_verdict']} changed={r['saturation_changed']}",flush=True)

if __name__=="__main__": asyncio.run(main())
