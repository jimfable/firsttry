#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
from typing import Any

POS = {"aufnehmen", "relevant", "include", "yes"}
NEG = {"nicht aufnehmen", "nicht relevant", "exclude", "no"}
DIGITAL = re.compile(r"(?:bfsg|bitv|wcag|barrierefreiheits(?:erklärung|erklaerung)|digitale\s+barrierefreiheit|screenreader|tastaturnavigation|kontrastmodus)", re.I)
EXPLICIT = re.compile(r"(?:barrierefrei|barrierearm|altersgerecht|seniorengerecht|generationenbad|behindertengerecht|rollstuhlgerecht|wohnraumanpassung|badewanne.{0,20}dusche|wohnumfeldverbesser)", re.I | re.S)
SERVICE = re.compile(r"(?:bad|bäder|baeder|badezimmer|dusche|wanne|sanitär|sanitaer|shk|wohnraum|umbau|sanierung|renovierung|fliesen|treppenlift|plattformlift|hublift|homelift|rollstuhllift|personenaufzug|aufzugsanlage|rampe|pflegekasse|kfw|haltegriff|duschsitz|waschtisch|türverbreiter|tuerverbreiter)", re.I)
LIFT = re.compile(r"(?:treppenlift|plattformlift|hublift|homelift|rollstuhllift|personenaufzug|aufzugsanlage)", re.I)
ACTION = re.compile(r"(?:wir\s+(?:bieten|planen|bauen|realisieren|sanieren|renovieren|installieren|montieren|verkaufen|vermieten|beraten)|leistungen|unsere\s+leistungen|spezialist|fachbetrieb|beratung|planung|umsetzung|umbau|sanierung|renovierung|modernisierung|einbau|montage|aus\s+einer\s+hand)", re.I)
VISITOR = re.compile(r"(?:geschäftsräume|geschaeftsraeume|filiale|ausstellung).{0,100}(?:barrierefrei|rollstuhlgerecht)|(?:barrierefrei|rollstuhlgerecht).{0,100}(?:erreichbar|zugänglich|zugaenglich|behindertenparkplatz)", re.I | re.S)
NEWBUILD = re.compile(r"(?:neubau\s+(?:einer|des|von)|wohnanlage|schule|klinikum|bürogebäude|buero(?:gebäude|gebaeude)|öffentliches\s+gebäude|oeffentliches\s+gebaeude)", re.I)
ADAPT = re.compile(r"(?:umbau|sanier|renov|modernis|wohnraumanpass|bad|badezimmer|dusche|wanne|haltegriff|duschsitz|lift|aufzug|rampe|türverbreiter|tuerverbreiter)", re.I)


def norm(value: Any) -> str:
    text = str(value or '').strip().lower()
    if text in POS:
        return 'Aufnehmen'
    if text in NEG:
        return 'Nicht aufnehmen'
    return ''


def evidence_text(row: dict[str, Any]) -> str:
    best = row.get('best_evidence')
    if isinstance(best, dict) and best.get('snippet'):
        return str(best['snippet'])
    evidence = row.get('evidence')
    if isinstance(evidence, str) and evidence.strip():
        return evidence
    snippet = row.get('snippet')
    if isinstance(snippet, str) and snippet.strip():
        return snippet
    return str(row.get('reason') or '')


def positive_is_credible(row: dict[str, Any]) -> bool:
    source = str(row.get('source_url') or row.get('evidence_url') or '').strip()
    if not source:
        return False
    context = evidence_text(row)
    if not context:
        return False
    # These exclusions act on the evidence window itself, never on a whole page plus footer.
    if DIGITAL.search(context):
        return False
    if VISITOR.search(context) and not (ACTION.search(context) and ADAPT.search(context)):
        return False
    if NEWBUILD.search(context) and not re.search(r"(?:umbau|sanier|renov|modernis|wohnraumanpass)", context, re.I):
        return False
    if not SERVICE.search(context):
        return False
    if LIFT.search(context):
        return bool(re.search(r"(?:montage|einbau|verkauf|mieten|vermiet|beratung|service|wartung|planung|nachrüstung|nachruestung)", context, re.I))
    return bool(EXPLICIT.search(context) and ACTION.search(context))


def load_rows(path: str) -> dict[int, dict[str, Any]]:
    data = json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
    if isinstance(data, dict):
        data = data.get('rows') or data.get('results') or []
    return {int(row['nr']): row for row in data}


