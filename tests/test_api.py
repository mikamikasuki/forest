"""Real FastAPI/SQLAlchemy integration in isolated interpreter/database processes.

No production module is imported in the pytest parent: each scenario gets its
own temporary FOREST_DATA_DIR and database before Settings or SQLAlchemy load.
"""
from __future__ import annotations

import io
import json
import lzma
import os
import socket
import struct
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]


def ok(response, status=200):
    assert response.status_code == status, f"{response.status_code}: {response.text[:4000]}"
    return response.json()


def await_intervention(client, identifier):
    import time
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        receipt = ok(client.get('/api/interventions/' + identifier))
        if receipt['status'] == 'applied': return receipt
        time.sleep(.02)
    raise AssertionError(receipt)


def create(client):
    return ok(client.post("/api/projects", json={"name": "Integration research", "goal": "Compare measured results", "budget": {"max_runs": 50, "seconds": 300, "allow_paid": False}}))


def graph(client, project):
    return ok(client.get(f"/api/projects/{project['id']}/graph"))


def command(client, project, operation, targets=None, **params):
    g = graph(client, project)
    return client.post(f"/api/projects/{project['id']}/graph/commands", json={"request_id": str(uuid4()), "expected_revision": g["revision"], "operation": operation, "targets": targets or [], "params": params})


def add_node(client, project, **fields):
    result = ok(command(client, project, "add_node", title="Baseline", type="experiment", **fields))
    return result["graph"]["nodes"][-1]


def case_bootstrap_and_persistence(client, app):
    health = ok(client.get("/api/health"))
    assert health["database"] == "sqlite"
    p = create(client)
    n = add_node(client, p, instructions="Reproduce the baseline")
    assert "forest_owner" in client.cookies
    renamed = ok(client.patch(f"/api/projects/{p['id']}", json={"name": "Revised title", "goal": "New controlled target", "expected_revision": 1}))
    assert renamed["revision"] == 2
    g = graph(client, p)
    assert g["goal"] == "New controlled target"
    assert g["nodes"][0]["instructions"] == "Reproduce the baseline"
    packet = ok(client.get(f"/api/nodes/{n['id']}/context"))
    assert packet["controls"]["goal"] == "New controlled target"
    from services.api.db import Session, Node, Project
    with Session() as session:
        assert session.get(Project, p["id"]).name == "Revised title"
        assert session.get(Node, n["id"]).instructions == "Reproduce the baseline"
    assert ok(client.patch(f"/api/projects/{p['id']}", json={"archived": True}))["archived"]
    assert ok(client.get("/api/projects", params={"archived": True}))[0]["id"] == p["id"]
    ok(client.delete(f"/api/projects/{p['id']}"))
    assert client.get(f"/api/projects/{p['id']}").status_code == 404


def case_library_reindex_replaces_passage_rows(client, app):
    from services.api.common import project_dir
    from services.api.db import Session, SourcePassage
    from sqlalchemy import select
    from research.literature import sources

    project = create(client)
    pdf_path = project_dir(project["id"]) / "library" / "source.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"test PDF placeholder")
    source = ok(client.post("/api/library/import", json={
        "project_id": project["id"],
        "paper": {"title": "Reindexed source", "pdf_path": str(pdf_path),
                  "passages": [{"text": "OLD ABSTRACT SENTINEL", "page": 0}]},
    }))
    endpoint = f"/api/library/{source['id']}"
    assert [row["data"]["text"] for row in ok(client.get(endpoint + "/passages"))] == ["OLD ABSTRACT SENTINEL"]

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(sources, "extract_pdf", lambda _path: [
            {"text": "NEW FULLTEXT SENTINEL", "page": 1},
            {"text": "SECOND FULLTEXT SENTINEL", "page": 2},
        ])
        ok(client.post(endpoint + "/reindex"))
        passages = ok(client.get(endpoint + "/passages"))
        assert [row["data"]["text"] for row in passages] == [
            "NEW FULLTEXT SENTINEL", "SECOND FULLTEXT SENTINEL",
        ]
        with Session() as session:
            rows = list(session.scalars(select(SourcePassage).where(
                SourcePassage.project_id == project["id"])))
            assert [row.data["text"] for row in rows if row.data.get("paper_id") == source["id"]] == [
                "NEW FULLTEXT SENTINEL", "SECOND FULLTEXT SENTINEL",
            ]

        monkeypatch.setattr(sources, "extract_pdf", lambda _path: [])
        ok(client.post(endpoint + "/reindex"))
        assert ok(client.get(endpoint + "/passages")) == []
    finally:
        monkeypatch.undo()


def case_library_stable_identifiers_preserve_distinct_sources(client, app):
    project = create(client)
    title = "Shared editorial title"

    doi_alpha = ok(client.post("/api/library/import", json={
        "project_id": project["id"],
        "paper": {"title": title, "doi": "10.9999/forest94-alpha",
                  "passages": [{"text": "ALPHA PASSAGE", "page": 1}]},
    }))
    doi_beta = ok(client.post("/api/library/import", json={
        "project_id": project["id"],
        "paper": {"title": title, "doi": "10.9999/forest94-beta",
                  "passages": [{"text": "BETA PASSAGE", "page": 2}]},
    }))
    assert doi_alpha["id"] != doi_beta["id"]
    assert [row["data"]["text"] for row in ok(
        client.get(f"/api/library/{doi_alpha['id']}/passages"))] == ["ALPHA PASSAGE"]
    assert [row["data"]["text"] for row in ok(
        client.get(f"/api/library/{doi_beta['id']}/passages"))] == ["BETA PASSAGE"]

    doi_repeat = ok(client.post("/api/library/import", json={
        "project_id": project["id"],
        "paper": {"title": "Updated alpha title", "doi": doi_alpha["data"]["doi"],
                  "passages": [{"text": "REPLACEMENT MUST NOT WIN", "page": 3}]},
    }))
    assert doi_repeat["id"] == doi_alpha["id"]

    arxiv_v1 = ok(client.post("/api/library/import", json={
        "project_id": project["id"],
        "paper": {"title": title, "arxiv_id": "2401.12345v1",
                  "passages": [{"text": "ARXIV V1 PASSAGE", "page": 4}]},
    }))
    arxiv_v2 = ok(client.post("/api/library/import", json={
        "project_id": project["id"],
        "paper": {"title": "Version two title", "arxiv_id": "2401.12345v2",
                  "passages": [{"text": "ARXIV V2 MUST NOT WIN", "page": 5}]},
    }))
    assert arxiv_v2["id"] == arxiv_v1["id"]
    arxiv_distinct = ok(client.post("/api/library/import", json={
        "project_id": project["id"],
        "paper": {"title": title, "arxiv_id": "2401.67890",
                  "passages": [{"text": "DISTINCT ARXIV PASSAGE", "page": 6}]},
    }))
    assert arxiv_distinct["id"] != arxiv_v1["id"]
    assert len(ok(client.get("/api/library", params={"project_id": project["id"]}))) == 4
    assert [row["data"]["text"] for row in ok(
        client.get(f"/api/library/{arxiv_v1['id']}/passages"))] == ["ARXIV V1 PASSAGE"]


def case_library_title_fallback_requires_unidentified_records(client, app):
    project = create(client)
    title = "Title fallback control"

    unidentified = ok(client.post("/api/library/import", json={
        "project_id": project["id"],
        "paper": {"title": title, "passages": [{"text": "TITLE ONLY", "page": 1}]},
    }))
    identified_doi = ok(client.post("/api/library/import", json={
        "project_id": project["id"],
        "paper": {"title": title, "doi": "10.9999/forest94-identified",
                  "passages": [{"text": "DOI SOURCE", "page": 2}]},
    }))
    identified_arxiv = ok(client.post("/api/library/import", json={
        "project_id": project["id"],
        "paper": {"title": title, "arxiv_id": "2401.54321",
                  "passages": [{"text": "ARXIV SOURCE", "page": 3}]},
    }))
    assert len({unidentified["id"], identified_doi["id"], identified_arxiv["id"]}) == 3

    unidentified_repeat = ok(client.post("/api/library/import", json={
        "project_id": project["id"],
        "paper": {"title": "  TITLE FALLBACK CONTROL  ",
                  "passages": [{"text": "REPLACEMENT MUST NOT WIN", "page": 4}]},
    }))
    assert unidentified_repeat["id"] == unidentified["id"]
    assert [row["data"]["text"] for row in ok(
        client.get(f"/api/library/{unidentified['id']}/passages"))] == ["TITLE ONLY"]

    other_project = create(client)
    isolated = ok(client.post("/api/library/import", json={
        "project_id": other_project["id"],
        "paper": {"title": title, "doi": identified_doi["data"]["doi"],
                  "passages": [{"text": "OTHER PROJECT", "page": 5}]},
    }))
    assert isolated["id"] != identified_doi["id"]
    isolated_unidentified = ok(client.post("/api/library/import", json={
        "project_id": other_project["id"],
        "paper": {"title": title, "passages": [{"text": "IDENTIFIED FIRST", "page": 6}]},
    }))
    assert isolated_unidentified["id"] != isolated["id"]
    assert len(ok(client.get("/api/library", params={"project_id": project["id"]}))) == 3
    assert len(ok(client.get("/api/library", params={"project_id": other_project["id"]}))) == 2


def case_graph_receipts_cycles_and_schema(client, app):
    p = create(client)
    path = f"/api/projects/{p['id']}/graph/commands"
    body = {"request_id": "one-logical-add", "expected_revision": 0, "operation": "add_node", "params": {"title": "A", "type": "experiment"}, "run": False}
    first = ok(client.post(path, json=body))
    assert ok(client.post(path, json=body)) == first
    assert len(graph(client, p)["nodes"]) == 1
    bad = {**body, "request_id": "new-stale-request"}
    conflict = client.post(path, json=bad)
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "REVISION_CONFLICT"
    n1 = first["graph"]["nodes"][0]
    n2 = add_node(client, p)
    ok(command(client, p, "add_dependency", source=n1["id"], target=n2["id"]))
    cycle = command(client, p, "add_dependency", source=n2["id"], target=n1["id"])
    assert cycle.status_code == 422 and cycle.json()["detail"]["code"] == "dependency_cycle"
    assert len(graph(client, p)["edges"]) == 1
    assert client.post(path, json={"request_id": "no-revision", "operation": "add_node"}).status_code == 422
    assert client.post(path, json={**body, "unknown": "field"}).status_code == 422
    assert client.post(path, json={**body, "project_id": str(uuid4())}).status_code == 422
    preview = ok(client.post(path.replace("commands", "preview"), json={**body, "request_id": "preview", "expected_revision": graph(client, p)["revision"]}))
    assert preview["changed_nodes"]
    assert len(graph(client, p)["nodes"]) == 2


def case_concurrent_edit_collision(client, app):
    p = create(client)
    n = add_node(client, p)
    def edit(index):
        return client.post(f"/api/projects/{p['id']}/graph/commands", json={"request_id": f"window-{index}", "expected_revision": 1,
                           "operation": "edit_node", "targets": [n["id"]], "params": {"instructions": f"change-{index}"}})
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(edit, range(6)))
    assert sorted(r.status_code for r in responses) == [200, 409, 409, 409, 409, 409], [(r.status_code, r.text) for r in responses]
    winner = next(i for i, r in enumerate(responses) if r.status_code == 200)
    assert graph(client, p)["nodes"][0]["instructions"] == f"change-{winner}"
    assert graph(client, p)["revision"] == 2


