"""Real arithmetic, plotting and LaTeX layout regressions; no model or fake receipts."""
from copy import deepcopy
import json
import math
from pathlib import Path

import pytest

from research.figures.render import render_figure
from research.paper.evidence import collect_evidence, write_manuscript
from research.paper.layout import normalize_layout, numeric_display, apply_layout
from research.paper.manuscript import compile_paper


def material(tmp_path, row_count=38):
    run = tmp_path / 'actual-arithmetic'
    run.mkdir()
    results = [{'n': n, 'sum': sum(range(n)), 'ratio': math.pi/n} for n in range(1, row_count+1)]
    (run/'metrics.json').write_text(json.dumps({'rows': results}))
    chart = [{'dataset': 'integer sum', 'method': str(row['n']), 'score': row['sum']} for row in results[:3]]
    outputs = render_figure(run/'figure', chart, {'metric': 'score', 'unit': 'integer total', 'layout_width_in': 6.5})
    figure_report = json.loads(Path(outputs['report']).read_text())
    (run/'figures.json').write_text(json.dumps([{'id': 'sums', 'path': 'figure/figure.pdf', **{key:figure_report[key] for key in ('width_in','minimum_font_pt')}}]))
    evidence = collect_evidence([{'id':'arithmetic', 'status':'completed', 'directory':str(run)}])
    rows = [[f'Sum up to {n-1}', f'[[metric:m{3*(n-1)+1}]]', f'[[metric:m{3*(n-1)+2}]]'] for n in range(1,row_count+1)]
    draft = {'title':'Arithmetic layout verification', 'abstract':'The first sum is [[metric:m1]].', 'claim_ids':[],
             'sections':[{'title':'Recorded arithmetic', 'paragraphs':['A local reviewer edited this exact sentence.'], 'blocks':[
                 {'type':'table', 'columns':['Sequence endpoint','Integer sum','Ratio'], 'rows':rows, 'caption':'All computed arithmetic rows.', 'label':'tab:arithmetic'},
                 {'type':'figure','panels':[{'figure_id':'arithmetic:sums','caption':'Computed sums.'},{'figure_id':'arithmetic:sums','caption':'The same saved evidence, repeated for layout verification.'}], 'caption':'Actual arithmetic figure panels.', 'label':'fig:arithmetic'}]}],
             'conclusion':'The supplied arithmetic values are preserved in the rendered tables.'}
    return evidence,draft


@pytest.mark.parametrize('columns', ['single','double'])
def test_actual_long_table_panels_and_layout_preflight(tmp_path, columns):
    evidence,draft = material(tmp_path)
    output=tmp_path/'paper'
    generated=write_manuscript(output,evidence,draft,layout={'columns':columns,'max_table_rows':14})
    plan=generated['layout_plan']; table=plan['blocks'][0]
    assert table['strategy']==('longtable' if columns=='single' else 'split')
    assert table['font_pt']==9
    source=(output/'paper.tex').read_text()
    assert '\\resizebox' not in source and '\\scriptsize' not in source
    assert 'raggedleft' in source and '\\begin{minipage}' in source
    assert all(cell[1] in json.dumps(draft) for cell in draft['sections'][0]['blocks'][0]['rows'])
    compiled=compile_paper(output)
    if compiled['status']=='unavailable': pytest.skip('Actual TeX compiler required')
    assert compiled['status']=='completed',compiled['log']
    report=compiled['preflight']
    assert report['actual_compilation'] and report['page_count']>=2
    assert report['checks']['glyphs']=='passed' and report['checks']['references']=='passed'
    assert report['checks']['assets']=='passed' and report['checks']['bindings']=='passed'
    assert (output/'layout_preflight.json').is_file()
    assert len(json.loads((output/'bindings.json').read_text()))==76


