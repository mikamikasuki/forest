"""Statistical evidence for tables, curves and prose, without file or model I/O.

Runs contain ``id`` and their original ``metrics`` object.  The explicit input
protocol is ``metrics.statistics = {observations, metrics, design, uncertainty,
comparisons}``.  An observation has dataset, method, metric, finite value, and
seed and/or unit_id; condition and x are optional.  Metric definitions declare
direction (higher/lower), unit and optional display precision.  Design declares
expected datasets, methods, metrics, seeds/units, conditions and x values.

Uncertainty defaults to descriptive SD. SE, Student-t intervals and bootstrap
intervals require an explicit ``independent: true`` declaration. ``unit`` selects
seed or unit_id. Repeated measurements are averaged *within* that unit first;
the resulting independent units receive equal weight. Object intervals condition
on the supplied seeds; seed intervals condition on supplied datasets/objects.
The caller must justify independence and the sampling target. A single unit has
no estimated uncertainty. Duplicate observations are errors unless every input
declares ``duplicate_policy: 'merge_identical'``; even then values must agree.

Legacy per_seed/summary/comparisons artifacts are admitted with their identities;
identical legacy observations use a recorded exact-identity deduplication policy.
Summary rows are never pooled as repetitions or used to manufacture intervals.
Their reported SD is retained only with at least two reported repetitions.

Output records identify dataset, method, metric, condition and x, carry the
estimand, uncertainty and actual source locations, and are grouped for display.
``attach_statistical_evidence`` resolves those locations to original metric IDs
and adds derived ``statmN`` entries with artifact='statistical_results'. All
numeric output leaves are bound to real pointers in the statistics result tree.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from itertools import product
import json
import math
from typing import Any, Literal, TypedDict

import numpy as np
from scipy import stats


class StatisticalRecord(TypedDict, total=False):
    id: str
    dataset: str
    method: str
    metric: str
    direction: Literal['higher', 'lower']
    unit: str
    precision: int
    condition: Any
    x: Any
    estimate: float
    sd: float | None
    se: float | None
    ci_low: float | None
    ci_high: float | None
    n_units: int
    n_seeds: int
    uncertainty: dict
    source_locations: list[dict]
    source_refs: list[str]
    refs: dict[str, str]


_KNOWN = {
    'accuracy': ('higher', 'fraction'), 'auc': ('higher', 'fraction'),
    'roc_auc': ('higher', 'fraction'), 'f1': ('higher', 'fraction'),
    'precision': ('higher', 'fraction'), 'recall': ('higher', 'fraction'),
    'brier': ('lower', 'score'), 'log_loss': ('lower', 'nats'),
    'ece': ('lower', 'fraction'), 'mse': ('lower', 'squared units'),
    'rmse': ('lower', 'units'), 'mae': ('lower', 'units'),
    'fit_seconds': ('lower', 's'), 'seconds': ('lower', 's'),
    'latency': ('lower', 's'), 'parameters': ('lower', 'count'),
}
_IDENTITY = ('dataset', 'method', 'metric', 'condition', 'x')
_VALUE_FIELDS = ('estimate', 'sd', 'se', 'ci_low', 'ci_high', 'confidence',
                 'n_units', 'n_seeds', 'n_observations', 'improvement', 'n_pairs', 'p_value', 'adjusted_p')


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError) as exc:
        raise ValueError('Statistical identities must be finite JSON values') from exc


def _key(row, fields=_IDENTITY):
    return tuple(_canonical(row.get(field)) for field in fields)


def _identity(row):
    return {field: deepcopy(row.get(field)) for field in _IDENTITY}


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite numeric measurement')
    return float(value)


def _label(value, name):
    if value is None or isinstance(value, (dict, list, bool)) or not str(value).strip():
        raise ValueError(f'Every statistical row requires an explicit {name}')
    return str(value)


def _pointer(*parts):
    return '/' + '/'.join(str(part).replace('~', '~0').replace('/', '~1') for part in parts)


def _locations(values):
    return [dict(zip(('run_id', 'pointer'), item)) for item in sorted(set(
        (str(value['run_id']), value['pointer']) for value in values))]


def _definition(metric, definitions):
    definition = deepcopy(definitions.get(metric, {}))
    known = _KNOWN.get(metric)
    if 'direction' not in definition and known:
        definition['direction'] = known[0]
    if definition.get('direction') not in {'higher', 'lower'}:
        raise ValueError(f'Metric {metric!r} requires direction higher or lower')
    if 'unit' not in definition and known:
        definition['unit'] = known[1]
    if not isinstance(definition.get('unit'), str) or not definition['unit'].strip():
        raise ValueError(f'Metric {metric!r} requires an explicit unit')
    precision = definition.get('precision', 3)
    if isinstance(precision, bool) or not isinstance(precision, int) or not 0 <= precision <= 10:
        raise ValueError('Metric precision must be an integer between zero and ten')
    definition['precision'] = precision
    return definition


def _uncertainty(config, has_objects):
    config = deepcopy(config or {})
    kind = config.get('type', 'sd')
    if kind not in {'sd', 'se', 't_ci', 'bootstrap', 'none'}:
        raise ValueError('Uncertainty type must be sd, se, t_ci, bootstrap or none')
    unit = config.get('unit')
    if unit is None:
        if has_objects:
            raise ValueError('Object observations require an explicit uncertainty.unit (seed or unit_id)')
        unit = 'seed'
    if unit not in {'seed', 'unit_id'}:
        raise ValueError('uncertainty.unit must identify seed or unit_id')
    if kind in {'se', 't_ci', 'bootstrap'} and config.get('independent') is not True:
        raise ValueError('Inferential uncertainty requires independent:true for the chosen statistical unit')
    confidence = _number(config.get('confidence', 0.95), 'confidence')
    if not 0 < confidence < 1:
        raise ValueError('Confidence must be between zero and one')
    samples = config.get('samples', 2000)
    if kind == 'bootstrap' and (isinstance(samples, bool) or not isinstance(samples, int) or samples < 100):
        raise ValueError('Bootstrap requires at least 100 integer draws')
    random_seed = config.get('seed', 0)
    if isinstance(random_seed, bool) or not isinstance(random_seed, int) or random_seed < 0:
        raise ValueError('Bootstrap random seed must be a nonnegative integer')
    return {**config, 'type': kind, 'unit': unit, 'confidence': confidence,
            'samples': samples, 'seed': random_seed}


def _describe(values, uncertainty):
    values = np.asarray(values, dtype=float)
    n = len(values)
    if not n or not np.isfinite(values).all():
        raise ValueError('Statistical summaries require finite observed units')
    estimate = float(values.mean())
    sd = float(values.std(ddof=1)) if n > 1 else None
    kind = uncertainty['type'] if n > 1 else 'none'
    result = {'estimate': estimate, 'sd': sd, 'se': None, 'ci_low': None,
              'ci_high': None, 'confidence': None}
    if kind in {'se', 't_ci', 'bootstrap'}:
        result['se'] = sd / math.sqrt(n)
    if kind == 't_ci':
        half = float(stats.t.ppf((1 + uncertainty['confidence']) / 2, n - 1)) * result['se']
        result.update(ci_low=estimate - half, ci_high=estimate + half,
                      confidence=uncertainty['confidence'])
    elif kind == 'bootstrap':
        rng = np.random.default_rng(uncertainty['seed'])
        samples = uncertainty['samples']
        # Bound memory independently of the number of observations.
        draws = np.empty(samples)
        chunk = max(1, min(samples, 1_000_000 // n))
        for start in range(0, samples, chunk):
            count = min(chunk, samples - start)
            draws[start:start + count] = values[rng.integers(0, n, size=(count, n))].mean(axis=1)
        alpha = (1 - uncertainty['confidence']) / 2
        low, high = np.quantile(draws, [alpha, 1 - alpha])
        result.update(ci_low=float(low), ci_high=float(high), confidence=uncertainty['confidence'])
    result['uncertainty'] = {
        'type': kind, 'unit': uncertainty['unit'],
        'confidence': result['confidence'], 'independent': uncertainty.get('independent', False),
        'scope': uncertainty.get('scope') or (
            'Across supplied object identities, conditional on supplied seeds and datasets'
            if uncertainty['unit'] == 'unit_id' else
            'Across supplied seeds, conditional on supplied datasets and measured objects'),
        'aggregation': 'Equal-weight unit means; repeated measurements averaged within unit',
    }
    if kind == 'bootstrap':
        result['uncertainty'].update(samples=uncertainty['samples'], seed=uncertainty['seed'],
                                     interval='percentile')
    return result


def _protocol(run):
    metrics = run.get('metrics')
    if not isinstance(metrics, dict):
        raise ValueError('Runs require their original metrics object')
    computed = metrics.get('statistical_results')
    if computed is None and isinstance(metrics.get('statistics'), dict) and 'records' in metrics['statistics'] and 'observations' not in metrics['statistics']:
        computed = metrics['statistics']
    if computed is not None:
        if not isinstance(computed, dict) or not isinstance(computed.get('records'), list):
            raise ValueError('statistical_results requires typed records')
        prefix = ['statistical_results'] if 'statistical_results' in metrics else ['statistics']
        return {'_computed': computed, 'metrics': computed.get('metrics', {}),
                'axes': computed.get('axes', {}), 'duplicate_policy': 'merge_identical'}, prefix, 'computed'
    explicit = metrics.get('statistics')
    if explicit is None and 'observations' in metrics:
        explicit = metrics
    if explicit is not None:
        if not isinstance(explicit, dict) or not isinstance(explicit.get('observations'), list):
            raise ValueError('statistics requires an observations array')
        prefix = [] if explicit is metrics else ['statistics']
        return explicit, prefix, 'explicit'
    if isinstance(metrics.get('per_seed'), list) or isinstance(metrics.get('summary'), list):
        context = run.get('method_context') or {}
        config = context.get('statistics', {}) if isinstance(context, dict) else {}
        return {**config, 'per_seed': metrics.get('per_seed', []),
                'summary': metrics.get('summary', []), 'comparisons': metrics.get('comparisons', []),
                'statistical_scope': metrics.get('statistical_scope')}, [], 'legacy'
    return None, [], None


def _context_condition(run, protocol):
    """Keep declared experimental conditions distinct from evidence scope.

    Scientific configurations are explicit named fields, not guesses extracted
    from commands or prose. Saved config.json may carry the original experiment
    configuration. Selection, randomness and analysis controls do not define a
    new condition; substantive fields such as model/training budgets do.
    """
    if 'condition' in protocol:
        return deepcopy(protocol['condition'])
    context = run.get('method_context') or {}
    if not isinstance(context, dict):
        return None
    for key in ('experimental_condition', 'condition'):
        if key in context:
            return deepcopy(context[key])
    settings = {}
    for key in ('hyperparameters', 'model_config', 'training_config', 'evaluation_config'):
        if key in context:
            settings[key] = deepcopy(context[key])
    saved = context.get('config.json')
    if isinstance(saved, dict):
        controls = {'datasets', 'methods', 'expected_methods', 'seeds', 'seed', 'bootstrap_seed',
                    'bootstrap_samples', 'ece_bins', 'confidence', 'candidate', 'primary_baseline',
                    'comparison_baselines', 'protocol_version', 'dataset_files', 'output_dir',
                    'input_run_id', 'input_run_ids', 'run_ids', 'statistics'}
        substantive = {key: deepcopy(value) for key, value in saved.items() if key not in controls}
        if substantive:
            settings['configuration'] = substantive
    return settings or None


def _axes(protocols):
    axes = {}
    for _, protocol, _, _ in protocols:
        supplied = protocol.get('axes', {})
        if not isinstance(supplied, dict):
            raise ValueError('Statistical axes must be an object')
        for axis, definition in supplied.items():
            if not isinstance(definition, dict):
                raise ValueError('Axis definitions require label and unit')
            clean = {key: deepcopy(definition[key]) for key in ('label', 'unit', 'scale') if key in definition}
            if axis in axes and axes[axis] != clean:
                raise ValueError(f'Conflicting axis definition {axis}')
            axes[axis] = clean
    return axes


def _merge_definitions(protocols):
    definitions = {}
    for _, protocol, _, _ in protocols:
        specs = protocol.get('metrics', {})
        if not isinstance(specs, dict):
            raise ValueError('Statistical metrics must map metric names to definitions')
        for metric, definition in specs.items():
            if not isinstance(definition, dict):
                raise ValueError('Metric definitions must be objects')
            normalized = _definition(metric, {metric: definition})
            if metric in definitions and normalized != definitions[metric]:
                raise ValueError(f'Conflicting definition of metric {metric}')
            definitions[metric] = normalized
    return definitions


def _observations(protocols, definitions, conditions):
    observations, summaries, requests, policies = [], [], [], []
    for run_id, protocol, prefix, kind in protocols:
        policy = protocol.get('duplicate_policy', 'merge_identical' if kind == 'legacy' else 'error')
        if policy not in {'error', 'merge_identical'}:
            raise ValueError('duplicate_policy must be error or merge_identical')
        policies.append(policy)
        if kind == 'computed':
            continue
        if kind == 'explicit':
            rows = protocol['observations']
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise ValueError('Observations must be objects')
                metric = _label(row.get('metric'), 'metric')
                definitions.setdefault(metric, _definition(metric, definitions))
                value = _number(row.get('value'), 'Observation value')
                if row.get('seed') is None and row.get('unit_id') is None:
                    raise ValueError('Every observation requires seed or unit_id')
                for axis in ('seed', 'unit_id', 'condition', 'x'):
                    _canonical(row.get(axis))
                observation = {**_identity(row), 'condition': deepcopy(row.get('condition', conditions[run_id])),
                               'dataset': _label(row.get('dataset'), 'dataset'),
                               'method': _label(row.get('method'), 'method'), 'metric': metric,
                               'value': value, 'seed': row.get('seed'), 'unit_id': row.get('unit_id'),
                               'config': protocol.get('uncertainty', {}), 'policy': policy,
                               'source_locations': [{'run_id': run_id, 'pointer': _pointer(*prefix, 'observations', index, 'value')}]}
                observations.append(observation)
        else:
            for index, row in enumerate(protocol['per_seed']):
                if not isinstance(row, dict):
                    raise ValueError('per_seed rows must be objects')
                if row.get('seed') is None:
                    raise ValueError('per_seed rows require a seed identity')
                metric_names = set(definitions) | (set(row) & set(_KNOWN))
                for metric in sorted(metric_names):
                    if row.get(metric) is None:
                        continue
                    definitions.setdefault(metric, _definition(metric, definitions))
                    observations.append({**_identity(row), 'condition': deepcopy(row.get('condition', conditions[run_id])),
                                         'dataset': _label(row.get('dataset'), 'dataset'),
                                         'method': _label(row.get('method'), 'method'), 'metric': metric,
                                         'value': _number(row[metric], metric), 'seed': row['seed'], 'unit_id': None,
                                         'config': protocol.get('uncertainty', {'type': 'sd', 'unit': 'seed'}),
                                         'policy': policy, 'source_locations': [{'run_id': run_id, 'pointer': _pointer('per_seed', index, metric)}]})
            for index, row in enumerate(protocol['summary']):
                if not isinstance(row, dict):
                    raise ValueError('summary rows must be objects')
                metric_names = set(definitions) | (set(row) & set(_KNOWN))
                for metric in sorted(metric_names):
                    if row.get(metric) is None:
                        continue
                    definitions.setdefault(metric, _definition(metric, definitions))
                    locations = [{'run_id': run_id, 'pointer': _pointer('summary', index, metric)}]
                    n_key = next((key for key in ('seeds', 'n_units', 'n') if row.get(key) is not None), None)
                    n = row[n_key] if n_key else None
                    if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 1):
                        raise ValueError('Summary repetition counts must be positive integers')
                    if n_key:
                        locations.append({'run_id': run_id, 'pointer': _pointer('summary', index, n_key)})
                    std = row.get(metric + '_std')
                    if std is not None:
                        std = _number(std, metric + '_std')
                        if std < 0:
                            raise ValueError('Reported SD cannot be negative')
                        locations.append({'run_id': run_id, 'pointer': _pointer('summary', index, metric + '_std')})
                    summaries.append({**_identity(row), 'condition': deepcopy(row.get('condition', conditions[run_id])),
                                      'dataset': _label(row.get('dataset'), 'dataset'),
                                      'method': _label(row.get('method'), 'method'), 'metric': metric,
                                      'value': _number(row[metric], metric), 'sd': std, 'n': n,
                                      'seed_count_reported': n_key == 'seeds', 'policy': policy,
                                      'source_locations': locations, 'scope': protocol.get('statistical_scope')})
        for index, request in enumerate(protocol.get('comparisons', [])):
            if not isinstance(request, dict):
                raise ValueError('Comparisons must be objects')
            request = deepcopy(request)
            if conditions[run_id] is not None and 'condition' not in request:
                request['condition'] = deepcopy(conditions[run_id])
            requests.append({'request': request, 'run_id': run_id,
                             'prefix': prefix + ['comparisons', index], 'kind': kind,
                             'uncertainty': protocol.get('uncertainty', {}), 'policy': policy})
    return observations, summaries, requests, policies


def _reported_results(protocols, definitions):
    records, comparisons = [], []
    for run_id, protocol, prefix, kind in protocols:
        if kind != 'computed':
            continue
        computed = protocol['_computed']
        for collection, target in (('records', records), ('comparisons', comparisons)):
            rows = computed.get(collection, [])
            if not isinstance(rows, list):
                raise ValueError('Computed statistical collections must be arrays')
            for position, supplied in enumerate(rows):
                if not isinstance(supplied, dict):
                    raise ValueError('Computed statistical records must be objects')
                row = deepcopy(supplied)
                metric = _label(row.get('metric'), 'metric')
                row['metric'] = metric
                row['dataset'] = _label(row.get('dataset'), 'dataset')
                definition = _definition(metric, {metric: {key: row[key] for key in ('direction', 'unit', 'precision') if key in row}})
                if metric in definitions and any(definitions[metric][key] != definition[key] for key in ('direction', 'unit', 'precision')):
                    raise ValueError(f'Conflicting definition of metric {metric}')
                definitions[metric] = definition
                row.update(definition)
                for axis in ('condition', 'x'):
                    row.setdefault(axis, None)
                    _canonical(row[axis])
                if collection == 'records':
                    row['method'] = _label(row.get('method'), 'method')
                    row['estimate'] = _number(row.get('estimate'), 'Computed estimate')
                else:
                    row['baseline'] = _label(row.get('baseline'), 'baseline')
                    row['candidate'] = _label(row.get('candidate'), 'candidate')
                    if row['baseline'] == row['candidate']:
                        raise ValueError('Computed comparisons require distinct methods')
                    row['improvement'] = _number(row.get('improvement'), 'Computed improvement')
                for field in ('sd', 'se', 'ci_low', 'ci_high', 'confidence', 'p_value', 'adjusted_p'):
                    if row.get(field) is not None:
                        row[field] = _number(row[field], field)
                    else:
                        row[field] = None
                if any(row[field] is not None and row[field] < 0 for field in ('sd', 'se')):
                    raise ValueError('Computed SD and SE must be nonnegative')
                if (row['ci_low'] is None) != (row['ci_high'] is None):
                    raise ValueError('Computed records require both interval endpoints')
                if row['ci_low'] is not None and row['ci_low'] > row['ci_high']:
                    raise ValueError('Computed interval endpoints are reversed')
                if row['ci_low'] is not None and (row['confidence'] is None or not 0 < row['confidence'] < 1):
                    raise ValueError('Computed intervals require a confidence level in (0,1)')
                for field in ('n_units', 'n_seeds', 'n_observations', 'n_pairs'):
                    count = row.get(field)
                    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < (0 if field == 'n_seeds' else 1)):
                        raise ValueError(f'Computed {field} requires a valid integer count')
                    row[field] = count
                uncertainty = row.get('uncertainty')
                if not isinstance(uncertainty, dict) or not isinstance(uncertainty.get('unit'), str) or not isinstance(uncertainty.get('scope'), str):
                    raise ValueError('Computed uncertainty requires its statistical unit and scope')
                if row['n_units'] == 1 and any(row.get(field) is not None for field in ('sd', 'se', 'ci_low', 'ci_high')):
                    raise ValueError('A single statistical unit cannot supply estimated uncertainty')
                for field in ('p_value', 'adjusted_p'):
                    if row[field] is not None and not 0 <= row[field] <= 1:
                        raise ValueError('Computed p-values must be in [0,1]')
                row['sampling_unit'] = row.get('sampling_unit', uncertainty['unit'])
                # Preserve historical provenance without treating it as a current
                # bundle location: this result is measured in the current run.
                row['upstream_source_locations'] = deepcopy(row.get('source_locations', []))
                row.pop('refs', None)
                row['source_refs'] = []
                row['source_locations'] = [
                    {'run_id': run_id, 'pointer': _pointer(*prefix, collection, position, field)}
                    for field in _VALUE_FIELDS if isinstance(supplied.get(field), (int, float)) and not isinstance(supplied[field], bool)]
                row['origin'] = 'reported_statistical_results'
                target.append(row)
    return records, comparisons


def _merge_reported(records, reported, *, comparison=False):
    fields = ('dataset', 'metric', 'condition', 'x', 'baseline', 'candidate') if comparison else _IDENTITY
    indexed = {_key(row, fields): row for row in records}
    for row in reported:
        identity = _key(row, fields)
        if identity not in indexed:
            row['id'] = ('c' if comparison else 's') + str(len(records))
            records.append(row)
            indexed[identity] = row
            continue
        current = indexed[identity]
        for field in ('direction', 'unit', 'sampling_unit'):
            if current.get(field) != row.get(field):
                raise ValueError(f'Overlapping statistical results disagree on {field}')
        # A reported result may supply uncertainty missing from a raw SD-only
        # summary, but cannot change a known estimate, count or interval.
        for field in _VALUE_FIELDS:
            left, right = current.get(field), row.get(field)
            if left is not None and right is not None and not math.isclose(float(left), float(right), rel_tol=1e-10, abs_tol=1e-12):
                raise ValueError(f'Overlapping statistical results disagree on {field}')
        if current.get('ci_low') is not None and row.get('ci_low') is not None:
            for field in ('type', 'unit', 'scope'):
                if current['uncertainty'].get(field) != row['uncertainty'].get(field):
                    raise ValueError('Overlapping intervals have different inferential scopes')
        if current.get('ci_low') is None and row.get('ci_low') is not None:
            current['uncertainty'] = deepcopy(row['uncertainty'])
        for field in _VALUE_FIELDS:
            if current.get(field) is None and row.get(field) is not None:
                current[field] = row[field]
        current['source_locations'] = _locations(current['source_locations'] + row['source_locations'])
        if row.get('upstream_source_locations'):
            current['upstream_source_locations'] = _locations(current.get('upstream_source_locations', []) + row['upstream_source_locations'])
        current['overlap_policy'] = 'Matching identity, estimates, counts and known uncertainty; merged sources counted once'
    return records


def _inherit_coverage(coverage, protocols, observations, records):
    available = {_key(row) for row in records}
    inherited = []
    for run_id, protocol, _, kind in protocols:
        if kind != 'computed':
            continue
        source = protocol['_computed'].get('coverage')
        if source is None:
            continue
        if not isinstance(source, dict) or not isinstance(source.get('complete'), bool):
            raise ValueError('Computed coverage requires a boolean complete flag')
        inherited.append({'run_id': run_id, 'complete': source['complete'],
                          'unit_grid_verified': source.get('unit_grid_verified', False)})
        for row in source.get('missing', []):
            if not isinstance(row, dict):
                raise ValueError('Computed coverage identities must be objects')
            if row.get('level', 'record') == 'record' and _key(row) in available:
                continue
            if row.get('level') == 'observation' and any(_key(item) == _key(row) and
                (row.get('seed') is None or _canonical(item['seed']) == _canonical(row['seed'])) and
                (row.get('unit_id') is None or _canonical(item['unit_id']) == _canonical(row['unit_id'])) for item in observations):
                continue
            if _canonical(row) not in {_canonical(item) for item in coverage['missing']}:
                coverage['missing'].append(deepcopy(row))
        for row in source.get('unexpected', []):
            if _canonical(row) not in {_canonical(item) for item in coverage['unexpected']}:
                coverage['unexpected'].append(deepcopy(row))
        if source.get('design_declared') is True:
            coverage['design_declared'] = True
        coverage['expected_count'] = max(coverage['expected_count'], source.get('expected_count', 0))
        if source.get('complete') is False and not source.get('missing') and not source.get('unexpected'):
            coverage['unverified_upstream'] = True
    if inherited:
        coverage['inherited'] = inherited
        if not observations:
            coverage['unit_grid_verified'] = all(item['unit_grid_verified'] for item in inherited)
        coverage['complete'] = not coverage['missing'] and not coverage['unexpected'] and not coverage.get('unverified_upstream', False)
    return coverage


def _deduplicate(observations):
    indexed, merged = {}, []
    for row in observations:
        identity = _key(row) + _key(row, ('seed', 'unit_id'))
        if identity not in indexed:
            indexed[identity] = deepcopy(row)
            continue
        previous = indexed[identity]
        if previous['value'] != row['value']:
            raise ValueError(f'Conflicting observation identity: {_identity(row)}, seed={row["seed"]}, unit_id={row["unit_id"]}')
        if previous['policy'] != 'merge_identical' or row['policy'] != 'merge_identical':
            raise ValueError('Duplicate observation identity requires duplicate_policy:merge_identical on every source')
        if _uncertainty(previous['config'], previous['unit_id'] is not None) != _uncertainty(row['config'], row['unit_id'] is not None):
            raise ValueError('Duplicate observations have conflicting uncertainty declarations')
        previous['source_locations'] = _locations(previous['source_locations'] + row['source_locations'])
        merged.append({**_identity(row), 'seed': row['seed'], 'unit_id': row['unit_id'],
                       'policy': 'Exact identity and equal value; preserve all sources and count once',
                       'source_locations': previous['source_locations']})
    return list(indexed.values()), merged


def _record_rows(observations, definitions):
    grouped = defaultdict(list)
    for row in observations:
        grouped[_key(row)].append(row)
    records, units_by_record = [], {}
    for identity, rows in sorted(grouped.items()):
        row = rows[0]
        has_objects = any(item['unit_id'] is not None for item in rows)
        configs = [_uncertainty(item['config'], has_objects) for item in rows]
        if any(config != configs[0] for config in configs[1:]):
            raise ValueError(f'Conflicting uncertainty declarations for {_identity(row)}')
        uncertainty = configs[0]
        axis = uncertainty['unit']
        if any(item[axis] is None for item in rows):
            raise ValueError(f'Every observation requires the chosen statistical unit {axis}')
        units = defaultdict(list)
        for item in rows:
            units[_canonical(item[axis])].append(item['value'])
        means = {key: float(np.mean(values)) for key, values in units.items()}
        record = {**_identity(row), **_definition(row['metric'], definitions),
                  **_describe(list(means.values()), uncertainty), 'id': 's' + str(len(records)),
                  'n_units': len(means), 'n_seeds': len({_canonical(item['seed']) for item in rows if item['seed'] is not None}),
                  'n_observations': len(rows), 'sampling_unit': axis,
                  'source_locations': _locations([location for item in rows for location in item['source_locations']]),
                  'source_refs': [], 'origin': 'recomputed_observations'}
        records.append(record)
        units_by_record[identity] = {'means': means, 'config': uncertainty, 'record': record}
    return records, units_by_record


def _summary_records(summaries, records, definitions, observations):
    indexed = {_key(record): record for record in records}
    standalone = {}
    for row in summaries:
        identity = _key(row)
        if identity in indexed and indexed[identity]['origin'] == 'recomputed_observations':
            record = indexed[identity]
            # A same-run summary is redundant, never an additional repetition.
            observation_runs = {loc['run_id'] for item in observations if _key(item) == identity
                                for loc in item['source_locations']}
            summary_runs = {loc['run_id'] for loc in row['source_locations']}
            if not summary_runs <= observation_runs:
                if row['policy'] != 'merge_identical' or not math.isclose(row['value'], record['estimate'], rel_tol=1e-10, abs_tol=1e-12) or (row['n'] is not None and row['n'] != record['n_units']):
                    raise ValueError('A summary overlaps observations without a verifiable exact-aggregate deduplication policy')
                if row['sd'] is not None and row['n'] and row['n'] > 1 and not math.isclose(row['sd'], record['sd'], rel_tol=1e-9, abs_tol=1e-12):
                    raise ValueError('Overlapping reported summary SD disagrees with observations')
                record['source_locations'] = _locations(record['source_locations'] + row['source_locations'])
                record['overlap_policy'] = 'Exact aggregate with identical estimate, repetition count and reported SD; not a new repetition'
                continue
            source_rows = [item for item in observations if _key(item) == identity and
                           any(location['run_id'] in summary_runs for location in item['source_locations'])]
            source_units = defaultdict(list)
            for item in source_rows:
                source_units[_canonical(item[record['sampling_unit']])].append(item['value'])
            source_values = [float(np.mean(values)) for values in source_units.values()]
            if not math.isclose(row['value'], float(np.mean(source_values)), rel_tol=1e-10, abs_tol=1e-12):
                raise ValueError('Reported summary disagrees with its recomputed observations')
            if row['n'] is not None and row['n'] != len(source_values):
                raise ValueError('Reported summary repetition count disagrees with observations')
            if len(source_values) > 1 and row['sd'] is not None and not math.isclose(row['sd'], float(np.std(source_values, ddof=1)), rel_tol=1e-9, abs_tol=1e-12):
                raise ValueError('Reported summary SD disagrees with observations')
            continue
        if identity in standalone:
            previous = standalone[identity]
            if (previous['value'], previous['sd'], previous['n']) != (row['value'], row['sd'], row['n']):
                raise ValueError('Conflicting summary identity; summary aggregates cannot be pooled as repetitions')
            if previous['policy'] != 'merge_identical' or row['policy'] != 'merge_identical':
                raise ValueError('Duplicate summary identity requires explicit merge_identical policy')
            previous['source_locations'] = _locations(previous['source_locations'] + row['source_locations'])
        else:
            standalone[identity] = deepcopy(row)
    for _, row in sorted(standalone.items()):
        n = row['n']
        sd = row['sd'] if n is not None and n > 1 else None
        records.append({**_identity(row), **_definition(row['metric'], definitions),
                        'id': 's' + str(len(records)), 'estimate': row['value'], 'sd': sd,
                        'se': None, 'ci_low': None, 'ci_high': None, 'confidence': None,
                        'n_units': n, 'n_seeds': n if row['seed_count_reported'] else None,
                        'n_observations': None, 'sampling_unit': 'seed' if row['seed_count_reported'] else 'reported unit',
                        'uncertainty': {'type': 'reported_sd' if sd is not None else 'none',
                                        'unit': 'seed' if row['seed_count_reported'] else 'reported unit',
                                        'scope': row['scope'] or 'Reported aggregate; unit identities unavailable',
                                        'confidence': None, 'aggregation': 'Unpooled reported summary'},
                        'source_locations': row['source_locations'], 'source_refs': [],
                        'origin': 'reported_summary'})
    return records


def _coverage(protocols, observations, records):
    design = defaultdict(list)
    declared = False
    for _, protocol, _, _ in protocols:
        supplied = protocol.get('design', {})
        if not isinstance(supplied, dict):
            raise ValueError('Statistical design must be an object')
        for axis in ('datasets', 'methods', 'metrics', 'seeds', 'units', 'conditions', 'x'):
            if axis not in supplied:
                continue
            declared = True
            values = supplied[axis]
            if not isinstance(values, list) or not values:
                raise ValueError(f'Design {axis} must be a nonempty array')
            for value in values:
                if _canonical(value) not in {_canonical(item) for item in design[axis]}:
                    design[axis].append(value)
    if not records:
        return {'complete': False, 'design_declared': declared, 'expected_count': 0,
                'observed_count': 0, 'missing': [], 'unexpected': [], 'unit_grid_verified': False}
    observed = {_key(record): _identity(record) for record in records}
    datasets = design['datasets'] or sorted({row['dataset'] for row in records})
    methods = design['methods'] or sorted({row['method'] for row in records})
    metric_names = design['metrics'] or sorted({row['metric'] for row in records})
    conditions = design['conditions'] or list({_canonical(row['condition']): row['condition'] for row in records}.values())
    x_values = design['x'] or list({_canonical(row['x']): row['x'] for row in records}.values())
    expected = {}
    for dataset, method, metric, condition, x in product(datasets, methods, metric_names, conditions, x_values):
        identity = dict(zip(_IDENTITY, (str(dataset), str(method), str(metric), condition, x)))
        expected[_key(identity)] = identity
    missing = [{'level': 'record', **value} for key, value in expected.items() if key not in observed]
    unexpected = [{'level': 'record', **value} for key, value in observed.items() if key not in expected]
    observed_units = {_key(row) + _key(row, ('seed', 'unit_id')) for row in observations}
    # Only a declared unit/seed grid establishes observation-level completeness.
    unit_grid = bool(design['seeds'] or design['units'])
    if unit_grid:
        expected_units = set()
        for identity in expected.values():
            for seed, unit in product(design['seeds'] or [None], design['units'] or [None]):
                if seed is None and design['units']:
                    # Unit coverage may include multiple supplied seeds; ignore seed.
                    present = any(_key(row) == _key(identity) and _canonical(row['unit_id']) == _canonical(unit) for row in observations)
                elif unit is None and design['seeds']:
                    present = any(_key(row) == _key(identity) and _canonical(row['seed']) == _canonical(seed) for row in observations)
                else:
                    present = _key(identity) + (_canonical(seed), _canonical(unit)) in observed_units
                expected_units.add(_key(identity) + (_canonical(seed), _canonical(unit)))
                if not present:
                    missing.append({'level': 'observation', **identity, 'seed': seed, 'unit_id': unit})
        for row in observations:
            if (design['seeds'] and _canonical(row['seed']) not in {_canonical(value) for value in design['seeds']}) or (design['units'] and _canonical(row['unit_id']) not in {_canonical(value) for value in design['units']}):
                unexpected.append({'level': 'observation', **_identity(row), 'seed': row['seed'], 'unit_id': row['unit_id']})
    return {'complete': not missing and not unexpected, 'design_declared': declared,
            'expected_count': len(expected), 'observed_count': len(observed),
            'missing': missing, 'unexpected': unexpected, 'unit_grid_verified': unit_grid,
            'expected': list(expected.values()), 'observed': list(observed.values()),
            'scope': 'Declared Cartesian design' if declared else 'Inferred record matrix; repetition grid is not established'}


def _paired(request, left, right, base_config):
    a, b = left['means'], right['means']
    if set(a) != set(b):
        raise ValueError('Paired comparisons require exactly matching statistical unit identities')
    if left['config']['unit'] != right['config']['unit']:
        raise ValueError('Paired comparisons require the same statistical unit')
    config = _uncertainty({**base_config, **request.get('uncertainty', {})}, left['config']['unit'] == 'unit_id')
    if config['unit'] != left['config']['unit']:
        raise ValueError('Comparison uncertainty must use the record statistical unit')
    sign = 1 if left['record']['direction'] == 'higher' else -1
    delta = np.array([sign * (a[key] - b[key]) for key in sorted(a)], dtype=float)
    descriptive = _describe(delta, config)
    output = {**_identity(left['record']), 'baseline': right['record']['method'],
              'candidate': left['record']['method'], 'direction': left['record']['direction'],
              'unit': left['record']['unit'], 'precision': left['record']['precision'],
              'improvement': descriptive.pop('estimate'), **descriptive, 'n_pairs': len(delta),
              'n_units': len(delta), 'n_seeds': min(left['record']['n_seeds'], right['record']['n_seeds']),
              'sampling_unit': left['config']['unit'],
              'p_value': None, 'adjusted_p': None, 'test': request.get('test'),
              'family': request.get('family', 'declared_comparisons'),
              'interpretation': 'Positive improvement favors candidate',
              'source_locations': _locations(left['record']['source_locations'] + right['record']['source_locations']),
              'source_refs': [], 'origin': 'recomputed_paired_units'}
    output.pop('method')
    test = request.get('test')
    if test not in {None, 'paired_t', 'permutation'}:
        raise ValueError('Comparison test must be paired_t, permutation or absent')
    if test:
        if config.get('independent') is not True:
            raise ValueError('A paired hypothesis test requires explicitly independent statistical units')
        if len(delta) < 2:
            raise ValueError('A paired hypothesis test requires at least two independent pairs')
        if test == 'paired_t':
            output['test_assumption'] = 'Independent pairs; normal differences for exact small-sample t inference'
            if np.std(delta, ddof=1) == 0:
                # A degenerate nonzero sample does not establish a population p-value.
                raise ValueError('Paired t test requires nonzero sample variance')
            output['p_value'] = float(stats.ttest_1samp(delta, 0.0).pvalue)
        else:
            if request.get('exchangeable_signs') is not True:
                raise ValueError('Sign permutation requires exchangeable_signs:true under the null')
            output['test_assumption'] = 'Independent pair differences with exchangeable signs under the null'
            n = len(delta)
            observed = abs(float(delta.mean()))
            if n <= 16:
                signs = np.array(list(product((-1, 1), repeat=n)))
                output['p_value'] = float(np.mean(np.abs((signs * delta).mean(axis=1)) >= observed - 1e-14))
            else:
                rng = np.random.default_rng(config['seed'])
                draws = config['samples']
                if isinstance(draws, bool) or not isinstance(draws, int) or draws < 100:
                    raise ValueError('Monte Carlo permutation requires at least 100 integer draws')
                extreme = 0
                for _ in range(draws):
                    extreme += abs(float(np.mean(rng.choice((-1, 1), n) * delta))) >= observed - 1e-14
                output['p_value'] = (extreme + 1) / (draws + 1)
    return output


def _comparisons(requests, unit_records, records, definitions):
    output, seen, declarations = [], {}, set()
    record_index = {_key(record): record for record in records}
    group_keys = ('dataset', 'metric', 'condition', 'x')
    for item in requests:
        request = item['request']
        baseline = _label(request.get('baseline'), 'baseline')
        candidate = _label(request.get('candidate'), 'candidate')
        if baseline == candidate:
            raise ValueError('A comparison requires distinct candidate and baseline')
        selectors = {key: request[key] for key in group_keys if key in request}
        if item['kind'] == 'legacy' and 'difference' in request:
            metric = _label(request.get('metric'), 'metric')
            definition = _definition(metric, definitions)
            difference = _number(request['difference'], 'Reported comparison difference')
            sign = 1 if definition['direction'] == 'higher' else -1
            low, high = request.get('ci_low'), request.get('ci_high')
            if (low is None) != (high is None):
                raise ValueError('Reported comparisons require both confidence interval endpoints')
            if low is not None:
                low, high = _number(low, 'ci_low'), _number(high, 'ci_high')
                if low > high:
                    raise ValueError('Reported interval endpoints are reversed')
                low, high = (low, high) if sign == 1 else (-high, -low)
            candidate_key = _key({**request, 'method': candidate})
            baseline_key = _key({**request, 'method': baseline})
            if candidate_key not in record_index or baseline_key not in record_index:
                raise ValueError('A reported comparison requires supplied candidate and baseline records')
            locations = [{'run_id': item['run_id'], 'pointer': _pointer(*item['prefix'], field)}
                         for field in ('difference', 'ci_low', 'ci_high', 'confidence', 'unique_test_objects', 'paired_predictions')
                         if isinstance(request.get(field), (int, float)) and not isinstance(request.get(field), bool)]
            result = {key: request.get(key) for key in group_keys}
            result.update(id='c' + str(len(output)), baseline=baseline, candidate=candidate,
                          **definition, improvement=sign * difference, ci_low=low, ci_high=high,
                          confidence=request.get('confidence'), n_pairs=request.get('unique_test_objects'),
                          n_units=request.get('unique_test_objects'),
                          n_seeds=min(record_index[candidate_key].get('n_seeds') or 0, record_index[baseline_key].get('n_seeds') or 0),
                          sd=None, se=None, p_value=None, adjusted_p=None, test=None, family=None,
                          uncertainty={'type': 'reported_ci' if low is not None else 'none',
                                       'unit': 'reported paired object', 'scope': request.get('interpretation', 'Reported paired contrast'),
                                       'confidence': request.get('confidence')},
                          interpretation='Positive improvement favors candidate',
                          source_locations=locations, source_refs=[], origin='reported_comparison')
        else:
            declaration = _canonical({'request': request, 'uncertainty': item['uncertainty']})
            if declaration in declarations:
                continue
            declarations.add(declaration)
            groups = {_key(record, group_keys): {key: record.get(key) for key in group_keys}
                      for record in records if record['method'] == candidate and all(_canonical(record.get(key)) == _canonical(value) for key, value in selectors.items())}
            if not groups:
                raise ValueError('A declared comparison has no matching candidate observations')
            for group in groups.values():
                left_key = _key({**group, 'method': candidate})
                right_key = _key({**group, 'method': baseline})
                if left_key not in unit_records or right_key not in unit_records:
                    raise ValueError('Recomputed comparisons require raw matched unit observations, not summary rows')
                result = _paired(request, unit_records[left_key], unit_records[right_key], item['uncertainty'])
                result['id'] = 'c' + str(len(output))
                identity = _key(result, group_keys + ('baseline', 'candidate'))
                if identity in seen:
                    raise ValueError('Duplicate declared contrast; define each comparison once across included runs')
                seen[identity] = result
                output.append(result)
            continue
        identity = _key(result, group_keys + ('baseline', 'candidate'))
        if identity in seen:
            previous = seen[identity]
            if item['policy'] != 'merge_identical' or any(previous[field] != result[field] for field in ('improvement', 'ci_low', 'ci_high')):
                raise ValueError('Duplicate or conflicting reported comparison identity')
            previous['source_locations'] = _locations(previous['source_locations'] + result['source_locations'])
        else:
            seen[identity] = result
            output.append(result)
    families = defaultdict(list)
    for result in output:
        if result['p_value'] is not None:
            families[result['family']].append(result)
    for family in families.values():
        running = 0.0
        for rank, result in enumerate(sorted(family, key=lambda value: value['p_value'])):
            running = min(1.0, max(running, (len(family) - rank) * result['p_value']))
            result['adjusted_p'] = running
            result['multiplicity'] = {'method': 'Holm', 'family': result['family'], 'size': len(family)}
    return output


def build_statistical_results(runs):
    """Recompute typed results over every supplied run; do not mutate inputs.

    Unrecognized metric objects produce an empty result (recognized=False), so
    arbitrary existing run formats retain their original paper workflow.
    Invalid recognized statistical evidence fails rather than silently falling
    back to a smaller dataset or a first-run plot.
    """
    runs = list(runs)
    protocols = []
    identifiers = set()
    for run in runs:
        run_id = _label(run.get('id'), 'run id')
        if run_id in identifiers:
            raise ValueError('Statistical evidence requires unique run IDs')
        identifiers.add(run_id)
        protocol, prefix, kind = _protocol(run)
        if protocol is not None:
            protocols.append((run_id, protocol, prefix, kind))
    if not protocols:
        return {'version': 1, 'recognized': False, 'records': [], 'comparisons': [],
                'groups': [], 'coverage': {'complete': False, 'design_declared': False,
                                         'expected_count': 0, 'observed_count': 0, 'missing': [], 'unexpected': []}}
    definitions = _merge_definitions(protocols)
    reported_records, reported_comparisons = _reported_results(protocols, definitions)
    run_index = {str(run['id']): run for run in runs}
    conditions = {run_id: _context_condition(run_index[run_id], protocol) for run_id, protocol, _, _ in protocols}
    axes = _axes(protocols)
    observations, summaries, requests, _ = _observations(protocols, definitions, conditions)
    observations, duplicates = _deduplicate(observations)
    records, unit_records = _record_rows(observations, definitions)
    records = _summary_records(summaries, records, definitions, observations)
    records = _merge_reported(records, reported_records)
    for record in records:
        record['x_label'] = axes.get('x', {}).get('label')
        record['x_unit'] = axes.get('x', {}).get('unit')
    comparisons = _comparisons(requests, unit_records, records, definitions)
    comparisons = _merge_reported(comparisons, reported_comparisons, comparison=True)
    grouped = defaultdict(list)
    for record in records:
        grouped[_key(record, ('dataset', 'metric', 'condition', 'x'))].append(record)
    groups = []
    for rows in grouped.values():
        row = rows[0]
        groups.append({key: row[key] for key in ('dataset', 'metric', 'condition', 'x', 'direction', 'unit')}
                      | {'record_ids': [record['id'] for record in rows], 'methods': [record['method'] for record in rows]})
    coverage = _inherit_coverage(_coverage(protocols, observations, records), protocols, observations, records)
    return {'version': 1, 'recognized': True, 'records': records, 'comparisons': comparisons,
            'groups': groups, 'metrics': definitions, 'axes': axes, 'coverage': coverage,
            'deduplications': duplicates,
            'policy': {'point_estimate': 'Equal weight across declared statistical-unit means',
                       'summary_pooling': 'Reported summaries are not independent repetitions',
                       'inference': 'Requires an explicit independent-unit declaration; intervals condition on the supplied other axes',
                       'comparison_direction': 'Positive improvement favors candidate',
                       'reported_p_values': 'Legacy bootstrap tails are not promoted to inferential p-values',
                       'source_runs': [run_id for run_id, _, _, _ in protocols]}}


def _numeric_leaves(value, pointer=''):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {'refs', 'source_locations', 'source_refs', 'upstream_source_locations'}:
                continue
            yield from _numeric_leaves(item, pointer + _pointer(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _numeric_leaves(item, pointer + _pointer(index))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield pointer, value


def attach_statistical_evidence(bundle):
    """Attach results and derived metrics atomically and idempotently in place.

    Derived pointers address the saved statistical_results artifact.
    Original measurements need run-scoped metric IDs in the bundle.
    """
    result = build_statistical_results(bundle.get('runs', []))
    if not result['recognized']:
        return bundle
    original = [item for item in bundle.get('metrics', []) if item.get('artifact') != 'statistical_results']
    runs = {str(run['id']): run['metrics'] for run in bundle.get('runs', [])}
    index = {}
    for metric in original:
        identity = (str(metric['run_id']), metric['pointer'])
        if identity in index and index[identity]['id'] != metric['id']:
            raise ValueError('Original metric locations must have unique IDs')
        index[identity] = metric
    for row in result['records'] + result['comparisons']:
        refs = []
        for location in row['source_locations']:
            metric = index.get((str(location['run_id']), location['pointer']))
            if metric is None:
                raise ValueError('Statistical result has an unbound original measurement at ' + location['pointer'])
            actual = runs[str(location['run_id'])]
            for token in location['pointer'][1:].split('/'):
                token = token.replace('~1', '/').replace('~0', '~')
                actual = actual[int(token)] if isinstance(actual, list) else actual[token]
            if metric.get('value') != actual:
                raise ValueError('Original metric binding disagrees with its actual run-scoped value')
            refs.append(metric['id'])
        row['source_refs'] = list(dict.fromkeys(refs))
    existing_ids = {metric['id'] for metric in original}
    derived, numeric_index = [], {}
    all_refs = list(dict.fromkeys(ref for row in result['records'] + result['comparisons'] for ref in row['source_refs']))
    source_run = result['policy']['source_runs'][0]
    counter = 0
    for pointer, value in _numeric_leaves(result):
        parts = pointer.split('/')
        row = None
        if len(parts) > 3 and parts[1] in {'records', 'comparisons'}:
            row = result[parts[1]][int(parts[2])]
        row_refs = row['source_refs'] if row else all_refs
        run_id = str(row['source_locations'][0]['run_id']) if row and row['source_locations'] else source_run
        identifier = 'statm' + str(counter)
        while identifier in existing_ids:
            counter += 1
            identifier = 'statm' + str(counter)
        counter += 1
        existing_ids.add(identifier)
        metric = {'id': identifier, 'run_id': run_id, 'artifact': 'statistical_results',
                  'pointer': pointer, 'value': value, 'source_refs': row_refs,
                  'evidence_label': 'MEASURED', 'measurement_status': 'derived_from_bound_measurements'}
        leaf = parts[-1].replace('~1', '/').replace('~0', '~')
        integer_display = leaf in {'n_units', 'n_seeds', 'n_observations', 'n_pairs',
                                   'expected_count', 'observed_count', 'size', 'samples', 'version', 'precision'}
        precision = row.get('precision', 3) if row else 3
        if leaf in {'confidence', 'p_value', 'adjusted_p'}:
            precision = 3
        metric['precision'] = 0 if integer_display else precision
        metric['format'] = '.0f' if integer_display else f'.{precision}g'
        if row:
            metric['statistical_identity'] = {key: row.get(key) for key in (
                'dataset', 'method', 'metric', 'condition', 'x', 'baseline', 'candidate', 'unit', 'direction')}
        derived.append(metric)
        numeric_index[pointer] = identifier
    for collection in ('records', 'comparisons'):
        for position, row in enumerate(result[collection]):
            row['refs'] = {field: numeric_index[_pointer(collection, position, field)]
                           for field in _VALUE_FIELDS if isinstance(row.get(field), (int, float)) and not isinstance(row[field], bool)}
    result['numeric_refs'] = numeric_index
    bundle['statistics'] = result
    bundle['metrics'] = original + derived
    return bundle
