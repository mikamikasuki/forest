"""Editable layout decisions and checks against an actually compiled manuscript."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re


DEFAULT_LAYOUT = {
    'columns': 'single', 'significant_digits': 5, 'scientific_notation': 'auto',
    'table_font_pt': 9, 'min_font_pt': 8, 'max_table_rows': 18,
    'float_placement': 'auto', 'figure_span': 'auto', 'table_span': 'auto',
}
PLACEMENTS = {'auto': '!htbp', 'top': 't', 'bottom': 'b', 'page': 'p', 'here': '!htbp'}
SPANS = {'auto', 'column', 'page'}


def normalize_layout(layout=None, template='article'):
    if layout is not None and not isinstance(layout, dict):
        raise ValueError('Manuscript layout must be an object')
    config = {**DEFAULT_LAYOUT, **(layout or {})}
    unknown = set(config) - set(DEFAULT_LAYOUT)
    if unknown:
        raise ValueError('Unknown manuscript layout fields: ' + ', '.join(sorted(unknown)))
    if config['columns'] not in ('single', 'double'):
        raise ValueError('Layout columns must be single or double')
    if template not in ('article', 'iclr2027'):
        raise ValueError('Layout supports article and iclr2027 templates')
    if template == 'iclr2027' and config['columns'] != 'single':
        raise ValueError('The official ICLR 2027 template is single-column; choose article for a double-column draft')
    if config['scientific_notation'] not in ('auto', 'always', 'never'):
        raise ValueError('scientific_notation must be auto, always or never')
    for key, lower, upper in [('significant_digits', 2, 10), ('max_table_rows', 4, 60)]:
        if isinstance(config[key], bool) or not isinstance(config[key], int) or not lower <= config[key] <= upper:
            raise ValueError(f'{key} must be an integer between {lower} and {upper}')
    for key in ('table_font_pt', 'min_font_pt'):
        if isinstance(config[key], bool) or not isinstance(config[key], (int, float)) or not math.isfinite(config[key]) or not 7 <= config[key] <= 12:
            raise ValueError(key + ' must be a finite point size between 7 and 12')
    if config['table_font_pt'] < config['min_font_pt']:
        raise ValueError('Table font size must meet the declared minimum; tables are never shrunk to fit')
    if config['float_placement'] not in PLACEMENTS or any(config[key] not in SPANS for key in ('figure_span', 'table_span')):
        raise ValueError('Invalid float placement or figure/table span')
    return config


def block_layout(block, config):
    local = block.get('layout', {})
    if not isinstance(local, dict):
        raise ValueError('Block layout must be an object')
    if set(local) - {'span', 'placement', 'strategy', 'max_rows', 'font_pt', 'panel_columns'}:
        raise ValueError('Unknown block layout field')
    result = {'span': config.get(block['type'] + '_span', 'auto'),
              'placement': config['float_placement'], 'strategy': 'auto',
              'font_pt': config['table_font_pt'], 'max_rows': config['max_table_rows'],
              'panel_columns': 2, **local}
    if result['span'] not in SPANS or result['placement'] not in PLACEMENTS:
        raise ValueError('Invalid block span or placement')
    if result['strategy'] not in ('auto', 'wrap', 'split', 'longtable'):
        raise ValueError('Table strategy must be auto, wrap, split or longtable')
    if isinstance(result['font_pt'], bool) or not isinstance(result['font_pt'], (int, float)) or not config['min_font_pt'] <= result['font_pt'] <= 12:
        raise ValueError('Block font size must meet the manuscript minimum and be at most 12 pt')
    for key, lower, upper in [('max_rows', 4, 60), ('panel_columns', 1, 3)]:
        if isinstance(result[key], bool) or not isinstance(result[key], int) or not lower <= result[key] <= upper:
            raise ValueError('Invalid block ' + key)
    return result


def numeric_display(value, config=None):
    """Display precision is separate from the original numeric binding."""
    config = config or DEFAULT_LAYOUT
    if isinstance(value, int):
        return str(value)
    digits = config['significant_digits']
    mode = config['scientific_notation']
    if mode == 'always' and value:
        display = f'{value:.{digits-1}e}'
    else:
        display = f'{value:.{digits}g}'
    if 'e' in display and mode == 'never':
        from decimal import Decimal
        return format(Decimal(display), 'f')
    if 'e' in display:
        mantissa, exponent = display.split('e')
        mantissa = mantissa.rstrip('0').rstrip('.') if '.' in mantissa else mantissa
        return '\\ensuremath{' + mantissa + '\\times 10^{' + str(int(exponent)) + '}}'
    return display


def block_identifier(section_index, block_index, appendix=False):
    return ('appendix' if appendix else 'section') + f'-{section_index}-block-{block_index}'


def plan_layout(draft, evidence, layout=None, template='article'):
    from research.paper.structure import section_blocks, wrap_equation, visual_placements
    config = normalize_layout(layout if layout is not None else draft.get('layout'), template)
    text_width = 5.5 if template == 'iclr2027' else 6.5
    column_width = text_width if config['columns'] == 'single' else (text_width - .25) / 2
    plan = {'version': 1, 'mode': 'structured_render', 'template': template, 'config': config,
            'text_width_in': text_width, 'column_width_in': column_width, 'blocks': [], 'warnings': [], 'visual_placements': []}
    figures = {figure['id']: figure for figure in evidence.get('figures', [])}
    for appendix, sections in [(False, draft['sections']), (True, draft.get('appendices', []))]:
        for si, section in enumerate(sections):
            for placement in visual_placements(section):
                placement = {**placement, 'id': block_identifier(si, placement['block_index'], appendix),
                             'section_index': si, 'section_title': section['title'],
                             'section_role': section.get('role'), 'appendix': appendix}
                blocks = section_blocks(section)
                preceding = next((bi for bi in range(placement['block_index']-1, -1, -1) if blocks[bi].get('type') == 'paragraph'), None)
                if preceding is not None:
                    placement['anchor_label'] = 'forest-anchor:' + block_identifier(si, preceding, appendix)
                plan['visual_placements'].append(placement)
                if not placement['has_local_reference']:
                    plan['warnings'].append({'code': 'visual_without_local_reference',
                        'message': 'Visual ' + str(placement['label'] or placement['id']) + ' needs a substantive numbered reference in its owning section.'})
            for bi, block in enumerate(section_blocks(section)):
                if block['type'] == 'equation':
                    wrapped = wrap_equation(block['latex'], config['columns']) != block['latex']
                    plan['blocks'].append({'id': block_identifier(si, bi, appendix), 'label': block.get('label'),
                        'type': 'equation', 'span': 'column', 'placement': 'here', 'strategy': 'aligned_relations' if wrapped else 'original_math',
                        'font_pt': 11, 'width_in': column_width, 'reasons': ['Line breaks at existing relation separators; mathematical content preserved'] if wrapped else []})
                    continue
                if block['type'] not in ('table', 'figure'):
                    continue
                settings = block_layout(block, config)
                kind = block['type']
                reasons = []
                span = settings['span']
                rows, columns = len(block.get('rows', [])), len(block.get('columns', []))
                panels = len(block.get('panels', [])) or 1
                if span == 'auto':
                    # Long labels and several numeric comparisons need page width,
                    # not a smaller font at the final physical size.
                    wide = columns > 3 if kind == 'table' else panels > 1 or block.get('width', .95) >= .9
                    span = 'page' if config['columns'] == 'double' and wide else 'column'
                    reasons.append('Automatic page span for a wide comparison' if span == 'page' else 'Fits the document column')
                strategy = settings['strategy']
                if kind == 'table' and strategy == 'auto':
                    strategy = ('longtable' if config['columns'] == 'single' else 'split') if rows > settings['max_rows'] else 'wrap'
                if kind == 'table' and strategy == 'longtable' and config['columns'] == 'double':
                    strategy = 'split'
                    reasons.append('Double-column pages use repeated table panels; longtable cannot span two columns')
                if kind == 'table' and strategy == 'split':
                    panels = math.ceil(rows / settings['max_rows'])
                    reasons.append('All rows retained in repeated panels with headers')
                if kind == 'figure':
                    ids = [panel['figure_id'] for panel in block.get('panels', [])] or [block['figure_id']]
                    panel_count = min(settings['panel_columns'], len(ids))
                    target_width = (text_width if span == 'page' else column_width) * block.get('width', .95) / panel_count
                    for figure_id in ids:
                        meta = figures.get(figure_id, {}).get('render_metadata', {})
                        if meta.get('width_in') and meta.get('minimum_font_pt'):
                            estimated_font = meta['minimum_font_pt'] * target_width / meta['width_in']
                            if estimated_font < config['min_font_pt']:
                                plan['warnings'].append({'code': 'figure_font_below_target', 'message': f'Figure {figure_id} is estimated at {estimated_font:.1f} pt after placement; regenerate at {target_width:.2f} inches or use a wider span.'})
                plan['blocks'].append({'id': block_identifier(si, bi, appendix), 'label': block.get('label'),
                    'type': kind, 'span': span, 'placement': settings['placement'], 'strategy': strategy,
                    'font_pt': settings['font_pt'], 'width_in': text_width if span == 'page' else column_width,
                    'rows': rows, 'columns': columns, 'panels': panels, 'max_rows': settings['max_rows'],
                    'panel_columns': settings['panel_columns'], 'reasons': reasons})
    return plan


def _column_source(source, config, template):
    if template == 'article':
        source = source.replace('% FOREST_COLUMN_SPACING\n\\raggedbottom\n', '')
        source = re.sub(r'% FOREST_FULL_WIDTH_ABSTRACT_BEGIN\n\\twocolumn\[\n\\begin\{@twocolumnfalse\}\n(.*?)\n\\end\{@twocolumnfalse\}\n\]\n% FOREST_FULL_WIDTH_ABSTRACT_END', lambda match: match.group(1), source, flags=re.S)
        options = '11pt,twocolumn' if config['columns'] == 'double' else '11pt'
        source = re.sub(r'\\documentclass(?:\[[^]]*\])?\{article\}', lambda _: '\\documentclass[' + options + ']{article}', source, count=1)
        source = re.sub(r'\\setlength\{\\columnsep\}\{[^}]+\}\s*', '', source)
        if config['columns'] == 'double':
            source = source.replace('\\begin{document}', '\\setlength{\\columnsep}{0.25in}\n% FOREST_COLUMN_SPACING\n\\raggedbottom\n\\begin{document}', 1)
            source = re.sub(r'\\maketitle(.*?\\end\{abstract\})', lambda match:
                '% FOREST_FULL_WIDTH_ABSTRACT_BEGIN\n\\twocolumn[\n\\begin{@twocolumnfalse}\n\\maketitle' + match.group(1) +
                '\n\\end{@twocolumnfalse}\n]\n% FOREST_FULL_WIDTH_ABSTRACT_END', source, count=1, flags=re.S)
    return source


def float_barriers(source):
    """Keep each visual in its argumentative section and after its declaration."""
    source = source.replace('% FOREST_RESULTS_BOUNDARY\n\\clearpage\n\\twocolumn\n', '')
    source = source.replace('% FOREST_RESULTS_BOUNDARY\n\\clearpage\n', '')
    if 'placeins' not in source:
        source = source.replace('\\begin{document}', '\\usepackage{placeins}\n\\begin{document}', 1)
    if not re.search(r'\\usepackage(?:\[[^]]*\])?\{[^}]*\bflafter\b', source):
        source = source.replace('\\begin{document}', '\\usepackage{flafter}\n\\begin{document}', 1)
    # Generic article floats can share a page with their argument. LaTeX's
    # default 70% top / 50% float-page thresholds isolate medium-sized figures
    # on mostly empty pages. Official venue styles keep their own thresholds.
    if '\\usepackage[margin=1in]{geometry}' in source and '% FOREST article float occupancy' not in source:
        settings = ('% FOREST article float occupancy\n'
                    '\\renewcommand{\\topfraction}{0.92}\n'
                    '\\renewcommand{\\bottomfraction}{0.85}\n'
                    '\\renewcommand{\\textfraction}{0.08}\n'
                    '\\renewcommand{\\floatpagefraction}{0.85}\n'
                    '\\renewcommand{\\dbltopfraction}{0.92}\n'
                    '\\renewcommand{\\dblfloatpagefraction}{0.85}\n')
        source=source.replace('\\begin{document}',settings+'\\begin{document}',1)
    source = re.sub(r'(\\section\*?\{|\\bibliographystyle\{|\\appendix\b)', lambda match: '\\FloatBarrier\n' + match.group(), source)
    source = re.sub(r'(?:\\FloatBarrier\s*){2,}', lambda _: '\\FloatBarrier\n', source)
    if re.search(r'\\documentclass\[[^]]*twocolumn[^]]*\]\{article\}', source):
        # Drain page-wide result floats and reset both columns together.
        source = source.replace('\\FloatBarrier\n\\section{Discussion}', '% FOREST_RESULTS_BOUNDARY\n\\clearpage\n\\twocolumn\n\\section{Discussion}')
    return source


_TABLE_TOKEN = re.compile(r'\\(begin|end)\s*\{([^{}\s]+)\}|\\(?:[A-Za-z]+|.)|[{}]|%[^\n]*')
_TABLE_TRIVIA = re.compile(r'\s*(?:%[^\n]*(?:\n\s*|$))*')
_ROW_SPACING = re.compile(r'\[[^\[\]{}%\\\r\n]+\]')


def _top_level_table_commands(source):
    """Find TeX control sequences outside braces, nested environments and comments."""
    groups = []
    environments = []
    commands = []
    for token in _TABLE_TOKEN.finditer(source):
        value = token.group()
        if value in ('{', r'\bgroup', r'\begingroup'):
            groups.append(r'\endgroup' if value == r'\begingroup' else '}')
        elif value in ('}', r'\egroup', r'\endgroup'):
            closer = '}' if value == r'\egroup' else value
            if not groups or groups.pop() != closer:
                return None
        elif value in (r'\verb', r'\catcode'):
            # These can change how subsequent tokens are interpreted.
            return None
        elif token.group(1) == 'begin':
            name = token.group(2)
            if name in ('verbatim', 'verbatim*', 'Verbatim', 'lstlisting', 'minted', 'comment'):
                return None
            environments.append((name, len(groups)))
        elif token.group(1) == 'end':
            if not environments or environments.pop() != (token.group(2), len(groups)):
                return None
        elif value in (r'\begin', r'\end'):
            return None
        elif not groups and not environments and value.startswith('\\'):
            commands.append(token)
    return None if groups or environments else commands


def _spaced_break_end(source, position):
    following = _TABLE_TRIVIA.match(source, position).end()
    if source[following:following+1] == '*':
        # A starred break forbids a page break between these rows.
        return None
    if source[following:following+1] == '[':
        spacing = _ROW_SPACING.match(source, following)
        return spacing.end() if spacing is not None else None
    return position


def _table_rows(source, commands, start, end):
    """Keep renderer-shaped rows intact rather than normalizing their TeX."""
    rows = []
    for token in commands:
        if not start <= token.start() < end:
            continue
        if token.group() in (r'\cr', r'\crcr', r'\hline', r'\cline',
                             r'\toprule', r'\midrule', r'\bottomrule', r'\cmidrule',
                             r'\specialrule', r'\noalign',
                             r'\endfirsthead', r'\endhead', r'\endfoot', r'\endlastfoot'):
            return None
        if token.group() in (r'\\', r'\tabularnewline', r'\addlinespace'):
            stop = _spaced_break_end(source, token.end())
            if stop is None:
                return None
            if token.group() == r'\addlinespace':
                if not rows or _TABLE_TRIVIA.match(source, start).end() != token.start():
                    return None
                # Like explicit row spacing, keep the gap with the preceding row
                # when the next row reflows into a different panel.
                rows[-1] += source[start:stop]
            else:
                rows.append(source[start:stop])
            start = stop
    tail = source[start:end]
    if not rows or _TABLE_TRIVIA.match(tail).end() != len(tail):
        return None
    if tail.strip():
        rows[-1] += tail
    return rows


def _table_parts(body, environment='tabular'):
    """Parse only the renderer's booktabs structure, preserving edited cell text."""
    start = body.find('\\begin{' + environment + '}{')
    if start < 0:
        return None
    spec_start = start + len('\\begin{' + environment + '}')
    depth, stop = 0, None
    for index in range(spec_start, len(body)):
        if body[index] == '{': depth += 1
        elif body[index] == '}':
            depth -= 1
            if depth == 0:
                stop = index + 1; break
    if stop is None:
        return None
    end = body.rfind('\\end{' + environment + '}')
    if end < stop:
        return None
    table = body[stop:end]
    commands = _top_level_table_commands(table)
    if commands is None:
        return None
    rules = {}
    for token in commands:
        rules.setdefault(token.group(), []).append(token)
    if not all(rules.get(name) for name in (r'\toprule', r'\midrule', r'\bottomrule')):
        return None
    top, mid = rules[r'\toprule'][0], rules[r'\midrule'][0]
    mid_end = _spaced_break_end(table, mid.end())
    if top.start() >= mid.start() or mid_end is None:
        return None
    if environment == 'longtable':
        footer = rules.get(r'\endfoot', [])
        if len(footer) != 1 or rules.get(r'\endlastfoot') or footer[0].start() <= mid_end:
            return None
        data_start, data_end = footer[0].end(), len(table)
    else:
        bottom = rules[r'\bottomrule'][-1]
        if bottom.start() <= mid_end or table[bottom.end():].strip():
            return None
        data_start, data_end = mid_end, bottom.start()
    rows = _table_rows(table, commands, data_start, data_end)
    if rows is None:
        return None
    return {'spec': body[spec_start:stop], 'header': table[top.start():mid_end], 'rows': rows,
            'start': start, 'stop': end+len('\\end{' + environment + '}'), 'caption': table[:top.start()].strip()}


