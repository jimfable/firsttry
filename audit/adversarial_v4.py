#!/usr/bin/env python3
"""Authoritative independent audit runner with explicit official-domain discovery.

Rows without a supplied website are searched by exact company name and city first.
Archive/browser requirements become explicitly not-applicable only after that discovery
pass yields no plausible official domain; this is recorded rather than fabricated.
"""
from __future__ import annotations
import argparse, json, random, time
from pathlib import Path
from urllib.parse import urlparse
import requests

from adversarial_v2 import audit, load, SEARCH_ENGINES, search_one, identity_score, domain

EXCLUDED_DOMAINS={
 'facebook.com','instagram.com','linkedin.com','youtube.com','sanitaer.org','sanitaerfinden.com',
 'gelbeseiten.de','11880.com','yelp.de','meinestadt.de','sellwerk.de','werkenntdenbesten.de',
 'google.com','bing.com','duckduckgo.com','search.brave.com','mojeek.com','search.yahoo.com','ecosia.org'
}

def discover_official(row):
    name=str(row.get('name') or ''); city=str(row.get('city') or '')
    queries=[f'"{name}" "{city}" offizielle Website',f'"{name}" "{city}" Impressum',f'"{name}" "{city}" Unternehmen Leistungen']
    s=requests.Session(); candidates=[]; attempts=0; errors=[]
    for eng in SEARCH_ENGINES:
        for q in queries:
            attempts+=1
            try:
                _,items,err=search_one(s,eng,q)
                if err: errors.append(f'{eng[0]}:{err}')
                for x in items:
                    d=domain(x['url'])
                    if not d or any(d==e or d.endswith('.'+e) for e in EXCLUDED_DOMAINS): continue
                    score=identity_score(x['title']+' '+x['snippet'],x['url'],name,city,'')
                    # An exact distinctive name token in the domain is a useful independent signal.
                    compact=''.join(ch for ch in name.lower() if ch.isalnum())
                    hostcompact=''.join(ch for ch in d.lower() if ch.isalnum())
                    if compact[:8] and compact[:8] in hostcompact: score+=0.25
                    candidates.append((score,x['url'],x))
            except Exception as e: errors.append(f'{eng[0]}:{type(e).__name__}:{e}')
    candidates.sort(key=lambda z:z[0],reverse=True)
    chosen=candidates[0] if candidates and candidates[0][0]>=0.42 else None
    return (chosen[1] if chosen else ''), {'attempts':attempts,'errors':errors,'top_candidates':[{'score':round(s,3),'url':u,'title':x.get('title','')} for s,u,x in candidates[:12]]}

def run_one(row):
    working=dict(row); discovery={'attempts':0,'errors':[],'top_candidates':[]}; original=working.get('website') or ''
    if not original:
        site,discovery=discover_official(working)
        if site: working['website']=site
    result=audit(working)
    result['supplied_website']=original
    result['discovered_website']=working.get('website') or '' if not original else ''
    result['official_domain_discovery']=discovery
    c=result.setdefault('coverage',{})
    c['official_domain_discovery_attempted']=not bool(original)
    c['official_domain_discovery_attempts']=discovery.get('attempts',0)
    if not original and not working.get('website'):
        c['archive_not_applicable_no_domain']=True
        c['common_crawl_not_applicable_no_domain']=True
        c['browser_not_applicable_no_domain']=True
    return result

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input','--primary',dest='input',required=True); ap.add_argument('--shard',type=int,required=True); ap.add_argument('--shards',type=int,required=True); ap.add_argument('--out',required=True); args=ap.parse_args()
    rows=load(args.input); selected=[r for i,r in enumerate(sorted(rows,key=lambda x:int(x['nr']))) if i%args.shards==args.shard]
    p=Path(args.out);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w',encoding='utf-8') as f:
        for i,row in enumerate(selected,1):
            errs=[];res=None
            for attempt in range(1,4):
                try: res=run_one(row);break
                except Exception as e:
                    errs.append(f'attempt {attempt}: {type(e).__name__}: {e}');time.sleep(3*attempt+random.random()*2)
            if res is None: raise RuntimeError(f"#{row.get('nr')} failed all research attempts: {' | '.join(errs)}")
            res['runner_attempt_errors']=errs
            f.write(json.dumps(res,ensure_ascii=False)+'\n');f.flush()
            print(f"[{args.shard}] {i}/{len(selected)} #{res['nr']} {res['verdict']}",flush=True)
if __name__=='__main__':main()
