#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
from typing import Any

POS = {"aufnehmen", "relevant", "include", "yes"}
NEG = {"nicht aufnehmen", "nicht relevant", "exclude", "no"}
DIGITAL_RE = re.compile(r"(?:bfsg|bitv|wcag|barrierefreiheits(?:erklärung|erklaerung)|digitale barrierefreiheit|screenreader|tastaturnavigation|kontrastmodus)", re.I)
SERVICE_RE = re.compile(r"(?:bad|bäder|baeder|badezimmer|dusche|sanitär|sanitaer|shk|wohnraum|umbau|sanierung|renovierung|fliesen|treppenlift|plattformlift|hublift|homelift|rollstuhllift|personenaufzug|aufzugsanlage|rampe|pflegekasse|kfw|haltegriff|duschsitz|türverbreiter|tuerverbreiter)", re.I)
EXPLICIT_RE = re.compile(r"(?:barrierefrei|barrierearm|altersgerecht|seniorengerecht|generationenbad|behindertengerecht|rollstuhlgerecht|wohnraumanpassung|badewanne.{0,20}dusche|wohnumfeldverbesser)", re.I | re.S)
LIFT_RE = re.compile(r"(?:treppenlift|plattformlift|hublift|homelift|rollstuhllift|personenaufzug|aufzugsanlage)", re.I)
OFFER_RE = re.compile(r"(?:wir\s+(?:bieten|planen|bauen|realisieren|sanieren|renovieren|installieren|montieren|verkaufen|vermieten|beraten)|leistungen|spezialist|fachbetrieb|beratung|planung|umsetzung|aus\s+einer\s+hand)", re.I)
VISITOR_RE = re.compile(r"(?:geschäftsräume|geschaeftsraeume|filiale|ausstellung).{0,80}(?:barrierefrei|rollstuhlgerecht)|(?:barrierefrei|rollstuhlgerecht).{0,80}(?:erreichbar|zugänglich|zugaenglich|behindertenparkplatz)", re.I | re.S)
NEWBUILD_ONLY_RE = re.compile(r"(?:neubau\s+(?:einer|des|von)|wohnanlage|schule|klinikum|bürogebäude|buero(?:gebäude|gebaeude)|öffentliches\s+gebäude|oeffentliches\s+gebaeude)", re.I)


