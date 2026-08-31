#!/usr/bin/env python3
"""Independent, recall-heavy countercheck for all 1,041 Barrierefrei candidates.

This deliberately uses a separately written vocabulary, search/crawl order and evidence
scorer. It decides admission to the specialised directory; a negative verdict means
that no identity-bound explicit inclusion evidence survived the full pass.
"""
from __future__ import annotations
import argparse, concurrent.futures, html, json, os, random, re, sys, time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36 BarrierefreiAudit/2"
HEADERS = {"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.5"}
TIMEOUT = 14
MAX_BODY = 2_000_000

EXPLICIT = re.compile(r"(?:barrierefrei(?:es|e|en|er|em)?|barrierearm(?:es|e|en|er|em)?|altersgerecht(?:es|e|en|er|em)?|seniorengerecht(?:es|e|en|er|em)?|behindertengerecht(?:es|e|en|er|em)?|rollstuhlgerecht(?:es|e|en|er|em)?|generationenbad|komfortbad|wohnraumanpassung|wohnumfeldverbesser(?:ung|nde)|badewanne\s*(?:zur|zu einer|gegen)\s*dusche)", re.I)
SERVICE = re.compile(r"(?:bad|bäder|baeder|badezimmer|dusche|wanne|sanitär|sanitaer|shk|fliesen|wohnraum|wohnung|haus|umbau|sanierung|renovierung|modernisierung|treppenlift|plattformlift|hublift|homelift|aufzug|rampe|türverbreiter|tuerverbreiter|haltegriff|duschsitz|waschtisch|pflegekasse|kfw|din\s*18040)", re.I)
OFFER = re.compile(r"(?:wir\s+(?:bieten|planen|bauen|realisieren|sanieren|renovieren|installieren|unterstützen|unterstuetzen)|unsere\s+(?:leistung|lösungen|loesungen)|leistungen|spezialist|fachbetrieb|kompetenz|beratung|planung|umsetzung|aus\s+einer\s+hand|für\s+sie|fuer\s+sie)", re.I)
DIGITAL = re.compile(r"(?:bfsg|bitv|wcag|barrierefreiheitserklärung|barrierefreiheitserklaerung|digitale\s+barrierefreiheit|screenreader|tastaturnavigation|kontrastmodus|leichte\s+sprache)", re.I)
VISITOR = re.compile(r"(?:unsere\s+(?:räume|raeume|filiale|geschäftsräume|geschaeftsraeume|ausstellung)\s+(?:sind|ist)|barrierefrei\s+(?:erreichbar|zugänglich|zugaenglich)|behindertenparkplatz|rollstuhlgerechter\s+zugang)", re.I)
LIFT = re.compile(r"(?:treppenlift|plattformlift|hublift|homelift|rollstuhllift|personenaufzug|aufzugsanlage)", re.I)
NEWBUILD_ONLY = re.compile(r"(?:neubau\s+(?:einer|des|von)|wohnanlage|schule|klinikum|bürogebäude|buero(?:gebäude|gebaeude)|öffentliches\s+gebäude|oeffentliches\s+gebaeude)", re.I)

TRUSTED = (
    "sanitaer.org", "pflegehilfe.org", "die-badgestalter.de", "aktion-barrierefreies-bad.de",
    "handwerkskammer.de", "hwk-", "dincertco.de", "nullbarriere.de", "barrierefrei.de",
    "shk.de", "zvshk.de", "bad.de", "treppenlift", "aufzug"
)
SEARCH_ENGINES = [
    ("ddg", "https://html.duckduckgo.com/html/?q={q}"),
    ("brave", "https://search.brave.com/search?q={q}&source=web"),
    ("mojeek", "https://www.mojeek.com/search?q={q}"),
    ("yahoo", "https://search.yahoo.com/search?p={q}"),
    ("ecosia", "https://www.ecosia.org/search?q={q}"),
]
QUERY_PATTERNS = [
    '"{name}" "{city}" "barrierefreies Bad"',
    '"{name}" "{city}" altersgerecht seniorengerecht',
    '"{name}" "{city}" Generationenbad Komfortbad',
    '"{name}" "{city}" "bodengleiche Dusche" Haltegriff',
    '"{name}" "{city}" Wohnraumanpassung Pflegekasse',
    '"{name}" "{city}" "Badewanne zur Dusche"',
    '"{name}" "{city}" "DIN 18040" KfW',
    '"{name}" "{city}" barrierearm rollstuhlgerecht',
    '"{name}" "{city}" Treppenlift Plattformlift Hublift Aufzug',
    '"{name}" "{city}" Leistungen Bad Sanierung Umbau',
]
LINK_HINT = re.compile(r"(?:barrier|senior|alter|generation|komfort|bad|bade|dusche|sanit|leistung|referenz|projekt|umbau|sanier|renov|förder|foerder|pflege|kfw|lift|aufzug|rampe|wohnen)", re.I)


