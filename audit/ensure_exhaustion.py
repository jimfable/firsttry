#!/usr/bin/env python3
from pathlib import Path

patches = {
    "audit/adversarial.py": (
'''                    for score, result in sorted(ranked, key=lambda x: -x[0]):
                        if fetched >= MAX_RESULT_FETCHES:
                            break
                        if score < 5:
                            continue
                        seen.add(result["url"])
                        await self.evaluate_url(result["url"], result["title"], result["snippet"], result["provider"], state, round_name)
                        fetched += 1
                    if fetched >= MAX_RESULT_FETCHES:
                        break
                if fetched >= MAX_RESULT_FETCHES:
                    break
            if fetched >= MAX_RESULT_FETCHES:
                break
''',
'''                    for score, result in sorted(ranked, key=lambda x: -x[0]):
                        if score < 5 or result["url"] in seen:
                            continue
                        seen.add(result["url"])
                        # Every query is issued. The cap limits page downloads only.
                        if fetched < MAX_RESULT_FETCHES:
                            await self.evaluate_url(result["url"], result["title"], result["snippet"], result["provider"], state, round_name)
                            fetched += 1
'''),
    "audit/saturation.py": (
'''                        for x in await self.search(q,state):
                            if fetched>=MAX_FETCH: break
                            if x["url"] in seen: continue
                            seen.add(x["url"]); await self.evaluate(x,state,"long-tail-search"); fetched+=1
                        if fetched>=MAX_FETCH: break
                    if fetched>=MAX_FETCH: break
''',
'''                        for x in await self.search(q,state):
                            if x["url"] in seen: continue
                            seen.add(x["url"])
                            # Issue every long-tail query; cap only page downloads.
                            if fetched < MAX_FETCH:
                                await self.evaluate(x,state,"long-tail-search"); fetched+=1
'''),
}

for filename, (old, new) in patches.items():
    path = Path(filename)
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Expected one exhaustion patch target in {filename}, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")
    print(f"Patched {filename}")
