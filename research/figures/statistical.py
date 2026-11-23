"""Publication-size empirical plots from identity-bound statistical results.

Rendering changes presentation only. Estimates, interval endpoints, repetitions
and comparison identities are supplied by the analysis layer, never a designer.
"""
from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
import textwrap

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np


COLORS = ['#0072B2', '#D55E00', '#009E73', '#CC79A7', '#E69F00', '#56B4E9', '#444444', '#882255']
MARKERS = ['o', 's', '^', 'D', 'v', 'P', 'X', '*']
LINES = ['-', '--', '-.', ':']
NUMERIC_FIELDS = ('estimate', 'sd', 'se', 'ci_low', 'ci_high', 'n_units', 'n_seeds',
                  'improvement', 'n_pairs', 'p_value', 'adjusted_p', 'confidence')


def _key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def validate_statistical_data(data):
    if not isinstance(data, dict) or not isinstance(data.get('statistical_results'), dict):
        raise ValueError('Statistical plotting requires a structured analysis result')
    result = data['statistical_results']
    bindings = {item['id']: item for item in data.get('metric_bindings', [])}
    if not bindings:
        raise ValueError('Statistical plots require actual numeric source bindings')
    if not result.get('coverage', {}).get('complete', False):
        raise ValueError('Complete the declared statistical experiment matrix before plotting')
    seen = set()
    for record in [*result.get('records', []), *result.get('comparisons', [])]:
        identifier = record.get('id')
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError('Statistical records need unique identities')
        seen.add(identifier)
        for field in NUMERIC_FIELDS:
            value = record.get(field)
            if value is None:
                continue
            reference = record.get('refs', {}).get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('Statistical plotting values must be finite numbers')
            if reference not in bindings or bindings[reference]['value'] != value:
                raise ValueError('Statistical plot value does not match its analysis binding: ' + str(identifier) + '/' + field)
            identity = bindings[reference].get('statistical_identity')
            if isinstance(identity, dict):
                for name, expected in identity.items():
                    if expected != record.get(name):
                        raise ValueError('Statistical plot identity differs from its source binding: ' + name)
        if record.get('ci_low') is not None and record.get('ci_high') is not None and record['ci_low'] > record['ci_high']:
            raise ValueError('Statistical interval endpoints are reversed')
        for name in ('sd', 'se'):
            if record.get(name) is not None and record[name] < 0:
                raise ValueError('Statistical spread cannot be negative')
    return result


def uncertainty_bounds(record):
    """Return exactly the declared uncertainty, never manufacture a band."""
    definition = record.get('uncertainty', {})
    kind = definition.get('type', 'none')
    kind = {'reported_sd': 'sd', 'reported_se': 'se'}.get(kind, kind)
    if record.get('ci_low') is not None and record.get('ci_high') is not None:
        if kind in ('none', 'sd', 'se'):
            raise ValueError('Interval endpoints require an interval uncertainty definition')
        return record['ci_low'], record['ci_high']
    field = 'sd' if kind == 'sd' else 'se' if kind == 'se' else None
    if field and record.get(field) is not None:
        return record['estimate'] - record[field], record['estimate'] + record[field]
    return None


def uncertainty_notes(records):
    notes = []
    definitions = {_key(record.get('uncertainty', {})): record.get('uncertainty', {}) for record in records}
    for definition in definitions.values():
        kind = definition.get('type', 'none')
        kind = {'reported_sd': 'sd', 'reported_se': 'se'}.get(kind, kind)
        unit = definition.get('unit')
        if kind in ('none', 'unavailable'):
            continue
        if not unit:
            raise ValueError('Statistical uncertainty requires its sampling unit')
        if kind in ('sd', 'se'):
            text = 'Mean ± ' + kind.upper() + ' across ' + str(unit)
        else:
            confidence = definition.get('confidence')
            if confidence is None:
                raise ValueError('Interval uncertainty requires a confidence level')
            name = {'t_ci': 'Student-t', 'bootstrap': 'bootstrap', 'reported_ci': 'reported'}.get(kind, kind.replace('_', ' '))
            text = f'{100 * confidence:g}% CI ({name}, across {unit})'
        scope = definition.get('scope')
        if scope:
            text += '; ' + str(scope)
        if text not in notes:
            notes.append(text)
    return notes