def test_reflow_preserves_current_edited_text_and_exact_values(tmp_path):
    evidence,draft=material(tmp_path,5)
    output=tmp_path/'paper'
    write_manuscript(output,evidence,draft)
    source=(output/'paper.tex').read_text().replace('A local reviewer edited this exact sentence.','The owner changed this sentence after generation.')
    (output/'paper.tex').write_text(source)
    before=json.loads((output/'bindings.json').read_text())
    plan=apply_layout(output,{'columns':'double','significant_digits':3,'figure_span':'page'})
    current=(output/'paper.tex').read_text()
    assert 'The owner changed this sentence after generation.' in current
    assert 'A local reviewer edited this exact sentence.' not in current
    assert '\\documentclass[11pt,twocolumn]{article}' in current
    assert '\\begin{figure*}' in current and '\\begin{table*}' in current
    after=json.loads((output/'bindings.json').read_text())
    assert [b['value'] for b in before]==[b['value'] for b in after]
    assert any(a['display']!=b['display'] for a,b in zip(before,after))
    assert plan['mode']=='existing_source_reflow'
    compiled=compile_paper(output)
    if compiled['status']=='unavailable': pytest.skip('Actual TeX compiler required')
    assert compiled['status']=='completed',compiled['log']


def test_current_longtable_reflows_to_double_columns_and_retains_edited_cells(tmp_path):
    evidence,draft=material(tmp_path,38)
    output=tmp_path/'paper'
    write_manuscript(output,evidence,draft,layout={'max_table_rows':14})
    source=(output/'paper.tex').read_text().replace('Sum up to 17','Owner-edited endpoint')
    (output/'paper.tex').write_text(source)
    plan=apply_layout(output,{'columns':'double','max_table_rows':8})
    current=(output/'paper.tex').read_text()
    assert '\\begin{longtable}' not in current
    assert current.count('\\begin{table*}')==5
    assert current.count('Owner-edited endpoint')==1
    assert all(item['rows']<=8 for item in plan['blocks'] if item['type']=='table')
    assert current.count('\\label{tab:arithmetic}')==1
    compiled=compile_paper(output)
    if compiled['status']=='unavailable': pytest.skip('Actual TeX compiler required')
    assert compiled['status']=='completed',compiled['log']
    assert compiled['preflight']['checks']['references']=='passed'


def test_venue_and_numeric_policy_are_explicit(tmp_path):
    with pytest.raises(ValueError,match='single-column'):
        normalize_layout({'columns':'double'},'iclr2027')
    with pytest.raises(ValueError,match='never shrunk'):
        normalize_layout({'table_font_pt':7,'min_font_pt':8})
    assert numeric_display(math.pi*1e8,normalize_layout({'significant_digits':3}))==r'\ensuremath{3.14\times 10^{8}}'
    assert numeric_display(math.pi*1e8,normalize_layout({'significant_digits':3,'scientific_notation':'never'}))=='314000000'
    assert numeric_display(.125,normalize_layout({'significant_digits':3,'scientific_notation':'always'}))==r'\ensuremath{1.25\times 10^{-1}}'
    (tmp_path/'paper.tex').write_text('\\documentclass{article}\n\\usepackage{iclr2027_conference,times}\n')
    with pytest.raises(ValueError,match='single-column'):
        apply_layout(tmp_path,{'columns':'double'})


def test_figure_unknown_methods_units_and_uncertainty_are_preserved(tmp_path):
    rows=[{'dataset':'integer sums','method':name,'score':sum(range(n))} for name,n in [('method-A',4),('method-B',5)]]
    result=render_figure(tmp_path/'plain',rows,{'metric':'score','unit':'integer total','paper_layout':{'columns':'double'}})
    report=json.loads(Path(result['report']).read_text())
    assert report['selected_methods']==['method-A','method-B']
    assert report['uncertainty']=={'type':'none'} and report['width_in']==3.125
    assert 'seed SD' not in Path(result['svg']).read_text()
    repeated=[{**row,'repeat':unit,'score':row['score']+unit} for row in rows for unit in range(3)]
    with pytest.raises(ValueError,match='explicit aggregation'):
        render_figure(tmp_path/'rejected',repeated,{'metric':'score'})
    result=render_figure(tmp_path/'aggregated',repeated,{'metric':'score','unit':'integer total','aggregation':{'method':'mean','unit':'repeat','uncertainty':'sd'}})
    report=json.loads(Path(result['report']).read_text())
    assert report['uncertainty']['unit']=='repeat' and report['uncertainty']['type']=='sd'
    assert 'standard deviation across repeat' in Path(result['svg']).read_text()
    with pytest.raises(ValueError,match='at least one'):
        render_figure(tmp_path/'empty',rows,{'metric':'score','methods':[]})


