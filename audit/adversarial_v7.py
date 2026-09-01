#!/usr/bin/env python3
"""Expanded independent verifier for functional and explicit accessibility services.

A firm need not use one exact keyword. It qualifies when identity-bound evidence
shows that it offers either an explicit barrier-free/age-appropriate conversion
or a concrete functional bundle such as a threshold-free shower plus grab bars,
seating, manoeuvring space, care-fund support or similar adaptations.
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

EXTENDED_EXPLICIT = re.compile(
    r"(?:"
    r"barriere(?:n)?(?:frei|arm|ärmer|aermer|reduziert|reduzierend)(?:es|e|en|er|em)?"
    r"|barrieren(?:abbau|reduzierung)"
    r"|altersgerecht(?:es|e|en|er|em)?"
    r"|seniorengerecht(?:es|e|en|er|em)?"
    r"|behindertengerecht(?:es|e|en|er|em)?"
    r"|rollstuhlgerecht(?:es|e|en|er|em)?"
    r"|generationenbad|komfortbad|wohnraumanpassung|wohnumfeldverbesser(?:ung|nde)"
    r"|badewanne\s*(?:zur|zu einer|gegen)\s*dusche|wanne\s+raus|dusche\s+statt\s+wanne"
    r")",
    re.I,
)
FUNCTIONAL = re.compile(
    r"(?:"
    r"(?:bodengleich\w*|ebenerdig\w*|schwellenlos\w*|stufenlos\w*).{0,420}"
    r"(?:haltegriff\w*|stützgriff\w*|stuetzgriff\w*|duschsitz\w*|pflegekasse|pflegegrad|"
    r"bewegungsfl(?:ä|ae)che\w*|rollstuhl\w*|senior\w*|alter\w*|rutsch(?:hemm|fest|sicher)\w*)"
    r"|(?:haltegriff\w*|stützgriff\w*|stuetzgriff\w*|duschsitz\w*|pflegekasse|pflegegrad|"
    r"bewegungsfl(?:ä|ae)che\w*|rollstuhl\w*|rutsch(?:hemm|fest|sicher)\w*).{0,420}"
    r"(?:bodengleich\w*|ebenerdig\w*|schwellenlos\w*|stufenlos\w*)"
    r"|unterfahrbar\w*\s+(?:waschtisch|waschbecken)"
    r"|türverbreiter\w*|tuerverbreiter\w*"
    r"|badewanne.{0,55}(?:zur|zu einer|gegen|raus|weg).{0,25}dusche"
    r"|dusche.{0,55}(?:statt|anstelle).{0,25}(?:badewanne|wanne)"
    r")",
    re.I | re.S,
)
ACTION = re.compile(
    r"(?:"
    r"wir\s+(?:bieten|planen|bauen|realisieren|sanieren|renovieren|modernisieren|"
    r"installieren|montieren|beraten|unterstützen|unterstuetzen|übernehmen|uebernehmen)"
    r"|(?:unterstütz|unterstuetz)\w*.{0,100}(?:umsetz|ausführ|ausfuehr|sanier|umbau)"
    r"|setz\w*.{0,55}\bum\b|führ\w*.{0,55}\baus\b|fuehr\w*.{0,55}\baus\b"
    r"|realisier\w*|installier\w*|montier\w*|übernehm\w*|uebernehm\w*"
    r"|umbau|sanierung|renovierung|modernisierung|nachrüstung|nachruestung|einbau|montage"
    r"|aus\s+einer\s+hand|unsere\s+leistungen|fachgerecht\w*.{0,80}(?:umsetz|ausführ|ausfuehr)"
    r")",
    re.I | re.S,
)
ADAPTATION = re.compile(
    r"(?:bad|bäder|baeder|badezimmer|dusche|wanne|waschtisch|waschbecken|haltegriff|duschsitz|"
    r"tür|tuer|wohnraum|wohnumfeld|umbau|sanier|renov|pflegekasse|pflegegrad|"
    r"treppenlift|plattformlift|hublift|homelift|rampe)",
    re.I,
)
DIGITAL_ONLY_PAGE = re.compile(
    r"(?:barrierefreiheitserkl(?:ä|ae)rung|digitale\s+barrierefreiheit|\bBFSG\b|\bBITV\b|\bWCAG\b)",
    re.I,
)
VISITOR_ONLY = re.compile(
    r"(?:geschäftsräume|geschaeftsraeume|filiale|ausstellung|parkplatz|eingang|standort)"
    r".{0,140}(?:barrierefrei|rollstuhlgerecht)|"
    r"(?:barrierefrei|rollstuhlgerecht).{0,140}(?:erreichbar|zugänglich|zugaenglich|behindertenparkplatz)",
    re.I | re.S,
)

extra_queries = [
    '"{name}" "{city}" "schwellenlose Dusche" Haltegriff',
    '"{name}" "{city}" "ebenerdige Dusche" Duschsitz',
    '"{name}" "{city}" "unterfahrbarer Waschtisch"',
    '"{name}" "{city}" Türverbreiterung Wohnraum',
    '"{name}" "{city}" barriereärmer Badezimmer',
    '"{name}" "{city}" Förderanträge altersgerechter Umbau',
    '"{name}" "{city}" "Bad im Alter" Pflegekasse',
    '"{name}" "{city}" "Wanne raus" Dusche',
]
for query in extra_queries:
    if query not in base.QUERY_PATTERNS:
        base.QUERY_PATTERNS.append(query)


def context_ok(snippet: str) -> bool:
    if not (base.SERVICE.search(snippet) or ADAPTATION.search(snippet)):
        return False
    if not (base.OFFER.search(snippet) or ACTION.search(snippet)):
        return False
    # Digital-accessibility language is rejected only when the same evidence
    # window lacks a physical adaptation and action. Footer links must not veto
    # an otherwise genuine bathroom-service paragraph.
    if DIGITAL_ONLY_PAGE.search(snippet) and not (ADAPTATION.search(snippet) and ACTION.search(snippet)):
        return False
    if VISITOR_ONLY.search(snippet) and not (ACTION.search(snippet) and re.search(r"(?:umbau|sanier|renov|modernis|bad|dusche|wanne|lift|rampe)", snippet, re.I)):
        return False
    if base.NEWBUILD_ONLY.search(snippet) and not re.search(r"(?:umbau|sanier|renov|modernis|wohnraumanpass|badewanne|dusche\s+statt)", snippet, re.I):
        return False
    return True


def evidence_snippets(text: str) -> list[str]:
    candidates = []
    for regex, before, after in ((EXTENDED_EXPLICIT, 420, 560), (FUNCTIONAL, 440, 580), (base.LIFT, 360, 470)):
        for match in regex.finditer(text):
            snippet = text[max(0, match.start() - before):min(len(text), match.end() + after)].strip()
            if not context_ok(snippet):
                continue
            if regex is base.LIFT and not re.search(
                r"(?:montage|einbau|verkauf|mieten|vermiet|beratung|service|wartung|planung|nachrüstung|nachruestung)",
                snippet,
                re.I,
            ):
                continue
            candidates.append(snippet)
    seen = set(); output = []
    for snippet in candidates:
        key = re.sub(r"\W+", "", snippet.lower())[:300]
        if key not in seen:
            seen.add(key); output.append(snippet)
    return output[:20]


def score_evidence(snippet: str, identity: float, official: bool, url: str) -> float:
    if not context_ok(snippet):
        return -999.0
    score = identity
    if EXTENDED_EXPLICIT.search(snippet): score += 0.36
    if FUNCTIONAL.search(snippet): score += 0.38
    if base.SERVICE.search(snippet) or ADAPTATION.search(snippet): score += 0.15
    if base.OFFER.search(snippet) or ACTION.search(snippet): score += 0.24
    if base.LIFT.search(snippet): score += 0.16
    if official: score += 0.20
    if any(token in base.domain(url) for token in base.TRUSTED): score += 0.08
    return score

# Patch the independent implementation's extraction and scoring only; its
# retrieval stack, engines and identity model remain separately implemented.
base.evidence_snippets = evidence_snippets
base.score_evidence = score_evidence


def run_one(row):
    result = expanded.run_one(row)
    result['functional_bundle_filter'] = True
    result['extended_inflection_filter'] = True
    coverage = result.setdefault('coverage', {})
    coverage['functional_bundle_filter'] = True
    coverage['extended_inflection_filter'] = True
    coverage['queries'] = [
        q.format(name=str(row.get('name') or '').replace('"',''), city=str(row.get('city') or '').replace('"',''))
        for q in base.QUERY_PATTERNS
    ]
    methods = list(coverage.get('methods') or [])
    for method in ('functional-accessibility-bundles', 'inflected-barrier-reduction-language'):
        if method not in methods:
            methods.append(method)
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
