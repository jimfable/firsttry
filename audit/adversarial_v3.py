#!/usr/bin/env python3
"""Fail-closed runner for the independent v2 verifier.

A technical exception is retried with fresh process state and ultimately fails the
workflow. It can never be converted into a negative business verdict.
"""
from __future__ import annotations
import argparse, json, random, sys, time
from pathlib import Path

from adversarial_v2 import audit, load


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input','--primary',dest='input',required=True)
    ap.add_argument('--shard',type=int,required=True)
    ap.add_argument('--shards',type=int,required=True)
    ap.add_argument('--out',required=True)
    args=ap.parse_args()
    rows=load(args.input)
    selected=[r for i,r in enumerate(sorted(rows,key=lambda x:int(x['nr']))) if i%args.shards==args.shard]
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',encoding='utf-8') as f:
        for i,row in enumerate(selected,1):
            errors=[]; result=None
            for attempt in range(1,4):
                try:
                    result=audit(row); break
                except Exception as e:
                    errors.append(f'attempt {attempt}: {type(e).__name__}: {e}')
                    time.sleep(3*attempt+random.random()*2)
            if result is None:
                raise RuntimeError(f"#{row.get('nr')} failed all independent research attempts: {' | '.join(errors)}")
            result['runner_attempt_errors']=errors
            f.write(json.dumps(result,ensure_ascii=False)+'\n'); f.flush()
            print(f"[{args.shard}] {i}/{len(selected)} #{result['nr']} {result['verdict']}",flush=True)
if __name__=='__main__': main()