def test_generic_method_diagram_requires_supplied_graph(tmp_path):
    with pytest.raises(ValueError,match='explicit nodes and edges'):
        render_figure(tmp_path/'missing',{},kind='method')
    result=render_figure(tmp_path/'graph',{'nodes':[{'id':'data','label':'Actual observations'},{'id':'estimate','label':'Declared estimator'}],
        'edges':[{'source':'data','target':'estimate'}]},kind='method')
    assert Path(result['pdf']).read_bytes().startswith(b'%PDF')
    assert 'Balanced logistic' not in Path(result['svg']).read_text()


def test_ambiguous_line_and_calibration_groups_are_not_silently_pooled(tmp_path):
    with pytest.raises(ValueError,match='explicitly aggregate'):
        render_figure(tmp_path/'line',[{'method':'observed','x':1,'score':sum(range(n))} for n in (4,5)],{'metric':'score','x':'x'},'line')
    rows=[{'method':'observed','dataset':dataset,'y_true':label,'probability':(.8 if label else .2)}
          for dataset in ('first','second') for label in (0,1)]
    with pytest.raises(ValueError,match='seed'):
        render_figure(tmp_path/'calibration',rows,{},'calibration')
    paths=render_figure(tmp_path/'calibration-facets',[{**row,'seed':0} for row in rows],{},'calibration')
    report=json.loads(Path(paths['report']).read_text())
    assert set(report['datasets'])=={'first','second'}
    assert report['uncertainty']['unit']=='seed'


def test_real_delayed_float_is_repaired_from_actual_compiled_pages(tmp_path):
    # Earlier queued floats occupy successive pages. The final small visual
    # initially lands several pages after its argument; no mocked compiler.
    source = r"""\documentclass{article}
\usepackage{graphicx,flafter,placeins}
\begin{document}
Queued large actual layout panels.
"""
    for index in range(3):
        source += r"\begin{figure}[p]\centering\rule{9cm}{18cm}\caption{Layout panel}" + r"\label{fig:queued" + str(index) + r"}\end{figure}" + '\n'
    source += r"\label{argument:target}This paragraph motivates Figure~\ref{fig:target}." + '\n'
    source += r"% FOREST visual anchor fig:target argument:target" + '\n'
    source += r"\begin{figure}[p]\centering\rule{9cm}{2cm}\caption{Target layout panel}\label{fig:target}\end{figure}" + '\n'
    source += r"\end{document}"
    (tmp_path/'paper.tex').write_text(source)
    first=compile_paper(tmp_path)
    if first['status']=='unavailable':pytest.skip('Actual TeX compiler required')
    assert first['status']=='completed'
    assert first['preflight']['visual_placements'][0]['page_distance']>1
    repaired=compile_paper(tmp_path,repair_layout=True)
    assert repaired['status']=='completed'
    assert repaired['preflight']['visual_placements'][0]['page_distance']<=1
    assert repaired['layout_repair_attempts']==1
    assert (tmp_path/'layout_repairs/attempt-1-before.tex').read_text()==source
    current=(tmp_path/'paper.tex').read_text()
    assert current.count('FOREST placement repair before argument:target')==1
    assert source.count('\\caption')==current.count('\\caption')


