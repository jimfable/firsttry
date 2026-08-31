#!/usr/bin/env python3
import argparse, json, pathlib, re, statistics

POS = {"aufnehmen", "relevant", "include", "yes"}
NEG = {"nicht aufnehmen", "nicht relevant", "exclude", "no"}
DIGITAL_RE = re.compile(r"(?:bfsg|bitv|wcag|barrierefreiheits(?:erklärung|erklaerung)|digitale barrierefreiheit|screenreader|tastaturnavigation)", re.I)
SERVICE_RE = re.compile(r"(?:bad|bäder|baeder|badezimmer|dusche|sanitär|sanitaer|shk|wohnraum|umbau|sanierung|renovierung|fliesen|treppenlift|plattformlift|hublift|aufzug|rampe|pflegekasse|kfw|haltegriff|duschsitz)", re.I)
EXPLICIT_RE = re.compile(r"(?:barrierefrei|barrierearm|altersgerecht|seniorengerecht|generationenbad|behindertengerecht|rollstuhlgerecht|wohnraumanpassung|badewanne.{0,20}dusche|wohnumfeldverbesser)", re.I | re.S)

def norm(v):
    s = str(v or "").strip().lower()
    if s in POS: return "Aufnehmen"
    if s in NEG: return "Nicht aufnehmen"
    return ""

def text(row):
    return " ".join(str(row.get(k) or "") for k in ("reason","evidence","claim_scope","snippet","source_title","source_url","best_evidence"))

def positive_is_credible(row):
    t = text(row)
    source = str(row.get("source_url") or row.get("evidence_url") or "").strip()
    if not source:
        return False
    if DIGITAL_RE.search(t) and not SERVICE_RE.search(t):
        return False
    if not SERVICE_RE.search(t):
        return False
    return bool(EXPLICIT_RE.search(t) or row.get("explicit_service_evidence") is True or row.get("evidence_kind") in {"official_explicit","trusted_explicit"})

def load_rows(path):
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("rows") or data.get("results") or []
    return {int(r["nr"]): r for r in data}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--primary", required=True)
    ap.add_argument("--adversarial", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--validation", required=True)
    args=ap.parse_args()
    p=load_rows(args.primary); a=load_rows(args.adversarial)
    ids=sorted(set(p)|set(a))
    errors=[]; final=[]
    if len(ids)!=1041: errors.append(f"expected 1041 ids, got {len(ids)}")
    if set(p)!=set(a): errors.append(f"primary/adversarial id mismatch: {len(set(p)^set(a))}")
    for nr in ids:
        pr=p.get(nr,{}) ; ar=a.get(nr,{})
        pv=norm(pr.get("verdict")); av=norm(ar.get("verdict"))
        if not pv: errors.append(f"#{nr}: invalid primary verdict {pr.get('verdict')!r}")
        if not av: errors.append(f"#{nr}: invalid adversarial verdict {ar.get('verdict')!r}")
        pcred = pv=="Aufnehmen" and positive_is_credible(pr)
        acred = av=="Aufnehmen" and positive_is_credible(ar)
        if pcred or acred:
            verdict="Aufnehmen"
            chosen = ar if acred and (not pcred or str(ar.get("confidence"))=="Sehr hoch") else pr
            rationale = "Mindestens einer der zwei unabhängigen Durchgänge fand einen identitätsgebundenen, expliziten Leistungsnachweis; der Treffer bestand den Digital-Barrierefrei-Filter."
        else:
            verdict="Nicht aufnehmen"
            chosen = ar if av=="Nicht aufnehmen" else pr
            rationale = "Nach tiefem Primäraudit und unabhängigem adversarialen Gegencheck blieb kein belastbarer expliziter Nachweis für das spezialisierte Verzeichnis. Das Urteil betrifft die Aufnahmefähigkeit, nicht die metaphysische Behauptung, dass der Betrieb niemals solche Arbeiten ausführt."
        disagreement = pv != av
        confidence = "Sehr hoch" if (pv==av and ((verdict=="Aufnehmen" and pcred and acred) or verdict=="Nicht aufnehmen")) else "Hoch"
        source = chosen.get("source_url") or chosen.get("evidence_url") or pr.get("source_url") or ar.get("source_url") or ""
        reason = chosen.get("reason") or chosen.get("evidence") or rationale
        final.append({
            "nr":nr,
            "name":pr.get("name") or ar.get("name") or "",
            "city":pr.get("city") or ar.get("city") or "",
            "website":pr.get("website") or ar.get("website") or "",
            "verdict":verdict,
            "confidence":confidence,
            "reason":reason,
            "decision_scope":"Entscheidung über Aufnahme in ein spezialisiertes Verzeichnis für barrierefreien/altersgerechten Umbau.",
            "source_url":source,
            "primary_verdict":pv,
            "adversarial_verdict":av,
            "disagreement":disagreement,
            "primary_credible_positive":pcred,
            "adversarial_credible_positive":acred,
            "arbitration":rationale,
            "primary":pr,
            "adversarial":ar,
        })
    by={r["nr"]:r for r in final}
    positive_controls={109,161,702,1946}
    negative_controls={969,1062,2579,3250}
    for nr in positive_controls:
        if by.get(nr,{}).get("verdict")!="Aufnehmen": errors.append(f"positive control #{nr} failed")
    for nr in negative_controls:
        if by.get(nr,{}).get("verdict")!="Nicht aufnehmen": errors.append(f"negative control #{nr} failed")
    for r in final:
        if r["verdict"]=="Aufnehmen" and not r["source_url"]:
            errors.append(f"#{r['nr']}: positive lacks source")
        if r["confidence"] not in {"Hoch","Sehr hoch"}:
            errors.append(f"#{r['nr']}: invalid confidence")
    summary={
        "rows":len(final),
        "verdicts":{v:sum(r["verdict"]==v for r in final) for v in ("Aufnehmen","Nicht aufnehmen")},
        "confidence":{v:sum(r["confidence"]==v for r in final) for v in ("Sehr hoch","Hoch")},
        "disagreements":sum(r["disagreement"] for r in final),
        "positive_controls":{str(n):by.get(n,{}).get("verdict") for n in sorted(positive_controls)},
        "negative_controls":{str(n):by.get(n,{}).get("verdict") for n in sorted(negative_controls)},
        "validation_errors":errors,
    }
    pathlib.Path(args.out).write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding="utf-8")
    pathlib.Path(args.validation).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    if errors: raise SystemExit("final validation failed: "+" | ".join(errors[:40]))
if __name__=="__main__": main()
