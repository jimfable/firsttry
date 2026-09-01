#!/usr/bin/env python3
"""All-fallbacks, fail-closed runner for authoritative v8."""
from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path

import aiohttp

import exhaustive as base
import exhaustive_v8 as strict


async def audit_once(auditor: strict.StrictAuditor, item: dict) -> dict:
    async with auditor.company_sem:
        state = base.AuditState(item)
        await auditor.resolve_official(state)
        await auditor.crawl_official(state)
        await auditor.external_search(state)
        if not auditor.credible_positives(state):
            await auditor.red_team_search(state)
        if not auditor.credible_positives(state):
            await auditor.wayback(state)
        if not auditor.credible_positives(state):
            await auditor.browser_fallback(state)
        if not auditor.credible_positives(state):
            await auditor.commoncrawl(state)
        return auditor.classify(state)


async def guarded(auditor: strict.StrictAuditor, item: dict) -> dict:
    failures: list[str] = []
    for attempt in range(1, 4):
        try:
            result = await audit_once(auditor, item)
            result["runner_attempt_errors"] = failures
            result["runner_attempts"] = attempt
            result["implementation"] = "exhaustive_v8_identity_locked_all_fallbacks"
            return result
        except Exception as exc:
            failures.append(f"attempt {attempt}: {type(exc).__name__}: {exc}"[:800])
            auditor.cache.clear()
            await asyncio.sleep(3 * attempt + random.random() * 2)
    raise RuntimeError(
        f"#{item.get('nr')} failed all exhaustive attempts: " + " | ".join(failures)
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-url", default=base.QUEUE_URL)
    parser.add_argument("--queue-file")
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.queue_file:
        queue = json.loads(Path(args.queue_file).read_text(encoding="utf-8"))
    else:
        async with aiohttp.ClientSession(headers={"User-Agent": base.USER_AGENT}) as bootstrap:
            async with bootstrap.get(args.queue_url, timeout=base.TIMEOUT) as response:
                response.raise_for_status()
                queue = json.loads(await response.text())
    subset = [item for index, item in enumerate(queue) if index % args.shards == args.shard]

    connector = aiohttp.TCPConnector(ssl=False, limit=100, limit_per_host=4, ttl_dns_cache=900)
    company_sem = asyncio.Semaphore(4)
    output = Path(args.out)
    async with aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": base.USER_AGENT, "Accept-Language": "de-DE,de;q=0.9,en;q=0.5"},
    ) as session:
        auditor = strict.StrictAuditor(session, company_sem)
        tasks = [asyncio.create_task(guarded(auditor, item)) for item in subset]
        with output.open("w", encoding="utf-8") as handle:
            for completed, future in enumerate(asyncio.as_completed(tasks), start=1):
                result = await future
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                handle.flush()
                print(
                    f"v8 shard {args.shard}: {completed}/{len(subset)} nr={result['nr']} "
                    f"verdict={result['verdict']} confidence={result['confidence']} "
                    f"attempts={result['runner_attempts']}",
                    flush=True,
                )


if __name__ == "__main__":
    asyncio.run(main())