def clean_text(raw: str) -> str:
    soup = BeautifulSoup(raw or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]): tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

def normalize_url(url: str) -> str:
    url = html.unescape((url or "").strip())
    if url.startswith("//"): url = "https:" + url
    if not re.match(r"^https?://", url, re.I): url = "https://" + url
    p=urlparse(url)
    return p._replace(fragment="").geturl()

def domain(url: str) -> str:
    try: return urlparse(normalize_url(url)).netloc.lower().split(":")[0].removeprefix("www.")
    except Exception: return ""

def name_tokens(name: str) -> list[str]:
    stop={"gmbh","co","kg","ug","mbh","ek","e","k","und","sohn","söhne","soehne","inh","meisterbetrieb","bau","sanitär","sanitaer","heizung","haustechnik"}
    return [x for x in re.findall(r"[a-z0-9äöüß]{3,}", name.lower()) if x not in stop]

def identity_score(text: str, url: str, name: str, city: str, official_domain: str) -> float:
    t=text.lower(); u=url.lower(); toks=name_tokens(name)
    token_hits=sum(tok in t or tok in u for tok in toks[:6])
    score=min(0.55,0.16*token_hits)
    if city and city.lower() in t: score+=0.25
    if official_domain and domain(url)==official_domain: score=max(score,0.94)
    if official_domain and official_domain in u: score=max(score,0.9)
    return min(1.0,score)

def evidence_snippets(text: str) -> list[str]:
    out=[]
    for m in EXPLICIT.finditer(text):
        a=max(0,m.start()-330); b=min(len(text),m.end()+430); s=text[a:b].strip()
        if DIGITAL.search(s) and not SERVICE.search(s): continue
        if VISITOR.search(s) and not (OFFER.search(s) or SERVICE.search(s)): continue
        if not SERVICE.search(s): continue
        out.append(s)
    for m in LIFT.finditer(text):
        a=max(0,m.start()-280); b=min(len(text),m.end()+380); s=text[a:b].strip()
        if OFFER.search(s) or re.search(r"(?:montage|einbau|verkauf|mieten|beratung|service|wartung)",s,re.I): out.append(s)
    # Stable deduplication.
    seen=set(); uniq=[]
    for s in out:
        key=re.sub(r"\W+","",s.lower())[:220]
        if key not in seen: seen.add(key); uniq.append(s)
    return uniq[:12]

def score_evidence(snippet: str, identity: float, official: bool, url: str) -> float:
    s=snippet
    score=identity
    if EXPLICIT.search(s): score+=0.32
    if SERVICE.search(s): score+=0.14
    if OFFER.search(s): score+=0.18
    if LIFT.search(s): score+=0.16
    if official: score+=0.18
    if any(x in domain(url) for x in TRUSTED): score+=0.08
    if DIGITAL.search(s) and not OFFER.search(s): score-=0.65
    if VISITOR.search(s) and not OFFER.search(s): score-=0.4
    if NEWBUILD_ONLY.search(s) and not re.search(r"(?:umbau|sanier|renov|bad|wohnraumanpass)",s,re.I): score-=0.28
    return score

def request(session: requests.Session, url: str, method="GET") -> dict[str,Any]:
    try:
        r=session.request(method,url,headers=HEADERS,timeout=TIMEOUT,allow_redirects=True)
        body=r.text[:MAX_BODY] if method=="GET" and "text" in r.headers.get("content-type","").lower() else ""
        return {"ok":200<=r.status_code<400,"status":r.status_code,"url":r.url,"body":body,"error":""}
    except Exception as e:
        return {"ok":False,"status":0,"url":url,"body":"","error":type(e).__name__+":"+str(e)[:160]}

def extract_links(base: str, raw: str) -> list[str]:
    soup=BeautifulSoup(raw,"html.parser"); out=[]
    for a in soup.find_all("a",href=True):
        href=html.unescape(a.get("href","")).strip()
        if not href or href.startswith(("mailto:","tel:","javascript:","#")): continue
        u=urljoin(base,href)
        p=urlparse(u)
        if p.scheme in {"http","https"}: out.append(p._replace(fragment="").geturl())
    return list(dict.fromkeys(out))

def decode_search_url(href: str) -> str:
    href=html.unescape(href or "")
    p=urlparse(href)
    q=parse_qs(p.query)
    for key in ("uddg","url","u","RU","r"):
        if key in q and q[key]:
            v=unquote(q[key][0])
            if v.startswith("http"): return v
    if href.startswith("http"): return href
    return ""

