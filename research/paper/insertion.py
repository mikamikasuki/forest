"""Insert observed figure assets at a unique author-selected prose anchor."""
from __future__ import annotations

import re
from research.paper.manuscript import tex


_TEX_STRUCTURE = re.compile(
    r'\\begin\{(?P<literal>verbatim\*?|Verbatim|lstlisting|minted)\}'
    r'[\s\S]*?\\end\{(?P=literal)\}'
    r'|\\verb\*?(?P<delimiter>[^a-zA-Z\s])(?:(?!(?P=delimiter))[^\r\n])*(?P=delimiter)'
    r'|\\(?:url|path|nolinkurl|href(?:\s*\[[^\]\r\n]*\])?)\s*\{[^{}\r\n]*\}'
    r'|%[^\r\n]*|\\(?P<direction>begin|end)\s*\{(?P<environment>[^{}]+)\}'
    r'|(?P<option>\\[A-Za-z@]+\*?(?:\s|%[^\r\n]*[\r\n])*\[)'
    r'|\\[^\r\n]|[{}\]]|\$\$?'
)
_PARAGRAPH_BREAK_COMMANDS = r'part|chapter|(?:sub){0,2}section|(?:sub)?paragraph|par|clearpage|newpage'
_PARAGRAPH_BREAK_LINE = re.compile(r'\\(?:' + _PARAGRAPH_BREAK_COMMANDS + r')\b')
_NON_PROSE_LINE = re.compile(
    r'^\s*(?:%|\$\$|\\\[|\\(?:' + _PARAGRAPH_BREAK_COMMANDS
    + r'|begin|end|caption|label|maketitle|appendix|bibliography|FloatBarrier)\b)'
)
_BARE_COMMAND = re.compile(r'\\[A-Za-z@]+\*?(?:\s*%[^\r\n]*)?')
_CLOSING_TOKEN = {'{': '}', r'\(': r'\)', r'\[': r'\]', '$': '$', '$$': '$$'}


def _at_prose_level(source):
    """Check that the next position is outside ordinary LaTeX groups and blocks."""
    closers = []
    for match in _TEX_STRUCTURE.finditer(source):
        token = match[0]
        environment = match['environment']
        direction = match['direction']
        if environment:
            token = '\\' + direction + '{' + environment + '}'
        if closers and token == closers[-1]:
            closers.pop()
        elif direction == 'begin':
            closers.append(r'\end{' + environment + '}')
        elif direction == 'end' or token in ('}', r'\)', r'\]'):
            return False
        elif token in _CLOSING_TOKEN:
            closers.append(_CLOSING_TOKEN[token])
        elif match['option']:
            closers.append(']')
    return not closers


def _ends_paragraph(following):
    for line in following.split('\n'):
        line = line.lstrip()
        if not line.startswith('%'):
            return not line or bool(_PARAGRAPH_BREAK_LINE.match(line))
    return True


def insert_figure(source, *, asset_path, caption, label, anchor_text, span='column'):
    if not isinstance(anchor_text, str) or not anchor_text.strip() or source.count(anchor_text) != 1:
        raise ValueError('Select a unique existing paragraph as the figure anchor')
    if not re.fullmatch(r'fig:[A-Za-z0-9:_-]+', label):
        raise ValueError('Figure label must be a simple fig: identifier')
    if not re.fullmatch(r'figures/[A-Za-z0-9_-]+/figure\.(?:pdf|png|jpg|jpeg)', asset_path):
        raise ValueError('Use the copied local figure asset')
    if '\\label{' + label + '}' in source:
        raise ValueError('This figure label is already in the paper')
    markers = (match for match in _TEX_STRUCTURE.finditer(source) if match['environment'] == 'document')
    body, end = next(markers, None), next(markers, None)
    match_start = source.index(anchor_text)
    anchor = anchor_text.strip()
    start = match_start + len(anchor_text) - len(anchor_text.lstrip())
    offset = start + len(anchor)
    if (body is None or end is None or body['direction'] != 'begin' or end['direction'] != 'end'
            or not body.end() <= start < offset <= end.start()):
        raise ValueError('The figure anchor must be in manuscript prose')
    prefix = source[body.end():start]
    line_tail, newline, following = source[offset:end.start()].partition('\n')
    if (prefix.rpartition('\n')[2].strip() or line_tail.strip()
            or _BARE_COMMAND.fullmatch(anchor)
            or any(_NON_PROSE_LINE.match(line) for line in anchor.splitlines())
            or not _at_prose_level(prefix) or not _at_prose_level(anchor)
            or not _ends_paragraph(following)):
        raise ValueError('Select complete lines of manuscript prose ending at a paragraph boundary')
    if span not in ('column', 'page'):
        raise ValueError('Figure span must be column or page')
    if not isinstance(caption, str) or not caption.strip():
        raise ValueError('A substantive figure caption is required')
    preamble = source[:body.start()]
    environment = 'figure*' if span == 'page' and 'twocolumn' in preamble else 'figure'
    anchor_label='forestargument:'+label
    offset += len(line_tail) + len(newline)
    insertion = ('' if newline else '\n')+r'\label{'+anchor_label+'}\n\n' + '\n'.join([
        '% FOREST visual anchor '+label+' '+anchor_label,
        'Figure~\\ref{' + label + '} ' + tex(caption) + '\n',
        '\\begin{' + environment + '}[!htbp]', r'\centering',
        r'\includegraphics[width=\linewidth]{' + asset_path + '}',
        r'\caption{' + tex(caption) + '}', r'\label{' + label + '}',
        '\\end{' + environment + '}',
    ]) + '\n\n'
    for package in ('graphicx','flafter','placeins'):
        if not re.search(r'\\usepackage(?:\[[^\]]*\])?\{[^}]*\b'+package+r'\b', preamble):
            preamble += '\\usepackage{'+package+'}\n'
    return preamble + source[body.start():offset] + insertion + source[offset:]
