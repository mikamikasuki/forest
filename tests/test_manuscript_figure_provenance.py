"""Regression checks for source-run provenance on manuscript studio figures."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from research.figures.render import render_figure
from research.paper.evidence import collect_evidence, resolve_run_metrics_file
from services.api.db import Base, Figure, Project, SourcePaper, TaskRun
from services.worker.execute import manuscript_figures


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'figures.sqlite'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _render_studio_pdf(root: Path, folder_name: str = 'studio_figure') -> dict[str, str]:
    target = root / 'figures' / folder_name
    rows = [
        {'dataset': 'd1', 'method': 'baseline', 'score': 10.0},
        {'dataset': 'd1', 'method': 'candidate', 'score': 12.0},
    ]
    rendered = render_figure(target, rows, {'metric': 'score', 'unit': 'points'}, 'bar')
    return {
        key: Path(path).relative_to(root).as_posix()
        for key, path in rendered.items()
        if Path(path).is_file()
    }


def _completed_run(session, project_id: str, root: Path, run_id: str = 'r-selected', value: int = 42) -> TaskRun:
    rel_output = f'runs/{run_id}'
    run_dir = root / rel_output
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics = {'observed_total': value}
    (run_dir / 'metrics.json').write_text(json.dumps(metrics))
    run = TaskRun(
        id=run_id,
        project_id=project_id,
        request_id=f'req-{run_id}',
        kind='experiment',
        status='completed',
        output_path=rel_output,
        metrics=metrics,
        config={'project_goal': 'Verify figure source-run provenance'},
    )
    session.add(run)
    session.flush()
    return run


@pytest.mark.parametrize(
    'unbound_fields',
    [
        {},
        {'source_run_ids': []},
        {'run_ids': []},
        {'source_run_ids': [], 'run_ids': []},
        {'source_run_ids': None, 'run_ids': None},
        {'source_run_ids': [], 'run_ids': ['r-selected']},
        {'source_run_ids': 'r-selected'},
        {'run_ids': 'r-selected'},
        {'source_run_ids': ['']},
        {'source_run_ids': [None]},
        {'source_run_ids': ['r-selected', 'r-selected']},
        {'source_run_ids': ['r-selected'], 'run_ids': ['r-other']},
    ],
)
def test_manuscript_figures_never_assigns_unbound_or_invalid_figure_to_selected_run(tmp_path, unbound_fields):
    Session = _session_factory(tmp_path)
    root = tmp_path / 'project'
    root.mkdir()
    outputs = _render_studio_pdf(root)

    with Session.begin() as session:
        project = Project(id='p-1', name='Provenance check', goal='Verify figure provenance')
        session.add(project)
        selected_run = _completed_run(session, project.id, root, 'r-selected')
        figure = Figure(
            id='fig-unbound',
            project_id=project.id,
            title='Unbound figure',
            status='ready_for_review',
            data={
                'kind': 'bar',
                'caption': 'Rendered figure without declared source runs.',
                'outputs': outputs,
                **unbound_fields,
            },
        )
        session.add(figure)

    with Session() as session:
        selected = [session.get(TaskRun, selected_run.id)]
        with pytest.raises(ValueError, match='Selected figure must declare source runs within the manuscript evidence scope'):
            manuscript_figures(session, 'p-1', root, selected, figure_ids=['fig-unbound'])

        discovered = manuscript_figures(session, 'p-1', root, selected)
        assert discovered == []

        evidence = collect_evidence(
            runs=[{
                'id': selected[0].id,
                'status': selected[0].status,
                'directory': str(root / selected[0].output_path),
                'metrics_file': resolve_run_metrics_file(root / selected[0].output_path),
                'config': selected[0].config,
            }],
            figures=discovered,
        )
        assert evidence['figures'] == []
        stored_figure = session.get(Figure, 'fig-unbound')
        assert stored_figure.data.get('source_run_ids') == unbound_fields.get('source_run_ids')
        assert stored_figure.data.get('run_ids') == unbound_fields.get('run_ids')


def test_manuscript_figures_admits_declared_runs_and_preserves_exact_provenance(tmp_path):
    Session = _session_factory(tmp_path)
    root = tmp_path / 'project'
    root.mkdir()
    unbound_outputs = _render_studio_pdf(root, 'unbound')
    bound_second_outputs = _render_studio_pdf(root, 'bound_second')
    bound_multi_outputs = _render_studio_pdf(root, 'bound_multi')
    outside_outputs = _render_studio_pdf(root, 'outside')

    with Session.begin() as session:
        project = Project(id='p-2', name='Declared provenance', goal='Preserve declared figure runs')
        session.add(project)
        run_first = _completed_run(session, project.id, root, 'r-first', 10)
        run_second = _completed_run(session, project.id, root, 'r-second', 20)
        _completed_run(session, project.id, root, 'r-outside', 30)
        session.add_all([
            Figure(
                id='fig-unbound',
                project_id=project.id,
                title='Unbound',
                status='ready_for_review',
                data={'kind': 'bar', 'caption': 'Unbound.', 'outputs': unbound_outputs},
            ),
            Figure(
                id='fig-second-only',
                project_id=project.id,
                title='Second run figure',
                status='ready_for_review',
                data={
                    'kind': 'bar',
                    'caption': 'Produced by the second run.',
                    'run_ids': [run_second.id],
                    'outputs': bound_second_outputs,
                },
            ),
            Figure(
                id='fig-multi',
                project_id=project.id,
                title='Joint comparison figure',
                status='ready_for_review',
                data={
                    'kind': 'bar',
                    'caption': 'Produced from both selected runs.',
                    'source_run_ids': [run_second.id, run_first.id],
                    'run_ids': [],
                    'outputs': bound_multi_outputs,
                },
            ),
            Figure(
                id='fig-outside',
                project_id=project.id,
                title='Outside scope figure',
                status='ready_for_review',
                data={
                    'kind': 'bar',
                    'caption': 'Uses an unselected run.',
                    'source_run_ids': [run_first.id, 'r-outside'],
                    'outputs': outside_outputs,
                },
            ),
        ])

    with Session() as session:
        selected = [session.get(TaskRun, 'r-first'), session.get(TaskRun, 'r-second')]
        with pytest.raises(ValueError, match='Selected figure uses runs outside the manuscript evidence scope'):
            manuscript_figures(session, 'p-2', root, selected, figure_ids=['fig-outside'])

        discovered = manuscript_figures(session, 'p-2', root, selected)
        assert [item['id'] for item in discovered] == ['fig-second-only', 'fig-multi']
        assert discovered[0]['run_id'] == 'r-second'
        assert discovered[0]['source_run_ids'] == ['r-second']
        assert discovered[1]['run_id'] == 'r-second'
        assert discovered[1]['source_run_ids'] == ['r-second', 'r-first']

        evidence = collect_evidence(
            runs=[{
                'id': run.id,
                'status': run.status,
                'directory': str(root / run.output_path),
                'metrics_file': resolve_run_metrics_file(root / run.output_path),
                'config': run.config,
            } for run in selected],
            figures=discovered,
        )
        by_id = {item['id']: item for item in evidence['figures']}
        assert set(by_id) == {'fig-second-only', 'fig-multi'}
        assert by_id['fig-second-only']['run_id'] == 'r-second'
        assert by_id['fig-second-only']['source_run_ids'] == ['r-second']
        assert by_id['fig-multi']['run_id'] == 'r-second'
        assert by_id['fig-multi']['source_run_ids'] == ['r-second', 'r-first']


def test_worker_paper_generate_rejects_selected_unbound_figure_and_ignores_unselected_unbound_figure(tmp_path):
    root = Path(__file__).resolve().parents[1]
    pythonpath = os.pathsep.join([str(root), *[p for p in os.environ.get('PYTHONPATH', '').split(os.pathsep) if p]])
    env = {
        **os.environ,
        'FOREST_DATA_DIR': str(tmp_path / 'data'),
        'FOREST_DATABASE_URL': f"sqlite:///{tmp_path / 'worker.sqlite'}",
        'FOREST_OWNER_TOKEN': 'figure-provenance-owner-token',
        'PYTHONPATH': pythonpath,
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), 'worker-check'],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _run_worker_check():
    from services.api.common import project_dir
    from services.api.db import PaperDocument, Session, migrate, uid
    from services.worker.execute import execute
    from services.worker.scheduler import enqueue

    migrate()
    pid = uid()
    root = project_dir(pid)
    outputs = _render_studio_pdf(root, 'unbound_worker')

    with Session.begin() as session:
        session.add(Project(id=pid, name='Worker provenance check', goal='Reject unbound manuscript figures'))
        run = _completed_run(session, pid, root, 'r-selected', 780)
        session.add(SourcePaper(
            id='src-1',
            project_id=pid,
            title='Reference calculation note',
            data={'abstract': 'Bounded arithmetic reference.'},
            status='available',
        ))
        session.add(Figure(
            id='fig-unbound',
            project_id=pid,
            title='Unbound studio figure',
            status='ready_for_review',
            data={
                'kind': 'bar',
                'caption': 'Rendered PDF without source_run_ids or run_ids.',
                'outputs': outputs,
            },
        ))
        origin = enqueue(session, pid, 'paper_generate', {'run_ids': [run.id], 'manuscript_type': 'full_paper'})
        origin.status = 'failed'
        origin.error = 'Saved response fixture; no model call'

    draft = {
        'manuscript_type': 'full_paper',
        'title': 'Bounded provenance check',
        'abstract': 'The observed total is [[metric:m0]].',
        'claim_ids': [],
        'sections': [
            {
                'role': role,
                'title': role.replace('_', ' ').title(),
                'paragraphs': [
                    'Reference note [[source:src-1]].' if role == 'related_work'
                    else 'Bounded empirical result [[metric:m0]].'
                ],
            }
            for role in ('introduction', 'related_work', 'method', 'experimental_setup', 'results', 'discussion')
        ],
        'conclusion': 'Provenance remains bound to declared runs.',
    }
    relative = 'paper/model_responses/provenance-fixture/attempt-1.json'
    response_file = root / origin.output_path / relative
    response_file.parent.mkdir(parents=True, exist_ok=True)
    response_file.write_text(json.dumps({
        'text': json.dumps(draft),
        'model': 'saved-fixture',
        'usage': {},
    }))

    with Session.begin() as session:
        explicit = enqueue(session, pid, 'paper_generate', {
            'run_ids': ['r-selected'],
            'figure_ids': ['fig-unbound'],
            'manuscript_type': 'full_paper',
            'saved_response_run_id': origin.id,
            'saved_response_path': relative,
        })

    with pytest.raises(ValueError, match='Selected figure must declare source runs within the manuscript evidence scope'):
        execute(explicit.id)

    explicit_folder = root / explicit.output_path / 'paper'
    assert not (explicit_folder / 'evidence.json').exists()
    with Session() as session:
        assert session.scalar(select(PaperDocument).where(PaperDocument.project_id == pid)) is None
        assert session.get(Figure, 'fig-unbound').data.get('source_run_ids') is None
        assert session.get(Figure, 'fig-unbound').data.get('run_ids') is None

    with Session.begin() as session:
        unselected = enqueue(session, pid, 'paper_generate', {
            'run_ids': ['r-selected'],
            'manuscript_type': 'full_paper',
            'saved_response_run_id': origin.id,
            'saved_response_path': relative,
        })

    has_tex = bool(shutil.which('tectonic') or shutil.which('pdflatex'))
    if has_tex:
        receipt = execute(unselected.id)
        assert receipt['actual_compilation']
    else:
        with pytest.raises(RuntimeError, match='No tectonic or pdflatex executable found'):
            execute(unselected.id)

    paper_folder = root / unselected.output_path / 'paper'
    saved_evidence = json.loads((paper_folder / 'evidence.json').read_text())
    assert saved_evidence['figures'] == []
    assert json.loads((paper_folder / 'figure_bindings.json').read_text()) == []


if __name__ == '__main__' and len(sys.argv) == 2 and sys.argv[1] == 'worker-check':
    _run_worker_check()