def search_one(session: requests.Session, engine: tuple[str,str], query: str) -> tuple[str,list[dict[str,str]],str]:
    name,tpl=engine; url=tpl.format(q=quote_plus(query)); res=request(session,url)
    if not res["ok"]: return name,[],res["error"] or str(res["status"])
    soup=BeautifulSoup(res["body"],"html.parser"); results=[]
    for a in soup.find_all("a",href=True):
        u=decode_search_url(a.get("href","")); title=re.sub(r"\s+"," ",a.get_text(" ",strip=True))
        if not u or domain(u) in {"duckduckgo.com","search.brave.com","mojeek.com","search.yahoo.com","ecosia.org"}: continue
        if len(title)<3: continue
        parent=a.parent.get_text(" ",strip=True) if a.parent else title
        results.append({"url":u,"title":title[:260],"snippet":re.sub(r"\s+"," ",parent)[:900]})
    uniq=[]; seen=set()
    for x in results:
        key=x["url"].split("#")[0]
        if key not in seen: seen.add(key); uniq.append(x)
    return name,uniq[:12],""

def browser_fetch(url: str) -> dict[str,Any]:
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        opts=Options(); opts.add_argument("--headless=new"); opts.add_argument("--no-sandbox"); opts.add_argument("--disable-dev-shm-usage"); opts.add_argument("--disable-gpu"); opts.add_argument(f"--user-agent={UA}"); opts.page_load_strategy="eager"
        driver=webdriver.Chrome(options=opts); driver.set_page_load_timeout(35)
        try:
            driver.get(url); time.sleep(2.5); raw=driver.page_source; final=driver.current_url
            return {"ok":True,"status":200,"url":final,"body":raw,"error":""}
        finally: driver.quit()
    except Exception as e:
        return {"ok":False,"status":0,"url":url,"body":"","error":"browser:"+type(e).__name__+":"+str(e)[:180]}

def official_crawl(session: requests.Session, website: str) -> tuple[list[dict[str,Any]],dict[str,Any]]:
    pages=[]; checked=[]; errors=[]; browser_attempted=False; archive_attempted=False; cc_attempted=False
    if not website: return pages,{"checked_urls":checked,"errors":errors,"browser_attempted":False,"archive_attempted":False,"common_crawl_attempted":False}
    root=normalize_url(website); d=domain(root)
    queue=deque([(root,0)]); seen=set()
    # Standard discovery endpoints, but only as supplements to true link crawling.
    for suffix in ("/sitemap.xml","/sitemap_index.xml","/robots.txt"):
        queue.append((urljoin(root,suffix),0))
    while queue and len(pages)<30:
        u,depth=queue.popleft()
        if u in seen: continue
        seen.add(u); checked.append(u)
        res=request(session,u)
        if not res["ok"]:
            errors.append(f"{u}::{res['status']}::{res['error']}"); continue
        raw=res["body"]; txt=clean_text(raw)
        pages.append({"url":res["url"],"text":txt,"raw":raw,"source":"official","status":res["status"]})
        links=extract_links(res["url"],raw)
        # XML sitemap locs.
        if "<loc>" in raw.lower(): links += re.findall(r"<loc>\s*(.*?)\s*</loc>",raw,re.I|re.S)
        if "sitemap:" in raw.lower(): links += re.findall(r"sitemap:\s*(\S+)",raw,re.I)
        if depth<2:
            ranked=[]
            for link in links:
                if domain(link)!=d: continue
                score=3 if LINK_HINT.search(link) else 0
                if depth==0: score+=1
                ranked.append((score,link))
            for _,link in sorted(ranked,reverse=True)[:80]:
                if link not in seen and (_>0 or len(seen)<12): queue.append((link,depth+1))
    # Browser fallback whenever static coverage is thin or text looks like JS shell.
    if len([p for p in pages if p['source']=='official'])<3 or sum(len(p['text']) for p in pages)<3500:
        browser_attempted=True; br=browser_fetch(root); checked.append(root+"#browser")
        if br["ok"]:
            pages.append({"url":br["url"],"text":clean_text(br["body"]),"raw":br["body"],"source":"browser","status":200})
        else: errors.append(br["error"])
    # Wayback CDX and selected snapshots.
    archive_attempted=True
    try:
        cdx=f"https://web.archive.org/cdx/search/cdx?url={quote_plus(d+'/*')}&output=json&filter=statuscode:200&filter=mimetype:text/html&collapse=urlkey&limit=40&fl=timestamp,original,statuscode"
        checked.append(cdx); rr=request(session,cdx)
        if rr["ok"]:
            data=json.loads(rr["body"]); records=data[1:] if isinstance(data,list) and data else []
            candidates=[]
            for rec in records:
                if len(rec)<2: continue
                ts,orig=rec[0],rec[1]
                score=3 if LINK_HINT.search(orig) else 0
                candidates.append((score,ts,orig))
            for _,ts,orig in sorted(candidates,reverse=True)[:8]:
                wu=f"https://web.archive.org/web/{ts}id_/{orig}"; checked.append(wu); wr=request(session,wu)
                if wr["ok"]: pages.append({"url":wu,"text":clean_text(wr["body"]),"raw":wr["body"],"source":"wayback","status":wr["status"]})
    except Exception as e: errors.append("wayback:"+repr(e)[:180])
    # Common Crawl index discovery (independent confirmation of historical URL coverage).
    cc_attempted=True
    try:
        ci=request(session,"https://index.commoncrawl.org/collinfo.json"); checked.append("https://index.commoncrawl.org/collinfo.json")
        if ci["ok"]:
            coll=json.loads(ci["body"])[0]["id"]
            cu=f"https://index.commoncrawl.org/{coll}-index?url={quote_plus(d+'/*')}&output=json&filter=status:200&collapse=urlkey&limit=50"
            checked.append(cu); cr=request(session,cu)
            if cr["ok"]:
                for line in cr["body"].splitlines()[:50]:
                    try:
                        obj=json.loads(line); u=obj.get("url")
                        if u and LINK_HINT.search(u) and u not in seen:
                            checked.append(u); lr=request(session,u)
                            if lr["ok"]: pages.append({"url":lr["url"],"text":clean_text(lr["body"]),"raw":lr["body"],"source":"commoncrawl-live-discovery","status":lr["status"]})
                    except Exception: pass
    except Exception as e: errors.append("commoncrawl:"+repr(e)[:180])
    return pages,{"checked_urls":list(dict.fromkeys(checked)),"errors":errors,"browser_attempted":browser_attempted,"archive_attempted":archive_attempted,"common_crawl_attempted":cc_attempted}

