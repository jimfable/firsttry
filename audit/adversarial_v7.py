#!/usr/bin/env python3
"""Expanded verifier that also recognizes concrete functional accessibility bundles.

A firm need not use the literal word 'barrierefrei' if it clearly offers a bundle such
as a threshold-free shower plus grab bars/shower seat/funding support, an accessible
washbasin, door widening or bath-to-shower conversion.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from pathlib import Path

import adversarial_v2 as base
import adversarial_v6 as expanded

FUNCTIONAL = re.compile(
    r"(?:"
    r"(?:bodengleich|ebenerdig|schwellenlos|stufenlos).{0,360}(?:haltegriff|stützgriff|stuetzgriff|duschsitz|pflegekasse|pflegegrad|bewegungsfläche|bewegungsflaeche|rollstuhl|senior|alter|rutschhemm)"
    r"|(?:haltegriff|stützgriff|stuetzgriff|duschsitz|pflegekasse|pflegegrad|bewegungsfläche|bewegungsflaeche|rollstuhl|rutschhemm).{0,360}(?:bodengleich|ebenerdig|schwellenlos|stufenlos)"
    r"|unterfahrbar(?:er|e|en|es)?\s+(?:waschtisch|waschbecken)"
    r"|türverbreiter(?:ung|ungen)|tuerverbreiter(?:ung|ungen)"
    r"|badewanne.{0,40}(?:zur|zu einer|gegen).{0,20}dusche"
    r"|dusche.{0,40}(?:statt|anstelle).{0,20}(?:badewanne|wanne)"
    r")",
    re.I | re.S,
)
ACTION = re.compile(r"(?:wir\s+(?:bieten|planen|bauen|realisieren|sanieren|renovieren|installieren|montieren|beraten)|umbau|sanierung|renovierung|modernisierung|nachrüstung|nachruestung|einbau|montage|aus\s+einer\s+hand|unsere\s+leistungen)", re.I)
ADAPTATION = re.compile(r"(?:bad|badezimmer|dusche|wanne|waschtisch|waschbecken|haltegriff|duschsitz|tür|tuer|wohnraum|umbau|sanier|renov|pflegekasse|pflegegrad)", re.I)

extra_queries = [
    '"{name}" "{city}" "schwellenlose Dusche" Haltegriff',
    '"{name}" "{city}" "ebenerdige Dusche" Duschsitz',
    '"{name}" "{city}" "unterfahrbarer Waschtisch"',
    '"{name}" "{city}" Türverbreiterung Wohnraum',
]
for query in extra_queries:
    if query not in base.QUERY_PATTERNS:
        base.QUERY_PATTERNS.append(query)


def context_ok(snippet: str) -> bool:
    if base.DIGITAL.search(snippet):
        return False
    if base.VISITOR.search(snippet) and not (ACTION.search(snippet) and ADAPTATION.search(snippet)):
        return False
    if base.NEWBUILD_ONLY.search(snippet) and not re.search(r"(?:umbau|sanier|renov|modernis|wohnraumanpass)", snippet, re.I):
        return False
    return bool(base.SERVICE.search(snippet) or ADAPTATION.search(snippet)) and bool(base.OFFER.search(snippet) or ACTION.search(snippet))


def evidence_snippets(text: str) -> list[str]:
    candidates = []
    for regex, before, after in ((base.EXPLICIT, 360, 480), (FUNCTIONAL, 390, 500), (base.LIFT, 320, 420)):
        for match in regex.finditer(text):
            snippet = text[max(0, match.start() - before):min(len(text), match.end() + after)].strip()
            if not context_ok(snippet):
                continue
            if regex is base.LIFT and not re.search(r"(?:montage|einbau|verkauf|mieten|vermiet|beratung|service|wartung|planung|nachrüstung|nachruestung)", snippet, re.I):
                continue
            candidates.append(snippet)
    seen = set(); output = []
    for snippet in candidates:
        key = re.sub(r"\W+", "", snippet.lower())[:280]
        if key not in seen:
            seen.add(key); output.append(snippet)
    return output[:16]


def score_evidence(snippet: str, identity: float, official: bool, url: str) -> float:
    if not context_ok(snippet):
        return -999.0
    score = identity
    if base.EXPLICIT.search(snippet): score += 0.34
    if FUNCTIONAL.search(snippet): score += 0.36
    if base.SERVICE.search(snippet) or ADAPTATION.search(snippet): score += 0.15
    if base.OFFER.search(snippet) or ACTION.search(snippet): score += 0.22
    if base.LIFT.search(snippet): score += 0.16
    if official: score += 0.20
    if any(token in base.domain(url) for token in base.TRUSTED): score += 0.08
    return score

base.evidence_snippets = evidence_snippets
base.score_evidence = score_evidence


def run_one(row):
    result = expanded.run_one(row)
    result['functional_bundle_filter'] = True
    coverage = result.setdefault('coverage', {})
    coverage['functional_bundle_filter'] = True
    coverage['queries'] = [q.format(name=str(row.get('name') or '').replace('"',''), city=str(row.get('city') or '').replace('"','')) for q in base.QUERY_PATTERNS]
    methods = list(coverage.get('methods') or [])
    if 'functional-accessibility-bundles' not in methods:
        methods.append('functional-accessibility-bundles')
    coverage['methods'] = methods
    return result


def load(path):
    return expanded.load(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', '--primary', dest='input', required=True)
    parser.add_argument('--shard', type=int, required=True)
    parser.add_argument('--shards', type=int, required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    rows = load(args.input)
    selected = [row for index, row in enumerate(sorted(rows, key=lambda x: int(x['nr']))) if index % args.shards == args.shard]
    output = Path(args.out); output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', encoding='utf-8') as handle:
        for index, row in enumerate(selected, 1):
            result = None; errors = []
            for attempt in range(1, 4):
                try:
                    result = run_one(row); break
                except Exception as exc:
                    errors.append(f'attempt {attempt}: {type(exc).__name__}: {exc}')
                    time.sleep(3 * attempt + random.random() * 2)
            if result is None:
                raise RuntimeError(f"#{row.get('nr')} failed all functional-bundle research attempts: {' | '.join(errors)}")
            result['runner_attempt_errors'] = errors
            handle.write(json.dumps(result, ensure_ascii=False) + '\n'); handle.flush()
            print(f"[{args.shard}] {index}/{len(selected)} #{result['nr']} {result['verdict']}", flush=True)


if __name__ == '__main__':
    main()
