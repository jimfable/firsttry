#!/usr/bin/env python3
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('finalize_v2', Path(__file__).with_name('finalize_v2.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

cases = [
    ({'verdict':'Aufnehmen','source_url':'https://betrieb.de/barrierefreiheit','reason':'Barrierefreiheitserklärung gemäß BFSG und WCAG, Tastaturnavigation und Screenreader.'}, False, 'digital accessibility'),
    ({'verdict':'Aufnehmen','source_url':'https://betrieb.de/bad','reason':'Wir planen und realisieren barrierefreie Badezimmer mit bodengleicher Dusche und Haltegriffen.'}, True, 'accessible bathroom service'),
    ({'verdict':'Aufnehmen','source_url':'https://betrieb.de/kontakt','reason':'Unsere Geschäftsräume sind barrierefrei erreichbar und verfügen über einen Behindertenparkplatz.'}, False, 'visitor accessibility'),
    ({'verdict':'Aufnehmen','source_url':'https://betrieb.de/projekt','reason':'Neubau einer barrierefreien Schule.'}, False, 'new-build reference'),
    ({'verdict':'Aufnehmen','source_url':'https://lift.de/treppenlift','reason':'Wir beraten, verkaufen und montieren Treppenlifte und Plattformlifte.'}, True, 'lift service'),
    ({'verdict':'Aufnehmen','source_url':'https://betrieb.de/wohnen','reason':'Wir sanieren altersgerecht und übernehmen Wohnraumanpassungen aus einer Hand.'}, True, 'housing adaptation'),
]
for row, expected, label in cases:
    actual = module.positive_is_credible(row)
    assert actual is expected, (label, actual, expected, row)
print('all authoritative evidence regression tests passed')