def _tabular_text(parts, rows, continued=False):
    header = re.sub(r'\\label\{[^}]+\}', '', parts['header']) if continued else parts['header']
    data = ''.join(rows)
    if not data[:1].isspace():
        data = '\n' + data
    if not data.endswith('\n'):
        data += '\n'
    return '\\begin{tabular}' + parts['spec'] + '\n' + header + data + '\\bottomrule\n\\end{tabular}'


def _longtable_spans(source):
    start = None
    for token in _TABLE_TOKEN.finditer(source):
        if token.group(2) != 'longtable':
            continue
        if token.group(1) == 'begin':
            start = token.start()
        elif start is not None:
            yield start, token.end()
            start = None


def apply_layout(directory, layout, template=None):
    """Reflow the current source without regenerating its prose from an old draft."""
    directory = Path(directory)
    source_path = directory / 'paper.tex'
    source = source_path.read_text()
    old = json.loads((directory / 'layout_plan.json').read_text()) if (directory / 'layout_plan.json').exists() else {}
    template_record = json.loads((directory / 'template.json').read_text()) if (directory / 'template.json').exists() else {}
    detected_template = 'iclr2027' if 'iclr2027_conference' in source else 'article'
    template = template or old.get('template') or template_record.get('template') or detected_template
    config = normalize_layout({**old.get('config', {}), **(layout or {})}, template)
    if template_record.get('template', detected_template) != template:
        from research.paper.manuscript import apply_template
        if template == 'iclr2027':
            source_path.write_text(_column_source(source, {**config, 'columns': 'single'}, 'article'))
        apply_template(directory, template)
        source = source_path.read_text()
    changed, warnings = [], []
    longtables = [(start, end, _table_parts(source[start:end], 'longtable'))
                  for start, end in _longtable_spans(source)] if config['columns'] == 'double' else []
    if any(parts is None for _, _, parts in longtables):
        # A retained longtable cannot compile in two-column mode. Defer only
        # the column change until its owner can make the custom table convertible.
        config = {**config, 'columns': 'single'}
        warnings.append({'code': 'custom_longtable_requires_review',
                         'message': 'Kept one column instead of the requested two: an unrecognized longtable was retained without conversion.'})

    # Convert recognized longtables, including edits, before float reflow.
    def convert_longtable(parts):
        caption = parts['caption'].rstrip().removesuffix('\\\\').rstrip()
        result = []
        chunks = [parts['rows'][i:i+config['max_table_rows']] for i in range(0,len(parts['rows']),config['max_table_rows'])]
        for index, rows in enumerate(chunks):
            title = caption if index == 0 else re.sub(r'\\label\{[^}]+\}', '', caption)
            result.append('\\begin{table*}[tbp]\n\\centering\n\\small\n' + title + '\n' + _tabular_text(parts, rows, continued=index > 0) + '\n\\end{table*}')
        return '\n\n'.join(result)
    if config['columns'] == 'double':
        for start, end, parts in reversed(longtables):
            source = source[:start] + convert_longtable(parts) + source[end:]
    source = float_barriers(_column_source(source, config, template))
    def float_block(match):
        kind, starred, placement, body = match.groups()
        if '\\includegraphics' not in body and not ('\\toprule' in body and '\\begin{tabular' in body):
            warnings.append({'code': 'custom_float_preserved', 'message': 'An unrecognized ' + kind + ' was retained without rewriting its content.'})
            return match.group()
        requested = config[kind + '_span']
        span = ('page' if config['columns'] == 'double' else 'column') if requested == 'auto' else requested
        star = '*' if span == 'page' and config['columns'] == 'double' else ''
        place = PLACEMENTS[config['float_placement']]
        parts = _table_parts(body) if kind == 'table' else None
        if kind == 'table':
            if parts is None:
                warnings.append({'code': 'custom_table_row_limit_unapplied', 'message': 'This table has no recognized editable row structure; max_table_rows was not applied.'})
                if '\\begin{tabular' in body:
                    return match.group()
            font = config['table_font_pt']
            command = '\\fontsize{' + str(font) + '}{' + str(round(font * 1.2, 2)) + '}\\selectfont'
            prefix = body[:parts['start']] if parts else body
            if re.search(r'\\fontsize\{[^}]+\}\{[^}]+\}\\selectfont', prefix):
                prefix = re.sub(r'\\fontsize\{[^}]+\}\{[^}]+\}\\selectfont', lambda _: command, prefix)
            else:
                prefix = re.sub(r'\\(?:small|footnotesize|scriptsize)\b', lambda _: command, prefix)
        chunks = [parts['rows'][i:i+config['max_table_rows']] for i in range(0,len(parts['rows']),config['max_table_rows'])] if parts else []
        changed.append({'id': 'existing-' + str(len(changed)), 'type': kind, 'span': span,
                        'placement': config['float_placement'], 'strategy': 'preserve_current_source',
                        'font_pt': config['table_font_pt'] if kind == 'table' else None,
                        'rows': len(parts['rows']) if parts else None, 'panels': len(chunks) or 1,
                        'reasons': ['Reflowed current source; prose and cells preserved']})
        bodies = [prefix if kind == 'table' else body]
        if parts:
            suffix = body[parts['stop']:]
            bodies = [prefix + body[parts['start']:parts['stop']] + suffix]
            if len(chunks) > 1:
                changed[-1]['strategy'] = 'split_current_rows'
                bodies = []
                for index, rows in enumerate(chunks):
                    # Remove repeated float labels, not labels in the owner's data rows.
                    before = re.sub(r'\\label\{[^}]+\}', '', prefix) if index else prefix
                    after = re.sub(r'\\label\{[^}]+\}', '', suffix) if index else suffix
                    bodies.append(before + _tabular_text(parts, rows, continued=index > 0) + after)
        return '\n\n'.join('\\begin{' + kind + star + '}[' + place + ']' + item + '\\end{' + kind + star + '}' for item in bodies)
    source = re.sub(r'\\begin\{(table|figure)(\*?)\}(?:\[([^]]*)\])?(.*?)\\end\{\1\2\}', float_block, source, flags=re.S)
    if config['columns'] == 'double':
        from research.paper.structure import wrap_equation
        def wrap_existing_equation(match):
            body = match.group(1)
            labels = re.findall(r'\\label\{[^}]+\}', body)
            expression = re.sub(r'\\label\{[^}]+\}', '', body).strip()
            wrapped = wrap_equation(expression, 'double')
            return '\\begin{equation}\n' + '\n'.join(labels) + '\n' + wrapped + '\n\\end{equation}' if wrapped != expression else match.group()
        source = re.sub(r'\\begin\{equation\}(.*?)\\end\{equation\}', wrap_existing_equation, source, flags=re.S)
    source_path.write_text(source)
    binding_path = directory / 'bindings.json'
    if binding_path.exists():
        bindings = json.loads(binding_path.read_text())
        macro_path = directory / 'results_macros.tex'
        macros = macro_path.read_text() if macro_path.exists() else ''
        for binding in bindings:
            before = '\\newcommand{\\' + binding['macro'] + '}{' + binding.get('display', str(binding['value'])) + '}'
            if before not in macros:
                warnings.append({'code': 'edited_numeric_macro_preserved', 'message': 'Numeric macro ' + binding['macro'] + ' differs from its binding and was preserved for review.'})
                continue
            binding['display'] = numeric_display(binding['value'], config)
            macros = macros.replace(before, '\\newcommand{\\' + binding['macro'] + '}{' + binding['display'] + '}')
        macro_path.write_text(macros)
        binding_path.write_text(json.dumps(bindings, indent=2))
    plan = {**old, 'version': 1, 'mode': 'existing_source_reflow', 'template': template, 'config': config,
            'text_width_in': 5.5 if template == 'iclr2027' else 6.5,
            'column_width_in': 3.125 if config['columns'] == 'double' else 5.5 if template == 'iclr2027' else 6.5,
            'blocks': changed, 'warnings': warnings}
    (directory / 'layout_plan.json').write_text(json.dumps(plan, indent=2))
    return plan


