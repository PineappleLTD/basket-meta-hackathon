"""Read the frozen Day 1 snapshot without changing its proposals or approvals."""
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path

from .engine import utc
from .paths import DATA_ROOT

ROOT = DATA_ROOT
DEFAULT_DATASET = ROOT / 'training_dataset_v3_audit_20261101/annotations/revised_master_20000.DRAFT.csv'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temp.replace(path)


def model_input(row):
    # Deliberate allowlist: no labels, rationales, eventual outcomes or prices.
    return {
        'headline': row['headline'], 'summary': row.get('summary', ''),
        'published_at': row['published_at'], 'market_question': row['market_question'],
        'resolution_rules': row['resolution_rules'], 'resolution_deadline': row['deadline'],
        'held_outcome': row['outcome'], 'held_position_thesis': row['position_thesis'],
    }


def load_dataset(path=DEFAULT_DATASET):
    path = Path(path)
    with path.open(newline='') as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise ValueError('Dataset is empty.')
    seen = set()
    assignments = defaultdict(set)
    identities = defaultdict(set)
    for row in rows:
        if row['pair_id'] in seen:
            raise ValueError('Duplicate pair_id in dataset.')
        seen.add(row['pair_id'])
        if row['split'] not in {'train', 'dev', 'final'} or not row['story_group']:
            raise ValueError('Invalid dataset split or story group.')
        assignments[row['story_group']].add(row['split'])
        for key in ['article_id', 'source_group']:
            identities[(key, row[key])].add(row['split'])
    if any(len(v) != 1 for v in [*assignments.values(), *identities.values()]):
        raise ValueError('Story/article/source identity crosses dataset splits.')
    # Include the second audit's exposure without modifying the frozen CSV.
    exposed = {r['story_group'] for r in rows if r['audit_exposed_story_group'].lower() == 'true'}
    audit = ROOT / 'outputs/01a17639-c318-7e91-9111-fc4aa70ac66d/dataset-audit-revision-20261101-20000/audit-source.BLIND.json'
    if audit.exists():
        ids = {r['pair_id'] for r in json.loads(audit.read_text())['rows']}
        exposed.update(r['story_group'] for r in rows if r['pair_id'] in ids)
    excluded, eligible = [], []
    for row in rows:
        reasons = []
        if row['current_timing_screen'] != 'No exclusion flagged':
            reasons.append('timing_excluded_or_unsure')
        if row['current_related'] != 'true':
            reasons.append('unrelated_or_unresolved')
        if row['current_sentiment'] not in {'-1', '0', '1'}:
            reasons.append('missing_direction')
        if row['story_group'] in exposed:
            reasons.append('audit_exposed_group')
        try:
            if utc(row['published_at']) >= utc(row['deadline']):
                reasons.append('at_or_after_deadline')
            if row['outcome'] not in {'YES', 'NO'} or any(not str(v).strip() for v in model_input(row).values()):
                reasons.append('invalid_input')
            if len(row['headline']) > 2000 or len(row['summary']) > 12000:
                reasons.append('input_too_long')
        except (ValueError, TypeError):
            reasons.append('invalid_input')
        if reasons:
            excluded.append({'pair_id': row['pair_id'], 'split': row['split'], 'reasons': reasons})
        else:
            eligible.append(row)
    counts = Counter(reason for row in excluded for reason in row['reasons'])
    report = {
        'dataset': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'total_rows': len(rows), 'eligible_rows': len(eligible), 'excluded_rows': len(excluded),
        'exclusion_reason_counts_overlapping': dict(counts),
        'eligible_by_split': dict(Counter(r['split'] for r in eligible)),
        'eligible_labels_by_split': {s: dict(Counter(r['current_sentiment'] for r in eligible if r['split'] == s)) for s in ['train', 'dev', 'final']},
        'audit_exposed_groups_excluded': len(exposed), 'split_overlap': False,
        'label_source': 'Day 1 v3 AI/user proposals; not human gold',
        'source_history_verified': False, 'production_ready': False,
    }
    return eligible, excluded, report


def pilot_rows(rows, per_class=50):
    """A deterministic, group-diverse train-only engineering pilot."""
    selected = {}
    for label in ['-1', '0', '1']:
        pool = sorted((r for r in rows if r['split'] == 'train' and r['current_sentiment'] == label), key=lambda r: digest(r['pair_id']))
        groups = set()
        for row in pool:
            if row['story_group'] not in groups:
                selected[row['pair_id']] = row
                groups.add(row['story_group'])
            if len(groups) >= per_class:
                break
        if len(groups) < per_class:
            raise ValueError('Not enough distinct training groups for this pilot size.')
    return list(selected.values())
