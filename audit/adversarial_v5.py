#!/usr/bin/env python3
"""Fail-closed independent verifier with strict context-level evidence filtering.

This wraps adversarial_v4's exhaustive domain discovery and search coverage, but
replaces its evidence extraction/scoring so footer terms cannot turn BFSG pages,
visitor-access statements or unrelated new-build references into positive evidence.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from pathlib import Path

import adversarial_v2 as base

ACTION = re.compile(r"(?:wir\s+(?:bieten|planen|bauen|realisieren|sanieren|renovieren|installieren|montieren|verkaufen|vermieten|beraten)|umbau|sanierung|renovierung|modernisierung|nachrüstung|nachruestung|einbau|montage|aus\s+einer\s+hand|unsere\s+leistungen)", re.I)
ADAPTATION = re.compile(r"(?:umbau|sanier|renov|modernis|wohnraumanpass|bad|badezimmer|dusche|wanne|haltegriff|duschsitz|waschtisch|lift|aufzug|rampe|türverbreiter|tuerverbreiter)", re.I)


def strict_evidence_snippets(text: str) -> list[str]:
    candidates = []
    for match in base.EXPLICIT.finditer(text):
        start = max(0, match.start() - 360)
        end = min(len(text), match.end() + 480)
        snippet = text[start:end].strip()
        # The matched context itself must be a construction/adaptation offer.
        if base.DIGITAL.search(snippet):
            continue
        if base.VISITOR.search(snippet) and not (ACTION.search(snippet) and ADAPTATION.search(snippet)):
            continue
        if base.NEWBUILD_ONLY.search(snippet) and not re.search(r"(?:umbau|sanier|renov|modernis|wohnraumanpass)", snippet, re.I):
            continue
        if not base.SERVICE.search(snippet):
            continue
        if not (base.OFFER.search(snippet) or ACTION.search(snippet)):
            continue
        candidates.append(snippet)
    for match in base.LIFT.finditer(text):
        start = max(0, match.start() - 320)
        end = min(len(text), match.end() + 420)
        snippet = text[start:end].strip()
        if base.DIGITAL.search(snippet):
            continue
        if base.VISITOR.search(snippet) and not ACTION.search(snippet):
            continue
        if re.search(r"(?:montage|einbau|verkauf|mieten|vermiet|beratung|service|wartung|planung|nachrüstung|nachruestung)", snippet, re.I):
            candidates.append(snippet)
    seen = set()
    unique = []
    for snippet in candidates:
        key = re.sub(r"\W+", "", snippet.lower())[:260]
        if key not in seen:
            seen.add(key)
            unique.append(snippet)
    return unique[:12]


def strict_score_evidence(snippet: str, identity: float, official: bool, url: str) -> float:
    if base.DIGITAL.search(snippet):
        return -999.0
    if base.VISITOR.search(snippet) and not (ACTION.search(snippet) and ADAPTATION.search(snippet)):
        return -999.0
    if base.NEWBUILD_ONLY.search(snippet) and not re.search(r"(?:umbau|sanier|renov|modernis|wohnraumanpass)", snippet, re.I):
        return -999.0
    if not base.SERVICE.search(snippet):
        return -999.0
    if not (base.OFFER.search(snippet) or ACTION.search(snippet)):
        return -999.0
    score = identity
    if base.EXPLICIT.search(snippet):
        score += 0.34
    if base.SERVICE.search(snippet):
        score += 0.15
    if base.OFFER.search(snippet) or ACTION.search(snippet):
        score += 0.22
    if base.LIFT.search(snippet):
        score += 0.16
    if official:
        score += 0.20
    if any(token in base.domain(url) for token in base.TRUSTED):
        score += 0.08
    return score

# Patch the globals consulted by base.audit. The exhaustive crawl/search logic remains unchanged.
base.evidence_snippets = strict_evidence_snippets
base.score_evidence = strict_score_evidence

import adversarial_v4 as domain_runner


def run_one(row):
    return domain_runner.run_one(row)


def load(path):
    return domain_runner.load(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', '--primary', dest='input', required=True)
    ap.add_argument('--shard', type=int, required=True)
    ap.add_argument('--shards', type=int, required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    rows = load(args.input)
    selected = [r for i, r in enumerate(sorted(rows, key=lambda x: int(x['nr']))) if i % args.shards == args.shard]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', encoding='utf-8') as handle:
        for index, row in enumerate(selected, 1):
            result = None
            errors = []
            for attempt in range(1, 4):
                try:
                    result = run_one(row)
                    break
                except Exception as exc:
                    errors.append(f'attempt {attempt}: {type(exc).__name__}: {exc}')
                    time.sleep(3 * attempt + random.random() * 2)
            if result is None:
                raise RuntimeError(f"#{row.get('nr')} failed all research attempts: {' | '.join(errors)}")
            result['runner_attempt_errors'] = errors
            result['strict_context_filter'] = True
            handle.write(json.dumps(result, ensure_ascii=False) + '\n')
            handle.flush()
            print(f"[{args.shard}] {index}/{len(selected)} #{result['nr']} {result['verdict']}", flush=True)


if __name__ == '__main__':
    main()