@pytest.mark.parametrize('environment', ['tabular', 'longtable'])
def test_reflow_counts_only_outer_row_breaks(tmp_path, environment):
    # A real multi-line owner edit at a panel boundary and a second-row
    # nested environment exercise the same parser through both callers.
    cell = r'\shortstack{one\\two\\three\\four\\five}'
    nested = r'\begin{tabular}[t]{@{}l@{}}Left\\Right\end{tabular}'
    edited = r'{\footnotesize Percent \% and \{brace\}}'
    header = '\\toprule\n' + r'Label & Value \\' + '\n\\midrule[0.5pt]\n'
    rows = '\n'.join([
        cell + r' & 1 \\',
        nested + r' & 2 \\',
        r'\begingroup Three\endgroup & 3 % ignore this \\ and }',
        r'\\',
        r'Four & 4 \\[2pt]',
        r'\addlinespace[3pt]',
        edited + r' & 5 \tabularnewline % retain final note',
    ]) + '\n'
    if environment == 'tabular':
        table = (r'\begin{table}\small\caption{Observed rows}\label{tab:rows}' + '\n' +
                 r'\begin{tabular}{lr}' + '\n' + header + rows + r'\bottomrule\end{tabular}\end{table}')
    else:
        table = (r'\begin{longtable}{lr}\caption{Observed rows}\label{tab:rows}\\' + '\n' +
                 header + '\\endfirsthead\n' + header + '\\endhead\n\\bottomrule\n\\endfoot\n' +
                 rows + r'\end{longtable}')
    source_path = tmp_path / 'paper.tex'
    inactive = r'% \begin{longtable}{l} commented draft \end{longtable}'
    source_path.write_text('\\documentclass{article}\n\\usepackage{booktabs,longtable}\n\\begin{document}\n' +
                           inactive + '\n' + table + '\n\\end{document}\n')
    before = compile_paper(tmp_path, repair_layout=False)
    if before['status'] != 'unavailable':
        assert before['status'] == 'completed', before['log']
    plan = apply_layout(tmp_path, {'columns': 'single' if environment == 'tabular' else 'double',
                                   'max_table_rows': 4})
    current = source_path.read_text()
    assert inactive in current
    assert plan['config']['columns'] == ('single' if environment == 'tabular' else 'double')
    assert current.count(r'\begin{table') == 2
    assert current.count(r'\label{tab:rows}') == 1
    assert current.count(cell) == current.count(nested) == current.count(edited) == 1
    assert current.count('% retain final note') == 1
    assert 'Four & 4 ' + r'\\[2pt]' + '\n' + r'\addlinespace[3pt]' in current
    assert r'5 \tabularnewline % retain final note' in current
    assert current.count(r'\midrule[0.5pt]') == 2
    assert r'\begingroup Three\endgroup & 3 % ignore this \\ and }' + '\n' + r'\\' in current
    table_plans = [block for block in plan['blocks'] if block['type'] == 'table']
    assert sum(block['rows'] for block in table_plans) == 5
    assert sum(block['panels'] for block in table_plans) == 2
    after = compile_paper(tmp_path, repair_layout=False)
    if before['status'] == 'unavailable':
        pytest.skip('Actual TeX compiler required to check that the reflowed source compiles')
    assert after['status'] == 'completed', after['log']


@pytest.mark.parametrize('environment', ['tabular', 'longtable'])
@pytest.mark.parametrize('row_suffix', [r'[\dimexpr 1pt\relax]', '*'])
def test_reflow_leaves_custom_row_breaks_intact(tmp_path, environment, row_suffix):
    header = r'\toprule Label \\ \midrule' + '\n'
    rows = r'First \\' + row_suffix + '\n' + r'Second \\' + '\n'
    if environment == 'tabular':
        table = (r'\begin{table}[t]\small\begin{tabular}{l}' + header + rows +
                 r'\bottomrule\end{tabular}\end{table}')
    else:
        table = (r'\begin{longtable}{l}\caption{Observed rows}\\' + '\n' +
                 header + '\\endfirsthead\n' + header + '\\endhead\n\\bottomrule\n\\endfoot\n' +
                 rows + r'\end{longtable}')
    source_path = tmp_path / 'paper.tex'
    source_path.write_text('\\documentclass{article}\n\\usepackage{booktabs,longtable}\n\\begin{document}\n' +
                           table + '\n\\end{document}\n')
    config = {'columns': 'single' if environment == 'tabular' else 'double',
              'max_table_rows': 4, 'table_font_pt': 10}
    plan = apply_layout(tmp_path, config)
    assert table in source_path.read_text()
    assert plan['config']['columns'] == 'single'
    assert [warning['code'] for warning in plan['warnings']] == [
        'custom_table_row_limit_unapplied' if environment == 'tabular' else 'custom_longtable_requires_review'
    ]
    compiled = compile_paper(tmp_path, repair_layout=False)
    if compiled['status'] == 'unavailable':
        pytest.skip('Actual TeX compiler required to check that the retained source compiles')
    assert compiled['status'] == 'completed', compiled['log']
