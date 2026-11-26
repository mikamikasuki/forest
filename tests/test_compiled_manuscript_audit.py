"""Real PDF compilation against an isolated complete audit fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from pypdf import PdfReader

from research.paper.manuscript import compile_paper
from research.paper.structure import FULL_PAPER_ROLES
from research.publication import assess_submission


pytestmark = pytest.mark.skipif(
    not (shutil.which('tectonic') or shutil.which('pdflatex')), reason='Requires a TeX compiler',
)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def readiness_inputs(root):
    # All other readiness inputs are synthetic, local control records.
    sources, peers = [], []
    for index in range(15):
        sid = f'fixture-peer-{index}'
        url = f'https://proceedings.mlr.press/v1/fixture{index}.html'
        sources.append({'id': sid, 'data': {'url': url, 'read_scope': 'full_text',
                        'passages': [{'id': 'excerpt', 'page': 1, 'text': 'Fixture peer text'}]}})
        detail = {'finding': 'Fixture finding', 'passage_ids': ['excerpt']}
        peers.append({'source_id': sid, 'acceptance_url': url, 'acceptance_kind': 'official_proceedings',
                      **dict.fromkeys(('relevance', 'experiment_design', 'figures', 'tables', 'adopted_changes'), detail),
                      'scale': dict.fromkeys(('datasets', 'baselines', 'seeds', 'independent_units'), 1)})
    datasets = ['d1', 'd2', 'd3']
    baselines = [f'baseline-{i}' for i in range(5)]
    ablations = [f'ablation-{i}' for i in range(4)]
    seeds = list(range(5))
    studies = [
        ('effectiveness', datasets, ['candidate', *baselines]),
        ('mechanism', ['d1'], ['candidate', *ablations]),
        ('scenario_value', ['d1'], ['candidate', baselines[0]]),
        ('alternative_explanation', ['d2'], ['candidate', baselines[1]]),
    ]
    cells = [{'dataset': dataset, 'method': method, 'seed': seed, 'study': duty}
             for duty, scope, methods in studies for dataset in scope for method in methods for seed in seeds]
    save(root / 'runs/measure/workspace/observations.json',
         [{**cell, 'unit_id': 'fixture-unit', 'measurement': cell['seed']} for cell in cells])
    stats = {'improvement': .2, 'ci_low': .1, 'ci_high': .3}
    runs = [
        {'id': 'measure', 'kind': 'experiment', 'output_path': 'runs/measure'},
        {'id': 'analysis', 'kind': 'analysis', 'output_path': 'runs/analysis', 'metrics': stats},
        {'id': 'review', 'kind': 'agent', 'output_path': 'runs/review', 'config': {'role': 'Submission Reviewer'}},
        {'id': 'compile', 'kind': 'paper_compile', 'output_path': 'runs/compile'},
    ]
    for run in runs:
        run['status'] = 'completed'
        save(root / run['output_path'] / 'result.json', {'status': 'completed', 'metrics': run.get('metrics', {})})
    manifest = {
        'target_venue': 'Fixture conference', 'central_claim': 'Mechanical audit fixture', 'candidate': 'candidate',
        'comparable_papers': peers,
        'datasets': [{'id': name, 'source': 'fixture', 'split_policy': 'held out', 'sample_size': 1} for name in datasets],
        'baselines': [{'id': name, 'source': 'fixture', 'fair_budget': 'matched'} for name in baselines],
        'ablations': [{'id': name, 'mechanism': 'fixture removal'} for name in ablations],
        'seeds': seeds,
        'studies': [{'id': duty, 'argumentative_duty': duty, 'datasets': scope, 'methods': methods, 'scope_rationale': 'Fixture'}
                    for duty, scope, methods in studies],
        'measured_cells': [{**cell, 'run_id': 'measure', 'raw_path': 'observations.json'} for cell in cells],
        'analysis_run_ids': ['analysis'], 'reviewer_run_ids': ['review'],
        'statistics': [{'effect': stats['improvement'], 'ci_low': stats['ci_low'], 'ci_high': stats['ci_high'], 'analysis_run_id': 'analysis'}],
        'manuscript': {'compile_run_id': 'compile', 'source_path': 'runs/compile/paper/paper.tex',
                       'pdf_path': 'runs/compile/paper/paper.pdf', 'layout_report': 'runs/compile/paper/layout_preflight.json'},
    }
    return sources, runs, manifest


def manuscript(root):
    folder = root / 'runs/compile/paper'
    folder.mkdir(parents=True)
    source = folder / 'paper.tex'
    source.write_text(r'''\documentclass{article}
\usepackage{float}
\begin{document}
Complete synthetic manuscript.
\section{Method}\label{arg:method}
Figure \ref{fig:mechanism} shows the method.
\begin{figure}[H]\centering\rule{8mm}{8mm}\caption{Method}\label{fig:mechanism}\end{figure}
\section{Results}\label{arg:results}
Table \ref{tab:comparison} and Figure \ref{fig:analysis} record the comparison.
\begin{table}[H]\centering\begin{tabular}{lc}Method & Value \\ Fixture & 1\end{tabular}
\caption{Comparison}\label{tab:comparison}\end{table}
\begin{figure}[H]\centering\rule{8mm}{8mm}\caption{Analysis}\label{fig:analysis}\end{figure}
\end{document}
''')
    visuals = {'method': [('figure', 'fig:mechanism', 'mechanism', 'arg:method')],
               'results': [('table', 'tab:comparison', 'effectiveness', 'arg:results'),
                           ('figure', 'fig:analysis', 'effectiveness', 'arg:results')]}
    save(folder / 'draft.json', {'manuscript_type': 'full_paper', 'sections': [
        {'role': role, 'blocks': [{'type': kind, 'label': label, 'argumentative_duty': duty}
                                  for kind, label, duty, _ in visuals.get(role, [])]} for role in sorted(FULL_PAPER_ROLES)]})
    save(folder / 'layout_plan.json', {'visual_placements': [
        {'label': label, 'anchor_label': anchor, 'has_local_reference': True}
        for specs in visuals.values() for _, label, _, anchor in specs]})
    return source


def review(root, compile_id='compile'):
    save(root / 'runs/review/workspace/submission_review.json',
         {'verdict': 'ready', 'unresolved_issues': [], 'reviewed_run_ids': ['measure', 'analysis', compile_id],
          'evidence': [f'runs/{compile_id}/paper/paper.pdf']})


def compile_revision(root, source, run):
    output = root / run['output_path']
    compiled = compile_paper(source.parent, evidence_root=output)
    assert compiled['status'] == 'completed', compiled['log']
    evidence = compiled['compile_evidence']
    run['metrics'] = {**evidence, 'paper_source': str(Path(evidence['paper_source']).relative_to(root)),
                      'pdf_path': str(Path(evidence['pdf_path']).relative_to(root))}
    save(output / 'result.json', {'status': 'completed', 'metrics': run['metrics']})
    return Path(compiled['pdf_path'])


@pytest.mark.parametrize('changed', ['source', 'pdf', 'legacy_receipt'])
def test_submission_requires_the_current_compiled_source_and_pdf(tmp_path, changed):
    root = tmp_path.resolve()
    sources, runs, manifest = readiness_inputs(root)
    source = manuscript(root)
    run = runs[-1]
    pdf = compile_revision(root, source, run)
    review(root)

    def audit():
        return assess_submission(root, sources=sources, runs=runs, manifest=manifest)

    fresh = audit()
    assert fresh['ready'], fresh['gaps']
    assert 'Complete synthetic manuscript' in PdfReader(pdf).pages[0].extract_text()
    if changed == 'source':
        original_pdf = pdf.read_bytes()
        attributes = source.stat()
        source.write_text(source.read_text().replace('Complete synthetic manuscript', 'Modified synthetic manuscript'))
        os.utime(source, ns=(attributes.st_atime_ns, attributes.st_mtime_ns))
        assert source.stat().st_size == attributes.st_size
        assert pdf.read_bytes() == original_pdf
    elif changed == 'pdf':
        replacement = tmp_path / 'replacement'
        replacement.mkdir()
        (replacement / source.name).write_text(
            source.read_text().replace('Complete synthetic manuscript', 'Modified synthetic manuscript'))
        different = compile_paper(replacement)
        assert different['status'] == 'completed', different['log']
        shutil.copyfile(different['pdf_path'], pdf)
        assert 'Modified synthetic manuscript' in PdfReader(pdf).pages[0].extract_text()
    else:
        run['metrics'].pop('compile_originals')
        save(root / run['output_path'] / 'result.json', {'status': 'completed', 'metrics': run['metrics']})

    review(root)
    stale = audit()
    assert not stale['ready']
    assert len(stale['gaps']) == 1
    assert stale['gaps'][0]['code'] == 'compiled_manuscript'
    assert 'Recompile' in stale['gaps'][0]['finding']

    recompiled = {**run, 'id': 'recompiled', 'output_path': 'runs/recompiled'}
    folder = root / recompiled['output_path'] / 'paper'
    shutil.copytree(source.parent, folder)
    pdf = compile_revision(root, folder / source.name, recompiled)
    runs.append(recompiled)
    manifest['manuscript'] = {
        'compile_run_id': recompiled['id'],
        'source_path': recompiled['metrics']['paper_source'],
        'pdf_path': recompiled['metrics']['pdf_path'],
        'layout_report': str((folder / 'layout_preflight.json').relative_to(root)),
    }
    review(root, recompiled['id'])
    recovered = audit()
    assert recovered['ready'], recovered['gaps']
    expected = 'Modified' if changed == 'source' else 'Complete'
    assert expected + ' synthetic manuscript' in PdfReader(pdf).pages[0].extract_text()


def test_compile_does_not_record_source_edited_during_compilation(tmp_path, monkeypatch):
    source = manuscript(tmp_path)
    launch = subprocess.run

    def compile_then_edit(*args, **kwargs):
        result = launch(*args, **kwargs)
        if result.returncode == 0:
            source.write_text(source.read_text().replace('Complete synthetic manuscript', 'Modified synthetic manuscript'))
        return result

    monkeypatch.setattr(subprocess, 'run', compile_then_edit)
    compiled = compile_paper(source.parent, evidence_root=source.parent.parent)
    assert compiled['status'] == 'failed'
    assert 'source changed during compilation' in compiled['log']
    assert 'compile_evidence' not in compiled


def test_compile_does_not_record_pdf_left_by_an_earlier_pass(tmp_path, monkeypatch):
    find_compiler = shutil.which
    if not find_compiler('pdflatex'):
        pytest.skip('Requires pdfLaTeX to exercise separate compiler passes')
    monkeypatch.setattr(shutil, 'which', lambda name: None if name == 'tectonic' else find_compiler(name))
    source = tmp_path / 'paper.tex'
    source.write_text(r'''\documentclass{article}
\newif\iffirstpass
\IfFileExists{paper.aux}{\firstpassfalse}{\firstpasstrue}
\begin{document}
\iffirstpass The initial pass produced this page.\fi
\end{document}
''')

    compiled = compile_paper(tmp_path, evidence_root=tmp_path / 'run')
    assert 'The initial pass produced this page.' in PdfReader(tmp_path / 'paper.pdf').pages[0].extract_text()
    assert compiled['status'] == 'failed'
    assert compiled['pdf_path'] is None
    assert 'compile_evidence' not in compiled