def compile_preflight(directory, source_name, completed, log, actual_compilation=True):
    """Compiler and artifact observations, never a scientific endorsement."""
    from research.paper.manuscript import check_paper
    directory = Path(directory)
    source = (directory / source_name).read_text()
    final_log = directory / (Path(source_name).stem + '.log')
    if final_log.exists():
        log = final_log.read_text(errors='replace')
    issues, assets = [], []
    checks = {key: 'not_checked' for key in ('glyphs', 'overflow', 'references', 'assets', 'pdf', 'bindings', 'visual_placement')}
    for code, pattern, severity in [('missing_glyph', r'Missing character:[^\n]*', 'error'),
                                    ('overflow', r'Overfull \\[hv]box[^\n]*', 'warning'),
                                    ('undefined_reference', r'[^\n]*(?:undefined references|Reference[^\n]*undefined|Citation[^\n]*undefined)[^\n]*', 'error')]:
        for message in dict.fromkeys(re.findall(pattern, log, re.I)):
            issues.append({'code': code, 'severity': severity, 'message': message.strip()})
    if actual_compilation:
        for key, code in [('glyphs', 'missing_glyph'), ('overflow', 'overflow'), ('references', 'undefined_reference')]:
            checks[key] = 'failed' if any(item['code'] == code for item in issues) else 'passed'
    for kind, pattern in [('figure', r'\\includegraphics(?:\[[^]]*\])?\{([^}]+)\}'),
                           ('input', r'\\input\{([^}]+)\}'), ('bibliography', r'\\bibliography\{([^}]+)\}')]:
        for relative in re.findall(pattern, source):
            relative += '.bib' if kind == 'bibliography' and not relative.endswith('.bib') else ''
            path = directory / relative
            exists = path.is_file() and path.resolve().is_relative_to(directory.resolve())
            assets.append({'path': relative, 'exists': exists, 'kind': kind})
            if not exists:
                issues.append({'code': 'missing_asset', 'severity': 'error', 'message': 'Missing local ' + kind + ': ' + relative})
    checks['assets'] = 'passed' if all(item['exists'] for item in assets) else 'failed'
    for item in check_paper(directory, source_name)['issues']:
        issues.append({**item, 'severity': 'error'})
    checks['bindings'] = ('failed' if any(item['code'] in ('stale_metric', 'stale_macro', 'missing_metric') for item in issues) else 'passed') if (directory/'bindings.json').exists() else 'not_checked'
    page_count = None
    pdf = directory / (Path(source_name).stem + '.pdf')
    if completed and pdf.exists():
        try:
            from pypdf import PdfReader
            page_count = len(PdfReader(pdf).pages)
            checks['pdf'] = 'passed'
        except Exception as exc:
            issues.append({'code': 'unreadable_pdf', 'severity': 'error', 'message': str(exc)})
            checks['pdf'] = 'failed'
    elif actual_compilation:
        checks['pdf'] = 'failed'
        issues.append({'code': 'compilation_failed', 'severity': 'error', 'message': 'Compilation did not produce a current PDF.'})
    plan_path = directory / 'layout_plan.json'
    observed_placements = []
    placements = []
    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
        for item in plan.get('warnings', []):
            issues.append({**item, 'severity': 'warning'})
        placements.extend(plan.get('visual_placements', []))
    # Editor-inserted figures carry source-visible anchors, so their current
    # compiled positions remain auditable even without a structured draft.
    for label, anchor in re.findall(r'% FOREST visual anchor ([A-Za-z][A-Za-z0-9:_.-]*) ([A-Za-z][A-Za-z0-9:_.-]*)', source):
        if not any(item.get('label') == label for item in placements):
            placements.append({'label':label,'anchor_label':anchor,'anchor_source':'editor_exact_paragraph','type':'figure'})
    auxiliary = directory / (Path(source_name).stem + '.aux')
    if actual_compilation and completed and auxiliary.is_file() and placements:
        pages = dict(re.findall(r'\\newlabel\{([^}]+)\}\{\{[^{}]*\}\{([^{}]+)\}', auxiliary.read_text(errors='replace')))
        for item in placements:
            visual_page = pages.get(item.get('label'))
            anchor_page = pages.get(item.get('anchor_label'))
            observation = {**item, 'visual_page': visual_page, 'anchor_page': anchor_page}
            if visual_page is not None and anchor_page is not None and visual_page.isdigit() and anchor_page.isdigit():
                observation['page_distance'] = int(visual_page) - int(anchor_page)
                if observation['page_distance'] < 0:
                    issues.append({'code': 'visual_precedes_argument', 'severity': 'error',
                        'message': 'Visual ' + str(item['label']) + ' appears before its argumentative paragraph.'})
                elif observation['page_distance'] > 1:
                    issues.append({'code': 'visual_far_from_argument', 'severity': 'warning',
                        'message': 'Visual ' + str(item['label']) + ' is ' + str(observation['page_distance']) + ' pages after its argumentative paragraph; revise span, panel structure or placement.'})
            else:
                issues.append({'code':'visual_anchor_unresolved','severity':'warning',
                    'message':'The current compiled source does not resolve both visual and paragraph pages for '+str(item['label'])})
            observed_placements.append(observation)
        checks['visual_placement'] = 'failed' if any(item['code'] == 'visual_precedes_argument' for item in issues) else 'needs_review' if any(item['code'] in ('visual_far_from_argument', 'visual_without_local_reference','visual_anchor_unresolved') for item in issues) else 'passed'
    if observed_placements:
        (directory / 'placement_report.json').write_text(json.dumps({'version': 1, 'status': checks['visual_placement'],
            'placements': observed_placements, 'scope': 'Actual LaTeX auxiliary page positions relative to argumentative paragraph anchors.'}, indent=2))
    status = 'unavailable' if not actual_compilation else 'failed' if any(item['severity'] == 'error' for item in issues) else 'needs_review' if issues else 'passed'
    report = {'status': status, 'actual_compilation': actual_compilation, 'page_count': page_count,
              'issues': issues, 'assets': assets, 'checks': checks,
              'visual_placements': observed_placements,
              'scope': 'Actual compiler output, readable PDF and local assets/bindings; visual reading and scientific validation remain separate.'}
    (directory / 'layout_preflight.json').write_text(json.dumps(report, indent=2))
    return report
