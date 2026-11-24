"""Figure insertion rejects non-prose anchors without mutating the saved paper."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from test_api import ROOT, create, ok, pdf_bytes


def case_paper_figure_requires_prose_anchor(client):
    from services.api.common import project_dir

    project = create(client)
    project_id = project['id']
    root = project_dir(project_id)
    original_asset = root / 'measured.pdf'
    original_asset.write_bytes(pdf_bytes('Measured output'))
    figure = ok(client.post('/api/figures', json={
        'project_id': project_id, 'title': 'Measured output',
        'data': {'outputs': {'pdf': original_asset.name}},
    }))
    figure = ok(client.patch(f"/api/figures/{figure['id']}", json={
        'expected_revision': figure['revision'], 'status': 'ready_for_review',
    }))
    paper_url = f'/api/papers/{project_id}'
    paper = ok(client.get(paper_url))
    heading = r'\section{Measured results}'
    first_line = 'The blue curve is explained by'
    paragraph = first_line + '\n' + r'\emph{the preceding simulation} within the 5\% tolerance.'
    trailing_space = '  \t'
    source = '\n'.join([
        r'\documentclass{article}', r'\usepackage{hyperref}', r'% Preamble ends at \begin{document}',
        r'\begin{document}', heading,
        r'\section[', 'Short title', r']{A longer title}',
        r'\[', 'x = 2', r'\]',
        '% Draft note', r'Reference: \url{https://example.org/report%20files}.',
        r'\begin{verbatim}', 'Literal } marker', r'\end{verbatim}',
        paragraph + trailing_space, '', 'A second paragraph.', r'\clearpage', '',
        r'\smallskip', '',
        'A comment does not break', '% ignore this newline', 'the paragraph.',
        r'\end{document}', '',
    ])
    paper = ok(client.patch(paper_url, json={
        'expected_revision': paper['revision'], 'data': {'source': source},
    }))
    request = {'expected_revision': paper['revision'], 'figure_id': figure['id']}
    copied_asset = root / 'paper' / 'figures' / figure['id'] / 'figure.pdf'

    invalid_anchors = (
        'Measured', heading, 'Short title', 'x = 2', '% Draft note', 'Literal } marker',
        first_line, r'\clearpage', r'\smallskip', 'A comment does not break',
    )
    for anchor in invalid_anchors:
        response = client.post(paper_url + '/figures', json={**request, 'anchor_text': anchor})
        assert response.status_code == 422, (anchor, response.text)
        assert response.json()['detail']['code'] == 'INVALID_FIGURE_ANCHOR'
    current = ok(client.get(paper_url))
    assert current['revision'] == paper['revision']
    assert current['data']['source'] == source
    assert not copied_asset.exists()

    inserted = ok(client.post(paper_url + '/figures', json={**request, 'anchor_text': paragraph}))
    result = inserted['data']['source']
    assert inserted['revision'] == paper['revision'] + 1
    assert result.index(paragraph) < result.index(r'\begin{figure}') < result.index('A second paragraph.')
    assert paragraph + trailing_space + '\n' + r'\label{forestargument:' in result
    assert copied_asset.read_bytes() == original_asset.read_bytes()


def test_paper_figure_requires_prose_anchor(tmp_path):
    env = {
        **os.environ,
        "FOREST_DATA_DIR": str(tmp_path / "data"),
        "FOREST_DATABASE_URL": "sqlite:///" + str(tmp_path / "paper.sqlite"),
        "FOREST_OWNER_TOKEN": "paper-figure-anchor-test-owner",
        "FOREST_MODEL": "",
        "PYTHONPATH": str(ROOT),
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=50,
    )
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from services.api.main import app

    with TestClient(app) as client:
        case_paper_figure_requires_prose_anchor(client)