def case_fork_merge_undo_and_files(client, app):
    p = create(client)
    n = add_node(client, p)
    main = graph(client, p)["branches"][0]
    path = main["workspace"] + "/model.py"
    ok(client.put(f"/api/projects/{p['id']}/file", json={"path": path, "content": "base\n", "expected_revision": 0}))
    fork_result = ok(command(client, p, "fork_branch", [n["id"]], name="Candidate"))
    await_intervention(client, fork_result['intervention']['id'])
    fork = fork_result["graph"]["branches"][-1]
    fork_path = fork["workspace"] + "/model.py"
    assert ok(client.get(f"/api/projects/{p['id']}/file", params={"path": fork_path}))["content"] == "base\n"
    ok(client.put(f"/api/projects/{p['id']}/file", json={"path": path, "content": "left\n", "expected_revision": 1}))
    ok(client.put(f"/api/projects/{p['id']}/file", json={"path": fork_path, "content": "right\n", "expected_revision": 0}))
    comparison = ok(client.get("/api/branches/compare", params={"left": main["id"], "right": fork["id"]}))
    assert comparison["mode"] == "three_way"
    assert comparison["files"][0]["status"] == "conflict"
    collision = command(client, p, "merge_branches", left=main["id"], right=fork["id"])
    assert collision.status_code == 409
    result = ok(command(client, p, "merge_branches", left=main["id"], right=fork["id"], resolution={"files": {"model.py": {"content": "combined\n"}}}))
    await_intervention(client, result['intervention']['id'])
    merged = result["graph"]["branches"][-1]
    assert ok(client.get(f"/api/projects/{p['id']}/file", params={"path": merged["workspace"] + "/model.py"}))["content"] == "combined\n"
    undone = ok(command(client, p, "undo"))
    assert len(undone["graph"]["branches"]) == 2
    assert "_history" not in undone["graph"]
    redone = ok(command(client, p, "redo"))
    assert len(redone["graph"]["branches"]) == 3
    assert redone["graph"]["branches"][-1]["requires_revalidation"]


def case_file_revision_and_escape(client, app):
    p = create(client)
    endpoint = f"/api/projects/{p['id']}/file"
    assert ok(client.put(endpoint, json={"path": "code/train.py", "content": "print(1)", "expected_revision": 0}))["revision"] == 1
    assert client.put(endpoint, json={"path": "code/train.py", "content": "lost", "expected_revision": 0}).status_code == 409
    assert ok(client.get(endpoint, params={"path": "code/train.py"}))["content"] == "print(1)"
    assert client.put(endpoint, json={"path": "../outside", "content": "escape"}).status_code == 403
    root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / p["id"]
    secret = root.parent / "outside.txt"
    secret.write_text("do not read")
    (root / "symlink.txt").symlink_to(secret)
    assert client.get(endpoint, params={"path": "symlink.txt"}).status_code == 403
    assert client.get(endpoint, params={"path": str(secret)}).status_code == 403
    ok(client.delete(endpoint, params={"path": "code/train.py"}))
    assert client.get(endpoint, params={"path": "code/train.py"}).status_code == 404


def case_delete_recreate_invalidates_stale_file_revisions(client, app):
    project = create(client)
    endpoint = f"/api/projects/{project['id']}/file"

    path = "notes/revision-aba.txt"
    original = ok(client.put(endpoint, json={
        "path": path, "content": "original bytes\n", "expected_revision": 0,
    }))
    assert original["revision"] == 1
    ok(client.delete(endpoint, params={"path": path}))
    stale_after_delete = client.put(endpoint, json={
        "path": path, "content": "stale editor\n",
        "expected_revision": original["revision"],
    })
    assert stale_after_delete.status_code == 409
    assert client.get(endpoint, params={"path": path}).status_code == 404
    workspace = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"]
    (workspace / path).write_text("executor regenerated bytes\n")
    regenerated = ok(client.get(endpoint, params={"path": path}))
    assert regenerated == {
        "path": path,
        "content": "executor regenerated bytes\n",
        "revision": 2,
        "origin": "executor_or_import",
    }
    ok(client.delete(endpoint, params={"path": path}))
    recreated = ok(client.put(endpoint, json={
        "path": path, "content": "new bytes\n",
    }))
    assert recreated["revision"] == 4
    stale = client.put(endpoint, json={
        "path": path, "content": "stale editor\n", "expected_revision": original["revision"],
    })
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "REVISION_CONFLICT"
    assert ok(client.get(endpoint, params={"path": path})) == {
        "path": path, "content": "new bytes\n", "revision": 4, "origin": "user_edited",
    }

    directory = "results"
    tracked_path = f"{directory}/tracked.txt"
    tracked = ok(client.put(endpoint, json={
        "path": tracked_path, "content": "tracked original\n", "expected_revision": 0,
    }))
    untracked_path = f"{directory}/executor-output.txt"
    workspace = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"]
    (workspace / directory / "executor-output.txt").write_text("executor original\n")
    untracked = ok(client.get(endpoint, params={"path": untracked_path}))
    assert untracked["revision"] == 0
    assert untracked["origin"] == "executor_or_import"

    ok(client.delete(endpoint, params={"path": directory}))
    stale_untracked_after_delete = client.put(endpoint, json={
        "path": untracked_path,
        "content": "stale executor editor\n",
        "expected_revision": untracked["revision"],
    })
    assert stale_untracked_after_delete.status_code == 409
    assert client.get(endpoint, params={"path": untracked_path}).status_code == 404
    tracked_new = ok(client.put(endpoint, json={
        "path": tracked_path, "content": "tracked replacement\n",
    }))
    untracked_new = ok(client.put(endpoint, json={
        "path": untracked_path, "content": "executor replacement\n",
    }))
    assert tracked_new["revision"] == 3
    assert untracked_new["revision"] == 2

    stale_tracked = client.put(endpoint, json={
        "path": tracked_path, "content": "stale tracked editor\n",
        "expected_revision": tracked["revision"],
    })
    stale_untracked = client.put(endpoint, json={
        "path": untracked_path, "content": "stale executor editor\n",
        "expected_revision": untracked["revision"],
    })
    assert stale_tracked.status_code == stale_untracked.status_code == 409
    assert ok(client.get(endpoint, params={"path": tracked_path}))["content"] == "tracked replacement\n"
    assert ok(client.get(endpoint, params={"path": untracked_path}))["content"] == "executor replacement\n"


def case_rename_invalidates_stale_untracked_file_path(client, app):
    project = create(client)
    endpoint = f"/api/projects/{project['id']}/file"
    old_path = "outputs/untracked-result.txt"
    new_path = "outputs/renamed-result.txt"
    project_root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"]
    source = project_root / old_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("executor result\n")

    opened = ok(client.get(endpoint, params={"path": old_path}))
    assert opened == {
        "path": old_path,
        "content": "executor result\n",
        "revision": 0,
        "origin": "executor_or_import",
    }
    ok(client.post(
        f"/api/projects/{project['id']}/file/rename",
        json={"path": old_path, "new_path": new_path},
    ))

    stale_save = client.put(endpoint, json={
        "path": old_path,
        "content": "stale editor write\n",
        "expected_revision": opened["revision"],
    })
    assert stale_save.status_code == 409
    assert stale_save.json()["detail"]["code"] == "REVISION_CONFLICT"
    assert client.get(endpoint, params={"path": old_path}).status_code == 404
    assert ok(client.get(endpoint, params={"path": new_path})) == {
        "path": new_path,
        "content": "executor result\n",
        "revision": 0,
        "origin": "executor_or_import",
    }

    source_directory = "sources"
    destination_directory = "archive"
    tracked_source = f"{source_directory}/tracked.txt"
    untracked_source = f"{source_directory}/untracked.txt"
    tracked_destination = f"{destination_directory}/tracked.txt"
    previous_destination = ok(client.put(endpoint, json={
        "path": tracked_destination,
        "content": "old destination bytes\n",
        "expected_revision": 0,
    }))
    ok(client.delete(endpoint, params={"path": tracked_destination}))
    (project_root / destination_directory).rmdir()
    tracked = ok(client.put(endpoint, json={
        "path": tracked_source,
        "content": "source tracked bytes\n",
        "expected_revision": 0,
    }))
    untracked_file = project_root / untracked_source
    untracked_file.parent.mkdir(parents=True, exist_ok=True)
    untracked_file.write_text("source executor bytes\n")
    untracked = ok(client.get(endpoint, params={"path": untracked_source}))
    assert untracked["revision"] == 0

    ok(client.post(
        f"/api/projects/{project['id']}/file/rename",
        json={"path": source_directory, "new_path": destination_directory},
    ))
    assert client.put(endpoint, json={
        "path": tracked_source,
        "content": "stale tracked bytes\n",
        "expected_revision": tracked["revision"],
    }).status_code == 409
    assert client.put(endpoint, json={
        "path": untracked_source,
        "content": "stale executor bytes\n",
        "expected_revision": untracked["revision"],
    }).status_code == 409
    assert client.put(endpoint, json={
        "path": tracked_destination,
        "content": "stale destination bytes\n",
        "expected_revision": previous_destination["revision"],
    }).status_code == 409
    assert client.get(endpoint, params={"path": tracked_source}).status_code == 404
    assert client.get(endpoint, params={"path": untracked_source}).status_code == 404
    assert ok(client.get(endpoint, params={"path": tracked_destination})) == {
        "path": tracked_destination,
        "content": "source tracked bytes\n",
        "revision": 3,
        "origin": "user_edited",
    }
    assert ok(client.get(endpoint, params={"path": f"{destination_directory}/untracked.txt"})) == {
        "path": f"{destination_directory}/untracked.txt",
        "content": "source executor bytes\n",
        "revision": 0,
        "origin": "executor_or_import",
    }


def case_posix_backslash_file_revision_keys(client, app):
    assert os.name == "posix"
    project = create(client)
    endpoint = f"/api/projects/{project['id']}/file"
    project_root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"]

    # On POSIX this is one filename containing a backslash, distinct from the
    # nested path with the same spelling after an incorrect backslash rewrite.
    backslash_path = r"records\entry.txt"
    slash_path = "records/entry.txt"
    untracked = project_root / backslash_path
    untracked.parent.mkdir(parents=True, exist_ok=True)
    untracked.write_text("original backslash-name bytes\n")
    opened = ok(client.get(endpoint, params={"path": backslash_path}))
    assert opened["revision"] == 0

    ok(client.delete(endpoint, params={"path": backslash_path}))
    nested_create = ok(client.put(endpoint, json={
        "path": slash_path,
        "content": "independent nested file\n",
        "expected_revision": 0,
    }))
    assert nested_create["revision"] == 1

    stale = client.put(endpoint, json={
        "path": backslash_path,
        "content": "stale backslash editor\n",
        "expected_revision": opened["revision"],
    })
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "REVISION_CONFLICT"
    assert client.get(endpoint, params={"path": backslash_path}).status_code == 404
    assert ok(client.get(endpoint, params={"path": slash_path}))["content"] == "independent nested file\n"

    rename_source = r"rename\untracked.txt"
    rename_target = r"renamed\untracked.txt"
    source = project_root / rename_source
    source.write_text("rename source bytes\n")
    rename_opened = ok(client.get(endpoint, params={"path": rename_source}))
    assert rename_opened["revision"] == 0
    ok(client.post(
        f"/api/projects/{project['id']}/file/rename",
        json={"path": rename_source, "new_path": rename_target},
    ))
    stale_rename = client.put(endpoint, json={
        "path": rename_source,
        "content": "stale renamed editor\n",
        "expected_revision": rename_opened["revision"],
    })
    assert stale_rename.status_code == 409
    assert stale_rename.json()["detail"]["code"] == "REVISION_CONFLICT"
    assert client.get(endpoint, params={"path": rename_source}).status_code == 404
    assert ok(client.get(endpoint, params={"path": rename_target}))["content"] == "rename source bytes\n"


