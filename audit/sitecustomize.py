"""Deterministic source guards loaded automatically for audit scripts.

Python adds the script directory to sys.path before importing sitecustomize, so
`python audit/<script>.py` applies these idempotent fixes before audit modules load.
"""
from pathlib import Path

root = Path(__file__).resolve().parent

v3 = root / "exhaustive_v3.py"
if v3.exists():
    text = v3.read_text(encoding="utf-8")
    fixed = text.replace("underfahrbar", "unterfahrbar")
    if fixed != text:
        v3.write_text(fixed, encoding="utf-8")

base = root / "exhaustive.py"
if base.exists():
    text = base.read_text(encoding="utf-8")
    old = 'for _, obj in sorted(records, reverse=True)[:8]:'
    new = 'for _, obj in sorted(records, key=lambda item: (-item[0], str(item[1].get("url", ""))))[:8]:'
    fixed = text.replace(old, new)
    if fixed != text:
        base.write_text(fixed, encoding="utf-8")