def _label(metric, unit, direction):
    result = str(metric).replace('_', ' ').strip().capitalize()
    if unit and str(unit) not in ('unitless', 'dimensionless', 'score'):
        result += ' (' + str(unit) + ')'
    if direction in ('higher', 'lower'):
        result += ' ↑' if direction == 'higher' else ' ↓'
    return result


def validate_display_labels(style, methods, datasets):
    """Aliases can clarify names but cannot exchange scientific identities."""
    for field, identities in (('labels', set(methods)), ('dataset_labels', set(datasets))):
        labels = style.get(field, {})
        if not isinstance(labels, dict) or any(not isinstance(key, str) or not isinstance(value, str) or not value.strip() for key, value in labels.items()):
            raise ValueError('Statistical display labels must map identities to nonempty strings')
        displayed = [labels.get(identity, identity).strip() for identity in identities]
        if len(displayed) != len(set(displayed)):
            raise ValueError('Statistical display labels must distinguish every scientific identity')
        if any(identity != value.strip() and value.strip() in identities for identity, value in labels.items()):
            raise ValueError('Statistical display labels cannot exchange method or dataset identities')


def _select(result, style, kind):
    source = result.get('comparisons', []) if kind == 'forest' else result.get('records', [])
    if not source:
        raise ValueError('The requested statistical plot has no computed results')
    metrics = list(dict.fromkeys(record['metric'] for record in source))
    metric = style.get('metric', metrics[0])
    if metric not in metrics:
        raise ValueError('Choose an actually computed statistical metric')
    records = [record for record in source if record['metric'] == metric]
    for field, selector in (('dataset', 'datasets'), ('condition', 'conditions')):
        if selector not in style:
            continue
        choices = style[selector]
        if not isinstance(choices, list) or not choices:
            raise ValueError(selector + ' must explicitly name observed groups')
        wanted = {_key(value) for value in choices}
        if not wanted <= {_key(record.get(field)) for record in records}:
            raise ValueError('Statistical plot scope contains an unobserved group')
        records = [record for record in records if _key(record.get(field)) in wanted]
    if kind != 'forest' and 'methods' in style:
        choices = style['methods']
        observed = {record['method'] for record in records}
        if not isinstance(choices, list) or not choices or len(set(choices)) != len(choices) or not set(choices) <= observed:
            raise ValueError('Choose distinct observed statistical methods')
        if set(choices) != observed and not style.get('comparison_scope'):
            raise ValueError('A method subset requires an explicit scientific comparison_scope')
        records = [record for record in records if record['method'] in choices]
    if not records:
        raise ValueError('The statistical plot selection is empty')
    units = {record.get('unit') for record in records}
    if len(units) != 1:
        raise ValueError('One metric panel must have one consistent measured unit')
    if style.get('unit') is not None and style['unit'] != next(iter(units)):
        raise ValueError('Plot styling cannot change measured units')
    ylabel = style.get('ylabel')
    unit = next(iter(units))
    if ylabel is not None:
        if not isinstance(ylabel, str) or not ylabel.strip():
            raise ValueError('Statistical axis labels must be nonempty strings')
        if unit == 'fraction' and ('%' in ylabel or 'percent' in ylabel.lower()):
            raise ValueError('A fraction cannot be displayed as a percentage without transforming the measured source')
        directions = {record.get('direction') for record in records}
        if directions == {'higher'} and '↓' in ylabel or directions == {'lower'} and '↑' in ylabel:
            raise ValueError('Axis direction cannot contradict the measured metric definition')
    return records, metric