def norm(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s in POS:
        return "Aufnehmen"
    if s in NEG:
        return "Nicht aufnehmen"
    return ""


def row_text(row: dict[str, Any]) -> str:
    parts = []
    for key in (
        "reason", "evidence", "claim_scope", "snippet", "source_title",
        "source_url", "best_evidence", "website"
    ):
        value = row.get(key)
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        parts.append(str(value or ""))
    return " ".join(parts)


def positive_is_credible(row: dict[str, Any]) -> bool:
    t = row_text(row)
    source = str(row.get("source_url") or row.get("evidence_url") or "").strip()
    if not source:
        return False
    explicit_flag = (
        row.get("explicit_service_evidence") is True
        or row.get("evidence_kind") in {"official_explicit", "trusted_explicit"}
    )
    # Digital-accessibility declarations and visitor-access statements are not construction services.
    if DIGITAL_RE.search(t) and not explicit_flag:
        return False
    if VISITOR_RE.search(t) and not explicit_flag and not OFFER_RE.search(t):
        return False
    # A one-off accessible new-build reference does not establish an adaptation service.
    if (
        NEWBUILD_ONLY_RE.search(t)
        and not explicit_flag
        and not re.search(r"(?:umbau|sanier|renov|wohnraumanpass|bad|dusche|lift|aufzug|rampe)", t, re.I)
    ):
        return False
    if explicit_flag:
        return bool(SERVICE_RE.search(t))
    if LIFT_RE.search(t) and OFFER_RE.search(t):
        return True
    return bool(EXPLICIT_RE.search(t) and SERVICE_RE.search(t) and OFFER_RE.search(t))


def load_rows(path: str) -> dict[int, dict[str, Any]]:
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("rows") or data.get("results") or []
    return {int(r["nr"]): r for r in data}


def choose_positive(primary: dict[str, Any], adversarial: dict[str, Any]) -> dict[str, Any] | None:
    candidates = []
    for row in (primary, adversarial):
        if positive_is_credible(row):
            score = 0.0
            if row.get("evidence_kind") == "official_explicit":
                score += 10
            elif row.get("evidence_kind") == "trusted_explicit":
                score += 6
            if row.get("explicit_service_evidence") is True:
                score += 4
            if str(row.get("confidence")) == "Sehr hoch":
                score += 2
            best = row.get("best_evidence") or {}
            if isinstance(best, dict):
                try:
                    score += float(best.get("score") or 0)
                except Exception:
                    pass
            candidates.append((score, row))
    return max(candidates, key=lambda x: x[0])[1] if candidates else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--primary", required=True)
    ap.add_argument("--adversarial", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--validation", required=True)
    args = ap.parse_args()

    primary = load_rows(args.primary)
    adversarial = load_rows(args.adversarial)
    ids = sorted(set(primary) | set(adversarial))
    errors: list[str] = []
    final: list[dict[str, Any]] = []

    if len(ids) != 1041:
        errors.append(f"expected 1041 ids, got {len(ids)}")
    if set(primary) != set(adversarial):
        errors.append(f"primary/adversarial id mismatch: {len(set(primary) ^ set(adversarial))}")

    for nr in ids:
        pr = primary.get(nr, {})
        ar = adversarial.get(nr, {})
        pv = norm(pr.get("verdict"))
        av = norm(ar.get("verdict"))
        if not pv:
            errors.append(f"#{nr}: invalid primary verdict {pr.get('verdict')!r}")
        if not av:
            errors.append(f"#{nr}: invalid adversarial verdict {ar.get('verdict')!r}")

        chosen_positive = choose_positive(pr, ar)
        if chosen_positive is not None:
            verdict = "Aufnehmen"
            chosen = chosen_positive
            arbitration = (
                "Mindestens einer der zwei unabhängigen Durchgänge fand einen identitätsgebundenen, "
                "expliziten Leistungsnachweis. Er bestand die Filter gegen digitale Barrierefreiheit, "
                "bloße Besucherzugänglichkeit und reine Neubau-Referenzen."
            )
        else:
            verdict = "Nicht aufnehmen"
            chosen = ar if av == "Nicht aufnehmen" else pr
            arbitration = (
                "Nach tiefem Primäraudit und unabhängigem adversarialem Gegencheck blieb kein "
                "belastbarer expliziter Nachweis, der den spezialisierten Aufnahme-Standard erfüllt. "
                "Das Urteil betrifft die Verzeichnisaufnahme, nicht die Behauptung, der Betrieb habe "
                "niemals eine entsprechende Einzelarbeit ausgeführt."
            )

        disagreement = pv != av
        if verdict == "Aufnehmen":
            both_positive = positive_is_credible(pr) and positive_is_credible(ar)
            confidence = "Sehr hoch" if both_positive else "Hoch"
        else:
            confidence = "Sehr hoch" if pv == av == "Nicht aufnehmen" else "Hoch"

        source = (
            chosen.get("source_url")
            or chosen.get("evidence_url")
            or pr.get("source_url")
            or ar.get("source_url")
            or pr.get("website")
            or ar.get("website")
            or ""
        )
        reason = chosen.get("reason") or chosen.get("evidence") or arbitration
        final.append({
            "nr": nr,
            "name": pr.get("name") or ar.get("name") or "",
            "city": pr.get("city") or ar.get("city") or "",
            "website": pr.get("website") or ar.get("website") or "",
            "verdict": verdict,
            "confidence": confidence,
            "reason": reason,
            "decision_scope": "Entscheidung über Aufnahme in ein spezialisiertes Verzeichnis für barrierefreien oder altersgerechten Umbau.",
            "source_url": source,
            "primary_verdict": pv,
            "adversarial_verdict": av,
            "disagreement": disagreement,
            "primary_credible_positive": positive_is_credible(pr),
            "adversarial_credible_positive": positive_is_credible(ar),
            "arbitration": arbitration,
            "primary": pr,
            "adversarial": ar,
        })

    by = {r["nr"]: r for r in final}
    positive_controls = {109, 161, 702, 1946}
    negative_controls = {969, 1062, 2579, 3250}
    for nr in positive_controls:
        if by.get(nr, {}).get("verdict") != "Aufnehmen":
            errors.append(f"positive control #{nr} failed")
    for nr in negative_controls:
        if by.get(nr, {}).get("verdict") != "Nicht aufnehmen":
            errors.append(f"negative control #{nr} failed")

    for row in final:
        if row["verdict"] == "Aufnehmen" and not row["source_url"]:
            errors.append(f"#{row['nr']}: positive lacks source")
        if row["confidence"] not in {"Hoch", "Sehr hoch"}:
            errors.append(f"#{row['nr']}: invalid confidence")
        if not row["reason"]:
            errors.append(f"#{row['nr']}: missing reason")

    summary = {
        "rows": len(final),
        "verdicts": {
            v: sum(r["verdict"] == v for r in final)
            for v in ("Aufnehmen", "Nicht aufnehmen")
        },
        "confidence": {
            v: sum(r["confidence"] == v for r in final)
            for v in ("Sehr hoch", "Hoch")
        },
        "disagreements": sum(r["disagreement"] for r in final),
        "positive_controls": {str(n): by.get(n, {}).get("verdict") for n in sorted(positive_controls)},
        "negative_controls": {str(n): by.get(n, {}).get("verdict") for n in sorted(negative_controls)},
        "validation_errors": errors,
    }
    pathlib.Path(args.out).write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    pathlib.Path(args.validation).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit("final validation failed: " + " | ".join(errors[:40]))


if __name__ == "__main__":
    main()
