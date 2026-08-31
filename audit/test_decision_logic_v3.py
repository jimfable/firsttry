#!/usr/bin/env python3
import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('finalize_v3',Path(__file__).with_name('finalize_v3.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

cases=[
 ({'source_url':'https://x.de/bfsg','best_evidence':{'snippet':'Barrierefreiheitserklärung nach BFSG und WCAG. Footer: Bad Heizung Sanitär.'}},False,'BFSG plus footer'),
 ({'source_url':'https://x.de/bad','best_evidence':{'snippet':'Wir planen und realisieren barrierefreie Badezimmer mit bodengleicher Dusche und Haltegriffen.'}},True,'explicit accessible bath'),
 ({'source_url':'https://x.de/kontakt','best_evidence':{'snippet':'Unsere Geschäftsräume sind barrierefrei erreichbar. In unserer Ausstellung beraten wir zu Bad und Heizung.'}},False,'visitor premises plus bath footer'),
 ({'source_url':'https://x.de/referenz','best_evidence':{'snippet':'Neubau einer barrierefreien Schule mit modernem Sanitärbereich.'}},False,'newbuild project'),
 ({'source_url':'https://lift.de','best_evidence':{'snippet':'Wir beraten, verkaufen und montieren Treppenlifte und Plattformlifte.'}},True,'lift offer'),
 ({'source_url':'https://x.de/wohnen','best_evidence':{'snippet':'Wir sanieren altersgerecht und übernehmen Wohnraumanpassungen aus einer Hand.'}},True,'housing adaptation'),
]
for row,want,label in cases:
    got=m.positive_is_credible(row)
    assert got is want,(label,got,want,row)
print('strict context decision tests passed')