def render_statistical_plot(output_dir, data, style=None, kind='bar'):
    """Render complete comparisons, learning curves and paired-effect panels."""
    from research.figures.render import figure_dimensions
    style = dict(style or {})
    if kind not in ('bar', 'line', 'forest', 'heatmap', 'scatter'):
        raise ValueError('This statistical recipe supports bar, line, forest, heatmap and scatter')
    result = validate_statistical_data(data)
    records, metric = _select(result, style, kind)
    identities = [item.get('statistical_identity', {}) for item in data['metric_bindings']]
    validate_display_labels(style,
        {record.get('method', record.get('candidate')) for record in records} |
        {item[field] for item in identities for field in ('method', 'baseline', 'candidate') if item.get(field)},
        {record['dataset'] for record in records} | {item['dataset'] for item in identities if item.get('dataset')})
    width, supplied_height, font = figure_dimensions(style)
    palette = style.get('palette', COLORS)
    from matplotlib.colors import is_color_like
    if not isinstance(palette, list) or not palette or any(not is_color_like(color) for color in palette):
        raise ValueError('Statistical palettes must contain valid colors')
    groups = defaultdict(list)
    for record in records:
        facet = (record['dataset'], _key(record.get('condition')))
        groups[facet].append(record)
    if kind == 'heatmap':
        groups = defaultdict(list)
        for record in records:
            groups[('Comparison', _key(record.get('condition')))].append(record)
    methods = list(dict.fromkeys(record.get('method', record.get('candidate')) for record in records))
    method_order = style.get('method_order', methods)
    if not isinstance(method_order, list) or len(set(method_order)) != len(method_order) or not set(methods) <= set(method_order):
        raise ValueError('Statistical method order must retain each observed method')
    colors = {method: palette[index % len(palette)] for index, method in enumerate(method_order)}
    labels = style.get('labels', {})
    datasets = style.get('dataset_labels', {})
    columns = min(2 if width >= 5.3 else 1, len(groups))
    rows = math.ceil(len(groups) / columns)
    legend_columns = style.get('legend_columns', min(4, len(methods), max(1, int(width / 1.7))))
    if isinstance(legend_columns, bool) or not isinstance(legend_columns, int) or not 1 <= legend_columns <= 8:
        raise ValueError('Statistical legend_columns must be between one and eight')
    legend_rows = math.ceil(len(methods) / legend_columns)
    notes = uncertainty_notes(records)
    brief = '; '.join(note.split('; ')[0] for note in notes)
    if kind in ('line', 'scatter') and any(item.get('ci_low') is not None for item in records):
        brief = brief.replace('% CI', '% pointwise CI')
    note_lines = textwrap.wrap(brief, max(32, int(width * 12)))
    note_font = max(8, font - .5)
    note_height = len(note_lines) * note_font / 72 * 1.3 + (.08 if notes else 0)
    legend_wrap = max(10, int(width * 72 / legend_columns / (font * .55) - 6))
    legend_line_count = max(len(textwrap.wrap(labels.get(method, method), legend_wrap)) for method in methods)
    legend_height = legend_rows * legend_line_count * font / 72 * 1.5 + .1 if kind in ('line', 'scatter', 'forest') else 0
    cell_height = max(2.0, .38 * max(len(items) for items in groups.values()) + 1.0) if kind == 'forest' else 2.25
    if kind == 'heatmap':
        cell_height = max(2.25, .62 * max(len({item['dataset'] for item in values}) for values in groups.values()) + 1.0)
    height = max(supplied_height, rows * cell_height + legend_height + note_height + .6)
    displayed = []
    with plt.rc_context({'font.family': 'DejaVu Serif', 'font.size': font, 'axes.labelsize': font,
                         'xtick.labelsize': font, 'ytick.labelsize': font, 'pdf.fonttype': 42,
                         'svg.fonttype': 'none', 'axes.spines.top': False, 'axes.spines.right': False}):
        fig, axes = plt.subplots(rows, columns, figsize=(width, height), squeeze=False,
                                 sharex=kind == 'forest', sharey=kind in ('line', 'bar'),
                                 gridspec_kw={'hspace': .5, 'wspace': .35})
        try:
            for panel, ((dataset, condition), items) in enumerate(groups.items()):
                ax = axes.flat[panel]
                condition_value = json.loads(condition)
                heading = str(datasets.get(dataset, dataset))
                if condition_value not in (None, '', 'default'):
                    heading += ' · ' + (', '.join(f'{key}={value}' for key, value in condition_value.items())
                                      if isinstance(condition_value, dict) else str(condition_value))
                if len(groups) > 1:
                    heading = '(' + chr(97 + panel) + ') ' + heading
                ax.set_title(textwrap.fill(heading, max(18, int(width / columns * 9))), loc='left', fontsize=font, pad=9)
                unit = items[0].get('unit')
                direction = items[0].get('direction')
                if kind in ('line', 'scatter'):
                    by_method = defaultdict(list)
                    for item in items:
                        x = item.get('x')
                        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
                            raise ValueError('A statistical curve requires an explicit finite numeric x variable; seed is a repetition identity')
                        by_method[item['method']].append(item)
                    grids = [{item['x'] for item in values} for values in by_method.values()]
                    if set(by_method) != set(methods):
                        raise ValueError('Statistical curves must retain every declared method in each dataset and condition')
                    if len({tuple(sorted(grid)) for grid in grids}) != 1:
                        raise ValueError('Statistical curves must retain every declared method at each observed checkpoint')
                    for method, values in by_method.items():
                        values.sort(key=lambda item: item['x'])
                        if len({item['x'] for item in values}) != len(values):
                            raise ValueError('Curve points must have one analysed estimate per method and checkpoint')
                        if kind == 'line' and len(values) < 2:
                            raise ValueError('A statistical curve needs at least two actual checkpoints')
                        index = method_order.index(method)
                        x = [item['x'] for item in values]
                        y = [item['estimate'] for item in values]
                        if kind == 'line':
                            ax.plot(x, y, color=colors[method], marker=MARKERS[index % len(MARKERS)],
                                    linestyle=LINES[index % len(LINES)], linewidth=1.4, markersize=3.8)
                            intervals = [uncertainty_bounds(item) for item in values]
                            if all(bounds is not None for bounds in intervals):
                                ax.fill_between(x, [bounds[0] for bounds in intervals], [bounds[1] for bounds in intervals],
                                                color=colors[method], alpha=.13, linewidth=0)
                            else:
                                for point, estimate, bounds in zip(x, y, intervals):
                                    if bounds is not None:
                                        ax.vlines(point, bounds[0], bounds[1], color=colors[method], linewidth=1)
                        else:
                            ax.scatter(x, y, color=colors[method], marker=MARKERS[index % len(MARKERS)], s=23)
                            for point, item in zip(x, values):
                                bounds = uncertainty_bounds(item)
                                if bounds is not None:
                                    ax.vlines(point, bounds[0], bounds[1], color=colors[method], linewidth=1)
                        displayed.extend(values)
                    axis = result.get('axes', {}).get('x', {})
                    xlabel = style.get('xlabel', axis.get('label', axis.get('name')))
                    if not isinstance(xlabel, str) or not xlabel.strip() or xlabel.lower() in ('seed', 'random seed'):
                        raise ValueError('Statistical curves require a named measured x variable, not repetition identities')
                    if not style.get('xlabel') and axis.get('unit'):
                        xlabel += ' (' + str(axis['unit']) + ')'
                    ax.set_xlabel(xlabel)
                    ax.set_ylabel(textwrap.fill(style.get('ylabel', _label(metric, unit, direction)), 26))
                    if style.get('xscale'):
                        if style['xscale'] not in ('linear', 'log') or style['xscale'] == 'log' and any(item['x'] <= 0 for item in items):
                            raise ValueError('Logarithmic x axes require positive actual checkpoints')
                        ax.set_xscale(style['xscale'])
                elif kind == 'bar':
                    by_method = {item['method']: item for item in items}
                    if len(by_method) != len(items) or set(by_method) != set(methods):
                        raise ValueError('Comparison panels need one record for every method; select an explicit checkpoint')
                    positions = np.arange(len(methods))
                    for index, method in enumerate(methods):
                        item = by_method[method]
                        ax.bar(index, item['estimate'], color=colors[method], width=.62, alpha=.8,
                               edgecolor='#333333', linewidth=.4)
                        bounds = uncertainty_bounds(item)
                        if bounds:
                            ax.vlines(index, bounds[0], bounds[1], color='#333333', linewidth=.9)
                            ax.hlines(bounds, index - .08, index + .08, color='#333333', linewidth=.9)
                        displayed.append(item)
                    ax.set_xticks(positions, [textwrap.fill(labels.get(method, method), 14) for method in methods],
                                  rotation=style.get('rotation', 30 if len(methods) > 4 else 0), ha='right' if len(methods) > 4 else 'center')
                    ax.set_ylabel(textwrap.fill(style.get('ylabel', _label(metric, unit, direction)), 26))
                elif kind == 'forest':
                    for index, item in enumerate(items):
                        if item.get('ci_low') is None or item.get('ci_high') is None:
                            raise ValueError('Paired-effect plots require actually computed confidence intervals')
                        method = item['candidate']
                        ax.hlines(index, item['ci_low'], item['ci_high'], color=colors[method], linewidth=1.6)
                        ax.plot(item['improvement'], index, marker=MARKERS[method_order.index(method) % len(MARKERS)],
                                color=colors[method], markersize=4)
                        displayed.append(item)
                    ax.set_yticks(range(len(items)), [textwrap.fill(labels.get(item['candidate'], item['candidate']), 16) + '\nvs ' + textwrap.fill(labels.get(item['baseline'], item['baseline']), 16)
                                  for item in items])
                    ax.invert_yaxis()
                    ax.axvline(0, color='#777777', linestyle='--', linewidth=.8)
                    ax.set_xlabel(style.get('xlabel', 'Paired improvement' + (' (' + str(unit) + ')' if unit else '') + ' →'))
                else:
                    dataset_names = list(dict.fromkeys(item['dataset'] for item in items))
                    lookup = {(item['dataset'], item['method']): item for item in items}
                    if len(lookup) != len(items) or len(lookup) != len(dataset_names) * len(methods):
                        raise ValueError('A statistical heatmap requires the complete selected dataset × method matrix')
                    values = np.array([[lookup[(name, method)]['estimate'] for method in methods] for name in dataset_names])
                    image = ax.imshow(values, cmap=style.get('cmap', 'Blues'), aspect='auto',
                                      vmin=min(item['estimate'] for item in records), vmax=max(item['estimate'] for item in records))
                    precision = items[0].get('precision', 3)
                    for yi, name in enumerate(dataset_names):
                        for xi, method in enumerate(methods):
                            item = lookup[(name, method)]
                            from matplotlib.colors import to_rgb
                            red, green, blue = to_rgb(image.cmap(image.norm(item['estimate'])))
                            color = '#111111' if .2126 * red + .7152 * green + .0722 * blue > .55 else '#FFFFFF'
                            annotation = f'{item["estimate"]:.{max(1, precision)}g}'
                            definition = item.get('uncertainty', {}).get('type')
                            if definition in ('sd', 'reported_sd') and item.get('sd') is not None:
                                annotation += '\n±' + f'{item["sd"]:.{max(1, precision)}g}'
                            elif definition in ('se', 'reported_se') and item.get('se') is not None:
                                annotation += '\n±' + f'{item["se"]:.{max(1, precision)}g}'
                            elif item.get('ci_low') is not None and item.get('ci_high') is not None:
                                annotation += '\n[' + f'{item["ci_low"]:.{max(1, precision)}g}' + ',\n' + f'{item["ci_high"]:.{max(1, precision)}g}' + ']'
                            ax.text(xi, yi, annotation, ha='center', va='center', fontsize=font, color=color)
                            displayed.append(item)
                    ax.set_xticks(range(len(methods)), [textwrap.fill(labels.get(method, method), 14) for method in methods], rotation=30, ha='right')
                    ax.set_yticks(range(len(dataset_names)), [datasets.get(name, name) for name in dataset_names])
                    fig.colorbar(image, ax=ax, fraction=.04, pad=.03, label=_label(metric, unit, direction))
                if kind != 'heatmap':
                    ax.grid(axis='x' if kind == 'forest' else 'y', alpha=.15, linewidth=.5)
                    ax.set_axisbelow(True)
            for empty in list(axes.flat)[len(groups):]:
                empty.set_visible(False)
            fig.subplots_adjust(left=.12 if kind != 'forest' else .27, right=.96, top=.91,
                                bottom=max(.17, (legend_height + note_height + .45) / height))
            if kind in ('line', 'scatter', 'forest'):
                handles = [Line2D([], [], color=colors[method], marker=MARKERS[method_order.index(method) % len(MARKERS)],
                                  linestyle=LINES[method_order.index(method) % len(LINES)] if kind == 'line' else '',
                                  label=textwrap.fill(labels.get(method, method), legend_wrap), markersize=4, linewidth=1.3)
                           for index, method in enumerate(methods)]
                fig.legend(handles=handles, loc='lower center', bbox_to_anchor=(.5, (note_height + .07) / height),
                           ncol=legend_columns, frameon=False, fontsize=font)
            # Definitions remain in the caption companion; short uncertainty
            # labels in the actual image retain their sampling interpretation.
            if notes:
                fig.text(.5, .045 / height, '\n'.join(note_lines), ha='center', va='bottom', fontsize=note_font)
            # Measure the actual font extents at physical output size. Repair
            # margins while preserving font size and record any remaining defect
            # for the deterministic candidate gate.
            geometry = []
            for attempt in range(3):
                fig.canvas.draw()
                renderer = fig.canvas.get_renderer()
                texts = [item for item in fig.findobj(match=lambda item: isinstance(item, matplotlib.text.Text))
                         if item.get_visible() and item.get_text() and (item.axes is None or item.axes.get_visible())]
                boxes = [item.get_window_extent(renderer) for item in texts]
                left = max(0, -min(box.x0 for box in boxes)) / fig.bbox.width
                right = max(0, max(box.x1 for box in boxes) - fig.bbox.width) / fig.bbox.width
                top = max(0, max(box.y1 for box in boxes) - fig.bbox.height) / fig.bbox.height
                bottom = max(0, -min(box.y0 for box in boxes)) / fig.bbox.height
                if not any((left, right, top, bottom)) or attempt == 2:
                    break
                margins = fig.subplotpars
                fig.subplots_adjust(left=margins.left + left + (.008 if left else 0),
                    right=margins.right - right - (.008 if right else 0),
                    top=margins.top - top - (.008 if top else 0),
                    bottom=margins.bottom + bottom + (.008 if bottom else 0))
            geometry = [{'text': item.get_text(), 'font_pt': item.get_fontsize(),
                         'inside_canvas': bool(box.x0 >= -1 and box.y0 >= -1 and box.x1 <= fig.bbox.width + 1 and box.y1 <= fig.bbox.height + 1)}
                        for item, box in zip(texts, boxes)]
            quality_issues = [{'code': 'text_outside_canvas', 'text': item['text']} for item in geometry if not item['inside_canvas']]
            output = Path(output_dir)
            output.mkdir(parents=True, exist_ok=True)
            paths = {}
            for extension in ('pdf', 'svg', 'png'):
                path = output / ('figure.' + extension)
                fig.savefig(path, dpi=300, facecolor='white')
                paths[extension] = str(path)
        finally:
            plt.close(fig)
    report = {'kind': kind, 'evidence_role': 'empirical', 'statistical_pipeline': True, 'production_pipeline': True,
              'width_in': width, 'height_in': height, 'minimum_font_pt': min(font, max(8, font - .5)),
              'vector_formats': ['pdf', 'svg'], 'metric': metric, 'unit': records[0].get('unit'),
              'input_rows': len(result.get('records', [])), 'plotted_rows': len(displayed), 'displayed_points': len(displayed),
              'selected_record_ids': [item['id'] for item in displayed], 'selected_methods': methods,
              'displayed_measurements': displayed, 'uncertainty': {'definitions': notes, 'scope': 'supplied analysis records'},
              'evidence_density': {'datasets': len({item['dataset'] for item in displayed}), 'methods': len(methods),
                                   'statistical_units': {item['id']: item.get('n_units', item.get('n_pairs')) for item in displayed}},
              'coverage': result['coverage'], 'panels': len(groups), 'warnings': [],
              'typography': geometry, 'quality_issues': quality_issues,
              'transformation': 'Identity-preserving display of computed statistics; no interpolation or smoothing',
              'comparison_scope': style.get('comparison_scope', 'All methods in the selected metric and conditions')}
    (output / 'figure_data.json').write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))
    (output / 'style.json').write_text(json.dumps({**style, 'kind': kind}, ensure_ascii=False, indent=2))
    (output / 'figure_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    caption = {'metric': metric, 'unit': records[0].get('unit'), 'record_ids': report['selected_record_ids'],
               'uncertainty_definitions': notes, 'argumentative_duty': style.get('argumentative_duty', 'effectiveness'),
               'instruction': 'State the supported conditional advantage and its actual comparison. Explain the interval and sampling unit once. Do not narrate analysis steps or invent a mechanism.'}
    (output / 'caption_context.json').write_text(json.dumps(caption, ensure_ascii=False, indent=2))
    (output / 'plot.py').write_text('''from pathlib import Path
import json
from research.figures.statistical import render_statistical_plot
root = Path(__file__).resolve().parent
style = json.loads((root / 'style.json').read_text())
kind = style.pop('kind')
render_statistical_plot(root, json.loads((root / 'figure_data.json').read_text()), style, kind)
''')
    paths.update({name: str(output / filename) for name, filename in {'source': 'plot.py', 'data': 'figure_data.json',
                  'style': 'style.json', 'report': 'figure_report.json', 'caption_context': 'caption_context.json'}.items()})
    return paths