def case_export_import_reference_roundtrip(client, app):
    p = create(client)
    n = add_node(client, p, config={"kind": "command", "command": ["python", "-c", "print(1)"]})
    run = ok(client.post(f"/api/nodes/{n['id']}/run", json={"request_id": "original-run"}))
    source_times = {
        "created_at": "2024-05-30T09:00:00+00:00",
        "started_at": "2024-05-30T09:01:12.125000+00:00",
        "finished_at": "2024-05-30T09:01:17.750000+00:00",
    }
    from services.api.db import Session, TaskRun
    with Session.begin() as session:
        row = session.get(TaskRun, run["id"])
        row.created_at = source_times["created_at"]
        row.started_at = source_times["started_at"]
        row.finished_at = source_times["finished_at"]
    # An imported queued run must be visibly interrupted, never silently restarted.
    fig = ok(client.post("/api/figures", json={"project_id": p["id"], "title": "Metrics", "data": {"run_ids": [run["id"]], "metric": "accuracy"}}))
    paper = ok(client.get(f"/api/papers/{p['id']}"))
    ok(client.patch(f"/api/papers/{p['id']}", json={"expected_revision": paper["revision"], "data": {"source": paper["data"]["source"], "bindings": [{"figure_id": fig["id"], "run_id": run["id"]}]}}))
    ok(client.put(f"/api/projects/{p['id']}/file", json={"path": run["output_path"] + "/stdout.txt", "content": "actual saved output\n"}))
    archive = client.post(f"/api/projects/{p['id']}/export", json={})
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        archive_files = {item.filename: zipped.read(item.filename) for item in zipped.infolist()}
    manifest = json.loads(archive_files["forest-project.json"])
    manifest["runs"][0]["started_at"] = "not-an-ISO-timestamp"
    archive_files["forest-project.json"] = json.dumps(manifest).encode()
    invalid_archive = io.BytesIO()
    with zipfile.ZipFile(invalid_archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, content in archive_files.items():
            zipped.writestr(name, content)
    invalid = client.post("/api/projects/import", files={"file": ("invalid-time.zip", invalid_archive.getvalue(), "application/zip")})
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "INVALID_ARCHIVE"
    assert len(ok(client.get("/api/projects"))) == 1
    restored = ok(client.post("/api/projects/import", files={"file": ("project.zip", archive.content, "application/zip")}))
    new_graph = graph(client, restored)
    assert restored["id"] != p["id"] and new_graph["nodes"][0]["id"] != n["id"]
    new_runs = ok(client.get(f"/api/projects/{restored['id']}/runs"))
    assert new_runs[0]["status"] == "interrupted"
    assert {key: new_runs[0][key] for key in source_times} == source_times
    assert new_graph["nodes"][0]["execution_status"] == "interrupted"
    new_fig = ok(client.get("/api/figures", params={"project_id": restored["id"]}))[0]
    assert new_fig["data"]["run_ids"] == [new_runs[0]["id"]]
    new_paper = ok(client.get(f"/api/papers/{restored['id']}"))
    assert new_paper["data"]["bindings"] == [{"figure_id": new_fig["id"], "run_id": new_runs[0]["id"]}]
    assert ok(client.get(f"/api/runs/{new_runs[0]['id']}/output"))["text"] == "actual saved output\n"


def case_export_rejects_oversized_files_and_keeps_boundary_roundtrip(client, app):
    p = create(client)
    root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / p["id"]
    (root / "keep.txt").write_text("retained export file\n")

    oversized = root / "research" / "oversized-artifact.bin"
    oversized.parent.mkdir(parents=True)
    with oversized.open("wb") as stream:
        stream.truncate(100 * 1024 * 1024 + 1)

    rejected = client.post(f"/api/projects/{p['id']}/export", json={})
    assert rejected.status_code == 413
    detail = rejected.json()["detail"]
    assert detail["code"] == "EXPORT_FILE_TOO_LARGE"
    assert "research/oversized-artifact.bin" in detail["message"]
    assert "100 MiB" in detail["message"]

    selected = client.post(f"/api/projects/{p['id']}/export", json={"paths": ["keep.txt"]})
    assert selected.status_code == 200, selected.text
    with zipfile.ZipFile(io.BytesIO(selected.content)) as archive:
        assert archive.read("files/keep.txt") == b"retained export file\n"
        assert "files/research/oversized-artifact.bin" not in archive.namelist()

    oversized.unlink()
    boundary = root / "boundary-artifact.bin"
    with boundary.open("wb") as stream:
        stream.truncate(100 * 1024 * 1024)
        stream.seek(0)
        stream.write(b"boundary-start")
        stream.seek(-len(b"boundary-end"), os.SEEK_END)
        stream.write(b"boundary-end")
    exported = client.post(f"/api/projects/{p['id']}/export", json={})
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert archive.getinfo("files/boundary-artifact.bin").file_size == 100 * 1024 * 1024
        boundary_member = archive.open("files/boundary-artifact.bin")
        assert boundary_member.read(len(b"boundary-start")) == b"boundary-start"
        boundary_member.seek(-len(b"boundary-end"), os.SEEK_END)
        assert boundary_member.read() == b"boundary-end"
        assert archive.testzip() is None
    restored = client.post("/api/projects/import", files={
        "file": ("boundary-project.zip", exported.content, "application/zip"),
    })
    assert restored.status_code == 200, restored.text
    restored_root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / restored.json()["id"]
    assert (restored_root / "boundary-artifact.bin").stat().st_size == 100 * 1024 * 1024
    with (restored_root / "boundary-artifact.bin").open("rb") as restored_boundary:
        assert restored_boundary.read(len(b"boundary-start")) == b"boundary-start"
        restored_boundary.seek(-len(b"boundary-end"), os.SEEK_END)
        assert restored_boundary.read() == b"boundary-end"
    assert (restored_root / "keep.txt").read_text() == "retained export file\n"


def case_project_provider_roundtrip_requires_explicit_resolution_when_missing(client, app):
    provider = ok(client.post('/api/providers', json={
        'name': 'Project archive provider', 'kind': 'openai',
        'base_url': 'http://127.0.0.1:1/v1', 'model': 'test-model',
    }))
    source = ok(client.post('/api/projects', json={
        'name': 'Provider-bound project', 'config': {'provider_id': provider['id']},
    }))
    exported = client.post(f"/api/projects/{source['id']}/export", json={})
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        entries = {item.filename: archive.read(item.filename) for item in archive.infolist()}
    manifest = json.loads(entries['forest-project.json'])
    assert manifest['project']['config']['provider_id'] == provider['id']
    restored = ok(client.post('/api/projects/import', files={
        'file': ('provider-bound.zip', exported.content, 'application/zip'),
    }))
    assert restored['config']['provider_id'] == provider['id']

    manifest['project']['config']['provider_id'] = 'unavailable-provider-id'
    entries['forest-project.json'] = json.dumps(manifest).encode()
    unavailable_archive = io.BytesIO()
    with zipfile.ZipFile(unavailable_archive, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, contents in entries.items():
            archive.writestr(name, contents)
    ok(client.patch('/api/settings', json={'default_provider_id': provider['id']}))
    unresolved = ok(client.post('/api/projects/import', files={
        'file': ('unavailable-provider.zip', unavailable_archive.getvalue(), 'application/zip'),
    }))
    assert unresolved['config'].get('provider_id') is None
    assert unresolved['config']['provider_selection_required'] is True
    node = ok(command(client, unresolved, 'add_node', title='Research agent', type='agent'))['graph']['nodes'][-1]
    response = client.post(f"/api/nodes/{node['id']}/run", json={'request_id': str(uuid4())})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'PROVIDER_SELECTION_REQUIRED'

    local_node = ok(command(
        client, unresolved, 'add_node', title='Local experiment', type='experiment',
        config={'command': ['true']},
    ))['graph']['nodes'][-1]
    local_run = client.post(f"/api/nodes/{local_node['id']}/run", json={'request_id': str(uuid4())})
    assert local_run.status_code == 200, local_run.text

    duplicate = ok(client.post(f"/api/projects/{unresolved['id']}/duplicate"))
    assert duplicate['config'].get('provider_id') is None
    assert duplicate['config']['provider_selection_required'] is True
    duplicate_agent = next(
        item for item in graph(client, duplicate)['nodes']
        if item['title'] == 'Research agent'
    )
    duplicate_run = client.post(f"/api/nodes/{duplicate_agent['id']}/run", json={'request_id': str(uuid4())})
    assert duplicate_run.status_code == 422
    assert duplicate_run.json()['detail']['code'] == 'PROVIDER_SELECTION_REQUIRED'

    resolved = ok(client.patch(f"/api/projects/{unresolved['id']}", json={
        'config': {**unresolved['config'], 'provider_id': provider['id']},
    }))
    assert resolved['config']['provider_id'] == provider['id']
    assert 'provider_selection_required' not in resolved['config']


def case_import_running_controller_requires_explicit_start(client, app, mode="assisted"):
    p = create(client)
    ok(client.patch(f"/api/projects/{p['id']}", json={"mode": mode}))
    source_branch_id = graph(client, p)["branches"][0]["id"]
    started = ok(client.post(f"/api/projects/{p['id']}/research/start", json={"autonomous": True, "branch_id": source_branch_id}))
    assert started["status"] == "running" and started["autonomous"] is True
    from services.worker.controller import advance_projects
    advance_projects()
    source_state = ok(client.get(f"/api/projects/{p['id']}/research"))
    assert source_state["controller"]["status"] == "running"
    assert source_state["controller"]["last_run"]
    assert source_state["active_runs"][0]["kind"] == "research_plan"

    archive = client.post(f"/api/projects/{p['id']}/export", json={})
    assert archive.status_code == 200
    imported = ok(client.post("/api/projects/import", files={"file": ("running-project.zip", archive.content, "application/zip")}))
    assert imported["mode"] == mode
    imported_id = imported["id"]
    imported_graph = graph(client, imported)
    imported_branch_id = imported_graph["branches"][0]["id"]
    before_tick = ok(client.get(f"/api/projects/{imported_id}/research"))
    imported_runs = ok(client.get(f"/api/projects/{imported_id}/runs"))
    assert before_tick["controller"]["status"] == "paused"
    assert before_tick["controller"]["autonomous"] is True
    assert imported_branch_id != source_branch_id
    assert before_tick["controller"]["branch_id"] == imported_branch_id
    assert "last_run" not in before_tick["controller"]
    assert before_tick["active_runs"] == []
    assert len(imported_runs) == 1 and imported_runs[0]["status"] == "interrupted"

    advance_projects()

    after_tick = ok(client.get(f"/api/projects/{imported_id}/research"))
    assert after_tick["controller"]["status"] == "paused"
    assert after_tick["active_runs"] == []
    assert ok(client.get(f"/api/projects/{imported_id}/runs")) == imported_runs
    source_after_tick = ok(client.get(f"/api/projects/{p['id']}/research"))
    assert source_after_tick["controller"]["status"] == "running"
    assert source_after_tick["active_runs"] == source_state["active_runs"]


def case_import_auto_mode_preserves_controller_pause_and_cleanup(client, app):
    case_import_running_controller_requires_explicit_start(client, app, mode="auto")


def case_cleanup_global_history_preserves_scoped_run_deletion(client, app):
    import datetime as dt
    from copy import deepcopy
    from services.api.common import project_dir
    from services.api.db import Project, Session, TaskRun

    projects=[create(client),create(client)]
    before={}
    run_ids={}
    outputs={}
    for project in projects:
        add_node(client,project)
        with Session.begin() as session:
            saved=session.get(Project,project['id'])
            saved.graph_meta={**saved.graph_meta,'cleanup_marker':project['id']}
            assert saved.graph_meta['_history']['undo']
            before[project['id']]=deepcopy(saved.graph_meta)
            run=TaskRun(project_id=project['id'],request_id='cleanup-fixture',kind='command',
                        status='completed',finished_at=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=90)).isoformat())
            session.add(run); session.flush()
            run.output_path=f'runs/{run.id}'
            run_ids[project['id']]=run.id
            output=project_dir(project['id'])/run.output_path
            output.mkdir(parents=True)
            (output/'retained.txt').write_text('Synthetic cleanup fixture')
            outputs[project['id']]=output

    selected,other=projects
    assert ok(client.post('/api/settings/cleanup',json={
        'days':30,'project_id':selected['id'],'clear_edit_history':False}))=={'deleted_runs':1}
    with Session() as session:
        assert session.get(TaskRun,run_ids[selected['id']]) is None
        assert session.get(TaskRun,run_ids[other['id']]) is not None
        for project in projects:
            assert session.get(Project,project['id']).graph_meta==before[project['id']]
    assert not outputs[selected['id']].exists() and outputs[other['id']].is_dir()

    with Session.begin() as session:
        run=TaskRun(project_id=selected['id'],request_id='cleanup-second-fixture',kind='command',
                    status='completed',finished_at=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=90)).isoformat())
        session.add(run); session.flush()
        run.output_path=f'runs/{run.id}'
        selected_output=project_dir(selected['id'])/run.output_path
        selected_output.mkdir(parents=True)
        (selected_output/'remove.txt').write_text('Synthetic cleanup fixture')
    assert ok(client.post('/api/settings/cleanup',json={
        'days':30,'project_id':selected['id'],'clear_edit_history':True}))=={'deleted_runs':1}
    with Session() as session:
        assert session.get(TaskRun,run_ids[other['id']]) is not None
        for project in projects:
            assert session.get(Project,project['id']).graph_meta=={
                key:value for key,value in before[project['id']].items() if key!='_history'}
    assert not selected_output.exists()
    assert (outputs[other['id']]/'retained.txt').read_text()=='Synthetic cleanup fixture'
    for project in projects:
        rejected=command(client,project,'undo')
        assert rejected.status_code==422,rejected.text
        assert rejected.json()['detail']['code']=='history_empty'
        assert len(graph(client,project)['nodes'])==1
    assert ok(client.post('/api/settings/cleanup',json={'days':30}))=={'deleted_runs':1}
    assert not outputs[other['id']].exists()


