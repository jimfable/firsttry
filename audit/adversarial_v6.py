#!/usr/bin/env python3
"""Strict-context verifier with expanded retrieval redundancy.

Adds Bing and Google HTML fronts plus a Jina Reader fallback for pages blocked to
normal HTTP. The strict evidence filters from adversarial_v5 remain authoritative.
"""
from __future__ import annotations

import argparse
import html
import json
import random
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import adversarial_v2 as base
import adversarial_v5 as strict
import adversarial_v4 as domain_runner

# Expand independent search surfaces.
expanded = list(base.SEARCH_ENGINES)
for item in [
    ('bing', 'https://www.bing.com/search?q={q}'),
    ('google', 'https://www.google.com/search?q={q}&num=10&hl=de'),
]:
    if item[0] not in {x[0] for x in expanded}:
        expanded.append(item)
base.SEARCH_ENGINES = expanded
domain_runner.SEARCH_ENGINES = expanded

# Google result links commonly use ?q=<url>; retain all prior decoders.
_original_decode = base.decode_search_url

def decode_search_url(href: str) -> str:
    value = _original_decode(href)
    if value:
        return value
    href = html.unescape(href or '')
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    candidate = unquote((query.get('q') or [''])[0])
    if candidate.startswith(('http://', 'https://')):
        return candidate
    return ''

base.decode_search_url = decode_search_url

# Direct HTTP first; Jina Reader only when the direct page is unavailable or a JS shell.
_original_request = base.request

def request(session, url: str, method='GET'):
    result = _original_request(session, url, method)
    if method != 'GET' or 'r.jina.ai/' in url:
        return result
    body = str(result.get('body') or '')
    if result.get('ok') and len(body) >= 600:
        return result
    if not str(url).startswith(('http://', 'https://')):
        return result
    reader = 'https://r.jina.ai/' + str(url)
    fallback = _original_request(session, reader, method)
    if fallback.get('ok') and len(str(fallback.get('body') or '')) > len(body):
        fallback['url'] = str(url) + '#jina-reader'
        fallback['retrieval_fallback'] = 'jina-reader'
        return fallback
    return result

base.request = request

# The functions in base.audit resolve these globals dynamically.
base.evidence_snippets = strict.strict_evidence_snippets
base.score_evidence = strict.strict_score_evidence


def run_one(row):
    result = domain_runner.run_one(row)
    result['strict_context_filter'] = True
    result['expanded_retrieval'] = True
    coverage = result.setdefault('coverage', {})
    methods = list(coverage.get('methods') or [])
    for method in ('bing', 'google', 'jina-reader-fallback'):
        if method not in methods:
            methods.append(method)
    coverage['methods'] = methods
    coverage['search_engines'] = [name for name, _ in expanded]
    coverage['expanded_retrieval'] = True
    return result


def load(path):
    return domain_runner.load(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', '--primary', dest='input', required=True)
    parser.add_argument('--shard', type=int, required=True)
    parser.add_argument('--shards', type=int, required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    rows = load(args.input)
    selected = [row for index, row in enumerate(sorted(rows, key=lambda x: int(x['nr']))) if index % args.shards == args.shard]
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', encoding='utf-8') as handle:
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
                raise RuntimeError(f"#{row.get('nr')} failed all expanded research attempts: {' | '.join(errors)}")
            result['runner_attempt_errors'] = errors
            handle.write(json.dumps(result, ensure_ascii=False) + '\n')
            handle.flush()
            print(f"[{args.shard}] {index}/{len(selected)} #{result['nr']} {result['verdict']}", flush=True)


if __name__ == '__main__':
    main()
