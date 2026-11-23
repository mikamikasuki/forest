"""Conservative declared comparison scope, independent of model judgments.

Matching declarations establish a shared evaluation scope, not scientific
correctness. Records and protocols remain editable; no content addressing is
used. Absent or contradictory metadata never creates a comparison group.
"""
from __future__ import annotations

from copy import deepcopy
import math


REQUIRED_FIELDS = ('dataset', 'dataset_version', 'split', 'evaluation_protocol',
                   'metric', 'statistical_unit', 'budget')
ALIASES = {
    'dataset': ('dataset', 'dataset_id', 'datasets'),
    'dataset_version': ('dataset_version', 'data_version', 'dataset_versions'),
    'split': ('split', 'split_id', 'split_policy'),
    'evaluation_protocol': ('evaluation_protocol', 'evaluation'),
    'metric': ('metric', 'metric_definition'),
    'statistical_unit': ('statistical_unit', 'sampling_unit'),
    'budget': ('comparison_budget', 'fair_budget', 'budget'),
}


def _present(value):
    if value is None:
        return False
    if isinstance(value,bool): return True  # Explicit protocol flags are meaningful declarations.
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    if isinstance(value, dict):
        return bool(value) and all(isinstance(key, str) and key and _present(item)
                                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return bool(value) and all(_present(item) for item in value)
    return False


def _unique(values):
    result=[]
    for value in values:
        if value not in result: result.append(deepcopy(value))
    return result


def _read(record, path):
    value = record
    for part in path.strip('/').replace('/', '.').split('.'):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _records(run):
    records = []
    for original in (run.get('config') or {}, run.get('metrics') or {}):
        if not isinstance(original, dict):
            continue
        records.append(original)
        observed = original.get('observed_metrics')
        if isinstance(observed, dict):
            records.append(observed)
        for base in (original, observed):
            if isinstance(base, dict):
                for name in ('comparison_signature', 'comparison', 'protocol'):
                    if isinstance(base.get(name), dict):
                        records.append(base[name])
    return records


def comparison_declaration(run, objective=None):
    """Resolve complete declarations and preserve extra objective constraints."""
    objective = {} if objective is None else objective
    if not isinstance(objective,dict):
        raise ValueError('The comparison objective must be an object')
    records = _records(run)
    signature, missing, conflicting = {}, [], []
    for field in REQUIRED_FIELDS:
        values = [record[name] for record in records for name in ALIASES[field]
                  if name in record and _present(record[name])]
        if field=='budget':
            bases=[]
            for original in (run.get('config') or {},run.get('metrics') or {}):
                if not isinstance(original,dict):continue
                bases.append(original)
                if isinstance(original.get('observed_metrics'),dict):bases.append(original['observed_metrics'])
            explicit=[original[name]['budget'] for original in bases for name in ('comparison_signature','comparison','protocol')
                      if isinstance(original.get(name),dict) and _present(original[name].get('budget'))]
            explicit.extend(record[name] for record in records for name in ('comparison_budget','fair_budget')
                            if _present(record.get(name)))
            if explicit:
                values=explicit
            else:
                # Agent/execution spending and timeout limits are not the fair
                # scientific budget. Only a clearly scientific plain fallback
                # may fill this field.
                scientific={'training_seconds','evaluation_seconds','training_steps','epochs',
                            'tuning_trials','compute','device','samples','flops','tokens','evaluations'}
                values=[record['budget'] for record in records if isinstance(record.get('budget'),dict)
                        and scientific.intersection(record['budget']) and _present(record['budget'])]
        if field == 'dataset_version':
            values.extend(record['dataset']['version'] for record in records
                          if isinstance(record.get('dataset'), dict)
                          and _present(record['dataset'].get('version')))
        if field == 'dataset':
            values = [value.get('id', value.get('name'))
                      if isinstance(value, dict) and ('id' in value or 'name' in value)
                      else value for value in values]
        if field=='evaluation_protocol':
            values=[{key:item for key,item in value.items() if key not in ('id','revision')}
                    if isinstance(value,dict) and 'definition' in value else value for value in values]
        # The objective selects an explicitly named metric. A supplied metric
        # definition can add units/aggregation, rather than losing that context.
        if field=='metric':
            names=[value.get('id',value.get('name',value.get('path')))
                   if isinstance(value,dict) else value for value in values]
            if objective.get('metric'):names.append(objective['metric'])
            names=[name for name in names if _present(name)]
            if len(_unique(names))>1:conflicting.append(field)
            definition={}
            for value in values:
                if not isinstance(value,dict):continue
                for name,item in value.items():
                    if name in ('id','name','path'):continue
                    if name in definition and definition[name]!=item:conflicting.append(field)
                    definition[name]=deepcopy(item)
            values=[{'name':names[0],**definition} if definition else names[0]] if names else []
        identities = _unique(value for value in values if _present(value))
        if len(identities) > 1:
            conflicting.append(field)
        if not identities:
            missing.append(field)
        else:
            signature[field] = deepcopy(next(value for value in values if _present(value)))
    extras = {}
    fields = objective.get('comparison_fields', [])
    if not isinstance(fields, list) or any(not isinstance(field, str) or not field.strip() for field in fields):
        raise ValueError('comparison_fields must contain explicit field paths')
    if len(set(fields)) != len(fields):
        raise ValueError('comparison_fields must be unique')
    for field in fields:
        if field in REQUIRED_FIELDS:
            continue
        values = [_read(record, field) for record in records]
        values = [value for value in values if _present(value)]
        identities = _unique(values)
        if not identities:
            missing.append(field)
        elif len(identities) > 1:
            conflicting.append(field)
        else:
            extras[field] = deepcopy(values[0])
    if extras:
        signature['comparison_fields'] = extras
    complete = not missing and not conflicting
    return {'run_id': run['id'], 'complete': complete, 'signature': signature,
            'missing_fields': list(dict.fromkeys(missing)),
            'conflicting_fields': list(dict.fromkeys(conflicting)),
            'scope': 'Declared dataset/version, split, evaluation, metric, statistical unit and comparison budget; not independent scientific validation'}


def evaluation_classification(runs):
    """Classify the supplied runs, with feedback taking precedence."""
    contexts = [(run.get('config') or {}).get('evaluation_context') or {} for run in runs]
    if any(context.get('classification') == 'adaptive' or context.get('feedback_run_ids') for context in contexts):
        return 'adaptive'
    if contexts and all(context.get('classification') == 'independent_confirmation' for context in contexts):
        return 'independent_confirmation'
    return 'exploratory'


def assess_comparability(runs, objective=None):
    """Compare actual selected runs without treating omissions as equal."""
    if not isinstance(runs, list) or not runs:
        raise ValueError('Select a nonempty list of actual runs')
    ids = [run.get('id') for run in runs if isinstance(run, dict)]
    if len(ids) != len(runs) or any(not isinstance(ident, str) or not ident.strip() for ident in ids) or len(set(ids)) != len(ids):
        raise ValueError('Comparison requires unique nonempty run IDs')
    declarations = [comparison_declaration(run, objective) for run in runs]
    pending = [run['id'] for run in runs if run.get('status') != 'completed']
    if pending:
        status, reason = 'unverified', 'Every selected run must complete before measured comparison'
    elif any(row['conflicting_fields'] for row in declarations):
        status, reason = 'incomparable', 'A run contains contradictory comparison declarations'
    elif any(not row['complete'] for row in declarations):
        status, reason = 'unverified', 'Required scientific comparison declarations are missing'
    elif len(_unique(row['signature'] for row in declarations)) > 1:
        status, reason = 'incomparable', 'Dataset/version, split, evaluation, metric, statistical unit, budget or configured comparison fields differ; rerun under a shared protocol'
    elif len(runs) < 2:
        status, reason = 'unverified', 'A measured comparison requires at least two distinct completed runs'
    else:
        status, reason = 'directly_comparable', 'Complete declared scientific comparison scopes agree'
    classification = evaluation_classification(runs)
    return {'directly_comparable': status == 'directly_comparable', 'evaluation_classification':classification,
            'held_out_confirmation_eligible':classification=='independent_confirmation',
            'independent_held_out_confirmation':classification=='independent_confirmation' and status=='directly_comparable' and all(
                run.get('verification_status')=='accepted' and run.get('numerical_verification',{}).get('ready') is True for run in runs),
            'comparison_status': status, 'reason': reason,
            'declarations': declarations, 'noncompleted_run_ids': pending,
            'verification_scope': 'Declaration equality only; source-bound independent verification is separate'}