def case_export_does_not_follow_config_symlink(client, app):
    p = create(client)
    root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / p["id"]
    private = root.parent / "private.json"
    private.write_text(json.dumps({"private_marker": "OUTSIDE_PROJECT_SECRET"}))
    (root / "config.json").symlink_to(private)
    response = client.post(f"/api/projects/{p['id']}/export", json={})
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert not any(b"OUTSIDE_PROJECT_SECRET" in archive.read(name) for name in archive.namelist())


def case_unsafe_and_invalid_archive_rejected(client, app):
    p = create(client)
    content = client.post(f"/api/projects/{p['id']}/export", json={}).content
    source = zipfile.ZipFile(io.BytesIO(content))
    manifest = json.loads(source.read("forest-project.json"))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("forest-project.json", json.dumps(manifest))
        archive.writestr("files/../../outside", "escape")
    assert client.post("/api/projects/import", files={"file": ("unsafe.zip", out.getvalue())}).status_code == 400
    manifest["graph"]["edges"] = [{"id": str(uuid4()), "source": "deleted", "target": "also-deleted", "relation": "depends_on"}]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("forest-project.json", json.dumps(manifest))
    assert client.post("/api/projects/import", files={"file": ("invalid.zip", out.getvalue())}).status_code == 422
    assert len(ok(client.get("/api/projects"))) == 1