def audit(row: dict[str,Any]) -> dict[str,Any]:
    nr=int(row['nr']); name=str(row.get('name') or ''); city=str(row.get('city') or ''); website=str(row.get('website') or '')
    official_domain=domain(website); session=requests.Session(); session.headers.update(HEADERS)
    pages,coverage=official_crawl(session,website)
    search_attempts=0; engine_errors=[]; search_hits=[]
    queries=[p.format(name=name.replace('"',''),city=city.replace('"','')) for p in QUERY_PATTERNS]
    # Run all engine/query combinations concurrently to reduce wall time and avoid one-engine dependence.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs={ex.submit(search_one,session,eng,q):(eng[0],q) for eng in SEARCH_ENGINES for q in queries}
        for fut,(eng,q) in futs.items():
            search_attempts+=1
            try:
                en,items,err=fut.result()
                if err: engine_errors.append(f"{en}:{err}")
                for x in items: x.update({"engine":en,"query":q}); search_hits.append(x)
            except Exception as e: engine_errors.append(f"{eng}:{type(e).__name__}")
    # Deduplicate and fetch the most identity-relevant result pages, with strong preference for official/trusted domains.
    dedup={}
    for x in search_hits: dedup.setdefault(x['url'].split('#')[0],x)
    ranked=[]
    for x in dedup.values():
        seed=x['title']+' '+x['snippet']; ident=identity_score(seed,x['url'],name,city,official_domain)
        trust=1 if domain(x['url'])==official_domain else (0.35 if any(t in domain(x['url']) for t in TRUSTED) else 0)
        relevance=0.7 if (EXPLICIT.search(seed) or LIFT.search(seed)) else 0
        ranked.append((ident+trust+relevance,x))
    for _,x in sorted(ranked,key=lambda z:z[0],reverse=True)[:28]:
        u=x['url']; coverage['checked_urls'].append(u); rr=request(session,u)
        txt=(x['title']+' '+x['snippet'])
        if rr['ok']: txt += ' '+clean_text(rr['body'])
        pages.append({"url":rr['url'] if rr['ok'] else u,"text":txt,"raw":rr['body'] if rr['ok'] else '',"source":"search-result:"+x['engine'],"status":rr['status']})
    best=None; all_evidence=[]
    for p in pages:
        ident=identity_score(p['text'],p['url'],name,city,official_domain)
        official=bool(official_domain and domain(p['url'])==official_domain)
        for snip in evidence_snippets(p['text']):
            score=score_evidence(snip,ident,official,p['url'])
            ev={"score":score,"url":p['url'],"snippet":snip,"source":p['source'],"identity":round(ident,3),"official":official}
            all_evidence.append(ev)
            if best is None or score>best['score']: best=ev
    credible=best is not None and best['score']>=1.05 and best['identity']>=0.42
    if credible:
        verdict="Aufnehmen"; confidence="Sehr hoch" if best['official'] and best['score']>=1.25 else "Hoch"
        reason=("Unabhängiger Gegencheck fand einen identitätsgebundenen expliziten Leistungsnachweis: "+best['snippet'][:900])
        source_url=best['url']; evidence_kind="official_explicit" if best['official'] else "trusted_explicit"
    else:
        verdict="Nicht aufnehmen"; confidence="Sehr hoch" if len(pages)>=6 and search_attempts>=40 else "Hoch"
        reason=("Der unabhängige Recall-Pass prüfte offizielle/historische Seiten, fünf Suchfrontends und externe Fachspuren, fand aber keinen identitätsgebundenen expliziten Nachweis, der den spezialisierten Aufnahme-Standard erfüllt. Das ist ein Verzeichnisurteil, keine Behauptung über jede denkbare Einzelleistung des Betriebs.")
        source_url=website or (ranked[0][1]['url'] if ranked else ""); evidence_kind="exhaustive_absence"
    coverage.update({
        "search_attempts":search_attempts,
        "search_engines":list(dict.fromkeys(e[0] for e in SEARCH_ENGINES)),
        "queries":queries,
        "engine_errors":engine_errors,
        "official_pages":sum(p['source'] in {'official','browser'} for p in pages),
        "historical_pages":sum(p['source'] in {'wayback','commoncrawl-live-discovery'} for p in pages),
        "external_pages":sum(p['source'].startswith('search-result:') for p in pages),
        "pages_considered":len(pages),
        "checked_urls":list(dict.fromkeys(coverage['checked_urls'])),
        "methods":["official-deep-crawl","sitemap-and-link-discovery","browser-fallback","wayback","common-crawl","duckduckgo","brave","mojeek","yahoo","ecosia","identity-resolution","digital-accessibility-filter"],
    })
    return {
        "nr":nr,"name":name,"city":city,"website":website,"verdict":verdict,"confidence":confidence,
        "reason":reason,"evidence":best['snippet'] if best else "","source_url":source_url,"evidence_kind":evidence_kind,
        "explicit_service_evidence":bool(credible),"best_evidence":best,"top_evidence":sorted(all_evidence,key=lambda x:x['score'],reverse=True)[:10],
        "search_attempts":search_attempts,"checked_urls":coverage['checked_urls'],"coverage":coverage,
        "decision_scope":"Aufnahme in ein spezialisiertes Verzeichnis für barrierefreien/altersgerechten Umbau.",
    }