def choose_positive(*rows: dict[str, Any]) -> dict[str, Any] | None:
    candidates = []
    for row in rows:
        if not positive_is_credible(row):
            continue
        score = 0.0
        if row.get('evidence_kind') == 'official_explicit':
            score += 10
        elif row.get('evidence_kind') == 'trusted_explicit':
            score += 6
        if str(row.get('confidence')) == 'Sehr hoch':
            score += 2
        best = row.get('best_evidence') or {}
        if isinstance(best, dict):
            try:
                score += float(best.get('score') or 0)
            except Exception:
                pass
        candidates.append((score, row))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--primary', required=True)
    parser.add_argument('--adversarial', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--validation', required=True)
    args = parser.parse_args()

    primary = load_rows(args.primary)
    adversarial = load_rows(args.adversarial)
    ids = sorted(set(primary) | set(adversarial))
    errors: list[str] = []
    output: list[dict[str, Any]] = []
    if len(ids) != 1041:
        errors.append(f'expected 1041 ids, got {len(ids)}')
    if set(primary) != set(adversarial):
        errors.append(f'primary/adversarial id mismatch: {len(set(primary) ^ set(adversarial))}')

    for nr in ids:
        first = primary.get(nr, {})
        second = adversarial.get(nr, {})
        first_verdict = norm(first.get('verdict'))
        second_verdict = norm(second.get('verdict'))
        if not first_verdict:
            errors.append(f'#{nr}: invalid primary verdict')
        if not second_verdict:
            errors.append(f'#{nr}: invalid adversarial verdict')
        chosen = choose_positive(first, second)
        if chosen is not None:
            verdict = 'Aufnehmen'
            confidence = 'Sehr hoch' if positive_is_credible(first) and positive_is_credible(second) else 'Hoch'
            arbitration = 'Mindestens ein identitätsgebundener, expliziter Leistungsabschnitt bestand die strikten Kontextfilter und die unabhängige Gegenprüfung.'
            reason = chosen.get('reason') or chosen.get('evidence') or arbitration
        else:
            verdict = 'Nicht aufnehmen'
            confidence = 'Sehr hoch' if first_verdict == second_verdict == 'Nicht aufnehmen' else 'Hoch'
            arbitration = ('Nach zwei vollständigen, getrennt ausgeführten Recherchewegen blieb kein identitätsgebundener expliziter Leistungsabschnitt, der den spezialisierten Aufnahme-Standard erfüllt. Das ist eine Aufnahmeentscheidung, keine Behauptung über jede theoretisch mögliche Einzelarbeit.')
            reason = arbitration
            chosen = second if second_verdict == 'Nicht aufnehmen' else first
        source = (chosen.get('source_url') or chosen.get('evidence_url') or first.get('source_url') or second.get('source_url') or first.get('website') or second.get('website') or '')
        output.append({
            'nr': nr,
            'name': first.get('name') or second.get('name') or '',
            'city': first.get('city') or second.get('city') or '',
            'website': first.get('website') or second.get('website') or '',
            'verdict': verdict,
            'confidence': confidence,
            'reason': reason,
            'decision_scope': 'Aufnahme in ein spezialisiertes Verzeichnis für barrierefreien oder altersgerechten Umbau.',
            'source_url': source,
            'primary_verdict': first_verdict,
            'adversarial_verdict': second_verdict,
            'disagreement': first_verdict != second_verdict,
            'primary_credible_positive': positive_is_credible(first),
            'adversarial_credible_positive': positive_is_credible(second),
            'arbitration': arbitration,
            'primary': first,
            'adversarial': second,
        })

    by = {row['nr']: row for row in output}
    positive_controls = {109, 161, 702, 1946}
    negative_controls = {969, 1062, 2579, 3250}
    for nr in positive_controls:
        if by.get(nr, {}).get('verdict') != 'Aufnehmen':
            errors.append(f'positive control #{nr} failed')
    for nr in negative_controls:
        if by.get(nr, {}).get('verdict') != 'Nicht aufnehmen':
            errors.append(f'negative control #{nr} failed')
    for row in output:
        if row['verdict'] == 'Aufnehmen' and not row['source_url']:
            errors.append(f"#{row['nr']}: positive lacks source")
        if row['confidence'] not in {'Hoch', 'Sehr hoch'}:
            errors.append(f"#{row['nr']}: invalid confidence")
        if not row['reason']:
            errors.append(f"#{row['nr']}: missing reason")

    validation = {
        'rows': len(output),
        'verdicts': {value: sum(row['verdict'] == value for row in output) for value in ('Aufnehmen', 'Nicht aufnehmen')},
        'confidence': {value: sum(row['confidence'] == value for row in output) for value in ('Sehr hoch', 'Hoch')},
        'disagreements': sum(row['disagreement'] for row in output),
        'positive_controls': {str(nr): by.get(nr, {}).get('verdict') for nr in sorted(positive_controls)},
        'negative_controls': {str(nr): by.get(nr, {}).get('verdict') for nr in sorted(negative_controls)},
        'validation_errors': errors,
    }
    pathlib.Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    pathlib.Path(args.validation).write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit('final validation failed: ' + ' | '.join(errors[:50]))


if __name__ == '__main__':
    main()
