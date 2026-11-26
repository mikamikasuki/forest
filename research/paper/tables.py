"""Evidence-bound statistical tables, independent of manuscript prose generation.

The statistical engine owns estimates and uncertainty.  This module selects
their identities, binds every displayed value to an evidence token, and records
the table semantics so editing a label cannot silently exchange two results.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re


_DUTIES = {'effectiveness', 'mechanism', 'scenario_value', 'alternative_explanation'}
_SPEC_FIELDS = {'kind', 'datasets', 'methods', 'metrics', 'conditions', 'x', 'uncertainty',
                'require_complete', 'metric_labels', 'max_numeric_columns', 'bold_best', 'scope'}
_TOKEN_ID = re.compile(r'[A-Za-z][A-Za-z0-9:_.-]*\Z')


def _key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _unique(values):
    result, seen = [], set()
    for value in values:
        key = _key(value)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _identity(record):
    return {field: record.get(field) for field in ('dataset', 'method', 'metric', 'condition', 'x')}


def _axis_setting(x, statistics=None, record=None, metrics=None):
    axis = (statistics or {}).get('axes', {}).get('x', {})
    name = axis.get('label') or axis.get('name') or 'Checkpoint'
    value = str(x)
    if record is not None and metrics is not None and record.get('refs', {}).get('x'):
        value = _token(record, 'x', metrics)
    unit = axis.get('unit')
    return str(name) + ' = ' + value + (' ' + str(unit) if unit and str(unit).lower() not in ('count', 'unitless', 'dimensionless') and str(unit).lower() not in str(name).lower() else '')


def _scope_label(condition, x, statistics=None, record=None, metrics=None):
    label = condition if isinstance(condition, str) else _key(condition) if condition is not None else ''
    if x is not None:
        label = (label + '; ' if label else '') + _axis_setting(x, statistics, record, metrics)
    return label or 'Evaluated conditions'


def _sampling_unit(unit, independent=False):
    noun = {'seed': 'seeded runs', 'unit_id': 'objects', 'reported unit': 'reported units'}.get(unit, str(unit) + ' units')
    return ('independent ' if independent else '') + noun


def _public_uncertainty_scope(scope):
    stock = {
        'Across supplied object identities, conditional on supplied seeds and datasets': 'conditional on the evaluated datasets and run initializations',
        'Across supplied seeds, conditional on supplied datasets and measured objects': 'on the evaluated data',
        'Reported aggregate; unit identities unavailable': 'source-reported aggregate',
    }
    return stock.get(scope, scope) if isinstance(scope, str) else _key(scope) if isinstance(scope, dict) else scope


def _statistical_spec(block):
    supplied = [name for name in ('statistics_spec', 'statistical_table') if name in block]
    if not supplied:
        return None
    if len(supplied) != 1:
        raise ValueError('A table must use either statistics_spec or statistical_table')
    spec = block[supplied[0]]
    if not isinstance(spec, dict) or set(spec) - _SPEC_FIELDS:
        raise ValueError('Statistical table specification has unsupported fields')
    if spec.get('kind', 'records') not in ('records', 'comparisons'):
        raise ValueError('Statistical table kind must be records or comparisons')
    if spec.get('uncertainty', 'auto') not in ('auto', 'sd', 'se', 'ci', 'none'):
        raise ValueError('Statistical table uncertainty must be auto, sd, se, ci or none')
    for field in ('datasets', 'methods', 'metrics', 'conditions', 'x'):
        if field in spec and (not isinstance(spec[field], list) or not spec[field] or len(_unique(spec[field])) != len(spec[field])):
            raise ValueError('Statistical table ' + field + ' must be a nonempty list of distinct identities')
    for field in ('datasets', 'methods', 'metrics'):
        if field in spec and any(not isinstance(value, str) or not value for value in spec[field]):
            raise ValueError('Statistical table ' + field + ' must contain actual string identities')
    for field in ('require_complete', 'bold_best'):
        if field in spec and not isinstance(spec[field], bool):
            raise ValueError('Statistical table ' + field + ' must be boolean')
    count = spec.get('max_numeric_columns', 6)
    if isinstance(count, bool) or not isinstance(count, int) or not 2 <= count <= 8:
        raise ValueError('Statistical tables use two to eight numeric columns per readable panel')
    labels = spec.get('metric_labels', {})
    if not isinstance(labels, dict) or any(not isinstance(key, str) or not isinstance(value, str) or not value.strip() for key, value in labels.items()):
        raise ValueError('Statistical metric_labels must map actual metrics to nonempty text')
    scope = spec.get('scope')
    if scope is not None and (not isinstance(scope, str) or not scope.strip()):
        raise ValueError('A selected statistical scope must have a concrete textual definition')
    return spec


def _token(record, field, metrics):
    value = record.get(field)
    reference = record.get('refs', {}).get(field)
    if not _finite(value) or not isinstance(reference, str) or not _TOKEN_ID.fullmatch(reference):
        raise ValueError('Statistical table values require finite computations and bound ' + field + ' references')
    if reference not in metrics or metrics[reference].get('value') != value:
        raise ValueError('Statistical table ' + field + ' reference does not match its computed record')
    return '[[metric:' + reference + ']]'


def _selected(records, spec):
    filtered = records
    for name, field in [('datasets', 'dataset'), ('methods', 'method'), ('metrics', 'metric'), ('conditions', 'condition'), ('x', 'x')]:
        if name == 'methods' and spec.get('kind') == 'comparisons':
            if name in spec:
                selected = set(spec[name])
                filtered = [record for record in filtered if record.get('candidate') in selected and record.get('baseline') in selected]
            continue
        if name in spec:
            allowed = {_key(value) for value in spec[name]}
            available = {_key(record.get(field)) for record in filtered}
            if not allowed <= available:
                raise ValueError('Statistical table selects unavailable ' + name + ' identities')
            filtered = [record for record in filtered if _key(record.get(field)) in allowed]
    # A main results table has one estimate per setting. Learning-curve points
    # require an explicit x selection rather than collapsing the last checkpoint.
    if 'x' not in spec:
        filtered = [record for record in filtered if record.get('x') is None]
    if not filtered:
        raise ValueError('Statistical table has no computed observations in its declared scope')
    return filtered


def _uncertainty(record, requested):
    definition = deepcopy(record.get('uncertainty') or {'type': 'none'})
    if not isinstance(definition, dict):
        raise ValueError('Statistical uncertainty requires its computed definition')
    kind = definition.get('type', 'none')
    mode = requested
    if mode == 'auto':
        mode = 'ci' if kind in ('bootstrap', 't_ci', 'ci') else 'sd' if kind in ('sd', 'reported_sd') else 'se' if kind == 'se' else 'none'
    if mode == 'ci' and (record.get('ci_low') is None or record.get('ci_high') is None):
        raise ValueError('Statistical table cannot request uncomputed confidence intervals')
    if mode in ('sd', 'se') and record.get(mode) is None:
        raise ValueError('Statistical table cannot request uncomputed ' + mode)
    if mode == 'ci' and record.get('ci_low') > record.get('ci_high'):
        raise ValueError('Statistical confidence interval bounds are reversed')
    if mode != 'none' and not definition.get('unit'):
        raise ValueError('Displayed uncertainty needs its actual statistical unit')
    if mode == 'ci':
        confidence = definition.get('confidence', record.get('confidence'))
        if not _finite(confidence) or not 0 < confidence < 1:
            raise ValueError('Displayed confidence intervals need their actual confidence level')
        definition['confidence'] = confidence
    return mode, definition


def _cell(record, spec, metrics):
    estimate = _token(record, 'estimate', metrics)
    mode, definition = _uncertainty(record, spec.get('uncertainty', 'auto'))
    if mode in ('sd', 'se'):
        return estimate + ' ± ' + _token(record, mode, metrics), mode, definition
    if mode == 'ci':
        return estimate + ' [' + _token(record, 'ci_low', metrics) + ', ' + _token(record, 'ci_high', metrics) + ']', mode, definition
    return estimate, mode, definition


def _notes(records, displays, metrics, bold_best, scope, statistics=None):
    notes = []
    if scope:
        notes.append('Scope: ' + scope.rstrip('.') + '.')
    definitions = _unique({'mode': mode, **definition} for mode, definition in displays)
    for definition in definitions:
        mode, unit = definition['mode'], definition.get('unit', 'observation')
        scope = _public_uncertainty_scope(definition.get('scope'))
        qualifier = '; ' + str(scope).rstrip('.') if scope else ''
        sampling = _sampling_unit(unit, definition.get('independent') is True)
        if mode == 'sd':
            notes.append('± denotes standard deviation across ' + sampling + qualifier + '.')
        elif mode == 'se':
            notes.append('± denotes standard error across ' + sampling + qualifier + '.')
        elif mode == 'ci':
            level = definition.get('confidence')
            notes.append('Brackets show ' + f'{100 * level:g}' + '% confidence intervals across ' + sampling + qualifier + '.')
        else:
            notes.append('Cells show observed point estimates.')
    # Group identical sampling definitions while keeping the actual numeric
    # reference. Distinct sample sizes are never compressed into an average n.
    sampling = {}
    for record in records:
        definition = record.get('uncertainty') or {}
        key = _key([record.get('n_units'), record.get('n_seeds'), record.get('sampling_unit') or definition.get('unit'), definition.get('independent', False)])
        sampling.setdefault(key, []).append(record)
    for group in sampling.values():
        record = group[0]
        definition = record.get('uncertainty') or {}
        sampling_unit = record.get('sampling_unit') or definition.get('unit')
        if len(group) == len(records):
            labels = 'Each comparison'
        else:
            contexts = _unique([row['dataset'], row.get('condition'), row.get('x')] for row in group)
            labels = ', '.join(dataset + (' (' + _scope_label(condition, x, statistics, next(row for row in group if row['dataset'] == dataset and _key(row.get('condition')) == _key(condition) and _key(row.get('x')) == _key(x)), metrics) + ')' if condition is not None or x is not None else '') for dataset, condition, x in contexts)
            labels += '; methods ' + ', '.join(_unique(row['method'] for row in group))
            labels += '; metrics ' + ', '.join(_unique(row['metric'] for row in group))
        if record.get('n_units') is None:
            labels += ': sample size unreported'
        else:
            labels += ': n = ' + _token(record, 'n_units', metrics)
            if sampling_unit:
                labels += ' ' + _sampling_unit(sampling_unit, definition.get('independent') is True)
        if record.get('n_seeds') is not None and record['n_seeds'] > 0 and not (sampling_unit == 'seed' and record.get('n_units') == record['n_seeds']):
            labels += '; ' + _token(record, 'n_seeds', metrics) + ' seeded runs'
        notes.append(labels + '.')
    if bold_best:
        notes.append('Bold identifies the best point estimate within each dataset, metric and condition.')
    return _unique(notes)


def _record_table(block, evidence, spec):
    statistics = evidence.get('statistics') or {}
    records = statistics.get('records', [])
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise ValueError('Statistical tables require typed computed records')
    selected = _selected(records, spec)
    if any(not isinstance(record.get('id'), str) or not _TOKEN_ID.fullmatch(record['id']) for record in selected) or len({record['id'] for record in selected}) != len(selected):
        raise ValueError('Statistical table record IDs must be distinct actual identifiers')
    metrics = {metric['id']: metric for metric in evidence.get('metrics', [])}
    complete = spec.get('require_complete', block.get('argumentative_duty') == 'effectiveness')
    if complete:
        coverage = statistics.get('coverage') or {}
        if coverage.get('design_declared') is not True:
            raise ValueError('A complete statistical table requires a declared experiment design')
        if coverage.get('complete') is not True:
            raise ValueError('A complete statistical table requires the declared experiment coverage')
        eligible = [record for record in records if (_key(record.get('x')) in {_key(value) for value in spec['x']} if 'x' in spec else record.get('x') is None)]
        selected_metrics = {record['metric'] for record in selected}
        expected = [record for record in eligible if record['metric'] in selected_metrics]
        if {_key(_identity(record)) for record in selected} != {_key(_identity(record)) for record in expected}:
            raise ValueError('A main statistical table cannot omit a dataset, method or condition from its comparison matrix')
    methods = spec.get('methods', _unique(record['method'] for record in selected))
    datasets = spec.get('datasets', _unique(record['dataset'] for record in selected))
    metric_names = spec.get('metrics', _unique(record['metric'] for record in selected))
    if set(spec.get('metric_labels', {})) - set(metric_names):
        raise ValueError('Statistical metric labels must identify selected metrics')
    settings = _unique([record.get('condition'), record.get('x')] for record in selected)
    index = {}
    for record in selected:
        identity = _key(_identity(record))
        if identity in index:
            raise ValueError('Statistical table has duplicate computed identities')
        if record.get('direction') not in ('higher', 'lower'):
            raise ValueError('Statistical metrics require higher or lower direction')
        index[identity] = record
    columns, column_groups, column_keys = ['Method'], [], []
    for dataset in datasets:
        start = len(columns)
        for metric in metric_names:
            members = [record for record in selected if record['dataset'] == dataset and record['metric'] == metric]
            if not members:
                continue
            definitions = {(record['direction'], record.get('unit', '')) for record in members}
            if len(definitions) != 1:
                raise ValueError('A statistical column must preserve metric direction and units')
            direction, unit = next(iter(definitions))
            title = spec.get('metric_labels', {}).get(metric, metric.replace('_', ' ').capitalize())
            title += ' ↑' if direction == 'higher' else ' ↓'
            if unit:
                title += ' (' + unit + ')'
            columns.append(title)
            column_keys.append((dataset, metric))
        column_groups.append({'label': dataset, 'start': start, 'count': len(columns) - start})
    rows, row_groups, cells, displayed = [], [], [], []
    for condition, x in settings:
        start = len(rows)
        for method in methods:
            row = [method]
            row_index = len(rows)
            for col, (dataset, metric) in enumerate(column_keys, 1):
                identity = {'dataset': dataset, 'method': method, 'metric': metric, 'condition': condition, 'x': x}
                record = index.get(_key(identity))
                if record is None:
                    if complete:
                        raise ValueError('A main statistical table has a missing method comparison cell')
                    row.append('—')
                    continue
                value, mode, definition = _cell(record, spec, metrics)
                row.append(value)
                displayed.append((mode, definition))
                cells.append({'row': row_index, 'column': col, 'record_id': record['id'], 'identity': identity,
                              'uncertainty': {'display': mode, **definition}, 'refs': deepcopy(record.get('refs', {})),
                              'source_refs': deepcopy(record.get('source_refs', []))})
            if any(cell != '—' for cell in row[1:]):
                rows.append(row)
        if len(settings) > 1:
            representative = next(record for record in selected if _key(record.get('condition')) == _key(condition) and _key(record.get('x')) == _key(x))
            row_groups.append({'label': _scope_label(condition, x, statistics, representative, metrics), 'start': start, 'count': len(rows) - start})
    bold_best = spec.get('bold_best', True)
    styles = {}
    if bold_best:
        for condition, x in settings:
            for col, (dataset, metric) in enumerate(column_keys, 1):
                candidates = [record for record in selected if record['dataset'] == dataset and record['metric'] == metric and _key(record.get('condition')) == _key(condition) and _key(record.get('x')) == _key(x)]
                if len(candidates) < 2:
                    continue
                target = (max if candidates[0]['direction'] == 'higher' else min)(record['estimate'] for record in candidates)
                for cell in cells:
                    if cell['column'] == col and cell['record_id'] in {record['id'] for record in candidates if record['estimate'] == target}:
                        styles[f"{cell['row']}:{col}"] = 'bold'
    notes = _notes(selected, displayed, metrics, bool(styles), spec.get('scope'), statistics)
    if len(settings) == 1 and settings[0] != [None, None]:
        notes.insert(0, _scope_label(*settings[0], statistics, selected[0], metrics) + '.')
    if any(cell == '—' for row in rows for cell in row):
        notes.append('— identifies a combination outside the supplied measurement matrix.')
    # Retain dataset groups whenever possible. A group wider than the readable
    # panel capacity is explicitly divided; no numeric column is dropped.
    panels, current = [], [0]
    capacity = spec.get('max_numeric_columns', 6)
    font_pt = block.get('layout', {}).get('font_pt', 9)
    method_width = max(60, max(len(method) for method in methods) * font_pt * .5)
    widths = [method_width]
    for dataset, metric in column_keys:
        candidates = [record for record in selected if record['dataset'] == dataset and record['metric'] == metric]
        def visible_length(record):
            mode, _ = _uncertainty(record, spec.get('uncertainty', 'auto'))
            fields = ['estimate'] + ([mode] if mode in ('sd', 'se') else ['ci_low', 'ci_high'] if mode == 'ci' else [])
            length = 0
            for field in fields:
                reference = metrics[record['refs'][field]]
                precision = reference.get('precision', record.get('precision'))
                if isinstance(precision, bool) or not isinstance(precision, int) or not 0 <= precision <= 10:
                    precision = 5
                fmt = reference.get('format')
                display = format(record[field], fmt) if isinstance(fmt, str) and re.fullmatch(r'\.\d{1,2}[fg]', fmt) else f'{record[field]:.{precision}g}'
                if 'e' in display:
                    mantissa, exponent = display.split('e')
                    length += len(mantissa) + 3 + len(str(int(exponent))) * .6
                else:
                    length += len(display)
            return length + (3 if mode in ('sd', 'se') else 5 if mode == 'ci' else 0)
        widths.append(max(40, max(visible_length(record) for record in candidates) * font_pt * .55))
    # A conservative single-column conference page is 5.5 inches. Statistical
    # values remain unbroken; expand to further panels rather than squeeze them.
    available_pt = 5.5 * 72
    def fits(indices):
        return len(indices) - 1 <= capacity and sum(widths[index] + 12 for index in indices) <= available_pt
    for group in column_groups:
        indices = list(range(group['start'], group['start'] + group['count']))
        if len(current) > 1 and not fits(current + indices):
            panels.append(current)
            current = [0]
        for column in indices:
            if len(current) > 1 and not fits(current + [column]):
                panels.append(current)
                current = [0]
            current.append(column)
    if len(current) > 1:
        panels.append(current)
    binding = {'version': 1, 'kind': 'records', 'record_ids': [record['id'] for record in selected],
               'identities': [_identity(record) for record in selected], 'cells': cells,
               'complete': complete, 'displayed_uncertainty': _unique(mode for mode, _ in displayed),
               'bold_policy': 'best_point_estimate'}
    return {'columns': columns, 'rows': rows, 'alignment': ['left'] + ['right'] * (len(columns) - 1),
            'column_groups': column_groups, 'row_groups': row_groups, 'cell_styles': styles,
            'column_panels': panels if len(panels) > 1 else [], 'column_weights': widths,
            'notes': notes, 'statistics_binding': binding}


def _comparison_table(block, evidence, spec):
    statistics = evidence.get('statistics') or {}
    comparisons = _selected(statistics.get('comparisons', []), spec)
    if any(not isinstance(record.get('id'), str) or not _TOKEN_ID.fullmatch(record['id']) for record in comparisons) or len({record['id'] for record in comparisons}) != len(comparisons):
        raise ValueError('Statistical comparison IDs must be distinct actual identifiers')
    if spec.get('uncertainty', 'auto') not in ('auto', 'ci'):
        raise ValueError('Paired effect tables display their computed confidence intervals')
    metrics = {metric['id']: metric for metric in evidence.get('metrics', [])}
    fields = ['improvement', 'ci_low', 'ci_high', 'n_pairs']
    include_p = all(record.get('adjusted_p') is not None for record in comparisons)
    columns = ['Comparison', 'Improvement', 'Confidence interval', 'Paired units'] + (['Adjusted p'] if include_p else [])
    rows, cells, notes, row_groups = [], [], [], []
    settings = _unique([record.get(field) for field in ('dataset', 'metric', 'condition', 'x')] for record in comparisons)
    comparisons = [record for setting in settings for record in comparisons if [record.get(field) for field in ('dataset', 'metric', 'condition', 'x')] == setting]
    for setting in settings:
        dataset, metric, condition, x = setting
        label = dataset + ' — ' + metric.replace('_', ' ')
        if condition is not None or x is not None:
            representative = next(record for record in comparisons if [record.get(field) for field in ('dataset', 'metric', 'condition', 'x')] == setting)
            label += '; ' + _scope_label(condition, x, statistics, representative, metrics)
        start = next(index for index, record in enumerate(comparisons) if [record.get(field) for field in ('dataset', 'metric', 'condition', 'x')] == setting)
        count = sum([record.get(field) for field in ('dataset', 'metric', 'condition', 'x')] == setting for record in comparisons)
        row_groups.append({'label': label, 'start': start, 'count': count})
    for ri, record in enumerate(comparisons):
        if not isinstance(record.get('candidate'), str) or not isinstance(record.get('baseline'), str):
            raise ValueError('Statistical comparisons require actual candidate and baseline identities')
        tokens = {field: _token(record, field, metrics) for field in fields}
        if record['ci_low'] > record['ci_high']:
            raise ValueError('Statistical comparison confidence bounds are reversed')
        row = [record['candidate'] + ' vs ' + record['baseline'], tokens['improvement'],
               '[' + tokens['ci_low'] + ', ' + tokens['ci_high'] + ']', tokens['n_pairs']]
        if include_p:
            row.append(_token(record, 'adjusted_p', metrics))
        rows.append(row)
        cells.append({'row': ri, 'comparison_id': record['id'], 'identity': {field: record.get(field) for field in ('dataset', 'metric', 'candidate', 'baseline', 'condition', 'x')}, 'refs': deepcopy(record['refs'])})
        definition = record.get('uncertainty') or {}
        confidence = definition.get('confidence', record.get('confidence'))
        if not _finite(confidence) or not 0 < confidence < 1:
            raise ValueError('Statistical comparison confidence level must be valid')
        unit = definition.get('unit', record.get('sampling_unit'))
        if not unit:
            raise ValueError('Statistical comparison intervals need their actual pairing unit')
        target = _sampling_unit(unit, definition.get('independent') is True)
        qualifier = _public_uncertainty_scope(definition.get('scope'))
        notes.append(f'{100 * confidence:g}' + '% confidence intervals across paired ' + target + ('; ' + str(qualifier).rstrip('.') if qualifier else '') + '.')
        if record.get('unit'):
            notes.append(record['metric'].replace('_', ' ') + ' is measured in ' + str(record['unit']) + '.')
        if record.get('condition') is not None or record.get('x') is not None:
            notes.append(record['dataset'] + ', ' + record['candidate'] + ' vs ' + record['baseline'] + ': ' + _scope_label(record.get('condition'), record.get('x'), statistics, record, metrics) + '.')
        if include_p:
            multiplicity = record.get('multiplicity') or {}
            family = multiplicity.get('family', record.get('family'))
            method = multiplicity.get('method')
            if family:
                notes.append('Adjusted p-values use ' + (str(method) + ' correction over ' if method else '') + 'comparison family ' + str(family) + '.')
    notes.insert(0, 'Positive differences favor the candidate in the original metric units.')
    binding = {'version': 1, 'kind': 'comparisons', 'comparison_ids': [record['id'] for record in comparisons], 'cells': cells,
               'displayed_uncertainty': ['ci']}
    pair_width = max(90, max(len(row[0]) for row in rows) * 4.5)
    return {'columns': columns, 'rows': rows, 'alignment': ['left'] + ['right'] * (len(columns) - 1),
            'column_groups': [], 'row_groups': row_groups, 'column_panels': [], 'cell_styles': {},
            'column_weights': [pair_width, 70, 140, 45] + ([50] if include_p else []),
            'notes': _unique(notes), 'statistics_binding': binding}


def _materialized(block, evidence):
    spec = _statistical_spec(block)
    if spec is None:
        return None
    if block.get('type') != 'table':
        raise ValueError('Statistical specifications belong to table blocks')
    if block.get('argumentative_duty') not in _DUTIES:
        raise ValueError('Statistical tables require an explicit argumentative duty')
    computed = _comparison_table(block, evidence, spec) if spec.get('kind') == 'comparisons' else _record_table(block, evidence, spec)
    if 'statistics_binding' in block:
        authored_notes = block['statistics_binding'].get('authored_notes', [])
    else:
        authored_notes = block.get('notes', [])
    if not isinstance(authored_notes, list) or any(not isinstance(note, str) or not note.strip() for note in authored_notes):
        raise ValueError('Statistical table notes must be nonempty text strings')
    prose = ' '.join([block.get('caption', ''), *authored_notes])
    types = set(computed['statistics_binding']['displayed_uncertainty'])
    if 'ci' not in types and re.search(r'\b(?:confidence intervals?|CI)\b', prose, re.I):
        raise ValueError('Table prose mislabels the displayed uncertainty as a confidence interval')
    if 'sd' not in types and re.search(r'\b(?:standard deviations?|SD)\b', prose, re.I):
        raise ValueError('Table prose mislabels the displayed uncertainty as standard deviation')
    if 'se' not in types and re.search(r'\b(?:standard errors?|SE)\b', prose, re.I):
        raise ValueError('Table prose mislabels the displayed uncertainty as standard error')
    if 'ci' in types:
        if spec.get('kind') == 'comparisons':
            selected_records = _selected((evidence.get('statistics') or {}).get('comparisons', []), spec)
        else:
            selected_records = _selected((evidence.get('statistics') or {}).get('records', []), spec)
        levels = {(record.get('uncertainty') or {}).get('confidence', record.get('confidence')) for record in selected_records}
        for level in re.findall(r'\b(\d+(?:\.\d+)?)\s*%\s*(?:confidence|CI)\b', prose, re.I):
            if not any(_finite(expected) and math.isclose(float(level), 100 * expected, abs_tol=1e-8) for expected in levels):
                raise ValueError('Table prose changes the computed confidence level')
    bare = re.sub(r'\[\[metric:[^]]+\]\]', '', prose)
    if re.search(r'\b(?:n|p(?:[- ]value)?)\s*[=<>]\s*[+-]?(?:\d|\.\d)|\b\d+\s+(?:seeds?|samples?|subjects?|participants?)\b', bare, re.I):
        raise ValueError('Statistical table prose must bind measured counts and test results to metric references')
    computed['notes'] += [note for note in authored_notes if note not in computed['notes']]
    computed['statistics_binding']['authored_notes'] = deepcopy(authored_notes)
    return computed


def materialize_statistical_tables(draft, evidence):
    """Return a deep copy with computed table cells; validate prior bindings first."""
    result = deepcopy(draft)
    for section in result.get('sections', []) + result.get('appendices', []):
        for block in section.get('blocks', []):
            computed = _materialized(block, evidence)
            if computed is None:
                continue
            if 'statistics_binding' in block:
                _validate_bound_table(block, computed)
            elif any(field in block for field in ('columns', 'rows')):
                # A supplied table must already match the computation. Never
                # replace a mislabeled authored matrix and silently call it valid.
                for field in ('columns', 'rows'):
                    if field in block and block[field] != computed[field]:
                        raise ValueError('Authored statistical table ' + field + ' does not match its computed identities')
            block.update(computed)
    return result


def _validate_bound_table(block, computed):
    for field, expected in computed.items():
        if block.get(field) != expected:
            raise ValueError('Statistical table ' + field + ' no longer matches its computed identity and uncertainty bindings')


def validate_statistical_tables(draft, evidence):
    """Reject omitted comparisons, changed cells, exchanged labels or false notes."""
    count = 0
    for section in draft.get('sections', []) + draft.get('appendices', []):
        for block in section.get('blocks', []):
            computed = _materialized(block, evidence)
            if computed is not None:
                if 'statistics_binding' not in block:
                    raise ValueError('Statistical tables must be materialized from computed records before validation')
                _validate_bound_table(block, computed)
                count += 1
            elif 'statistics_binding' in block:
                raise ValueError('A statistical binding requires its original statistical specification')
    return {'tables': count, 'identity_validation': 'passed'}


def default_statistical_tables(evidence):
    """Available table plans; the writer chooses main-text or appendix placement."""
    statistics = evidence.get('statistics') or {}
    records = statistics.get('records', [])
    blocks = []
    if any(record.get('x') is None for record in records):
        blocks.append({'type': 'table', 'statistics_spec': {'kind': 'records', 'require_complete': True},
                       'label': 'tab:statistical-results', 'argumentative_duty': 'effectiveness',
                       'caption': 'Measured performance across the evaluated methods and datasets.'})
    comparisons = statistics.get('comparisons', [])
    if comparisons:
        comparison_spec = {'kind': 'comparisons'}
        if any(record.get('x') is not None for record in comparisons):
            comparison_spec['x'] = _unique(record.get('x') for record in comparisons)
        blocks.append({'type': 'table', 'statistics_spec': comparison_spec,
                       'label': 'tab:statistical-effects', 'argumentative_duty': 'effectiveness',
                       'caption': 'Paired improvements for the evaluated method comparisons.'})
    checkpoints = _unique(record['x'] for record in records if record.get('x') is not None)
    metrics = {metric['id']: metric for metric in evidence.get('metrics', [])}
    for index, checkpoint in enumerate(checkpoints):
        representative = next(record for record in records if _key(record.get('x')) == _key(checkpoint))
        checkpoint_label = _axis_setting(checkpoint, statistics, representative, metrics)
        blocks.append({'type': 'table', 'statistics_spec': {'kind': 'records', 'require_complete': True,
                      'x': [checkpoint]},
                      'label': 'tab:statistical-checkpoint-' + str(index), 'argumentative_duty': 'scenario_value',
                      'caption': 'Method comparisons at ' + checkpoint_label + '.'})
    return blocks