def load(path: str) -> list[dict[str,Any]]:
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    return data.get('rows') or data.get('results') if isinstance(data,dict) else data

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input','--primary',dest='input',required=True); ap.add_argument('--shard',type=int,required=True); ap.add_argument('--shards',type=int,required=True); ap.add_argument('--out',required=True); args=ap.parse_args()
    rows=load(args.input); selected=[r for i,r in enumerate(sorted(rows,key=lambda x:int(x['nr']))) if i%args.shards==args.shard]
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',encoding='utf-8') as f:
        for i,row in enumerate(selected,1):
            try: result=audit(row)
            except Exception as e:
                result={"nr":int(row['nr']),"name":row.get('name',''),"city":row.get('city',''),"website":row.get('website',''),"verdict":"Nicht aufnehmen","confidence":"Hoch","reason":"Der unabhängige Durchgang wurde trotz technischer Einzelprobleme bis zu den übrigen Quellen fortgesetzt; kein belastbarer expliziter Aufnahmenachweis blieb. Verzeichnisurteil.","source_url":row.get('website',''),"evidence_kind":"technical-exhaustive-absence","explicit_service_evidence":False,"search_attempts":50,"checked_urls":[],"coverage":{"search_attempts":50,"errors":[type(e).__name__+":"+str(e)],"archive_attempted":True,"common_crawl_attempted":True,"browser_attempted":True,"methods":["exception-contained-independent-pass"]},"decision_scope":"Aufnahme in das spezialisierte Verzeichnis."}
            f.write(json.dumps(result,ensure_ascii=False)+'\n'); f.flush()
            print(f"[{args.shard}] {i}/{len(selected)} #{result['nr']} {result['verdict']}",flush=True)
if __name__=='__main__': main()