def case_late_corrupt_archive_returns_invalid_archive_without_orphan_workspace(client, app):
    source = create(client)
    contents = {
        "code/00-before-corruption.bin": "valid member written before failure",
        "code/zz-corrupt.bin": "late CRC member " + ("0123456789abcdef" * 16),
    }
    for path, value in contents.items():
        response = client.put(
            f"/api/projects/{source['id']}/file",
            json={"path": path, "content": value, "expected_revision": 0},
        )
        assert response.status_code == 200, response.text

    exported = client.post(f"/api/projects/{source['id']}/export", json={})
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        entries = [(item.filename, archive.read(item.filename)) for item in archive.infolist()]
    corrupt_name = "files/code/zz-corrupt.bin"
    ordered_entries = [(name, value) for name, value in entries if name != corrupt_name]
    ordered_entries.append((corrupt_name, dict(entries)[corrupt_name]))
    malformed = io.BytesIO()
    with zipfile.ZipFile(malformed, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, value in ordered_entries:
            archive.writestr(name, value)

    damaged = bytearray(malformed.getvalue())
    with zipfile.ZipFile(io.BytesIO(damaged)) as archive:
        info = archive.getinfo(corrupt_name)
        assert json.loads(archive.read("forest-project.json"))["format"] == "forest-project-v1"
        local_header = struct.unpack_from("<IHHHHHIIIHH", damaged, info.header_offset)
        assert local_header[0] == 0x04034B50
        data_offset = info.header_offset + 30 + local_header[-2] + local_header[-1]
        damaged[data_offset] ^= 1
    with zipfile.ZipFile(io.BytesIO(damaged)) as archive:
        with pytest.raises(zipfile.BadZipFile, match="Bad CRC-32"):
            archive.read(corrupt_name)

    projects_root = Path(os.environ["FOREST_DATA_DIR"]) / "projects"
    existing_directories = {path.name for path in projects_root.iterdir()}
    rejected = client.post(
        "/api/projects/import",
        files={"file": ("late-corrupt.zip", bytes(damaged), "application/zip")},
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"]["code"] == "INVALID_ARCHIVE"
    assert {path.name for path in projects_root.iterdir()} == existing_directories
    assert len(ok(client.get("/api/projects"))) == 1
    assert ok(client.get(f"/api/projects/{source['id']}"))["id"] == source["id"]

    imported = ok(client.post(
        "/api/projects/import",
        files={"file": ("valid-control.zip", exported.content, "application/zip")},
    ))
    for path, value in contents.items():
        readback = ok(client.get(
            f"/api/projects/{imported['id']}/file", params={"path": path}
        ))
        assert readback["content"] == value


def case_bzip2_and_lzma_corruption_return_invalid_archive_without_orphan_workspace(client, app):
    source = create(client)
    contents = {
        "code/00-before-corruption.bin": "valid member written before failure",
        "code/zz-corrupt.bin": "compressed archive probe " * 1000,
    }
    for path, value in contents.items():
        response = client.put(
            f"/api/projects/{source['id']}/file",
            json={"path": path, "content": value, "expected_revision": 0},
        )
        assert response.status_code == 200, response.text

    exported = client.post(f"/api/projects/{source['id']}/export", json={})
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        entries = [(item.filename, archive.read(item.filename)) for item in archive.infolist()]

    corrupt_name = "files/code/zz-corrupt.bin"
    for method, corrupt_offset in ((zipfile.ZIP_BZIP2, 0), (zipfile.ZIP_LZMA, "middle")):
        ordered_entries = [(name, value) for name, value in entries if name != corrupt_name]
        ordered_entries.append((corrupt_name, dict(entries)[corrupt_name]))
        malformed = io.BytesIO()
        with zipfile.ZipFile(malformed, "w", compression=method) as archive:
            for name, value in ordered_entries:
                archive.writestr(name, value)

        damaged = bytearray(malformed.getvalue())
        with zipfile.ZipFile(io.BytesIO(damaged)) as archive:
            info = archive.getinfo(corrupt_name)
            header = struct.unpack_from("<IHHHHHIIIHH", damaged, info.header_offset)
            assert header[0] == 0x04034B50
            data_offset = info.header_offset + 30 + header[-2] + header[-1]
            offset = corrupt_offset if corrupt_offset != "middle" else max(1, info.compress_size // 2)
            damaged[data_offset + offset] ^= 0x80

        with zipfile.ZipFile(io.BytesIO(damaged)) as archive:
            with pytest.raises(OSError if method == zipfile.ZIP_BZIP2 else lzma.LZMAError):
                archive.read(corrupt_name)

        projects_root = Path(os.environ["FOREST_DATA_DIR"]) / "projects"
        existing_directories = {path.name for path in projects_root.iterdir()}
        rejected = client.post(
            "/api/projects/import",
            files={"file": ("late-corrupt-compressed.zip", bytes(damaged), "application/zip")},
        )
        assert rejected.status_code == 400
        assert rejected.json()["detail"]["code"] == "INVALID_ARCHIVE"
        assert {path.name for path in projects_root.iterdir()} == existing_directories
        assert len(ok(client.get("/api/projects"))) == 1
        assert ok(client.get(f"/api/projects/{source['id']}"))["id"] == source["id"]

    imported = ok(client.post(
        "/api/projects/import",
        files={"file": ("valid-control.zip", exported.content, "application/zip")},
    ))
    for path, value in contents.items():
        readback = ok(client.get(
            f"/api/projects/{imported['id']}/file", params={"path": path}
        ))
        assert readback["content"] == value


def case_visitors_shares_and_origin_denial(client, app):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    p = create(client)
    n = add_node(client, p, instructions="private instructions")
    share = ok(client.post(f"/api/projects/{p['id']}/share", json={"node_ids": [n["id"]]}))
    with TestClient(app, base_url="http://visitor.example", client=("198.51.100.12", 5000)) as visitor:
        assert visitor.get("/api/projects").status_code == 401
        assert visitor.post(f"/api/nodes/{n['id']}/run", json={}).status_code == 401
        assert visitor.get("/api/providers").status_code == 401
        shared = ok(visitor.get("/api/shares/" + share["token"]))
        assert shared["read_only"] and len(shared["nodes"]) == 1
        assert "private instructions" not in json.dumps(shared)
        with pytest.raises(WebSocketDisconnect) as blocked:
            with visitor.websocket_connect(f"/ws/projects/{p['id']}/terminal"):
                pytest.fail("Visitor terminal must not connect")
        assert blocked.value.code == 1008
    assert client.post("/api/projects", json={"name": "CSRF"}, headers={"origin": "https://evil.example"}).status_code == 403
    ok(client.delete("/api/shares/" + share["token"]))
    assert client.get("/api/shares/" + share["token"]).status_code == 404


def case_real_provider_failure_and_local_host(client, app):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    provider = ok(client.post("/api/providers", json={"name": "Intentionally unavailable endpoint", "kind": "openai", "base_url": f"http://127.0.0.1:{port}/v1", "model": "test-no-model", "api_key": "TEST_ONLY_SECRET", "config": {"timeout": 1}}))
    configured=ok(client.post('/api/projects',json={'name':'Explicit provider selection','config':{'provider_id':provider['id']}}))
    assert configured['config']['provider_id']==provider['id']
    assert "api_key" not in provider and provider["has_key"]
    response = client.post(f"/api/providers/{provider['id']}/test")
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "PROVIDER_CONNECTION_FAILED"
    listed = ok(client.get("/api/providers"))
    assert listed[0]["status"] == "failed" and "TEST_ONLY_SECRET" not in json.dumps(listed)
    hosts = ok(client.get("/api/hosts"))
    assert ok(client.post(f"/api/hosts/{hosts[0]['id']}/test"))["connected"] is True
    system = ok(client.get("/api/system"))
    assert system["model_connected"] is False and isinstance(system["gpu"], list)
    secrets_path = Path(os.environ["FOREST_DATA_DIR"]) / "secrets.json"
    assert secrets_path.stat().st_mode & 0o077 == 0


def case_run_receipt_and_immutable_start_config(client, app):
    p = create(client)
    n = add_node(client, p, instructions="Original instructions", config={"kind": "command", "command": ["python", "-c", "print(1)"]})
    body = {"request_id": "same-launch", "scope": "single"}
    first = ok(client.post(f"/api/nodes/{n['id']}/run", json=body))
    second = ok(client.post(f"/api/nodes/{n['id']}/run", json=body))
    assert first["id"] == second["id"]
    ok(command(client, p, "edit_node", [n["id"]], instructions="Revised instructions"))
    actual = ok(client.get(f"/api/runs/{first['id']}"))
    assert actual["config"]["instructions"] == "Original instructions"
    assert actual["node_revision"] == 0
    assert ok(client.get(f"/api/nodes/{n['id']}"))["instructions"] == "Revised instructions"
    assert len(ok(client.get(f"/api/projects/{p['id']}/runs"))) == 1


def case_graph_apply_and_run_preserves_dag_dependencies(client, app):
    p = create(client)
    config = {"kind": "command", "command": ["python", "-c", "print(1)"]}
    first = add_node(client, p, config=config)
    second = add_node(client, p, config=config)
    ok(command(client, p, "add_dependency", source=first["id"], target=second["id"]))
    response = ok(client.post(f"/api/projects/{p['id']}/graph/commands", json={
        "request_id": "edit-and-run-dag", "expected_revision": graph(client, p)["revision"], "operation": "edit_node",
        "targets": [first["id"]], "params": {"instructions": "New controlled configuration"}, "run": True}))
    assert len(response["run_ids"]) == 2
    runs = {r["node_id"]: r for r in ok(client.get(f"/api/projects/{p['id']}/runs"))}
    assert runs[second["id"]]["dependencies"] == [runs[first["id"]]["id"]]
    assert runs[first["id"]]["node_revision"] == 1


def case_pruned_branch_and_missing_inputs_block_scheduling(client, app):
    p = create(client)
    n = add_node(client, p, inputs=[{"path": "unavailable.csv"}])
    denied = client.post(f"/api/nodes/{n['id']}/run", json={})
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "INPUT_UNAVAILABLE"
    ok(command(client, p, "edit_node", [n["id"]], inputs=[]))
    ok(command(client, p, "prune_branch", branch_id=n["branch_id"]))
    denied = client.post(f"/api/nodes/{n['id']}/run", json={})
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "BRANCH_INACTIVE"
    assert ok(client.get(f"/api/projects/{p['id']}/runs")) == []


def case_concurrent_launch_is_exactly_once(client, app):
    p = create(client)
    n = add_node(client, p, config={"kind": "command", "command": ["python", "-c", "print(1)"]})
    def launch(_):
        return client.post(f"/api/nodes/{n['id']}/run", json={"request_id": "same-concurrent-launch"})
    with ThreadPoolExecutor(max_workers=5) as pool:
        runs = [ok(response) for response in pool.map(launch, range(5))]
    assert len({run["id"] for run in runs}) == 1
    assert len(ok(client.get(f"/api/projects/{p['id']}/runs"))) == 1


def case_duplicate_retains_bindings_without_following_links(client, app):
    p = create(client)
    n = add_node(client, p, config={"kind": "command", "command": ["python", "-c", "print(1)"]})
    run = ok(client.post(f"/api/nodes/{n['id']}/run", json={"request_id": "to-copy"}))
    source_times = {
        "created_at": "2024-05-30T09:00:00+00:00",
        "started_at": "2024-05-30T09:01:12.125000+00:00",
        "finished_at": "2024-05-30T09:01:17.750000+00:00",
    }
    from services.api.db import Session, TaskRun
    with Session.begin() as session:
        source_run = session.get(TaskRun, run["id"])
        for field, value in source_times.items():
            setattr(source_run, field, value)
    fig = ok(client.post("/api/figures", json={"project_id": p["id"], "title": "Measured", "data": {"run_ids": [run["id"]]}}))
    root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / p["id"]
    private = root.parent / "outside.txt"
    private.write_text("outside project")
    (root / "secret.txt").symlink_to(private)
    copied = ok(client.post(f"/api/projects/{p['id']}/duplicate"))
    copied_run = ok(client.get(f"/api/projects/{copied['id']}/runs"))[0]
    copied_fig = ok(client.get("/api/figures", params={"project_id": copied["id"]}))[0]
    assert copied_fig["id"] != fig["id"] and copied_fig["data"]["run_ids"] == [copied_run["id"]]
    assert copied_run["status"] == "interrupted"
    assert {field: copied_run[field] for field in source_times} == source_times
    assert client.get(f"/api/projects/{copied['id']}/file", params={"path": "secret.txt"}).status_code == 404


def case_project_copy_preserves_run_mode(client, app):
    for mode in ("auto", "assisted", "manual"):
        source = ok(client.post("/api/projects", json={
            "name": f"{mode} mode copy",
            "goal": "Preserve the selected run mode",
            "mode": mode,
            "budget": {"allow_paid": False},
        }))

        duplicate = ok(client.post(f"/api/projects/{source['id']}/duplicate", json={}))
        assert duplicate["mode"] == mode
        assert ok(client.get(f"/api/projects/{duplicate['id']}"))["mode"] == mode

        archive = client.post(f"/api/projects/{source['id']}/export", json={})
        assert archive.status_code == 200
        with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
            manifest = json.loads(zipped.read("forest-project.json"))
        assert manifest["project"]["mode"] == mode

        imported = client.post(
            "/api/projects/import",
            files={"file": ("forest-project.zip", archive.content, "application/zip")},
        )
        imported_project = ok(imported)
        assert imported_project["mode"] == mode
        assert ok(client.get(f"/api/projects/{imported_project['id']}"))["mode"] == mode

    legacy_source = ok(client.post("/api/projects", json={
        "name": "legacy archive mode",
        "goal": "Keep old archives compatible",
        "mode": "manual",
        "budget": {"allow_paid": False},
    }))
    archive = client.post(f"/api/projects/{legacy_source['id']}/export", json={})
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        contents = {item.filename: zipped.read(item.filename) for item in zipped.infolist()}
    manifest = json.loads(contents["forest-project.json"])
    manifest["project"].pop("mode")
    contents["forest-project.json"] = json.dumps(manifest).encode()
    legacy_archive = io.BytesIO()
    with zipfile.ZipFile(legacy_archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name, content in contents.items():
            zipped.writestr(name, content)
    imported_legacy = ok(client.post(
        "/api/projects/import",
        files={"file": ("legacy-project.zip", legacy_archive.getvalue(), "application/zip")},
    ))
    assert imported_legacy["mode"] == "assisted"


def case_duplicate_copy_failure_removes_partial_workspace(client, app):
    from unittest.mock import patch
    import services.api.main as api_main

    source = create(client)
    source_path = "code/duplicate-probe.txt"
    source_bytes = "copied payload from source"
    uploaded = client.put(
        f"/api/projects/{source['id']}/file",
        json={"path": source_path, "content": source_bytes, "expected_revision": 0},
    )
    assert uploaded.status_code == 200, uploaded.text

    projects_root = Path(os.environ["FOREST_DATA_DIR"]) / "projects"
    before_directories = {path.name for path in projects_root.iterdir() if path.is_dir()}
    real_copy2 = api_main.shutil.copy2
    copied_destinations = []

    def copy_then_fail(src, dst, *args, **kwargs):
        result = real_copy2(src, dst, *args, **kwargs)
        copied_destinations.append(str(dst))
        assert Path(dst).read_text() == source_bytes
        raise OSError("injected copy failure after a real file copy")

    with patch.object(api_main.shutil, "copy2", side_effect=copy_then_fail):
        with pytest.raises(OSError, match="injected copy failure after a real file copy"):
            client.post(f"/api/projects/{source['id']}/duplicate")

    assert len(copied_destinations) == 1
    assert {path.name for path in projects_root.iterdir() if path.is_dir()} == before_directories
    assert len(ok(client.get("/api/projects"))) == 1
    assert ok(client.get(f"/api/projects/{source['id']}/file", params={"path": source_path}))["content"] == source_bytes

    duplicate = ok(client.post(f"/api/projects/{source['id']}/duplicate"))
    copied = ok(client.get(f"/api/projects/{duplicate['id']}/file", params={"path": source_path}))
    assert copied["content"] == source_bytes
    assert len(ok(client.get("/api/projects"))) == 2


def case_invalid_import_modes_rejected_before_project_creation(client, app):
    source = create(client)
    exported = client.post(f"/api/projects/{source['id']}/export", json={})
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        entries = {item.filename: archive.read(item.filename) for item in archive.infolist()}

    projects_root = Path(os.environ["FOREST_DATA_DIR"]) / "projects"
    existing_projects = {path.name for path in projects_root.iterdir()}
    for mode in ("operator", None, "x" * 21):
        manifest = json.loads(entries["forest-project.json"])
        manifest["project"]["mode"] = mode
        malformed = dict(entries)
        malformed["forest-project.json"] = json.dumps(manifest).encode()
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, contents in malformed.items():
                archive.writestr(name, contents)

        response = client.post(
            "/api/projects/import",
            files={"file": ("invalid-mode.zip", payload.getvalue(), "application/zip")},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "INVALID_ARCHIVE"
        assert {path.name for path in projects_root.iterdir()} == existing_projects
        assert len(ok(client.get("/api/projects"))) == 1


def case_project_updates_validate_run_modes(client, app):
    project = create(client)
    for mode in ("auto", "assisted", "manual"):
        updated = ok(client.patch(f"/api/projects/{project['id']}", json={"mode": mode}))
        assert updated["mode"] == mode
        project = ok(client.get(f"/api/projects/{project['id']}"))
        assert project["mode"] == mode

    for mode in ("operator", None, 123, "x" * 21):
        before = ok(client.get(f"/api/projects/{project['id']}"))
        response = client.patch(
            f"/api/projects/{project['id']}",
            json={"mode": mode, "expected_revision": before["revision"]},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "INVALID_MODE"
        after = ok(client.get(f"/api/projects/{project['id']}"))
        assert after["mode"] == before["mode"]
        assert after["revision"] == before["revision"]


def case_actual_terminal(client, app):
    p = create(client)
    with client.websocket_connect(f"/ws/projects/{p['id']}/terminal") as terminal:
        terminal.send_text("printf '__FOREST_%s__\\n' EXECUTED\n")
        text = ""
        for _ in range(30):
            text += terminal.receive_text()
            if "__FOREST_EXECUTED__" in text:
                break
        assert "__FOREST_EXECUTED__" in text
        terminal.send_text("FOREST_RECONNECT_VALUE=7391\n")
        terminal.send_text("printf '__STORED_%s__\\n' $FOREST_RECONNECT_VALUE\n")
        for _ in range(30):
            if "__STORED_7391__" in terminal.receive_text(): break
        else: raise AssertionError('Shell variable was not set')
    with client.websocket_connect(f"/ws/projects/{p['id']}/terminal") as terminal:
        terminal.send_text("printf '__RECONNECTED_%s__\\n' $FOREST_RECONNECT_VALUE\n")
        text=''
        for _ in range(30):
            text+=terminal.receive_text()
            if '__RECONNECTED_7391__' in text: break
        assert '__RECONNECTED_7391__' in text
        terminal.send_text('{"action":"close"}')


def case_terminal_run_controls_do_not_signal_retained_processes(client, app):
    import signal
    import time
    import psutil
    from research.agents.processes import ManagedProcesses
    from services.api.db import Session, TaskRun

    p = create(client)
    n = add_node(client, p, config={"kind": "command", "command": [sys.executable, "-c", "pass"]})
    run = ok(client.post(f"/api/nodes/{n['id']}/run", json={"request_id": "retained-process-controls"}))
    output = Path(os.environ['FOREST_DATA_DIR']) / 'projects' / p['id'] / run['output_path']
    manager = ManagedProcesses(output / 'workspace')
    process = manager.start([sys.executable, '-c', 'import time; time.sleep(30)'])
    pid = process['pid']
    try:
        # A terminal database row can still have retained process receipts.
        with Session.begin() as session:
            row = session.get(TaskRun, run['id'])
            row.status = 'completed'
            row.pid = pid
            row.process_created = process['process_created']
        for action in ('pause', 'resume'):
            if action == 'resume':
                manager.signal_all(signal.SIGSTOP)
                deadline = time.monotonic() + 3
                while psutil.Process(pid).status() != psutil.STATUS_STOPPED and time.monotonic() < deadline:
                    time.sleep(.01)
                assert psutil.Process(pid).status() == psutil.STATUS_STOPPED
            response = client.post(f"/api/runs/{run['id']}/{action}", json={})
            assert response.status_code == 409
            assert response.json()['detail']['code'] == 'INVALID_RUN_STATE'
            time.sleep(.05)
            assert (psutil.Process(pid).status() == psutil.STATUS_STOPPED) == (action == 'resume')
            assert ok(client.get(f"/api/runs/{run['id']}"))['status'] == 'completed'

        with Session.begin() as session:
            row = session.get(TaskRun, run['id'])
            row.config = {**row.config, 'execution_backend': 'container'}
        for action in ('pause', 'resume'):
            response = client.post(f"/api/runs/{run['id']}/{action}", json={})
            assert response.status_code == 409
            assert response.json()['detail']['code'] == 'INVALID_RUN_STATE'
        assert not (output / '.forest-container-processes').exists()
        retried = ok(client.post(f"/api/runs/{run['id']}/retry", json={'request_id': 'explicit-retry'}))
        assert retried['id'] != run['id'] and retried['status'] == 'queued'
    finally:
        manager.cancel_all()
        deadline = time.monotonic() + 3
        while manager.inspect(process['process_id'])['status'] not in ('completed', 'failed', 'cancelled') and time.monotonic() < deadline:
            time.sleep(.02)


def case_provider_health_does_not_reuse_old_chat_success(client, app):
    from services.api.db import Session, Provider
    # No server listens on this reserved, freshly closed loopback socket.
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0)); port=listener.getsockname()[1]
    with Session.begin() as session:
        session.add(Provider(name='Offline after chat',kind='ollama',base_url=f'http://127.0.0.1:{port}',model='missing-model',status='connected'))
    report=ok(client.get('/api/system'))
    assert report['model_connected'] is False
    assert report['provider_status'][0]['last_chat_test']=='connected'
    assert report['provider_status'][0]['available'] is False


def case_project_time_budget_reserves_concurrent_runs(client, app):
    from services.api.db import Session, TaskRun

    project = ok(client.post('/api/projects', json={
        'name': 'Concurrent project time reservation',
        'goal': 'Ensure queued Workspace runs share the remaining project time.',
        'budget': {'max_runs': 20, 'seconds': 10, 'allow_paid': False},
    }))
    nodes = [
        add_node(client, project,
                 instructions=f'Concurrent budget node {index}',
                 config={'kind': 'command', 'command': [sys.executable, '-c', 'pass'], 'timeout': 8})
        for index in range(2)
    ]
    request_ids = [f'budget-reservation-{index}' for index in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            lambda pair: client.post(f"/api/nodes/{pair[0]['id']}/run", json={'request_id': pair[1]}),
            zip(nodes, request_ids),
        ))
    runs = [ok(response) for response in responses]
    assert sorted(run['config']['timeout'] for run in runs) == [2.0, 8.0]
    assert sorted(run['resource']['time_budget']['effective_total_timeout_seconds'] for run in runs) == [2.0, 8.0]
    assert all(run['resource']['time_budget']['project_budget_seconds_at_enqueue'] == 10 for run in runs)

    duplicate = ok(client.post(f"/api/nodes/{nodes[0]['id']}/run", json={'request_id': request_ids[0]}))
    assert duplicate['id'] == runs[0]['id']
    exhausted = client.post(f"/api/nodes/{nodes[0]['id']}/run", json={'request_id': 'budget-reservation-no-capacity'})
    assert exhausted.status_code == 409
    assert exhausted.json()['detail']['code'] == 'TIME_BUDGET_EXHAUSTED'

    # Once a reserved run ends early, its unused allocation is available again.
    longest = next(run for run in runs if run['config']['timeout'] == 8.0)
    with Session.begin() as session:
        row = session.get(TaskRun, longest['id'])
        row.status = 'completed'
        row.resource = {**row.resource, 'elapsed_seconds': 1.0}
    released = ok(client.post(f"/api/nodes/{nodes[0]['id']}/run", json={'request_id': 'budget-reservation-released-cap'}))
    assert released['config']['timeout'] == 7.0
    assert released['resource']['time_budget']['effective_total_timeout_seconds'] == 7.0


def case_selected_dependency_runs_defer_time_reservation(client, app):
    project = ok(client.post('/api/projects', json={
        'name': 'Deferred project time reservation',
        'goal': 'Reserve compute time when a selected dependency becomes runnable.',
        'budget': {'max_runs': 20, 'seconds': 10, 'allow_paid': False},
    }))
    config = {'kind': 'command', 'command': [sys.executable, '-c', 'pass'], 'timeout': 8}
    producer = add_node(client, project, instructions='Producer', config=config)
    consumer = add_node(client, project, instructions='Consumer', config=config)
    ok(command(client, project, 'add_dependency', source=producer['id'], target=consumer['id']))

    queued = ok(client.post(f"/api/nodes/{consumer['id']}/run", json={
        'request_id': 'deferred-budget-chain', 'scope': 'ancestors',
    }))
    runs = {run['node_id']: run for run in queued['runs']}
    first = runs[producer['id']]
    second = runs[consumer['id']]
    assert first['config']['timeout'] == 8.0
    assert first['resource']['time_budget']['reservation_state'] == 'held'
    assert second['config']['timeout'] == 8.0
    assert second['resource']['time_budget']['reservation_state'] == 'deferred'
    assert second['resource']['time_budget']['effective_total_timeout_seconds'] is None


def case_verifier_linked_to_active_producer_defers_when_budget_is_reserved(client, app):
    project = ok(client.post('/api/projects', json={
        'name': 'Verification dependency waits for reserved project time',
        'goal': 'Keep an automatically linked verifier queued until its producer completes.',
        'budget': {'max_runs': 20, 'seconds': 8, 'allow_paid': False},
    }))
    producer = ok(command(client, project, 'add_node', title='Producer', type='experiment', config={
        'kind': 'command', 'command': [sys.executable, '-c', 'pass'], 'timeout': 8,
    }))['graph']['nodes'][-1]
    verifier = ok(command(client, project, 'add_node', title='Check producer', type='verification', config={
        'kind': 'verification',
        'verification': {
            'producer_node_id': producer['id'],
            'checks': [{
                'id': 'rows', 'kind': 'csv_integrity', 'source': 'observations.csv',
                'required_columns': ['unit', 'value'], 'unique_by': ['unit'],
                'numeric_columns': ['value'],
            }],
        },
    }))['graph']['nodes'][-1]

    source = ok(client.post(f"/api/nodes/{producer['id']}/run", json={
        'request_id': 'verification-budget-producer',
    }))
    assert source['resource']['time_budget']['reservation_state'] == 'held'
    assert source['resource']['time_budget']['effective_total_timeout_seconds'] == 8

    dependent = ok(client.post('/api/verification/run', json={
        'project_id': project['id'], 'node_id': verifier['id'],
        'request_id': 'verification-budget-dependent',
    }))
    assert source['id'] in dependent['dependencies']
    assert dependent['status'] == 'queued'
    assert dependent['resource']['time_budget']['reservation_state'] == 'deferred'
    assert dependent['resource']['time_budget']['effective_total_timeout_seconds'] is None


def case_paused_container_resume_persists_elapsed_before_reconnect(client, app):
    import time
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch
    from services.api.db import Session, TaskRun

    project = create(client)
    ok(client.patch(f"/api/projects/{project['id']}", json={
        'budget': {'max_runs': 20, 'seconds': 3, 'allow_paid': False},
    }))
    node = add_node(client, project, config={
        'kind': 'command', 'command': [sys.executable, '-c', 'pass'], 'timeout': 10,
        'execution_backend': 'container',
    })
    run = ok(client.post(f"/api/nodes/{node['id']}/run", json={
        'request_id': 'container-pause-resume-accounting',
    }))
    with Session.begin() as session:
        row = session.get(TaskRun, run['id'])
        row.status = 'running'
        row.pid = 2**30  # The host executor is absent; the container adapter owns pause/resume.
        row.process_created = 1.0
        row.started_at = (datetime.now(timezone.utc) - timedelta(seconds=0.3)).isoformat()
        row.resource = {**row.resource, 'elapsed_seconds': 0.2, 'elapsed_before_attempt': 0.0}

    actions = []

    def container_control(config, output, action):
        actions.append(action)
        return {'status': 'paused' if action == 'pause' else 'running'}

    with patch('runners.container.control_container', side_effect=container_control):
        paused = ok(client.post(f"/api/runs/{run['id']}/pause", json={}))
        await_intervention(client, paused['intervention']['id'])
        paused = ok(client.get(f"/api/runs/{run['id']}"))
        assert paused['status'] == 'paused' and paused['pid'] is None
        assert paused['resource']['paused_live_attempt'] is True
        time.sleep(0.15)
        resumed = ok(client.post(f"/api/runs/{run['id']}/resume", json={}))
        await_intervention(client, resumed['intervention']['id'])
        resumed = ok(client.get(f"/api/runs/{run['id']}"))

    assert resumed['status'] == 'queued'
    assert resumed['resource']['elapsed_seconds'] >= 0.4
    assert 'paused_live_attempt' not in resumed['resource']
    assert resumed['resource']['container_reconnect_pending_dispatch'] is True
    # The external container stays paused until worker budget admission.
    assert actions == ['pause']
    time.sleep(0.15)
    from services.worker.scheduler import _elapsed_seconds_at
    with Session() as session:
        waiting_for_worker = session.get(TaskRun, run['id'])
        effective_elapsed = _elapsed_seconds_at(waiting_for_worker)
    assert effective_elapsed >= resumed['resource']['elapsed_seconds'] + 0.1


def case_paused_waiting_child_resume_defers_continue_until_worker_dispatch(client, app):
    import signal
    import time
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch
    from services.api.db import Session, TaskRun
    from services.worker.scheduler import _elapsed_seconds_at

    project = create(client)
    ok(client.patch(f"/api/projects/{project['id']}", json={
        'budget': {'max_runs': 20, 'seconds': 8, 'allow_paid': False},
    }))
    node = add_node(client, project, config={
        'kind': 'command', 'command': [sys.executable, '-c', 'pass'], 'timeout': 10,
    })
    run = ok(client.post(f"/api/nodes/{node['id']}/run", json={
        'request_id': 'waiting-live-child-pause-resume',
    }))
    with Session.begin() as session:
        row = session.get(TaskRun, run['id'])
        row.status = 'waiting'
        row.pid = None
        row.started_at = (datetime.now(timezone.utc) - timedelta(seconds=0.3)).isoformat()
        row.resource = {
            **row.resource,
            'elapsed_seconds': 0.1,
            'elapsed_before_attempt': 0.0,
            'elapsed_before_wait': 0.1,
            'waiting_started_at': time.time() - 0.2,
            'time_budget': {
                'requested_task_timeout_seconds': 10,
                'effective_total_timeout_seconds': 8,
                'reservation_state': 'held',
            },
        }

    class FakeManager:
        def __init__(self):
            self.status = 'running'
            self.signals = []

        def all(self):
            return [{'process_id': 'fixture-child', 'status': self.status}]

        def signal_all(self, signum):
            self.signals.append(signum)
            self.status = 'paused' if signum == signal.SIGSTOP else 'running'

    manager = FakeManager()
    with patch('research.execution.process_manager.process_manager', return_value=manager):
        paused = ok(client.post(f"/api/runs/{run['id']}/pause", json={}))
        await_intervention(client, paused['intervention']['id'])
        paused = ok(client.get(f"/api/runs/{run['id']}"))
        assert paused['status'] == 'paused'
        assert paused['resource']['paused_live_attempt'] is True
        time.sleep(0.1)
        resumed = ok(client.post(f"/api/runs/{run['id']}/resume", json={}))
        await_intervention(client, resumed['intervention']['id'])
        resumed = ok(client.get(f"/api/runs/{run['id']}"))

    assert resumed['status'] == 'queued'
    assert resumed['resource']['live_process_pending_dispatch'] is True
    assert 'paused_live_attempt' not in resumed['resource']
    assert manager.signals == [signal.SIGSTOP]
    time.sleep(0.1)
    with Session() as session:
        waiting_for_worker = session.get(TaskRun, run['id'])
        effective_elapsed = _elapsed_seconds_at(waiting_for_worker)
    assert effective_elapsed >= resumed['resource']['elapsed_seconds'] + 0.08


def case_retry_restores_requested_timeout_when_project_budget_is_removed(client, app):
    from services.api.db import Session, TaskRun

    project = ok(client.post('/api/projects', json={
        'name': 'Retry timeout after budget removal',
        'goal': 'Keep the requested task timeout when a project cap is removed.',
        'budget': {'max_runs': 20, 'seconds': 4, 'allow_paid': False},
    }))
    node = add_node(client, project, config={
        'kind': 'command', 'command': [sys.executable, '-c', 'pass'], 'timeout': 30,
    })
    run = ok(client.post(f"/api/nodes/{node['id']}/run", json={
        'request_id': 'budgeted-timeout-before-retry',
    }))
    assert run['config']['timeout'] == 4
    assert run['resource']['time_budget']['requested_task_timeout_seconds'] == 30
    with Session.begin() as session:
        session.get(TaskRun, run['id']).status = 'completed'

    ok(client.patch(f"/api/projects/{project['id']}", json={
        'budget': {'max_runs': 20, 'allow_paid': False},
    }))
    retried = ok(client.post(f"/api/runs/{run['id']}/retry", json={
        'request_id': 'budget-removed-retry',
    }))
    assert retried['id'] != run['id']
    assert retried['status'] == 'queued'
    assert retried['config']['timeout'] == 30


def case_duplicate_legacy_unsupported_mode_defaults_to_assisted(client, app):
    from services.api.db import Session, Project
    source = create(client)
    for legacy_mode in ('operator', '', 'AUTO'):
        with Session.begin() as session:
            session.get(Project, source['id']).mode = legacy_mode
        duplicate = ok(client.post(f"/api/projects/{source['id']}/duplicate"))
        assert duplicate['mode'] == 'assisted'
        assert ok(client.get(f"/api/projects/{duplicate['id']}"))['mode'] == 'assisted'
        assert ok(client.get(f"/api/projects/{source['id']}"))['mode'] == legacy_mode


def case_retry_refreshes_node_after_project_lock(client, app):
    from services.api.db import Session, Node
    from services.worker.scheduler import enqueue
    project = create(client)
    node = add_node(client, project, config={'kind': 'command', 'command': [sys.executable, '-c', 'pass']})
    with Session() as stale:
        old = stale.get(Node, node['id'])
        revision = old.revision
        with Session.begin() as editor:
            current = editor.get(Node, node['id'])
            current.revision += 1
            current.extra = {**current.extra, 'latest_run_id': 'current-run'}
            current.execution_status = 'completed'
        assert old.revision == revision
        retry = enqueue(stale, project['id'], 'command', old.config, 'stale-retry', old,
                        node_revision=revision)
        stale.commit()
        assert retry.node_revision == revision
    with Session() as session:
        current = session.get(Node, node['id'])
        assert current.revision == revision + 1
        assert current.extra['latest_run_id'] == 'current-run'
        assert current.execution_status == 'completed'



def case_launch_refreshes_execution_kind_after_project_lock(client, app):
    from services.api.db import Session, Node
    from services.worker.scheduler import enqueue_nodes, enqueue_selected

    for launch in ('single', 'selected'):
        project = create(client)
        nodes = [add_node(client, project, config={'kind': 'agent'}) for _ in range(2)]
        with Session() as pending:
            # Both nodes were read before the project lock, while an owner save
            # could still commit. Hold the stale identities through scheduling.
            cached = [pending.get(Node, node['id']) for node in nodes]
            with Session.begin() as editor:
                for node in nodes:
                    current = editor.get(Node, node['id'])
                    current.config = {'kind': 'command', 'command': [sys.executable, '-c', 'pass']}
                    current.revision += 1
            assert all(node.config['kind'] == 'agent' for node in cached)
            if launch == 'single':
                runs = enqueue_nodes(pending, nodes[0]['id'], request_id='fresh-kind')
            else:
                runs = enqueue_selected(pending, [node['id'] for node in nodes], request_id='fresh-kind')
            pending.commit()
            assert all(run.kind == run.config['kind'] == 'command' for run in runs)
            assert all(run.node_revision == node['revision'] + 1 for run in runs
                       for node in nodes if node['id'] == run.node_id)


def case_regenerated_run_output_does_not_inherit_file_tombstone_origin(client, app):
    from services.api.common import effective_file_origin
    from services.api.db import FileRevision, Session, TaskRun

    assert effective_file_origin('deleted', 'executor_measurement') == 'executor_measurement'
    assert effective_file_origin('renamed', 'executor_measurement') == 'executor_measurement'
    assert effective_file_origin('user_edited', 'executor_measurement') == 'user_edited'

    project = create(client)
    node = add_node(client, project, config={
        'kind': 'command', 'command': [sys.executable, '-c', 'pass'],
    })
    run = ok(client.post(f"/api/nodes/{node['id']}/run", json={
        'request_id': 'run-for-regenerated-output-provenance',
    }))
    relative = run['output_path'] + '/predictions.csv'
    root = Path(os.environ['FOREST_DATA_DIR']) / 'projects' / project['id']
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('measurement\n7\n')
    with Session.begin() as session:
        session.get(TaskRun, run['id']).status = 'completed'

    ok(client.delete(f"/api/projects/{project['id']}/file", params={'path': relative}))
    with Session() as session:
        tombstone = session.query(FileRevision).filter_by(
            project_id=project['id'], path=relative
        ).one()
        assert tombstone.origin == 'deleted'

    # Run output files are written directly, so this path leaves the retained
    # tombstone row untouched when the same artifact path reappears.
    path.write_text('measurement\n8\n')
    rows = ok(client.get(f"/api/data/{run['id']}/rows"))
    assert rows['origin'] == 'measured', rows


def case_zero_padded_csv_identifiers_survive_data_rows(client, app):
    from services.api.db import Session, TaskRun
    project = create(client)
    node = add_node(client, project, config={
        'kind': 'command', 'command': [sys.executable, '-c', 'pass'],
    })
    run = ok(client.post(f"/api/nodes/{node['id']}/run", json={
        'request_id': 'run-for-zero-padded-identifiers',
    }))
    relative = run['output_path'] + '/identifiers.csv'
    root = Path(os.environ['FOREST_DATA_DIR']) / 'projects' / project['id']
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'postal_code,subject_id,serial,score,passed\n'
        '02108,000123,0000,9.5,True\n'
        '10001,000124,0007,10.5,False\n'
    )
    with Session.begin() as session:
        session.get(TaskRun, run['id']).status = 'completed'

    # The CSV bytes on disk keep the zeros; the readback must not corrupt them.
    assert '02108' in path.read_text()
    rows = ok(client.get(f"/api/data/{run['id']}/rows", params={'path': relative}))
    assert rows['columns'] == ['postal_code', 'subject_id', 'serial', 'score', 'passed']
    assert rows['rows'][0]['postal_code'] == '02108', rows
    assert rows['rows'][0]['subject_id'] == '000123', rows
    assert rows['rows'][0]['serial'] == '0000', rows
    assert rows['rows'][1]['postal_code'] == '10001', rows
    # Genuinely numeric and boolean columns keep their JSON types.
    assert rows['rows'][0]['score'] == 9.5 and rows['rows'][0]['passed'] is True, rows
    assert rows['rows'][1]['passed'] is False, rows
    # Numeric sort keeps numeric (not lexicographic) ordering: 10.5 > 9.5.
    sorted_rows = ok(client.get(f"/api/data/{run['id']}/rows", params={
        'path': relative, 'sort': 'score', 'descending': 'true',
    }))
    assert [row['score'] for row in sorted_rows['rows']] == [10.5, 9.5], sorted_rows


def case_raw_data_float_precision(client, app):
    import math
    import pandas as pd
    from services.api.db import Session, TaskRun

    project = create(client)
    node = add_node(client, project, config={
        'kind': 'command', 'command': [sys.executable, '-c', 'pass'],
    })
    run = ok(client.post(f"/api/nodes/{node['id']}/run", json={
        'request_id': 'run-for-float-precision',
    }))
    root = Path(os.environ['FOREST_DATA_DIR']) / 'projects' / project['id']
    directory = root / run['output_path']
    directory.mkdir(parents=True, exist_ok=True)
    values = [0.12345678901234567, 0.0000000000123456789, 1e-30, -1e-30]
    csv_path = directory / 'precise.csv'
    csv_path.write_text('value\n' + '\n'.join(map(repr, values)) + '\n')
    parquet_path = directory / 'precise.parquet'
    frame = pd.DataFrame({
        'value': values + [math.nan, math.inf, -math.inf],
        'label': ['001'] * 7,
        'when': [pd.Timestamp('2026-01-01')] * 7,
    })
    frame.to_parquet(parquet_path)
    with Session.begin() as session:
        session.get(TaskRun, run['id']).status = 'completed'

    for path in (csv_path, parquet_path):
        before = path.read_bytes()
        response = ok(client.get(f"/api/data/{run['id']}/rows", params={
            'path': path.relative_to(root).as_posix(),
        }))
        returned = [row['value'] for row in response['rows']]
        # CSV parsing has its own floating-point conversion. This checks that
        # JSON serialization preserves every bit of the parsed values.
        if path == csv_path:
            from services.api.common import infer_csv_column_types
            expected = infer_csv_column_types(pd.read_csv(path, dtype=str))['value'].tolist()
        else:
            expected = values
        assert returned[:4] == expected, returned
        assert returned[1] != 0 and returned[2] != 0 and returned[3] != 0
        if path == parquet_path:
            assert returned[4:] == [None, None, None], returned
            assert response['rows'][0]['label'] == '001'
            assert response['rows'][0]['when'] == 1767225600000
        assert path.read_bytes() == before

    page = ok(client.get(f"/api/data/{run['id']}/rows", params={
        'path': parquet_path.relative_to(root).as_posix(),
        'sort': 'value', 'descending': 'true', 'offset': 1, 'limit': 2,
    }))
    assert page['total'] == 7
    assert [row['value'] for row in page['rows']] == values[:2]


def case_run_output_keeps_utf8_character_split_at_page_boundary(client, app):
    project = create(client)
    node = add_node(client, project, config={
        'kind': 'command', 'command': [sys.executable, '-c', 'pass'],
    })
    run = ok(client.post(f"/api/nodes/{node['id']}/run", json={
        'request_id': 'run-for-utf8-chunk-boundary',
    }))
    path = Path(os.environ['FOREST_DATA_DIR']) / 'projects' / project['id'] / (run['output_path'] + '/stdout.txt')
    path.parent.mkdir(parents=True, exist_ok=True)

    def read_pages():
        # The Run Panel follows the returned byte cursor; page 1 uses the default 100000-byte limit.
        first = ok(client.get(f"/api/runs/{run['id']}/output", params={'offset': 0, 'limit': 100000}))
        second = ok(client.get(f"/api/runs/{run['id']}/output", params={'offset': first['offset'], 'limit': 100000}))
        return first['text'] + second['text']

    # 2-, 3- and 4-byte characters cut by the 100000-byte page limit must survive reassembly.
    for filler, character in ((99999, 'é'), (99999, '中'), (99998, '😀')):
        expected = 'a' * filler + character + 'b'
        path.write_bytes(expected.encode())
        text = read_pages()
        assert text == expected, f"{character!r} split across the page boundary: {text[-6:]!r}"
        assert '\ufffd' not in text

    # Invalid bytes are not a partial character: they must decode with replacement and the
    # cursor must advance past them so a polling reader is never stuck on the same page.
    path.write_bytes(b'a\xff\xfeb')
    page = ok(client.get(f"/api/runs/{run['id']}/output", params={'offset': 0, 'limit': 100000}))
    assert page['text'] == 'a\ufffd\ufffdb' and page['offset'] == 4

    # A still-growing file may end inside a character; the cursor stays on the character
    # start and the next poll returns it whole once the writer has finished the sequence.
    path.write_bytes(b'a\xe4\xb8')
    partial = ok(client.get(f"/api/runs/{run['id']}/output", params={'offset': 0, 'limit': 100000}))
    assert partial['text'] == 'a' and partial['offset'] == 1
    path.write_bytes(b'a\xe4\xb8\xadb')
    grown = ok(client.get(f"/api/runs/{run['id']}/output", params={'offset': partial['offset'], 'limit': 100000}))
    assert grown['text'] == '中b' and grown['offset'] == 5

    # A held-back prefix that turns out to be invalid is released once more bytes arrive,
    # so a polling reader is never stuck behind undecodable data.
    path.write_bytes(b'a\xe4')
    held = ok(client.get(f"/api/runs/{run['id']}/output", params={'offset': 0, 'limit': 100000}))
    assert held['text'] == 'a' and held['offset'] == 1
    path.write_bytes(b'a\xe4b')
    released = ok(client.get(f"/api/runs/{run['id']}/output", params={'offset': held['offset'], 'limit': 100000}))
    assert released['text'] == '\ufffdb' and released['offset'] == 3

    # A page limit smaller than the next character must still make progress, or a client
    # following the cursor would repeat the same request forever.
    for limit, content in ((1, 'é中b'), (2, '中😀b'), (3, '😀b'), (1, 'ab')):
        path.write_bytes(content.encode())
        pages, offset, text = [], 0, ''
        while True:
            page = ok(client.get(f"/api/runs/{run['id']}/output", params={'offset': offset, 'limit': limit}))
            pages.append((page['offset'], page['text']))
            text += page['text']
            assert page['offset'] > offset or not page['text'], f"cursor stalled at {offset} with limit={limit}: {pages}"
            if page['offset'] == offset:
                break
            offset = page['offset']
            if offset >= len(content.encode()):
                break
        assert text == content, f"limit={limit} reassembled {text!r}"
def case_figure_render_is_invalidated_after_data_or_style_edit(client, app):
    p = create(client)
    image_path = 'figures/current.png'
    ok(client.put(f"/api/projects/{p['id']}/file", json={"path": image_path, "content": "old rendered bytes"}))
    figure = ok(client.post('/api/figures', json={
        'project_id': p['id'], 'title': 'Current figure', 'status': 'ready_for_review',
        'data': {'kind': 'bar', 'metric': 'score', 'style': {'title': 'Old'},
                 'data': {'values': [1]}, 'visual_review_status': 'selected',
                 'outputs': {'png': image_path}},
    }))
    paper = ok(client.get(f"/api/papers/{p['id']}"))
    anchor = 'The measured result appears here.'
    source = paper['data']['source'].replace('Write the evidence-supported question here.', anchor)
    paper = ok(client.patch(f"/api/papers/{p['id']}", json={
        'expected_revision': paper['revision'], 'data': {'source': source},
    }))

    edited = ok(client.patch(f"/api/figures/{figure['id']}", json={
        'expected_revision': figure['revision'],
        'data': {**figure['data'], 'style': {'title': 'New'}, 'data': {'values': [2]},
                 'caption': 'New measured result.'},
    }))
    assert edited['status'] == 'needs_review'
    assert 'outputs' not in edited['data']
    assert 'visual_selection' not in edited['data']
    assert edited['data']['visual_review_status'] == 'stale'
    response = client.post(f"/api/papers/{p['id']}/figures", json={
        'expected_revision': paper['revision'], 'figure_id': figure['id'], 'anchor_text': anchor,
    })
    assert response.status_code == 409, response.text


def case_new_projects_inherit_default_run_mode(client, app):
    ok(client.patch('/api/settings', json={'default_mode': 'manual'}))
    inherited = ok(client.post('/api/projects', json={'name': 'Inherit configured mode'}))
    explicit = ok(client.post('/api/projects', json={
        'name': 'Keep explicit mode', 'mode': 'assisted',
    }))

    assert inherited['mode'] == 'manual'
    assert ok(client.get(f"/api/projects/{inherited['id']}"))['mode'] == 'manual'
    assert explicit['mode'] == 'assisted'


def case_first_settings_patch_preserves_defaults_and_unknown_fields(client, app):
    initial = ok(client.get('/api/settings'))
    assert initial == {'language': 'en', 'theme': 'light', 'retention_days': 30, 'default_mode': 'assisted'}
    saved = ok(client.patch('/api/settings', json={'retention_days': 14, 'extension': {'future': True}}))
    assert saved == {**initial, 'retention_days': 14, 'extension': {'future': True}}
    changed = ok(client.patch('/api/settings', json={'theme': 'dark'}))
    assert changed == {**saved, 'theme': 'dark'}
    assert ok(client.get('/api/settings')) == changed


def case_library_import_exports_books_with_persisted_publication_type(client, app):
    import httpx
    from pybtex.database import parse_string
    from research.literature import sources
    from services.api.db import Session, SourcePaper

    project = create(client)
    metadata = {
        "type": "edited-book", "title": ["An Edited Handbook"],
        "editor": [{"given": "Casey", "family": "Editor"}],
        "publisher": "Example Press", "published": {"date-parts": [[2024]]},
        "DOI": "10.1234/forest107", "URL": "https://doi.org/10.1234/forest107",
    }
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sources, "_get", lambda *args, **kwargs: httpx.Response(
            200, json={"message": metadata}))
        saved = ok(client.post("/api/library/import", json={
            "project_id": project["id"], "identifier": "10.1234/forest107",
        }))
    assert saved["data"]["type"] == "edited-book"
    with Session() as session:
        persisted = session.get(SourcePaper, saved["id"])
        assert persisted.data["type"] == "edited-book"
        assert persisted.data["editors"] == ["Casey Editor"]

    ok(client.post("/api/library/import", json={
        "project_id": project["id"],
        "paper": {"title": "A Journal Article", "type": "journal-article",
                  "authors": ["Alex Author"], "year": 2024,
                  "journal": "Example Journal", "volume": "12", "pages": "4-9"},
    }))
    response = client.get("/api/library/export-bibtex", params={"project_id": project["id"]})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-bibtex")
    entries = parse_string(response.text, "bibtex").entries.values()
    by_title = {item.fields["title"]: item for item in entries}
    book = by_title["An Edited Handbook"]
    assert book.type == "book"
    assert book.fields["publisher"] == "Example Press"
    assert book.persons["editor"][0].last_names == ["Editor"]
    assert "journal" not in book.fields
    article = by_title["A Journal Article"]
    assert article.type == "article"
    assert article.fields["journal"] == "Example Journal"
    assert article.fields["volume"] == "12"
    assert article.fields["pages"] == "4-9"


def case_graph_command_shape_validation(client, app):
    from fastapi.testclient import TestClient
    from services.api.db import Session, CommandReceipt, TaskRun
    from services.interventions.models import Intervention
    from sqlalchemy import func, select

    project = create(client)
    first = add_node(client, project)
    second = add_node(client, project)
    before = graph(client, project)
    bad = (
        [(op, {}) for op in ("add_dependency", "remove_dependency")]
        + [(op, {"patch": value}) for op in ("edit_node", "apply_instruction_patch")
           for value in (None, True, [], 3.5)]
        + [(op, {"node_ids": value}) for op in ("fork_branch", "clone_subtree")
           for value in (None, True, -1, 3.5)]
        + [("split_node", {"parts": value}) for value in (None, True, 0, 3.5, [None, {}])]
        + [("edit_node", {"branch_id": value}) for value in ([], {})]
        + [("add_dependency", {"source": first["id"], "target": value}) for value in ([], {})]
    )
    def side_effect_counts():
        with Session() as session:
            return tuple(session.scalar(select(func.count()).select_from(model))
                         for model in (CommandReceipt, Intervention, TaskRun))

    before_counts = side_effect_counts()
    # Keep the real HTTP 500 visible on the original implementation.
    requests = TestClient(app, raise_server_exceptions=False)
    try:
        requests.cookies.update(client.cookies)
        for operation, params in bad:
            for endpoint in ("preview", "commands"):
                response = requests.post(f"/api/projects/{project['id']}/graph/{endpoint}", json={
                    "request_id": str(uuid4()), "expected_revision": before["revision"],
                    "operation": operation, "targets": [first["id"]], "params": params, "run": False,
                })
                detail = ok(response, 422)["detail"]
                assert detail["code"] == "invalid_command"
                assert detail["retryable"] is False
                assert detail["field"].startswith("params.")
                assert graph(client, project) == before
                assert side_effect_counts() == before_counts
    finally:
        requests.close()
    # A valid dependency with two target IDs still previews and commits normally.
    valid = {"request_id": str(uuid4()), "expected_revision": before["revision"],
             "operation": "add_dependency", "targets": [first["id"], second["id"]], "params": {}, "run": False}
    ok(client.post(f"/api/projects/{project['id']}/graph/preview", json=valid))
    assert graph(client, project) == before
    result = ok(client.post(f"/api/projects/{project['id']}/graph/commands", json=valid))
    assert result["graph"]["revision"] == before["revision"] + 1
    assert any(edge["source"] == first["id"] and edge["target"] == second["id"] for edge in result["graph"]["edges"])


def case_invalid_task_kind_does_not_write_graph_or_enqueue(client, app):
    from sqlalchemy import func, select
    from services.api.db import CommandReceipt, Session, TaskRun
    from services.interventions.models import Intervention

    project = create(client)
    node = add_node(client, project, config={"kind": "command", "command": [sys.executable, "-c", "pass"]})
    before = graph(client, project)
    with Session() as session:
        counts = tuple(session.scalar(select(func.count()).select_from(model))
                       for model in (CommandReceipt, Intervention, TaskRun))
    for operation, params in [
        ("add_node", {"type": "experiment", "config": {"kind": "not_a_real_kind"}}),
        ("add_node", {"node": {"type": "experiment", "config": {"kind": "not_a_real_kind"}}}),
        ("edit_node", {"config": {"kind": "not_a_real_kind"}}),
        ("edit_node", {"patch": {"config": {"kind": "not_a_real_kind"}}}),
    ]:
        for endpoint in ("preview", "commands"):
            response = client.post(f"/api/projects/{project['id']}/graph/{endpoint}", json={
                "request_id": str(uuid4()), "expected_revision": before["revision"],
                "operation": operation, "targets": [node["id"]] if operation == "edit_node" else [],
                "params": params, "run": True,
            })
            assert ok(response, 422)["detail"]["code"] == "invalid_task_kind"
            assert graph(client, project) == before
    response = client.patch(f"/api/nodes/{node['id']}", json={"config": {"kind": "not_a_real_kind"}})
    assert ok(response, 422)["detail"]["code"] == "invalid_task_kind"
    assert graph(client, project) == before
    with Session() as session:
        assert tuple(session.scalar(select(func.count()).select_from(model))
                     for model in (CommandReceipt, Intervention, TaskRun)) == counts


CASES = [name.removeprefix("case_") for name in list(globals()) if name.startswith("case_")]


@pytest.mark.parametrize("case", CASES)
def test_isolated_api_scenario(tmp_path, case):
    if case == "posix_backslash_file_revision_keys" and os.name != "posix":
        pytest.skip("Backslashes are filename characters only on POSIX")
    env = {**os.environ, "FOREST_DATA_DIR": str(tmp_path / "data"), "FOREST_DATABASE_URL": "sqlite:///" + str(tmp_path / "isolated.sqlite"),
           "FOREST_OWNER_TOKEN": "isolated-test-owner-token", "FOREST_MODEL": "", "PYTHONPATH": str(ROOT)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--case", case], cwd=ROOT, env=env, capture_output=True, text=True, timeout=50)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SCENARIO PASSED" in result.stdout


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from services.api.main import app
    selected = sys.argv[2]
    assert selected in CASES
    with TestClient(app) as client:
        globals()["case_" + selected](client, app)
    print("SCENARIO PASSED:", selected)
