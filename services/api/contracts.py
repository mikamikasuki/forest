"""Documentation-only contracts for the existing FOREST HTTP API.

These dictionaries enrich OpenAPI; they are not request validators or response
models. Keep the wire behavior in the route handlers. Open configuration and
scientific records deliberately remain extensible JSON objects.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def ref(name: str) -> dict[str, Any]:
    return {"$ref": f"#/components/schemas/{name}"}


def array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def obj(properties: dict[str, Any] | None = None, required: tuple[str, ...] = (),
        description: str = "", *, additional: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "object", "properties": properties or {},
                              "additionalProperties": additional}
    if required:
        result["required"] = list(required)
    if description:
        result["description"] = description
    return result


def field(kind: str, description: str = "", *, nullable: bool = False,
          **metadata: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"type": [kind, "null"] if nullable else kind, **metadata}
    if description:
        result["description"] = description
    return result


S = field("string")
I = field("integer")
N = field("number")
B = field("boolean")
JSON = ref("JsonValue")
J = ref("JsonObject")
STRINGS = array(S)
IDENTITY = {
    "id": field("string", "Record identifier."),
    "created_at": field("string", "Creation timestamp, normally UTC ISO 8601."),
    "updated_at": field("string", "Last database update timestamp, normally UTC ISO 8601."),
}
RESOURCE_PROPERTIES = {
    **IDENTITY, "project_id": S, "title": S, "revision": I, "status": S,
    "data": {**J, "description": "Editable kind-specific scientific record. Keys depend on the resource kind and task output."},
}
SCHEMAS: dict[str, dict[str, Any]] = {
    "JsonValue": {"description": "Any JSON value. Its structure is defined by the selected scientific tool or configuration."},
    "JsonObject": obj(description="Extensible JSON object; unknown keys are retained where the endpoint merges this object."),
    "Project": obj({**IDENTITY, "name": S, "description": S, "goal": S,
        "current_direction": S, "revision": I, "archived": B, "mode": S,
        "budget": J, "config": J,
        "graph_meta": {**J, "description": "Included in list/create/update responses; omitted from the single-project GET."}},
        ("id", "name", "description", "goal", "current_direction", "revision", "archived", "mode", "budget", "config")),
    "ResearchNode": obj({**IDENTITY, "project_id": S, "branch_id": S, "type": S,
        "title": S, "instructions": S, "revision": I, "config": J,
        "position": obj({"x": N, "y": N}), "execution_status": S,
        "research_status": S, "deliverable_status": S, "archived": B,
        "inputs": array(JSON), "outputs": array(JSON), "context_overrides": J,
        "comments": array(JSON)},
        ("id", "project_id", "branch_id", "type", "title", "instructions", "revision", "config", "position", "execution_status", "research_status", "deliverable_status", "archived", "inputs", "outputs", "context_overrides", "comments"),
        "A path node. Additional kernel/runtime metadata is flattened into this object, not returned as an extra field. Newly created graph snapshots need not contain database timestamps."),
    "Branch": obj({**IDENTITY, "project_id": S, "name": S,
        "root_node_id": field("string", nullable=True), "status": S,
        "workspace": S, "is_main": B, "config": J},
        ("id", "name", "status", "workspace", "is_main", "config"),
        "Editable branch/workspace. Graph command snapshots may lack database timestamps or project_id; persisted branch rows contain them. Additional branch metadata is flattened."),
    "GraphEdge": obj({**IDENTITY, "project_id": S, "source": S, "target": S,
        "relation": S, "input_mapping": J}, ("id", "source", "target", "relation"),
        "Typed relation. depends_on and consumes participate in execution cycle checks. Snapshot edges need not contain database timestamps or project_id."),
    "GraphState": obj({"project_id": S, "revision": I, "goal": S, "budget": J,
        "nodes": array(ref("ResearchNode")), "edges": array(ref("GraphEdge")),
        "branches": array(ref("Branch")), "groups": array(J)},
        ("project_id", "revision", "nodes", "edges", "branches"),
        "Public editable graph. Additional graph metadata is retained; private _history is excluded from graph-read and graph-command responses."),
    "GraphImpact": obj({"changed_nodes": STRINGS, "affected_nodes": STRINGS,
        "rerun_nodes": STRINGS, "refresh_nodes": STRINGS, "reasons": J,
        "categories": J, "files": array(J), "conflicts": array(J),
        "actions": array(J), "input_bindings_required": array(J), "undo_scope": S},
        ("changed_nodes", "affected_nodes", "rerun_nodes", "refresh_nodes", "reasons", "categories", "files", "conflicts"),
        "Selective consequences of an edit. Preview calculates this without persisting graph edits or workspace copies."),
    "GraphCommandResult": obj({"revision": I, "graph": ref("GraphState"),
        "impact": ref("GraphImpact"), "run_nodes": STRINGS, "run_ids": STRINGS},
        ("revision", "graph", "impact", "run_nodes", "run_ids")),
    "Run": obj({**IDENTITY, "project_id": S,
        "node_id": field("string", nullable=True), "branch_id": field("string", nullable=True),
        "request_id": S, "kind": S, "status": S, "config": J,
        "node_revision": I, "priority": I, "dependencies": STRINGS,
        "worker_id": field("string", nullable=True), "pid": field("integer", nullable=True),
        "process_created": field("number", "Process identity creation time, not an ISO timestamp.", nullable=True),
        "started_at": field("string", nullable=True), "finished_at": field("string", nullable=True),
        "exit_code": field("integer", nullable=True), "error": field("string", nullable=True),
        "output_path": field("string", "Path relative to the project workspace."),
        "metrics": J, "resource": J,
        "manuscript_evidence": obj({"ready": B, "reason": S, "metrics_file": S,
            "numeric_measurements": I})},
        ("id", "project_id", "node_id", "branch_id", "request_id", "kind", "status", "config", "node_revision", "priority", "dependencies", "worker_id", "pid", "process_created", "started_at", "finished_at", "exit_code", "error", "output_path", "metrics", "resource"),
        "A queued or saved task run. A successful submission means it was enqueued, not that its scientific work completed. Configuration redaction differs by view; see the endpoint description."),
    "ToolExecution": obj({**IDENTITY, "run_id": S, "tool": S, "arguments": J,
        "status": S, "result": J, "elapsed": field("number", nullable=True)},
        ("id", "run_id", "tool", "arguments", "status", "result", "elapsed")),
    "RunDetail": {"allOf": [ref("Run"), obj({"tools": array(ref("ToolExecution"))}, ("tools",))]},
    "RunCollection": obj({"runs": array(ref("Run")), "run_ids": STRINGS}, ("runs",)),
    "SelectedRunCollection": obj({"runs": array(ref("Run")), "run_ids": STRINGS}, ("runs", "run_ids")),
    "RecordItem": obj(RESOURCE_PROPERTIES, tuple(RESOURCE_PROPERTIES)),
    "Provider": obj({**IDENTITY, "name": S, "kind": S, "base_url": S, "model": S,
        "allow_paid": B, "status": S, "config": J, "has_key": B},
        ("id", "name", "kind", "base_url", "model", "allow_paid", "status", "config", "has_key"),
        "Configured text/image endpoint. credential_ref is replaced with has_key; the stored credential itself is not returned."),
    "Host": obj({**IDENTITY, "name": S, "kind": S, "status": S, "config": J},
        ("id", "name", "kind", "status", "config")),
    "Agent": obj({**IDENTITY, "name": S, "role": S, "instructions": S,
        "provider_id": field("string", nullable=True), "tools": array(JSON), "config": J, "enabled": B},
        ("id", "name", "role", "instructions", "provider_id", "tools", "config", "enabled")),
    "Worker": obj({**IDENTITY, "name": S, "pid": I, "heartbeat": S,
        "capabilities": J, "online": B}, ("id", "name", "pid", "heartbeat", "capabilities", "online")),
    "ApiErrorDetail": obj({"code": S, "message": S, "retryable": B,
        "suggestion": S, "details": J, "expected_revision": I, "current_revision": I},
        ("code", "message", "retryable"),
        "Application errors use detail.code/message/retryable. suggestion and context keys are optional; graph errors can flatten their context into detail."),
    "ApiError": obj({"detail": {"anyOf": [ref("ApiErrorDetail"), S,
        array(obj({"loc": array(JSON), "msg": S, "type": S, "input": JSON, "ctx": J}))]}},
        ("detail",), "FastAPI validation failures have an array detail; application failures normally have an object detail. Preserve both shapes."),
    "Authenticated": obj({"authenticated": B}, ("authenticated",)),
    "Health": obj({"status": S, "database": S, "version": S}, ("status", "database", "version")),
    "Deleted": obj({"deleted": S}, ("deleted",)),
    "ProjectPatch": obj({"name": S, "description": S, "goal": S, "current_direction": S,
        "archived": B, "mode": S, "budget": J, "config": J,
        "expected_revision": field("integer", "Optional project/graph revision; omission uses the current revision.")}),
    "NodePatch": obj({"title": S, "instructions": S, "type": S, "config": J,
        "inputs": array(JSON), "position": J, "context_overrides": J, "comments": array(JSON),
        "archived": B, "expected_revision": field("integer", "The PROJECT graph revision, not the node revision.")},
        description="Passed to edit_node with optional project revision. Kernel-managed id/project_id/revision/execution_status/last_run_id/last_run_revision cannot be changed here. Other editable node metadata is extensible."),
    "ResourcePatch": obj({"title": S, "status": S, "data": J,
        "replace_data": field("boolean", "When true, replace the complete resource data object instead of merging it."),
        "expected_revision": field("integer", "Optional resource revision; omission uses the current resource revision.")}),
    "RunActionRequest": obj({"request_id": field("string", "Used by retry; other run actions ignore it."),
        "priority": field("integer", "Used by priority, default 0; handler converts with int()."),
        "context_char_budget": field("integer", "resume only; Agent run, integer >= 1000."),
        "agent_budget": {**J, "description": "resume only; merges editable per-Agent token/cost/time hard limits after budget validation. Legacy steps is accepted as a bounded work-round hint, never a cumulative research limit."}}),
    "QueuedTaskRequest": obj({"project_id": S, "request_id": field("string", nullable=True),
        "node_id": field("string", nullable=True), "run_ids": STRINGS,
        "provider_id": field("string", nullable=True)}, ("project_id",),
        "Task-specific fields are passed to the scheduler/worker. Scientific input schemas depend on the task kind; this is not a closed universal tool schema."),
    "FileEntry": obj({"path": S, "size": I, "modified": N, "is_dir": B}, ("path", "size", "modified", "is_dir")),
    "FileList": obj({"files": array(ref("FileEntry")), "has_more": B,
                      "next_cursor": field("string", nullable=True)}, ("files", "has_more", "next_cursor")),
    "FileExistenceRequest": obj({"paths": array(S)}, ("paths",)),
    "FileExistenceResult": obj({"existing": STRINGS, "directories": STRINGS}, ("existing", "directories")),
    "FileRead": obj({"path": S, "content": S, "revision": I, "origin": S}, ("path", "content", "revision", "origin")),
    "FileWritten": obj({"path": S, "revision": I, "origin": S}, ("path", "revision", "origin")),
    "FilePath": obj({"path": S}, ("path",)),
    "FileUpload": obj({"path": S, "size": I, "origin": S}, ("path", "size", "origin")),
    "TablePreview": obj({"path": S, "format": S, "columns": STRINGS, "rows": array(J),
        "total": I, "offset": I, "limit": I, "returned": I, "has_more": B,
        "truncated_columns": B, "column_count": I, "truncated_cells": I},
        ("path", "format", "columns", "rows", "total", "offset", "limit", "returned", "has_more", "truncated_columns", "column_count", "truncated_cells")),
    "DataRows": obj({"columns": STRINGS, "rows": array(J), "total": I, "origin": S}, ("columns", "rows", "total", "origin")),
    "OutputPage": obj({"text": S, "offset": field("integer", "Next byte cursor in stdout.txt, before any search filtering."), "status": S}, ("text", "offset", "status")),
    "ContextSnapshot": obj({"project_id": field("string", nullable=True), "node_id": S, "branch_id": S,
        "role": S, "graph_revision": I, "node_revision": I, "controls": J,
        "materials": array(J), "retrievable_materials": array(J), "omitted": array(J),
        "summaries_stale": STRINGS, "imported_branches": STRINGS, "overrides": J,
        "capacity": obj({"max_chars": I, "effective_preview_chars": I, "required_control_chars": I,
            "used_content_chars": I, "policy": S, "soft_preview_hint": B, "truncated": B}), "text": S},
        ("project_id", "node_id", "branch_id", "role", "graph_revision", "node_revision", "controls", "materials", "retrievable_materials", "omitted", "summaries_stale", "imported_branches", "overrides", "capacity", "text")),
    "GraphPage": obj({"revision": I, "offset": I, "nodes": array(ref("ResearchNode")),
        "edges": array(ref("GraphEdge")), "next_offset": field("integer", nullable=True)},
        ("revision", "offset", "nodes", "edges", "next_offset")),
    "GraphBatchRequest": obj({"request_id": field("string", "Optional idempotency key. Shared with graph command receipts in this project."),
        "expected_revision": I, "commands": {**array(J), "minItems": 1, "maxItems": 200}}, ("expected_revision", "commands")),
    "GraphBatchResult": obj({"revision": I, "applied": I, "node_count": I, "edge_count": I}, ("revision", "applied", "node_count", "edge_count")),
    "ComparisonDeclaration": obj({"run_id": S, "complete": B, "signature": J,
        "missing_fields": STRINGS, "conflicting_fields": STRINGS, "scope": S},
        ("run_id", "complete", "signature", "missing_fields", "conflicting_fields", "scope")),
    "ComparedRun": obj({"id": S, "status": S, "metrics": J, "resource": J, "config": J}, ("id", "status", "metrics", "resource", "config")),
    "ExperimentComparison": obj({"runs": array(ref("ComparedRun")), "directly_comparable": B,
        "comparison_status": field("string", enum=["directly_comparable", "incomparable", "unverified"]),
        "reason": S, "declarations": array(ref("ComparisonDeclaration")),
        "noncompleted_run_ids": STRINGS, "verification_scope": S},
        ("runs", "directly_comparable", "comparison_status", "reason", "declarations", "noncompleted_run_ids", "verification_scope")),
    "Trial": obj({"run_id": S, "status": S, "metric": S, "value": field("number", nullable=True),
        "disposition": S, "conditions": J, "comparison_declaration": ref("ComparisonDeclaration"),
        "comparison_eligible": B, "verification_status": S, "numerical_verification": J,
        "evidence_label": S, "comparison_status": S, "comparator_run_id": field("string", nullable=True),
        "exploratory_group": J}, ("run_id", "status", "metric", "value", "disposition")),
    "ResearchState": obj({"objective": J, "trials": array(ref("Trial")), "controller": J,
        "active_runs": array(obj({"id": S, "kind": S, "status": S, "node_id": field("string", nullable=True), "resource": J})),
        "decisions": array(ref("RecordItem")), "counts": obj({"runs": I, "completed": I, "failed": I}), "coverage": S},
        ("objective", "trials", "controller", "active_runs", "decisions", "counts")),
    "RouteSignal": obj({"kind": S, "node_ids": STRINGS, "run_ids": STRINGS, "finding": S}, ("kind", "node_ids", "run_ids", "finding")),
    "RouteReviewSettings": obj({"enabled": B, "window": I, "repeated_failures": I,
        "unchanged_counterexample_repeats": I, "planning_without_execution": I, "review_every_runs": I},
        ("enabled", "window", "repeated_failures", "unchanged_counterexample_repeats", "planning_without_execution", "review_every_runs")),
    "RouteHealth": obj({"replan_required": B, "signals": array(ref("RouteSignal")),
        "settings": ref("RouteReviewSettings"), "reviewed_run_ids": STRINGS,
        "node_count": I, "total_run_count": I, "scope": S},
        ("replan_required", "signals", "settings", "reviewed_run_ids", "node_count", "total_run_count", "scope")),
    "Verification": obj({"run_id": S, "verification_status": S, "reason": S,
        "verification_run_id": S, "verifier_node_id": field("string", nullable=True),
        "checks": array(J), "check_scope_paths": STRINGS, "numerical_scope": array(J),
        "verification_scope": S, "verifications": array(J)},
        ("run_id", "verification_status", "checks", "check_scope_paths", "verification_scope"),
        "Verifier runs return their current verdict; producers return current linked verifier verdicts. Extra binding/check data depends on the declared verification contract. Completion alone is not acceptance."),
    "VerificationRequest": obj({"project_id": S, "node_id": field("string", nullable=True),
        "request_id": field("string", nullable=True), "verification": J, "command": JSON,
        "execution_backend": S}, ("project_id",),
        "Other task configuration keys pass through. Service-owned verification bindings, source identities, verdicts and receipts cannot be supplied by the caller."),
    "RunSession": obj({"run_id": S, "status": S, "session": {"anyOf": [J, {"type": "null"}]}}, ("run_id", "status", "session")),
    "RunConfiguration": obj({"run_id": S, "config": J}, ("run_id", "config")),
    "LineageRun": obj({"id": S, "missing": B, "source_freshness": array(J),
        "node_id": field("string", nullable=True), "node_title": field("string", nullable=True),
        "status": S, "node_revision": I, "current_node_revision": field("integer", nullable=True),
        "current": B, "dependencies": STRINGS, "config": J, "metrics": J, "resource": J,
        "files": array(obj({"path": S, "bytes": I}, ("path", "bytes")))}, ("id",),
        "An available dependency has execution, source freshness and file data; an unavailable/cross-project dependency is only {id, missing:true}."),
    "RunLineage": obj({"run_id": S, "project_id": S, "runs": array(ref("LineageRun"))}, ("run_id", "project_id", "runs")),
    "RepositoryInput": obj({"url": field("string", "Uncredentialed github.com HTTPS or git SSH repository URL; <=512 characters."),
        "ref": field("string", "Branch/tag/HEAD/commit; default HEAD; <=256 characters, validated as a safe Git ref."),
        "directory": field("string", "Relative run-workspace directory without traversal; default source; <=256 characters."),
        "transport": field("string", enum=["auto", "https", "ssh"], default="auto"),
        "credential": field("string", "Named worker-owned credential profile, not credential material; <=64 safe name characters.", nullable=True)},
        ("url",), "Only these five repository fields are accepted. SSH preparation needs a configured named identity/known_hosts profile on the worker.", additional=False),
    "RepositoryCloneRequest": obj({"repository": ref("RepositoryInput"),
        "request_id": field("string", "Optional 1–128 character retry key: [A-Za-z0-9][A-Za-z0-9_.:-]{0,127}.")},
        ("repository",), additional=False),
    "RepositoryProvenance": obj({"url": S, "requested_ref": S, "commit": S, "directory": S, "transport": S}, ("url", "requested_ref", "commit", "directory", "transport")),
    "RunRepository": obj({"run_id": S, "project_id": S, "status": S,
        "repository": {"anyOf": [ref("RepositoryProvenance"), {"type": "null"}]}}, ("run_id", "project_id", "status", "repository")),
    "ProcessDiagnostic": obj({"process_id": S, "backend": S, "status": S, "command": JSON,
        "state_source": S, "observed_at": field("number", nullable=True), "process_alive": B,
        "exit_code": JSON, "started_at": JSON, "finished_at": JSON, "elapsed_seconds": JSON},
        ("process_id", "backend", "status", "command", "state_source", "observed_at"),
        "Optional receipt fields are returned only when present and scalar in the saved state. Commands may be a string, string array or null; local identity can add process_alive."),
    "RunDiagnostics": obj({"run_id": S, "status": S, "phase": S, "execution_backend": S,
        "repository": {"anyOf": [J, {"type": "null"}]}, "processes": array(ref("ProcessDiagnostic")),
        "processes_truncated": B, "logs": obj({"bytes": I, "updated_at": field("number", nullable=True)}, ("bytes", "updated_at")),
        "elapsed_seconds": field("number", nullable=True), "observed_at": N,
        "observation": S, "remote_state_is_live": B},
        ("run_id", "status", "phase", "execution_backend", "repository", "processes", "processes_truncated", "logs", "elapsed_seconds", "observed_at", "observation", "remote_state_is_live")),
    "ProviderUsage": obj({"provider_id": S, "limit_usd": field("number", nullable=True),
        "estimated_cost_usd": field("number", nullable=True), "known_cost_usd": N, "unknown_cost_requests": I, "reserved_usd": N, "remaining_usd": field("number", nullable=True),
        "cost_source": S, "request_count": I, "uncertain_requests": I,
        "requests": array(obj({"id": S, "run_id": field("string", nullable=True), "project_id": field("string", nullable=True),
            "model": S, "api": S, "purpose": S, "report_job_id": field("string", nullable=True), "status": S, "estimated_cost_usd": field("number", nullable=True),
            "reserved_usd": N, "request_id": field("string", nullable=True), "usage": JSON, "created_at": S}))},
        ("provider_id", "limit_usd", "estimated_cost_usd", "known_cost_usd", "unknown_cost_requests", "reserved_usd", "remaining_usd", "cost_source", "request_count", "uncertain_requests", "requests")),
    "ProjectUsage": obj({"project_id": S, "allow_paid": B, "limits": J,
        "estimated_cost_usd": field("number", nullable=True), "known_cost_usd": N, "unknown_cost_requests": I, "reserved_usd": N, "remaining_usd": field("number", nullable=True),
        "cost_source": S, "uncertain_requests": I, "run_count": I, "elapsed_seconds": N,
        "providers": array(obj({"id": S, "name": S, "scope": S, "allow_paid": B,
            "limit_usd": field("number", nullable=True), "estimated_cost_usd": field("number", nullable=True), "known_cost_usd": N, "unknown_cost_requests": I, "reserved_usd": N,
            "remaining_usd": field("number", nullable=True), "cost_source": S})),
        "active_run_limits": array(obj({"run_id": S, "status": S, "agent_budget": J}))},
        ("project_id", "allow_paid", "limits", "estimated_cost_usd", "known_cost_usd", "unknown_cost_requests", "reserved_usd", "remaining_usd", "cost_source", "uncertain_requests", "run_count", "elapsed_seconds", "providers", "active_run_limits")),
    "ProviderHealth": obj({"id": S, "available": B, "checked_at": S, "last_chat_test": S, "reason": S}, ("id", "available", "checked_at", "last_chat_test")),
    "SystemState": obj({"cpu_percent": N, "memory": obj({"total": I, "used": I, "percent": N}),
        "disk": obj({"total": I, "used": I, "free": I}), "gpu": array(obj({"name": S, "memory_used_mb": S, "memory_total_mb": S, "utilization_percent": S})),
        "workers": array(ref("Worker")), "latex": field("string", nullable=True),
        "model_connected": B, "provider_status": array(ref("ProviderHealth")), "platform": S, "isolation": S},
        ("cpu_percent", "memory", "disk", "gpu", "workers", "latex", "model_connected", "provider_status", "platform", "isolation")),
    "ProviderWrite": obj({"name": S, "kind": S, "base_url": S, "model": S,
        "allow_paid": B, "config": J, "api_key": field("string", "Write-only credential; nonempty values replace the stored key.")}),
    "ProviderCreate": obj({"name": S, "kind": field("string", enum=["ollama", "openai", "codex_cli"], default="openai"),
        "base_url": field("string", "HTTP(S) base URL."), "model": S, "allow_paid": B, "config": J,
        "api_key": field("string", "Optional write-only credential.")}, ("name", "base_url", "model")),
    "HostWrite": obj({"name": S, "kind": S, "config": J}),
    "HostCreate": obj({"name": S, "kind": S, "config": J}, ("name",)),
    "AgentWrite": obj({"name": S, "role": S, "instructions": S,
        "provider_id": field("string", nullable=True), "tools": array(JSON), "config": J, "enabled": B}),
    "AgentCreate": obj({"name": S, "role": S, "instructions": S,
        "provider_id": field("string", nullable=True), "tools": array(JSON), "config": J, "enabled": B}, ("name", "role")),
    "CleanupRequest": obj({"days": field("integer", "Terminal runs older than this many days; int conversion, clamped at zero; default 30."),
        "project_id": field("string", "Optional restriction for run cleanup."),
        "clear_edit_history": field("boolean", "Clears graph edit history for ALL projects, independently of project_id.")}),
    "CleanupResult": obj({"deleted_runs": I}, ("deleted_runs",)),
    "ShareRequest": obj({"node_ids": STRINGS, "description": B}),
    "ShareCreated": obj({"id": S, "url": S, "token": S}, ("id", "url", "token")),
    "ShareSnapshot": obj({"project": obj({"name": S, "description": S}, ("name", "description")),
        "nodes": array(obj({"id": S, "title": S, "type": S, "execution_status": S,
            "research_status": S, "deliverable_status": S, "position": J})), "read_only": B}, ("project", "nodes", "read_only")),
    "PublicationProfile": obj({"id": S, "version": I, "target": S, "manuscript_type": S,
        "accepted_papers": I, "datasets": I, "baselines": I, "seeds": I, "ablations": I,
        "experiment_duties": STRINGS, "evidence_manifest": S, "scale_policy": S,
        "budget_policy": S, "writing_policy": S, "architecture_figure_policy": S}, ("id", "version", "target"),
        "full_submission adds editable evidence/design targets; operational has only id, version and target."),
    "PublicationProfilePatch": obj({"id": S, "version": I, "target": S, "manuscript_type": S,
        "accepted_papers": I, "datasets": I, "baselines": I, "seeds": I, "ablations": I,
        "experiment_duties": STRINGS, "evidence_manifest": S, "scale_policy": S,
        "budget_policy": S, "writing_policy": S, "architecture_figure_policy": S},
        description="Partial profile fields merged with the current project profile before normalization. No fields are required just to patch an existing full_submission profile."),
    "PublicationAudit": obj({"profile": ref("PublicationProfile"), "ready": B, "status": S,
        "gaps": array(J), "manifest_path": field("string", nullable=True), "independent_verification": array(J),
        "counts": J, "targets": J, "comparable_scale_counts": J,
        "remaining_cells": array(J), "verification_scope": S},
        ("profile", "ready", "status", "gaps"),
        "Delivery assessment with profile-dependent coverage and gap details. Missing manifest returns counts:{}; operational scope does not return a submission matrix. Additional assessment fields are extensible; readiness is not venue acceptance."),
    "ProtocolRequest": obj({**{name: S for name in ("research_question", "hypothesis", "baseline", "candidate", "metric", "statistical_unit", "split_policy", "selection_policy", "decision_rule")},
        "argumentative_duty": field("string", enum=["effectiveness", "mechanism", "scenario_value", "alternative_explanation"]),
        "meaningful_effect": field("number", minimum=0), "phase": field("string", enum=["exploratory", "confirmatory"]),
        "test_used_for_selection": B},
        ("research_question", "hypothesis", "baseline", "candidate", "metric", "statistical_unit", "split_policy", "selection_policy", "decision_rule", "argumentative_duty", "meaningful_effect", "phase"),
        "Confirmatory phase requires test_used_for_selection:false. full_submission additionally requires the full design fields documented in API_REFERENCE.md."),
    "ProtocolResult": obj({"protocol": J, "status": S, "publication_profile": ref("PublicationProfile"), "verification_scope": S},
        ("protocol", "status", "publication_profile", "verification_scope")),
    "WritingEdit": obj({"original": S, "replacement": field("string", nullable=True), "reason": S,
        "start": I, "end": I, "line": I, "requires_evidence_judgment": B},
        ("original", "replacement", "reason", "start", "end", "line", "requires_evidence_judgment")),
    "WritingReview": obj({"edits": array(ref("WritingEdit")), "style_version": I, "profile": J,
        "coverage": S, "editing_policy": S}, ("edits", "style_version", "profile", "coverage", "editing_policy")),
    "ObjectiveRequest": obj({"metric": field("string", "Blank/omitted metric clears the objective."),
        "direction": field("string", enum=["min", "max"], default="min"),
        "comparison_fields": array(S)}),
    "PaperPatch": obj({"title": S, "data": J, "expected_revision": I}),
    "PaperRevisionRequest": obj({"expected_revision": I, "request_id": field("string", nullable=True)}),
    "PaperGenerationRequest": obj({"run_ids": STRINGS, "request_id": field("string", nullable=True), "manuscript_type": S}, ("run_ids",)),
    "PaperFigureRequest": obj({"expected_revision": I, "figure_id": S, "anchor_text": S,
        "caption": S, "span": S}, ("expected_revision", "figure_id", "anchor_text")),
    "PaperLayoutRequest": obj({"expected_revision": I, "request_id": field("string", nullable=True), "template": S, "layout": J}, ("expected_revision",)),
    "PaperExportRequest": obj({"expected_revision": I, "format": field("string", "pdf returns the current compiled PDF; other/omitted values return a source ZIP."), "source": S, "bibtex": S}),
    "PaperCheck": obj({"issues": array(J), "writing_profile": J, "coverage": S, "status": S}, ("issues", "writing_profile", "coverage", "status")),
    "ProposalApplyRequest": obj({"expected_revision": I, "commands": array(J), "indices": array(I)}),
    "ProposalApplied": obj({"graph": ref("GraphState"), "accepted_indices": array(I)}, ("graph", "accepted_indices")),
    "ReviewApplyRequest": obj({"expected_revision": I, "indices": array(I)}, ("expected_revision", "indices")),
    "ResearchControlRequest": obj({"branch_id": field("string", nullable=True), "required_artifacts": array(JSON),
        "autonomous": B, "max_cycles": field("integer", "Deprecated legacy input; recorded as history and does not limit research lifetime.", nullable=True, deprecated=True)}),
    "ResearchControl": obj({"status": S, "phase": S, "branch_id": field("string", nullable=True),
        "required_artifacts": array(JSON), "autonomous": B, "legacy_max_cycles": field("integer", "Historical planning cap; never enforced.", nullable=True),
        "paused_run_ids": STRINGS, "process_control_errors": array(obj({"run_id": S, "error": S}))},
        ("status", "process_control_errors")),
    "IdeaAdoptRequest": obj({"expected_revision": I, "request_id": S, "branch_id": S,
        "title": S, "instructions": S, "config": J, "inputs": array(JSON)}),
    "PairedStatisticsRequest": obj({"path": S, "unit_column": S, "baseline_column": S, "candidate_column": S,
        "request_id": field("string", nullable=True), "direction": S, "confidence": N,
        "bootstrap_samples": I, "seed": I, "meaningful_effect": N},
        ("path", "unit_column", "baseline_column", "candidate_column")),
    "SymbolicCheckRequest": obj({"project_id": S, "expression": S, "variable": S, "kind": S, "values": J, "title": S}, ("project_id", "expression")),
    "LibrarySearchRequest": obj({"query": S, "source": S, "limit": I}, ("query",)),
    "LibraryImportRequest": obj({"project_id": S, "identifier": S, "paper": JSON}, ("project_id",)),
    "IdListRequest": obj({"ids": STRINGS}, ("ids",)),
    "ExperimentCompareRequest": obj({"run_ids": STRINGS, "objective": J}, ("run_ids",)),
    "SelectedRunsRequest": obj({"node_ids": STRINGS, "request_id": field("string", nullable=True), "config": J}),
    "BrowserReadRequest": obj({"project_id": S, "url": S, "screenshot": B}, ("project_id", "url")),
    "CheckpointRequest": obj({"payload": JSON}),
    "CheckpointResult": obj({"checkpoint": J, "status": S}, ("checkpoint", "status")),
    "FileRenameRequest": obj({"path": S, "new_path": S}, ("path", "new_path")),
    "ProjectExportRequest": obj({"paths": STRINGS}),
    "LibrarySearchResult": obj({"results": array(J)}, ("results",)),
    "LibraryCompareResult": obj({"papers": array(obj({"id": S, "title": S, "abstract": S,
        "authors": JSON, "year": JSON, "reading_scope": S, "notes": JSON, "evidence_label": S}))}, ("papers",)),
}

# Aliases make the same wire concepts available to existing UI naming styles.
SCHEMAS.update({"GraphNode": ref("ResearchNode"), "TaskRun": ref("Run"),
                "ResourceRecord": ref("RecordItem")})

# Specialized record data describes the existing workbench surfaces. The
# extensible data objects still admit tool-specific fields not listed here.
SCHEMAS.update({
    "InputReference": obj({"kind": S, "id": S, "project_id": S, "node_id": S,
        "branch_id": S, "path": S, "destination": S, "project_scope": B,
        "revision": I, "verification_node_id": S, "available": B,
        "missing": B, "needs_rebinding": B},
        description="Explicit input binding. File path is relative to the referenced source branch unless project_scope:true; a node-only reference can expose context without copying a file. destination is relative to the receiving run workspace. Typed kind/id can bind saved idea/paper/dataset/run/figure/analysis records. Preserve missing/rebinding/source revision metadata during path edits."),
    "FigureOutputs": {"type": "object", "additionalProperties": S,
        "properties": {name: S for name in ("svg", "pdf", "png", "source", "data", "style", "report", "selection", "caption_context", "prompt")},
        "description": "Available output key to PROJECT-relative file path. A key is absent until that artifact exists; no format is universal."},
    "FigureRegion": obj({"x": N, "y": N, "width": N, "height": N},
        description="Canvas selection with normalized top-left coordinates used by the current figure editor. This is descriptive task input, not a new runtime geometry validator."),
    "FigureStyle": obj({"title": S, "xlabel": S, "ylabel": S, "color": JSON,
        "width": N, "layout_width_in": N, "height": N, "font_size": N,
        "span": S, "paper_layout": ref("PaperLayout"), "paper_template": S,
        "uncertainty": JSON, "legend": JSON},
        description="Physical dimensions are inches and font_size is points. Renderer-specific plot/calibration/statistical style settings remain extensible."),
    "MethodDiagramSpec": obj({"nodes": array(J), "edges": array(J), "storyboard": J,
        "story_context": J, "narrative_mode": S, "key_operation_id": S,
        "production_scene": J, "production_contract": J,
        "production_composition": J, "production_raster": J, "asset_data": J},
        description="Scientific method topology and editable production scene/composition. nodes/edges use supplied operation IDs/labels; production_scene contains native panels/objects/connections/annotations. Renderer-specific fields remain editable through Figure data."),
    "VisualSelection": obj({"version": I, "status": S, "candidate_id": S,
        "ranking": array(obj({"candidate_id": S, "score": N, "eligible": B, "required_repairs": array(JSON)})),
        "reviews": array(J), "placement": {"anyOf": [J, {"type": "null"}]},
        "selection_policy": S, "publication_gate_passed": B, "quality_status": S},
        description="Selected actual candidate, complete ranking and independent review results. Optional raster completion adds publication_gate_passed/quality_status. placement can be null."),
    "FigureData": obj({"kind": field("string", "Renderer type, including bar, line, heatmap, forest, calibration, method and image; actual supported settings depend on the renderer."),
        "run_ids": STRINGS, "source_run_ids": STRINGS, "metric": S,
        "purpose": S, "caption": S, "style": ref("FigureStyle"),
        "data": {**JSON, "description": "Actual imported/derived plot measurements, calibration prediction rows, or a MethodDiagramSpec. Never replace numerical data with generated pixels."},
        "image_prompt": S, "image_variants": array(JSON), "candidates": array(J),
        "narrative_mode": S, "code": S, "code_origin": S,
        "outputs": ref("FigureOutputs"),
        "visual_selection": {"anyOf": [ref("VisualSelection"), {"type": "null"}]},
        "visual_review_status": S, "svg_path": S, "png_path": S},
        description="Saved Figure data. Selection, scientific data, code, rendering style and artifact paths are separate editable fields. Ready status is on the enclosing resource; native editable render and whole-image raster output are different kinds."),
    "FigureRecord": obj({**RESOURCE_PROPERTIES, "data": ref("FigureData")}, tuple(RESOURCE_PROPERTIES)),
    "FigureTaskRequest": obj({"expected_revision": I, "request_id": field("string", nullable=True),
        "instruction": S, "region": {"anyOf": [ref("FigureRegion"), {"type": "null"}]},
        "run_ids": STRINGS, "provider_id": S, "visual_review_attempts": I,
        "image_candidate_run_id": S, "image_candidate_iteration": I},
        description="Revision work needs instruction. expected_revision, when supplied to /revise, compares the Figure resource revision. Image-candidate reuse refers to a real terminal figure run and retained candidate manifest. Other worker options remain extensible."),
    "PaperLayout": obj({"columns": field("string", enum=["single", "double"], default="single"),
        "significant_digits": field("integer", minimum=2, maximum=10, default=5),
        "scientific_notation": field("string", enum=["auto", "always", "never"], default="auto"),
        "table_font_pt": field("number", minimum=7, maximum=12, default=9),
        "min_font_pt": field("number", minimum=7, maximum=12, default=8),
        "max_table_rows": field("integer", minimum=4, maximum=60, default=18),
        "float_placement": field("string", enum=["auto", "top", "bottom", "page", "here"], default="auto"),
        "figure_span": field("string", enum=["auto", "column", "page"], default="auto"),
        "table_span": field("string", enum=["auto", "column", "page"], default="auto")},
        description="Existing normalized manuscript layout. table_font_pt must be >=min_font_pt. article allows one/two columns; iclr2027 requires single. Unknown layout keys are rejected by the layout worker/handler.", additional=False),
    "PaperData": obj({"source": S, "bibtex": S,
        "pdf_path": field("string", "Compiled PDF locator; null after failed or unavailable compilation.", nullable=True), "source_dir": S,
        "log": S, "errors": array(JSON), "bindings": array(J), "numeric_bindings": JSON,
        "source_run_ids": STRINGS, "figure_bindings": array(J),
        "manually_edited": B, "content_origin": S, "compiled_revision": I,
        "template": S, "layout": {"anyOf": [ref("PaperLayout"), {"type": "null"}]},
        "layout_plan": {"anyOf": [J, {"type": "null"}]},
        "layout_preflight": {"anyOf": [J, {"type": "null"}]},
        "writing_profile": J, "draft_reuse": J, "accepted_review_id": S, "stale_reason": S},
        description="Manuscript source, real compilation artifacts and evidence/figure bindings. Most generated fields are absent in a new draft. Cleared layout/layout_plan/layout_preflight can be null. A PDF is current only when compiled_revision equals the enclosing PaperDocument revision."),
    "PaperRecord": obj({**RESOURCE_PROPERTIES, "data": ref("PaperData")}, tuple(RESOURCE_PROPERTIES)),
    "ReviewData": obj({"kind": S, "origin": S, "summary": S,
        "paper_id": field("string", nullable=True), "paper_revision": field("integer", nullable=True),
        "run_id": S, "issues": array(JSON), "edits": array(J),
        "queued_paper_snapshot": {"anyOf": [J, {"type": "null"}]},
        "proposed_title": S, "proposed_data": ref("PaperData"), "diff": S},
        description="Task-specific review. A paper_generation review stores a complete proposed source/assets bundle and accepts indices:[0]; ordinary paper revisions store exact original/replacement spans. Statistical/scientific review findings remain extensible."),
    "ReviewRecord": obj({**RESOURCE_PROPERTIES, "data": ref("ReviewData")}, tuple(RESOURCE_PROPERTIES)),
})
SCHEMAS["PaperLayoutRequest"]["properties"]["layout"] = ref("PaperLayout")
SCHEMAS["PaperGenerationRequest"]["properties"].update({"title": S, "template": S,
    "layout": ref("PaperLayout"), "instructions": S, "figure_ids": STRINGS,
    "saved_response_run_id": S, "saved_response_path": S, "draft_revision_path": S})

SCHEMAS.update({
    "ResearchAnalysisSnapshot": obj({"project_revision": I, "fingerprint": S, "path": S, "sha256": S},
                                    ("project_revision", "fingerprint")),
    "ResearchAnalysisFocus": obj({"scope": S, "node_id": field("string", nullable=True),
        "branch_id": field("string", nullable=True), "node_ids": STRINGS, "run_ids": STRINGS,
        "upstream_node_ids": STRINGS, "downstream_node_ids": STRINGS},
        ("scope", "node_id", "branch_id", "node_ids", "run_ids", "upstream_node_ids", "downstream_node_ids")),
    "ResearchAnalysisContext": obj({"project": J, "graph": ref("GraphState"), "runs": array(J),
        "resources": J, "materials": array(J), "route_health": ref("RouteHealth"), "coverage": J,
        "focus": ref("ResearchAnalysisFocus"), "snapshot": ref("ResearchAnalysisSnapshot"),
        "provider": J, "counts": J, "execution_dependencies": array(J), "tool_executions": array(J),
        "events": array(J), "file_revisions": array(J)},
        ("project", "graph", "runs", "resources", "materials", "route_health", "coverage", "focus", "snapshot", "provider", "counts")),
    "ResearchAnalysisHistoryItem": obj({"id": S, "status": S, "created_at": S,
        "error": field("string", nullable=True), "scope": S, "node_id": field("string", nullable=True),
        "branch_id": field("string", nullable=True), "question": S, "project_revision": I, "report_available": B},
        ("id", "status", "created_at", "error", "scope", "node_id", "branch_id", "question", "project_revision", "report_available")),
    "ResearchAnalysisHistory": obj({"runs": array(ref("ResearchAnalysisHistoryItem"))}, ("runs",)),
    "ResearchAnalysisResult": obj({"run_id": S, "status": S, "error": field("string", nullable=True),
        "artifact_error": field("string", nullable=True), "report": {"anyOf": [J, {"type": "null"}]},
        "citations": array(J), "literature_searches": array(J), "snapshot": ref("ResearchAnalysisSnapshot"),
        "current": B, "stale_reasons": STRINGS, "artifacts": J, "model": field("string", nullable=True),
        "usage": J, "focus": ref("ResearchAnalysisFocus"), "question": S, "verification_scope": S},
        ("run_id", "status", "error", "artifact_error", "report", "citations", "literature_searches", "snapshot", "current", "stale_reasons", "artifacts")),
})


OPERATION_CONTRACTS: dict[tuple[str, str], dict[str, Any]] = {}


def operation(method: str, path: str, tag: str, summary: str, description: str,
              response: dict[str, Any] | None = None, *, body: str | None = None,
              media: tuple[str, ...] = (), errors: dict[int, str] | None = None,
              public: bool = False, owner_only: bool = False,
              parameters: dict[str, str] | None = None,
              actions: tuple[str, ...] = ()) -> None:
    OPERATION_CONTRACTS[(method.lower(), path)] = {
        "tag": tag, "summary": summary, "description": description,
        "response": response or J, "body": body, "media": media,
        "errors": errors or {}, "public": public, "owner_only": owner_only,
        "parameters": parameters or {}, "actions": actions,
    }


operation("post", "/api/auth/login", "Authentication", "Sign in as the owner",
    "Compare body.token with the configured/generated owner token and set the forest_owner HttpOnly, SameSite=Strict cookie for 14 days. HTTPS sets Secure. A supplied Origin must have the same hostname as the request. The token is never returned.",
    ref("Authenticated"), body="AuthLoginRequest", errors={401: "INVALID_TOKEN", 403: "ORIGIN_DENIED"}, public=True)
SCHEMAS["AuthLoginRequest"] = obj({"token": S}, ("token",))
operation("get", "/api/auth/status", "Authentication", "Read owner sign-in state",
    "Return whether the cookie or exact Authorization: Bearer <owner-token> matches. This public endpoint does not itself grant the trusted-loopback owner cookie.", ref("Authenticated"), public=True)
operation("get", "/api/health", "System", "Check API and database health",
    "Execute a database SELECT and return status, dialect and API version. This endpoint is public.", ref("Health"), public=True)
operation("get", "/api/projects", "Projects", "List projects",
    "Return a bare array ordered by updated_at descending. Optional archived filters the rows; limit is capped at 200 and offset is clamped to zero. Pagination is not wrapped in a total/cursor object.", array(ref("Project")))
operation("post", "/api/projects", "Projects", "Create a project",
    "Create a project and its main workspace/graph. When mode is omitted, use the saved default_mode preference, falling back to assisted if unset or invalid; an explicit mode takes precedence. Return the persisted project; successful creation uses HTTP 200.", ref("Project"))
operation("get", "/api/projects/{ident}", "Projects", "Read a project",
    "Return the project with graph_meta omitted. Read /graph for the editable graph state.", ref("Project"), errors={404: "NOT_FOUND"})
operation("patch", "/api/projects/{ident}", "Projects", "Edit project settings and goal",
    "Apply only name, description, goal, current_direction, archived, mode, budget and config. budget/config replace their whole values. Unknown top-level keys are ignored. Optional expected_revision compares the PROJECT graph revision; every successful patch increments it. Goal edits mark node contexts for refresh and emit project_changed.", ref("Project"), body="ProjectPatch", errors={404: "NOT_FOUND", 409: "REVISION_CONFLICT"})
operation("delete", "/api/projects/{ident}", "Projects", "Delete a project",
    "Cancel active local/container/remote execution, interrupt cost reservations, delete project database records and remove its workspace. Return the removed identifier.", ref("Deleted"), errors={404: "NOT_FOUND"})
operation("post", "/api/projects/{ident}/duplicate", "Projects", "Duplicate a project and its workspace",
    "Copy graph/resources/manuscripts/runs/files with remapped identifiers, excluding symlinks and private graph edit history. Active copied runs are marked interrupted; duplication does not replay experiments. Return the new project.", ref("Project"), errors={404: "NOT_FOUND"})
SCHEMAS["GoalContractVersion"] = obj({**IDENTITY, "project_id": S, "revision": I,
    "parent_version_id": field("string", nullable=True), "project_revision": I,
    "objective": S, "success_criteria": array(JSON), "contract": J, "reason": S,
    "changes": J, "evidence_run_ids": STRINGS, "affected_branch_ids": STRINGS},
    ("id", "project_id", "revision", "objective", "success_criteria", "contract", "reason"))
SCHEMAS["GoalContractHistory"] = obj({"current": ref("GoalContractVersion"),
    "versions": array(ref("GoalContractVersion")), "history_endpoint": S, "rule": S,
    "project_revision": I, "intervention": J}, ("current", "versions", "history_endpoint", "rule"))
SCHEMAS["GoalContractRevisionRequest"] = obj({"request_id": S, "expected_revision": I,
    "expected_goal_revision": I, "objective": S, "success_criteria": array(JSON),
    "contract": J, "reason": S, "evidence_run_ids": STRINGS, "affected_branch_ids": STRINGS})
SCHEMAS["EvaluationProtocolVersion"] = obj({**IDENTITY, "project_id": S, "protocol_id": S,
    "revision": I, "parent_version_id": field("string", nullable=True), "name": S,
    "definition": J, "reason": S, "feedback_run_ids": STRINGS,
    "classification": field("string", enum=["adaptive", "exploratory", "independent_confirmation"])},
    ("id", "project_id", "protocol_id", "revision", "definition", "classification", "feedback_run_ids"))
SCHEMAS["EvaluationProtocolRevisionRequest"] = obj({"request_id": S, "expected_revision": I,
    "expected_protocol_revision": I, "protocol_id": S, "name": S, "definition": J,
    "reason": S, "feedback_run_ids": STRINGS,
    "classification": field("string", enum=["adaptive", "exploratory", "independent_confirmation"])}, ("definition",))
operation("get", "/api/projects/{project_id}/goal-contract", "Research evolution", "Read the scientific goal history",
    "Return the current editable Goal Contract and every original version. Legacy projects initialize their original contract under the project writer lock. Historical results retain their saved goal conditions.",
    ref("GoalContractHistory"), errors={404: "NOT_FOUND"})
operation("post", "/api/projects/{project_id}/goal-contract/versions", "Research evolution", "Revise a scientific goal",
    "Revise objective, criteria or contract with a reason and existing same-project evidence/branch references. Revision guards are optional. request_id replays the original accepted result before stale revision checks; conflicting reuse is rejected. A completed controller can reopen; prior runs and resource accounting are preserved.",
    ref("GoalContractHistory"), body="GoalContractRevisionRequest",
    errors={404: "NOT_FOUND", 409: "REVISION_CONFLICT or REQUEST_ID_CONFLICT", 422: "Invalid goal, evidence, branch or request identity"})
operation("get", "/api/projects/{project_id}/evaluation-protocols", "Research evolution", "Read evaluation protocol versions",
    "Return a bare array of original evaluation versions ordered by creation time. Definitions and feedback history remain inspectable after revision.",
    array(ref("EvaluationProtocolVersion")), errors={404: "NOT_FOUND"})
operation("post", "/api/projects/{project_id}/evaluation-protocols", "Research evolution", "Revise an evaluation protocol",
    "Record a nonempty definition with optional protocol_id, expected revisions and request_id. A missing protocol_id creates a new explicit identity. Actual feedback use marks evaluation adaptive and persists across revisions of that scope. Declaring independent_confirmation is a scope label, not proof of execution or independent verification. Accepted replay returns the original version; changed intent conflicts.",
    ref("EvaluationProtocolVersion"), body="EvaluationProtocolRevisionRequest",
    errors={404: "NOT_FOUND", 409: "REVISION_CONFLICT or REQUEST_ID_CONFLICT", 422: "Invalid protocol, classification, evidence or request identity"})
operation("get", "/api/projects/{ident}/graph", "Graph", "Read the full editable graph",
    "Return nodes, typed edges, branches, project revision, goal, budget and additional public graph metadata. Exclude _history.", ref("GraphState"), errors={404: "NOT_FOUND"})
operation("post", "/api/projects/{ident}/graph/preview", "Graph", "Preview a graph command",
    "Require the current expected_revision. Return only GraphImpact, not a graph or receipt. Preview writes no graph/workspace changes. body.project_id, when set, must match the path project. Invalid node patch, dependency endpoint, fork node-ID list or split-part shapes are rejected before execution with invalid_command and the offending field.", ref("GraphImpact"), errors={400: "Graph command errors", 404: "NOT_FOUND or missing_node", 409: "revision_conflict or patch_conflict", 422: "CROSS_PROJECT or invalid_command"})
operation("post", "/api/projects/{ident}/graph/commands", "Graph", "Apply an editable graph command",
    "Apply one kernel command under the project writer transaction. The project request_id deduplicates saved command receipts before revision comparison; reuse it only for the same logical submission. Return public graph, impact, run_nodes and run_ids. run:true enqueues selected affected nodes. Preview conflicts/missing inputs must be inspected; undo restores graph content, not completed external effects. Invalid node patch, dependency endpoint, fork node-ID list or split-part shapes are rejected before execution with invalid_command and the offending field.", ref("GraphCommandResult"), errors={400: "Graph command errors", 404: "NOT_FOUND or missing_node", 409: "revision_conflict or patch_conflict", 422: "CROSS_PROJECT or invalid_command"})
operation("get", "/api/nodes/{ident}", "Graph", "Read a persisted path node",
    "Return a node with flattened kernel/runtime metadata. Its revision is distinct from the containing project's graph revision.", ref("ResearchNode"), errors={404: "NOT_FOUND"})
operation("patch", "/api/nodes/{ident}", "Graph", "Edit a path node",
    "Translate the body into an edit_node graph command with a server-created request ID. expected_revision is the PROJECT graph revision; omission reads the current revision. Return GraphCommandResult, not a node. Editable nested node objects use the kernel's deep merge.", ref("GraphCommandResult"), body="NodePatch", errors={400: "invalid_patch", 404: "NOT_FOUND", 409: "revision_conflict"})
operation("get", "/api/nodes/{ident}/context", "Graph", "Inspect a node context snapshot",
    "Build a role-aware preview from current graph/goal/budget, declared input materials and context overrides. Material previews can be truncated; retrievable_materials and omitted describe the full-source lookup opportunities. Capacity is a soft preview hint, not an execution result.", ref("ContextSnapshot"), errors={404: "NOT_FOUND"})
operation("post", "/api/nodes/{ident}/context/rebuild", "Graph", "Merge context overrides and rebuild",
    "When expected_revision is supplied, require it to match the current project revision. Merge the remaining extensible body into node.context_overrides, force needs_refresh:false, emit context_changed and return a fresh ContextSnapshot.", ref("ContextSnapshot"), body="JsonObject", errors={404: "NOT_FOUND", 409: "REVISION_CONFLICT"})
operation("post", "/api/nodes/{ident}/run", "Runs", "Enqueue a node execution scope",
    "Use RunRequest scope/config/request_id. A single scheduled run returns a Run directly; any other count returns {runs,run_ids}. Resolve this union before reading status/id. Provider-backed runs in imported projects with an unavailable archived provider require an explicit provider or workspace-default selection before scheduling.", {"anyOf": [ref("Run"), ref("SelectedRunCollection")]}, errors={400: "Scheduling/input errors", 404: "NOT_FOUND", 409: "Submission/revision conflicts", 422: "PROVIDER_SELECTION_REQUIRED"})
operation("post", "/api/branches/{ident}/run", "Runs", "Enqueue a branch execution path",
    "Choose the branch root (or first unarchived node) and enqueue its descendants. RunRequest.scope is accepted but the handler uses descendants. Return {runs}; there is no guaranteed run_ids field.", ref("RunCollection"), errors={400: "EMPTY_BRANCH or scheduling/input errors", 404: "NOT_FOUND"})
operation("get", "/api/branches/compare", "Graph", "Compare branch workspaces",
    "Require left and right branch IDs in the same project. Return the BranchWorkspace file/config comparison plus full left/right branch records and the combined run list.", obj({"left": ref("Branch"), "right": ref("Branch"), "runs": array(ref("Run"))}, ("left", "right", "runs")), errors={400: "CROSS_PROJECT", 404: "NOT_FOUND"})
operation("get", "/api/projects/{ident}/runs", "Runs", "List saved project runs",
    "Bare Run array, created_at descending; limit capped at 500. include_manuscript_evidence=true adds a readiness result based on each completed experiment/command/agent run's readable numeric metrics artifact.", array(ref("Run")), errors={404: "NOT_FOUND"}, parameters={"limit": "Maximum number of runs, capped at 500.", "offset": "Number of runs to skip.", "include_manuscript_evidence": "Attach manuscript evidence readiness and validation details."})
operation("get", "/api/runs/{ident}", "Runs", "Inspect a run and tool executions",
    "Return RunDetail. Remove config.provider_snapshot and attach tools ordered by created_at; other task-specific configuration keys remain as saved.", ref("RunDetail"), errors={404: "NOT_FOUND"})
operation("post", "/api/runs/{ident}/{action}", "Runs", "Control a saved run",
    "Actions: cancel, pause, resume, retry, skip, priority. retry requires a terminal run and returns a NEW Run. cancel is idempotent for terminal runs. pause accepts queued/waiting/budget_exhausted/running; resume accepts paused/waiting_input/waiting/budget_exhausted plus the documented legacy Agent context failure. skip accepts queued only. priority converts body.priority with int(). Resume can merge Agent budget and context_char_budget (integer >=1000). Return the persisted Run; process actions can fail with 409.", ref("Run"), body="RunActionRequest",
    errors={404: "NOT_FOUND or UNKNOWN_ACTION", 409: "RUN_ACTIVE, INVALID_RUN_STATE or PROCESS_UNAVAILABLE", 422: "INVALID_RUN_KIND, INVALID_CONTEXT_BUDGET or INVALID_AGENT_BUDGET"}, actions=("cancel", "pause", "resume", "retry", "skip", "priority"))
operation("get", "/api/runs/{ident}/output", "Runs", "Read a byte-cursor output page",
    "Read stdout.txt at max(offset,0), at most min(limit,1000000) bytes and return the next byte offset. A trailing multi-byte UTF-8 character cut by the page limit is held back so the next page re-reads it whole; when the page limit is smaller than the next character the read grows by at most three bytes so the cursor still advances; invalid bytes decode with replacement. Optional case-insensitive search filters lines AFTER the cursor advances. A missing output file returns empty text at the requested nonnegative offset.", ref("OutputPage"), errors={404: "NOT_FOUND"}, parameters={"offset": "Byte offset, not line number.", "limit": "Maximum bytes; default 100000, capped at 1000000.", "search": "Case-insensitive literal filter on the already-read lines."})
operation("get", "/api/projects/{ident}/events", "Events", "Subscribe to project change events",
    "Server-Sent Events, not JSON. Begin with event: connected and data: {}. Persisted events include id: sequence, event: event.type and JSON data: event.data. Reconnect with Last-Event-ID to replay later sequences in batches of 100. If retained history starts after the requested cursor or the cursor exceeds the latest saved event, emit cursor_reset with id: resume_after_sequence and JSON requested_after_sequence, oldest_available_sequence, latest_available_sequence and resume_after_sequence; reread authoritative REST state, then continue from that ID. With no saved events, an ahead cursor resets to checkpoint 0. Invalid/missing cursor starts at zero and can also require this reset. Send a comment heartbeat roughly once per second; the stream ends on disconnect. Headers Cache-Control:no-cache and X-Accel-Buffering:no.", S, media=("text/event-stream",), errors={404: "NOT_FOUND"})
operation("get", "/api/system", "System", "Read runtime and provider availability",
    "Return CPU/memory/disk/GPU and worker heartbeat views. GPU counters are strings from nvidia-smi; latex is an executable path or null. Provider availability checks model-list transport (15-second cache), distinct from the saved chat test. No scientific run is launched.", ref("SystemState"))
operation("get", "/api/settings", "Configuration", "Read application preferences",
    "Return the saved extensible preference object, or language=en, theme=light, retention_days=30 and default_mode=assisted when no settings row exists.", J)
operation("patch", "/api/settings", "Configuration", "Merge application preferences",
    "Shallow-merge arbitrary preference keys. No revision guard; nested values replace their previous values.", J, body="JsonObject")
operation("post", "/api/settings/cleanup", "Configuration", "Remove retained terminal-run artifacts",
    "Delete terminal runs whose finished_at is older than the days cutoff and their output directories; mark dependent material stale. project_id optionally restricts run deletion. clear_edit_history applies to every project's graph history even when project_id is supplied.", ref("CleanupResult"), body="CleanupRequest")
for collection, name, response, create_body, patch_body in (
        ("providers", "provider", "Provider", "ProviderCreate", "ProviderWrite"),
        ("hosts", "execution host", "Host", "HostCreate", "HostWrite"),
        ("agents", "Agent definition", "Agent", "AgentCreate", "AgentWrite")):
    operation("get", f"/api/{collection}", "Configuration", f"List {collection}",
        f"Return a bare array of persisted {name} records.", array(ref(response)))
    create_description = {"providers": "Accept ollama, openai-compatible or codex_cli kind and an HTTP(S) base_url. Codex CLI requires a localhost placeholder and the host's existing CLI login. Store a supplied api_key outside the returned provider; return has_key. Name/base_url/model are needed to create a usable record.",
        "hosts": "Store name, kind and extensible execution configuration. This does not connect to the host; test it separately.",
        "agents": "Store name, role, instructions, optional provider, tools, config and enabled. Explicit Agent configuration records customized tools so default upgrades do not overwrite them."}[collection]
    operation("post", f"/api/{collection}", "Configuration", f"Create an {name}", create_description,
        ref(response), body=create_body, errors={400: "INVALID_PROVIDER or INVALID_URL"} if collection == "providers" else {})
    patch_description = {"providers": "Replace supplied known fields; nonempty api_key updates the stored key. Empty/null api_key does not clear it. Mark status untested. This patch does not repeat create-time kind/URL validation.",
        "hosts": "Replace supplied name/kind/config and mark the connection untested.",
        "agents": "Replace supplied known fields; config replaces the object. Custom tools remain marked customized. No revision guard."}[collection]
    operation("patch", f"/api/{collection}/{{ident}}", "Configuration", f"Edit an {name}", patch_description,
        ref(response), body=patch_body, errors={404: "NOT_FOUND"})
operation("post", "/api/providers/{ident}/test", "Configuration", "Test a model with a real request",
    "Send a short JSON-status prompt through ModelClient. This DOES perform a model call and can consume tokens/cost. Persist connected/failed; status is the connection outcome, while request_status preserves the ModelClient request state when available. Other ModelClient result fields are extensible.", obj({"status": S, "request_status": field("string", nullable=True)}, ("status",)), errors={404: "NOT_FOUND", 502: "PROVIDER_CONNECTION_FAILED (retryable)"})
operation("get", "/api/providers/{ident}/references", "Configuration", "Inspect active provider references",
    "List project, node, Agent, reporting and global-default references, plus the count of preserved historical model requests.", obj({"references": array(JSON), "historical_request_count": I}, ("references", "historical_request_count")), errors={404: "NOT_FOUND"})
operation("post", "/api/providers/{ident}/retire", "Configuration", "Retire a model provider safely",
    "Replace or clear every active reference, remove the stored credential, and mark the provider retired while preserving historical model request records. replacement_provider_id may be null to clear references.", JSON, body="JsonObject", errors={404: "NOT_FOUND", 422: "INVALID_REPLACEMENT"})
operation("post", "/api/providers/{ident}/restore", "Configuration", "Restore a retired model provider",
    "Mark a retired provider untested so it can be edited and used again. Credentials removed during retirement must be configured again.", ref("Provider"), errors={404: "NOT_FOUND"})
operation("get", "/api/providers/{ident}/usage", "Configuration", "Read account-wide provider spending",
    "Read configured-rate estimates and reservations across all projects using this provider. requests contains the 100 most recent records; request_count is the full count. Missing limit/remaining/individual estimate values are null, not zero.", ref("ProviderUsage"), errors={404: "NOT_FOUND"})
operation("get", "/api/providers/{ident}/models", "Configuration", "Read the provider-native model list",
    "Return the provider response unchanged: Ollama normally uses {models:[...]}; OpenAI-compatible endpoints normally use {data:[...]}. Other JSON shapes remain provider-defined. This is a transport/list request, not a chat completion. Do not assume a normalized bare array.", JSON, errors={404: "NOT_FOUND"})
operation("post", "/api/hosts/{ident}/test", "Configuration", "Test an execution host connection",
    "Local success is {connected:true,platform}; remote success is the RemoteRunner test result. Persist connected or failed. No experiment is launched.", obj({"connected": B, "platform": S}), errors={404: "NOT_FOUND", 502: "HOST_CONNECTION_FAILED"})
operation("post", "/api/projects/{ident}/share", "Sharing", "Create a selected read-only share",
    "Store the body as share selection and return id, relative /share/<token> URL and token. node_ids determines visible nodes; description opts into the project description. Default selection exposes no nodes.", ref("ShareCreated"), body="ShareRequest", errors={404: "NOT_FOUND"})
operation("get", "/api/shares/{token}", "Sharing", "Read a selected share snapshot",
    "Public token URL. Return only selected node display/status fields and project name/optional description. It does not expose node instructions, files, graph edges, runs or credentials.", ref("ShareSnapshot"), errors={404: "NOT_FOUND"}, public=True)
operation("delete", "/api/shares/{token}", "Sharing", "Revoke a share token",
    "Require an already-valid owner cookie or bearer token even on trusted loopback; the public-prefix middleware shortcut does not authorize revocation. Disable the share and return revoked:true.", obj({"revoked": B}, ("revoked",)), errors={401: "UNAUTHORIZED", 404: "NOT_FOUND"}, owner_only=True)

operation("get", "/api/projects/{ident}/files", "Files", "List project workspace files",
    "Return a bounded page of eligible workspace entries in depth-first filesystem traversal order. Pass the opaque next_cursor to continue; has_more indicates that entries remain. Cursors are process-local and expire after 10 minutes; on expiration, restart from the first page. Skip symlinks and hidden path components except .forest-bases. Paths are relative to the project root and modified is a filesystem epoch time.", ref("FileList"), errors={404: "NOT_FOUND", 409: "FILE_CURSOR_EXPIRED", 422: "INVALID_FILE_CURSOR"})
operation("post", "/api/projects/{ident}/files/existing", "Files", "Preflight upload destinations",
    "Return existing files and directories for supplied relative paths in the project workspace. Accepts at most 1,000 paths per request so multi-file uploads can confirm replacements before publishing any selected file; directories cannot be replaced by file uploads.", ref("FileExistenceResult"), body="FileExistenceRequest", errors={403: "PATH_ESCAPE", 404: "NOT_FOUND", 422: "INVALID_FILE_PATHS"})
operation("get", "/api/projects/{ident}/file", "Files", "Read an editable text file",
    "Return text, file revision and origin from one snapshot serialized with owner uploads, edits, moves and deletes. Paths are normalized relative to the project root. Binary files need download/preview. The file revision is distinct from project, node and resource revisions. The default revision/origin for executor/import files is 0/executor_or_import.", ref("FileRead"), errors={400: "NOT_FILE", 403: "PATH_ESCAPE", 404: "NOT_FOUND or MISSING_ARTIFACT", 413: "FILE_TOO_LARGE (>10000000 bytes)", 415: "BINARY_FILE"})
operation("get", "/api/projects/{ident}/file/preview", "Files", "Read a bounded source-table excerpt",
    "CSV/TSV/Parquet preview: offset 0..10000000, limit 1..500. total describes the complete source table; at most 100 displayed columns, at most 1000 source columns, bounded cell lengths/nesting. Report truncation fields instead of implying the excerpt is complete. File size and Parquet decoded row group limit are 128 MiB. Source changes during read return retryable TABLE_CHANGED.", ref("TablePreview"), errors={400: "NOT_FILE", 403: "PATH_ESCAPE", 404: "NOT_FOUND or MISSING_ARTIFACT", 409: "TABLE_CHANGED", 413: "TABLE_TOO_LARGE, TABLE_TOO_WIDE or TABLE_ROW_GROUP_TOO_LARGE", 415: "UNSUPPORTED_TABLE_FORMAT", 422: "TABLE_PARSE_FAILED", 503: "PARQUET_ENGINE_UNAVAILABLE"})
operation("put", "/api/projects/{ident}/file", "Files", "Save an editable text file",
    "FileWrite contains path/content, optional FILE expected_revision and create_only (default false). With create_only:true, reject an occupied path with FILE_EXISTS while holding the publication lock, preserving its bytes and revision. Otherwise atomically replace file content, increment its revision, synchronize working manuscript files and mark consuming records stale. A first write to a missing path may use expected_revision=0; after deletion it advances that path's retained revision generation. Omitting expected_revision accepts the current revision.", ref("FileWritten"), errors={403: "PATH_ESCAPE", 404: "NOT_FOUND", 409: "FILE_EXISTS, REVISION_CONFLICT or WORKSPACE_PENDING"})
operation("delete", "/api/projects/{ident}/file", "Files", "Delete a file or directory",
    "Delete the selected file or directory recursively and mark consumers stale; removing the project root is rejected. Advance and retain file revision generations for removed paths so stale editor tokens conflict if a path is recreated. No expected_revision guard.", ref("Deleted"), errors={400: "INVALID_PATH", 403: "PATH_ESCAPE", 404: "NOT_FOUND or MISSING_ARTIFACT"})
operation("post", "/api/projects/{ident}/file/rename", "Files", "Move or rename a workspace path",
    "Use path and new_path, both within the project root. Create destination parents, reject an existing destination and mark consumers of the old path stale. Move file revision rows to the new paths and increment their revisions.", ref("FilePath"), body="FileRenameRequest", errors={403: "PATH_ESCAPE", 404: "NOT_FOUND or MISSING_ARTIFACT", 409: "FILE_EXISTS"})
operation("get", "/api/projects/{ident}/download", "Files", "Read a binary or text workspace file",
    "FileResponse with inline Content-Disposition and the file's basename. Content type depends on its filename; use the response bytes/blob, not JSON. No text-size limit is applied here.", media=("application/octet-stream", "application/pdf", "image/svg+xml", "image/png", "text/plain"), errors={400: "NOT_FILE", 403: "PATH_ESCAPE", 404: "NOT_FOUND or MISSING_ARTIFACT"})
operation("post", "/api/projects/{ident}/upload", "Files", "Upload a workspace file",
    "multipart/form-data field file; directory is a QUERY parameter, default uploads. Only the uploaded filename basename is used. Publish the file, increment its file revision (including identical-byte replacements), set user_import origin, mark consumers stale and return path/size/origin. Older editor expected_revision values then return REVISION_CONFLICT. Exceeding configured max_upload_mb preserves an existing destination and removes only the temporary file.", ref("FileUpload"), errors={403: "PATH_ESCAPE", 404: "NOT_FOUND", 409: "WORKSPACE_PENDING", 413: "UPLOAD_TOO_LARGE"})
operation("post", "/api/projects/{ident}/export", "Files", "Export a project backup ZIP",
    "ZIP contains forest-project.json and eligible workspace files. Optional paths selects included file prefixes, not a smaller graph/resource/run manifest. Export omits private graph history and machine fields while preserving the nonsecret project provider ID; credentials remain excluded. An included ordinary file above 100 MiB is rejected with a structured 413 EXPORT_FILE_TOO_LARGE error naming its relative path; symlinks and hidden paths are skipped. Do not treat the archive as an encrypted secret store.", body="ProjectExportRequest", media=("application/zip",), errors={404: "NOT_FOUND", 413: "EXPORT_FILE_TOO_LARGE"})
operation("post", "/api/projects/import", "Files", "Import a project backup ZIP",
    "multipart/form-data file with forest-project-v1 manifest. Validate graph and archive paths, reject symlinks/path traversal, more than `max_project_archive_members` ZIP entries (default `10000`, configured by `FOREST_MAX_PROJECT_ARCHIVE_MEMBERS`), and >500 MiB decompressed archives. The member count includes directories and entries outside `files/`; rejection returns `413 ARCHIVE_TOO_LARGE` before manifest access or project creation. The configured limit must be a positive integer. Create a new project with remapped graph/resource identifiers; preserve its selected provider when that ID exists in the current workspace. If the saved provider is unavailable, mark provider_selection_required and prevent provider-backed runs from silently falling back until the owner edits project settings. Active imported work is interrupted and execution is not replayed.", ref("Project"), errors={400: "INVALID_ARCHIVE, UNSAFE_ARCHIVE or graph validation errors", 413: "UPLOAD_TOO_LARGE or ARCHIVE_TOO_LARGE"})

operation("post", "/api/library/search", "Literature", "Search publication metadata",
    "query is required; source defaults crossref, limit defaults 8 and is capped at 30. Return {results:[provider-specific metadata]}. This does not import results into a project.", ref("LibrarySearchResult"), body="LibrarySearchRequest", errors={502: "LITERATURE_SEARCH_FAILED (retryable)"})
operation("post", "/api/library/import", "Literature", "Import a source identifier or metadata",
    "Supply project_id and an identifier (DOI/arXiv/public PDF) or paper metadata as an object or list. Save records and passages. Within a project, a matching publication reuses its saved record during concurrent imports. Returns one RecordItem or {records:[...]}. Source metadata remains extensible.", {"anyOf": [ref("RecordItem"), obj({"records": array(ref("RecordItem"))}, ("records",))]}, body="LibraryImportRequest", errors={400: "MISSING_IDENTIFIER", 404: "NOT_FOUND", 422: "IMPORT_FAILED"})
operation("get", "/api/library/export-bibtex", "Literature", "Export project bibliography text",
    "Require query project_id; return application/x-bibtex text with attachment references.bib. Do not parse as JSON.", media=("application/x-bibtex",))
operation("get", "/api/library/{ident}/passages", "Literature", "Read saved source passages",
    "Return a bare array of SourcePassage RecordItems when indexed passage rows exist, otherwise the source data.passages array. Raw fallback passage objects can have page/text/source_url rather than record IDs.", array({"anyOf": [ref("RecordItem"), J]}), errors={404: "NOT_FOUND"})
operation("post", "/api/library/compare", "Literature", "Compare selected source metadata",
    "ids selects sources in one project. Return papers with title/abstract/authors/year/reading_scope/notes and REPORTED label. This compares records, not verified experimental effects.", ref("LibraryCompareResult"), body="IdListRequest", errors={400: "CROSS_PROJECT", 404: "NOT_FOUND"})
operation("post", "/api/library/{ident}/reindex", "Literature", "Extract passages from a saved PDF",
    "Require data.pdf_path or fulltext_path within the project. Replace data.passages, set reading_scope=fulltext, increment the source resource revision and mark consuming material stale.", ref("RecordItem"), errors={403: "PATH_ESCAPE", 404: "NOT_FOUND or MISSING_ARTIFACT", 409: "FULLTEXT_UNAVAILABLE"})
operation("post", "/api/browser/read", "Literature", "Import a public web-page reading",
    "Require project_id and url; screenshot is optional (default false). Read the real page, save text/optional screenshot relative paths and a SourcePaper with a page passage. Return the saved RecordItem.", ref("RecordItem"), body="BrowserReadRequest", errors={404: "NOT_FOUND"})
for path, kind, description in (
    ("/api/research/ideas", "ideas", "Enqueue idea generation; scientific judgments and task-specific options are in the extensible request body."),
    ("/api/research/suggest-paths", "suggest_paths", "Enqueue path proposals. scope=node sends the selected node and its execution ancestors; scope=branch sends only the selected node's branch; scope=project sends the full graph. Node and branch scopes require node_id."),
    ("/api/reviews", "review", "Enqueue scientific review; POST /reviews does not directly create a Review resource record."),
    ("/api/analysis/run", "analysis", "Enqueue an analysis task over declared real inputs; output fields depend on the selected analysis type.")):
    operation("post", path, "Research", f"Enqueue {kind} work", description + " Return Run, not the final scientific output.", ref("Run"), body="QueuedTaskRequest", errors={400: "Scheduling/input errors", 404: "NOT_FOUND"})
operation("post", "/api/ideas/{ident}/adopt", "Research", "Adopt an idea into an editable execution path",
    "Create hypothesis plus configured experiment, or hypothesis/implementation/experiment nodes when implementation is needed. Optional expected_revision is the project graph revision. request_id is 1..120 characters and adoption-specific; replaying the same request_id for the same idea returns the current graph. An idea that already has nodes in the graph rejects a second adoption without that receipt with IDEA_ALREADY_ADOPTED (409). Undoing the adoption permits a new path. inputs accepts up to 50 explicit path/reference inputs in this project. config is the executable experiment configuration, not a dummy case. Return the updated GraphState as one graph edit/undo.", ref("GraphState"), body="IdeaAdoptRequest", errors={404: "NOT_FOUND", 409: "REVISION_CONFLICT, REQUEST_ID_CONFLICT, BRANCH_INACTIVE or IDEA_ALREADY_ADOPTED", 422: "INVALID_REQUEST_ID, INVALID_EXPERIMENT_CONFIG, INVALID_INPUTS, CROSS_PROJECT, MISSING_BRANCH, INVALID_TITLE or INVALID_INSTRUCTIONS"})
operation("post", "/api/theory/check", "Research", "Evaluate a restricted symbolic expression",
    "Require project_id/expression; variable defaults x. kind differentiate/solve/numeric selects an operation; any other/omitted kind simplifies. values supplies numeric substitutions. Restricted arithmetic grammar permits sin/cos/exp/log/sqrt/abs, bounded constants/powers and no arbitrary eval. Save a checked Derivation RecordItem, with result/latex and an explicit supplied-expression scope.", ref("RecordItem"), body="SymbolicCheckRequest", errors={400: "INVALID_EXPRESSION", 404: "NOT_FOUND", 422: "EXPRESSION_FAILED"})
operation("post", "/api/experiments/{ident}/launch", "Experiments", "Enqueue a saved experiment specification",
    "Merge saved experiment data with the optional body, bind experiment_id/revision and optional associated node, and return Run. Body fields are experiment configuration overrides.", ref("Run"), body="JsonObject", errors={400: "Scheduling/input errors", 404: "NOT_FOUND"})
operation("post", "/api/experiments/compare", "Experiments", "Compare declared experimental scopes",
    "Require unique nonempty run_ids from one project; objective is optional. Check dataset/version, split, evaluation, metric, statistical unit, fair scientific budget and configured fields. Noncompleted/missing declarations are unverified; conflicting scopes are incomparable. Matching declarations are not independent evidence verification. config.provider_snapshot is omitted from compared rows.", ref("ExperimentComparison"), body="ExperimentCompareRequest", errors={400: "CROSS_PROJECT", 404: "NOT_FOUND", 422: "INVALID_COMPARISON"})
operation("get", "/api/data/{ident}/rows", "Experiments", "Read run or analysis table rows",
    "ident resolves a TaskRun first, then an Analysis record (not a DatasetAsset ID). path defaults to run.output_path/predictions.csv or analysis.data.path. CSV/Parquet is fully read; filter is literal substring across cells and sort is an existing column. Return columns/selected rows/filtered total/origin. offset is clamped at zero; limit is capped at 1000. For bounded uploaded-table previews use /projects/{ident}/file/preview.", ref("DataRows"), errors={403: "PATH_ESCAPE", 404: "NOT_FOUND or MISSING_ARTIFACT"})
operation("post", "/api/figures/{ident}/render", "Figures", "Enqueue figure rendering",
    "Capture figure_id/revision/data plus body overrides and return a queued Run. Optional body request_id/configuration is passed to the figure worker; outputs are available only after the task completes and its resource is updated.", ref("Run"), body="FigureTaskRequest", errors={404: "NOT_FOUND"})
operation("post", "/api/figures/{ident}/revise", "Figures", "Enqueue a figure revision",
    "Optional expected_revision compares the Figure resource revision, default current. Pass instruction (singular), optional region and other figure task parameters in the extensible body. Return queued Run; it is not the revised image itself.", ref("Run"), body="FigureTaskRequest", errors={404: "NOT_FOUND", 409: "REVISION_CONFLICT"})
operation("get", "/api/figures/{ident}/export", "Figures", "Download a rendered figure output",
    "Query format defaults svg and indexes figure.data.outputs; available keys depend on the actual render. Return file bytes with filename figure.<format>. Missing output returns 409; do not infer that every figure supports every common format.", media=("image/svg+xml", "image/png", "application/pdf", "application/octet-stream"), errors={403: "PATH_ESCAPE", 404: "NOT_FOUND or MISSING_ARTIFACT", 409: "ARTIFACT_UNAVAILABLE"})
operation("get", "/api/papers/{ident}", "Manuscripts", "Read or initialize a project manuscript",
    "ident may be a PaperDocument ID or a project ID. When a project has no manuscript, this GET creates its initial draft. Return PaperRecord; clients must not treat the endpoint as a purely side-effect-free lookup.", ref("PaperRecord"), errors={404: "NOT_FOUND"})
operation("patch", "/api/papers/{ident}", "Manuscripts", "Save manuscript text and metadata",
    "Resolve paper/project ID, optionally compare the PaperDocument revision, shallow-merge body.data and set manual/needs_update state. Increment paper revision; source/bibtex changes clear previous layout checks and write the working source. Return PaperRecord.", ref("PaperRecord"), body="PaperPatch", errors={404: "NOT_FOUND", 409: "REVISION_CONFLICT"})
operation("post", "/api/papers/{ident}/generate", "Manuscripts", "Enqueue source-grounded manuscript generation",
    "Resolve paper/project ID. Require a nonempty run_ids list of completed experiment/command/agent runs in this project, each with a readable metrics file containing finite numeric measurements. Reject invalid evidence before enqueue and recheck it during generation. manuscript_type defaults full_paper. Other generation options are extensible. Return Run for generation/review workflow, not automatically applied manuscript text.", ref("Run"), body="PaperGenerationRequest", errors={404: "NOT_FOUND", 409: "GOAL_APPLICABILITY_REQUIRED", 422: "EVIDENCE_REQUIRED or INVALID_EVIDENCE"})
operation("post", "/api/papers/{ident}/figures", "Manuscripts", "Insert a reviewed figure at an exact text anchor",
    "Require CURRENT paper expected_revision, figure_id and anchor_text. Figure must be from this project and ready_for_review/available with a rendered PDF or image. Copy chosen output/supporting assets, insert caption/local label and binding, increment paper revision and invalidate layout checks. caption defaults figure caption/title; span defaults column. Return PaperRecord.", ref("PaperRecord"), body="PaperFigureRequest", errors={403: "PATH_ESCAPE", 404: "NOT_FOUND or MISSING_ARTIFACT", 409: "REVISION_CONFLICT or FIGURE_UNAVAILABLE", 422: "CROSS_PROJECT or INVALID_FIGURE_ANCHOR"})
operation("post", "/api/papers/{ident}/compile", "Manuscripts", "Enqueue compilation of the saved manuscript",
    "Resolve paper/project ID, optionally compare PaperDocument revision and capture exact source/bibtex/paper_revision. Return queued Run; this is not a compiled PDF response.", ref("Run"), body="PaperRevisionRequest", errors={404: "NOT_FOUND", 409: "REVISION_CONFLICT"})
operation("post", "/api/papers/{ident}/layout", "Manuscripts", "Enqueue manuscript layout changes",
    "Require current PaperDocument expected_revision. Merge body.layout with saved layout and normalize the chosen template; template defaults saved template, inferred iclr2027 source, or article. Return Run from the layout workflow.", ref("Run"), body="PaperLayoutRequest", errors={404: "NOT_FOUND", 409: "REVISION_CONFLICT", 422: "INVALID_LAYOUT"})
operation("post", "/api/papers/{ident}/check", "Manuscripts", "Inspect citations, references and literal writing issues",
    "Resolve existing paper/project ID without creating a draft. Return issues/status/coverage/writing_profile. Check citation keys, labels, selected literal defensive phrases, stale bindings and missing materials; this is not an exhaustive scientific semantic review.", ref("PaperCheck"), errors={404: "NOT_FOUND"})
operation("post", "/api/papers/{ident}/revise", "Manuscripts", "Enqueue a revision proposal",
    "Resolve paper/project ID, optionally compare PaperDocument revision, capture saved source/revision and pass the extensible body to paper_revise. Return Run. Saved manuscript edits are applied via review acceptance or explicit manual editing.", ref("Run"), body="PaperRevisionRequest", errors={404: "NOT_FOUND", 409: "REVISION_CONFLICT"})
operation("post", "/api/papers/{ident}/export", "Manuscripts", "Export current manuscript PDF or source ZIP",
    "Resolve an existing paper/project ID. Optional expected_revision compares PaperDocument revision. For a source ZIP, optional source and bibtex fields export the editor's current draft without saving it. format=pdf requires an existing compiled PDF of the CURRENT revision; source ZIP includes a PDF only when its source matches the saved revision. Source ZIP excludes .aux/.blg/.log artifacts.", body="PaperExportRequest", media=("application/zip", "application/pdf"), errors={403: "PATH_ESCAPE", 404: "NOT_FOUND or MISSING_ARTIFACT", 409: "REVISION_CONFLICT, PDF_UNAVAILABLE or STALE_PDF"})

RESOURCE_KINDS = ("library", "experiments", "datasets", "ideas", "theories", "claims", "figures", "analyses", "reviews")
for kind in RESOURCE_KINDS:
    record_type = {"figures": "FigureRecord", "reviews": "ReviewRecord"}.get(kind, "RecordItem")
    operation("get", f"/api/{kind}", "Resources", f"List {kind} records",
        "Require query project_id. Return a bare resource array ordered by updated_at descending; limit capped at 500, offset clamped at zero. Kind-specific data is extensible.", array(ref(record_type)), errors={404: "NOT_FOUND"})
    if kind != "reviews":
        operation("post", f"/api/{kind}", "Resources", f"Create a {kind} record",
            "ResourceCreate supplies project_id/title/data. Create the record and emit artifact_available. This creates editable metadata; it does not execute the related scientific task.", ref(record_type), errors={404: "NOT_FOUND"})
    operation("get", f"/api/{kind}/{{ident}}", "Resources", f"Read a {kind} record",
        "Return the persisted resource; data structure depends on the resource kind.", ref(record_type), errors={404: "NOT_FOUND"})
    operation("patch", f"/api/{kind}/{{ident}}", "Resources", f"Edit a {kind} record",
        "Optional expected_revision compares this RESOURCE revision, default current. Replace supplied title/status; shallow-merge data unless replace_data:true, which replaces the complete data object. Ignore other top-level keys. Increment resource revision and mark consuming materials stale." + (" For figures, changes to render inputs (kind/style/code/code_origin/run_ids/metric/data/caption/purpose/image_prompt/narrative_mode/image_variants/candidates) clear old render outputs and review selection and set needs_review; render and review the new revision before insertion." if kind=="figures" else ""), ref(record_type), body="ResourcePatch", errors={404: "NOT_FOUND", 409: "REVISION_CONFLICT"})
    operation("delete", f"/api/{kind}/{{ident}}", "Resources", f"Delete a {kind} record",
        "Delete the metadata record and mark consuming materials stale; this handler does not delete all associated workspace files. Return deleted identifier.", ref("Deleted"), errors={404: "NOT_FOUND"})

operation("post", "/api/projects/{ident}/research/{action}", "Research", "Control the research controller",
    "Actions start/pause/stop. start sets PLAN/running with optional branch_id/required_artifacts/autonomous and resumes previously paused runs. Legacy max_cycles is history only; goals, actual resource hard limits, cancellation and actionable blockers govern lifetime. A selected branch must belong to the project and have active status; restore pruned, archived, or disabled branches before starting. pause/stop process-control affected active runs after updating controller configuration. Return controller fields plus process_control_errors; HTTP 200 can contain individual process-control errors and must be inspected.", ref("ResearchControl"), body="ResearchControlRequest", errors={404: "NOT_FOUND, UNKNOWN_ACTION, or UNKNOWN_BRANCH", 409: "BRANCH_NOT_RUNNABLE"}, actions=("start", "pause", "stop"))
operation("post", "/api/research/proposals/{ident}/apply", "Research", "Apply selected proposed graph commands",
    "ident is a Hypothesis/proposal record. Optional expected_revision compares PROJECT graph revision. commands defaults saved proposal commands; indices defaults all. Apply sequential kernel commands and return graph/accepted_indices. Empty selection is rejected. Each selected command can have filesystem effects; client should preview and supply valid indices.", ref("ProposalApplied"), body="ProposalApplyRequest", errors={400: "EMPTY_SELECTION or graph command errors", 404: "NOT_FOUND", 409: "REVISION_CONFLICT or graph conflicts"})
operation("post", "/api/reviews/{ident}/apply", "Manuscripts", "Apply selected saved manuscript proposals",
    "Require current PAPER expected_revision and valid indices. The saved review.paper_revision must still match. Generation reviews accept indices:[0] for their complete saved bundle; ordinary edits require each original passage to occur exactly once. Return updated PaperRecord, increment revision and update working source.", ref("PaperRecord"), body="ReviewApplyRequest", errors={404: "NOT_FOUND", 409: "REVISION_CONFLICT or AMBIGUOUS_SPAN", 422: "INVALID_SELECTION"})
operation("post", "/api/projects/{ident}/runs/selected", "Runs", "Enqueue selected path nodes",
    "node_ids must belong to this project. Pass optional request_id/config to the scheduler and return {runs,run_ids}. Empty selection can return empty arrays; inspect scheduling failures as errors.", ref("SelectedRunCollection"), body="SelectedRunsRequest", errors={400: "CROSS_PROJECT or scheduling/input errors", 404: "NOT_FOUND"})
operation("patch", "/api/runs/{ident}/checkpoint", "Runs", "Edit a stopped debug checkpoint payload",
    "Require waiting_input run with resource.checkpoint. Set checkpoint.override to body.payload (omission sets null). Return checkpoint/status; this does not resume execution. Resume separately via the run action endpoint.", ref("CheckpointResult"), body="CheckpointRequest", errors={404: "NOT_FOUND", 409: "NO_CHECKPOINT"})

operation("get", "/api/projects/{ident}/research/route-health", "Research", "Inspect current route repetition signals",
    "Calculate actual repeated current-revision failures, unchanged repeated counterexample checks and planning without execution. Return editable diagnostic thresholds, signals and reviewed run IDs. New counterexample evidence is progress. This deterministic view does not judge semantic goal drift; enqueue the full route review for that.", ref("RouteHealth"), errors={404: "NOT_FOUND"})
operation("post", "/api/projects/{ident}/research/route-review", "Research", "Enqueue a full recorded-route review",
    "Enqueue research_route_review with extensible body/request_id and record its trigger/covered run IDs in controller.route_review. Return Run; the review output is saved after execution and does not directly verify experimental results.", ref("Run"), body="JsonObject", errors={404: "NOT_FOUND"})
operation("get", "/api/projects/{ident}/research/analysis/context", "Research", "Inspect saved project, node or path analysis context",
    "Return every saved research node/path/dependency, scientific run and linked resource, verification receipt, tool receipt and material locator. scope changes the focus while retaining the full project context. Material text is not loaded in this preview. The snapshot fingerprint includes execution/source changes beyond the graph revision. AI analysis history is separate from scientific source records. Provider availability is configuration readback, not a connection test.",
    ref("ResearchAnalysisContext"), errors={404: "NOT_FOUND, NODE_NOT_FOUND or BRANCH_NOT_FOUND", 422: "NODE_REQUIRED, BRANCH_REQUIRED or SCOPE_CONFLICT"})
operation("post", "/api/projects/{ident}/research/analysis", "Research", "Request source-bound read-only AI analysis",
    "Require a saved request_id and reviewed expected_revision; optional expected_fingerprint also rejects changed results/materials. Save the complete sanitized context snapshot, enqueue research_analysis through the existing provider/time/spending controls, and return Run with provider_snapshot/env omitted. The run has no node/branch execution binding and does not modify the graph/controller. Same request and payload replay the saved receipt before the revision guard; changed intent under the same ID conflicts. Literature retrieval uses selected Crossref/arXiv sources during execution. Only actual retrieved/library citation IDs are accepted. Advice remains a proposed Analysis record and cannot verify science or execute its suggestions.",
    ref("Run"), errors={404: "NOT_FOUND, NODE_NOT_FOUND or BRANCH_NOT_FOUND", 409: "REQUEST_ID_CONFLICT, REVISION_CONFLICT, CONTEXT_CHANGED or TIME_BUDGET_EXHAUSTED", 422: "PROVIDER_REQUIRED, invalid scope or request validation"})
operation("get", "/api/projects/{ident}/research/analysis", "Research", "Read AI analysis request history",
    "Return every analysis receipt in newest-first order, retaining full IDs, requested focus/question, saved project revision, actual status/error and report-file availability. Report availability alone does not establish completed execution or report integrity; read the result endpoint.",
    ref("ResearchAnalysisHistory"), errors={404: "NOT_FOUND"})
operation("get", "/api/runs/{ident}/research-analysis", "Research", "Read an actual AI analysis report and source freshness",
    "For a research_analysis run, return actual lifecycle/error, original snapshot identities, current/stale reasons, source citations/search receipts and artifact locators. A report is exposed only after completed execution and matching recorded report SHA256; missing/changed files yield report:null and artifact_error. Graph edits, later scientific run changes and source-file changes make the advice stale while retaining its history. Scientific verification and AI advice remain distinct.",
    ref("ResearchAnalysisResult"), errors={404: "NOT_FOUND", 422: "INVALID_RUN_KIND"})
operation("get", "/api/writing-policy", "Manuscripts", "Read scientific writing and revision policies",
    "Return extensible writing profile plus manuscript_contract and revision_contract strings.", obj({"manuscript_contract": S, "revision_contract": S}, ("manuscript_contract", "revision_contract")))
operation("get", "/api/publication-profile", "Research", "Read default publication requirements",
    "Return default PublicationProfile, textual instructions and available role names. Profile targets are editable design requirements, not completed measurements.", obj({"profile": ref("PublicationProfile"), "instructions": S, "roles": STRINGS}, ("profile", "instructions", "roles")))
operation("get", "/api/projects/{ident}/publication", "Research", "Audit current publication delivery",
    "Assess current saved manifest/design/evidence/compiled manuscript and source-bound verification. Return profile-dependent coverage and gaps; ready does not mean a conference has accepted the paper.", ref("PublicationAudit"), errors={404: "NOT_FOUND"})
operation("patch", "/api/projects/{ident}/publication", "Research", "Merge the editable publication profile",
    "Merge body into the saved profile, normalize full_submission/operational requirements, increment PROJECT revision and emit project_changed. No expected_revision guard. Return normalized profile/revision.", obj({"profile": ref("PublicationProfile"), "revision": I}, ("profile", "revision")), body="PublicationProfilePatch", errors={404: "NOT_FOUND", 422: "INVALID_PUBLICATION_PROFILE"})
operation("get", "/api/projects/{ident}/research", "Research", "Read controller state and measured trial comparisons",
    "Default returns objective, declared comparable trial groups, current controller configuration, active runs, controller-origin decisions and run counts. Optional/required verification policies control comparison eligibility. Trial fields are absent until applicable; null value is unmeasured, not zero. overview=true returns bounded recorded lifecycle and counts with empty trials and explicit coverage, without filesystem/verification replay; comparisons require the default explicit inspection.", ref("ResearchState"), errors={404: "NOT_FOUND"})
operation("get", "/api/projects/{ident}/usage", "Research", "Read project spending and separate provider limits",
    "Return project configured-rate costs/reservations/time and active Agent limits. Provider rows explicitly describe all_projects scope; do not subtract them from the project allowance a second time. Missing spending limits return null remaining_usd.", ref("ProjectUsage"), errors={404: "NOT_FOUND"})
operation("get", "/api/runs/{ident}/lineage", "Runs", "Inspect run dependencies and current file freshness",
    "Traverse same-project run dependencies. Available rows include config (without provider_snapshot/env), metrics, files and comparisons between saved workspace and current branch working files. Missing/cross-project dependencies are {id,missing:true}. current describes node revision/result flags, not semantic correctness.", ref("RunLineage"), errors={404: "NOT_FOUND"})
operation("get", "/api/runs/{ident}/session", "Runs", "Read the saved Agent session",
    "Return {run_id,status,session}. Missing agent_session.json means session:null. Full session is extensible scientific state and excludes cumulative resource accounting. For a cancelled run, session status follows the authoritative cancelled run. summary=true includes lifecycle, round, recovery, goal_contract, status/wait_for/updated_at/budget_reason/reconciliation_reason and transcript_count; selected fields can be null for legacy checkpoints. summary usage reads totals/active_seconds/completeness explicitly from the separate matching agent_usage.json resource ledger, or is null when unavailable. Invalid or unreadable ledgers return usage:null with usage_error diagnostics, never zero consumption. waiting with wait_for.type=work_round is automatic checkpoint continuation under the same Run ID and resource ledger.", ref("RunSession"), errors={404: "NOT_FOUND"})
operation("patch", "/api/runs/{ident}/configuration", "Runs", "Merge editable execution configuration",
    "Shallow-merge task parameters; reject service-managed provider_snapshot/execution_attempt/resolved_inputs/project_goal/repository-source/verification-binding/result/analysis-snapshot identities. For research_analysis only budget, agent_budget, timeout and priority are editable; changed question/scope/literature/provider requires a new analysis intent. Validate supplied repository for eligible run kinds. Scientific changes after execution mark verification unverified. Return config with provider_snapshot/env omitted. No revision guard; unknown editable keys remain extensible for other kinds.", ref("RunConfiguration"), body="JsonObject", errors={404: "NOT_FOUND", 409: "ANALYSIS_INTENT_BOUND", 422: "INVALID_CONFIGURATION or INVALID_REPOSITORY"})
operation("get", "/api/projects/{ident}/graph/page", "Graph", "Read a node page with incident edges",
    "Filter optional branch_id and case-insensitive title query; order created_at/id. Clamp offset>=0 and limit 1..500 for selection, but echo the original offset. edges includes any edge incident to a displayed node, so its other endpoint may be outside this page. next_offset is null on a short page; a full final page may require one empty follow-up.", ref("GraphPage"), errors={404: "NOT_FOUND"})
operation("post", "/api/projects/{ident}/protocol/validate", "Experiments", "Validate an editable experimental protocol",
    "Require the base protocol judgment/unit/selection/threshold fields. Confirmatory phase requires test_used_for_selection:false. A full_submission project additionally requires complete dataset/baseline/ablation/seed/study/accepted-source design and rationale fields. Return structurally_checked protocol/profile/scope; no experiment executes.", ref("ProtocolResult"), body="ProtocolRequest", errors={404: "NOT_FOUND", 422: "INVALID_PROTOCOL"})
operation("post", "/api/projects/{ident}/writing/review", "Manuscripts", "Propose minimal literal writing edits",
    "body.source is text (default empty). Return character offsets and one-based line numbers. replacement:null means an evidence judgment is required; never apply it as empty replacement. This endpoint does not edit a saved paper.", ref("WritingReview"), body="WritingReviewRequest", errors={404: "NOT_FOUND"})
SCHEMAS["WritingReviewRequest"] = obj({"source": S})
operation("post", "/api/projects/{ident}/graph/batch", "Graph", "Apply a batch of supported graph edits",
    "Require current PROJECT expected_revision and between 1 and 200 commands per request. Supports add_node/edit_node/add_dependency/remove_dependency/prune_branch/restore_branch/set_main_branch; one edit_node may update selected node positions atomically with params.positions keyed by target ID. Workspace fork/merge and other operations require separate graph commands. Optional request_id shares project command-receipt scope. One undo snapshot is saved; result counts and final graph revision are returned, not the graph itself.", ref("GraphBatchResult"), body="GraphBatchRequest", errors={404: "NOT_FOUND", 409: "REVISION_CONFLICT", 422: "INVALID_COMMANDS or UNSUPPORTED_BATCH_OPERATION"})
operation("post", "/api/projects/{ident}/statistics/review", "Experiments", "Enqueue an independent statistics review",
    "Merge the extensible body, force review_scope=statistics and return queued Run. Optional request_id controls scheduler reuse; review findings arrive through saved run/resource output.", ref("Run"), body="JsonObject", errors={404: "NOT_FOUND"})
operation("post", "/api/projects/{ident}/statistics/paired", "Experiments", "Enqueue paired real-data inference",
    "Require project-relative existing path and unit_column/baseline_column/candidate_column. Worker analysis_type=paired uses independent unit clusters; optional direction lower/higher, confidence, bootstrap_samples, seed and meaningful_effect configure the analysis. When required policy or an explicit verifier guard applies, path must select the uniquely admitted source-bound checked artifact or its verified graph input copy. Optional unguarded requests remain available. Return Run, not a confidence interval immediately.", ref("Run"), body="PairedStatisticsRequest", errors={403: "PATH_ESCAPE", 404: "NOT_FOUND or MISSING_ARTIFACT", 422: "MISSING_FIELD"})
operation("patch", "/api/projects/{ident}/objective", "Research", "Replace the editable comparison objective",
    "Trim metric; blank/omitted metric clears the objective. direction defaults min and must be min/max. comparison_fields defaults dataset/protocol_version. Increment PROJECT revision and return the objective object only. No expected_revision guard.", J, body="ObjectiveRequest", errors={404: "NOT_FOUND", 422: "INVALID_OBJECTIVE"})
operation("post", "/api/projects/{ident}/repositories/clone", "Repositories", "Validate and enqueue a Git checkout",
    "Only repository/request_id top-level fields are accepted. Normalize the input; all Git/network work runs in a worker. A reused request_id must identify the identical normalized repository submission or return 409. Return queued Run.", ref("Run"), body="RepositoryCloneRequest", errors={404: "NOT_FOUND", 409: "REQUEST_ID_CONFLICT", 422: "INVALID_REPOSITORY, INVALID_REQUEST_ID or repository field errors"})
operation("get", "/api/runs/{ident}/repository", "Repositories", "Read validated checkout provenance",
    "Return repository:null before a source manifest exists. A present manifest must be a bounded valid object with a normalized source URL/ref/directory/transport and a Git commit identity. Expose only those provenance fields, never a credential profile/key.", ref("RunRepository"), errors={404: "NOT_FOUND", 422: "INVALID_REPOSITORY_MANIFEST"})
operation("get", "/api/runs/{ident}/diagnostics", "Runs", "Inspect saved command and process receipts",
    "Return bounded/redacted saved local/container/remote receipts, log size/update time and source provenance. Only valid local process identities can add process_alive; remote/container status is the last saved observation and remote_state_is_live=false. This endpoint does not start Git/Docker/SSH/model operations.", ref("RunDiagnostics"), errors={404: "NOT_FOUND"})
operation("get", "/api/runs/{ident}/verification", "Verification", "Read current source-bound verification",
    "For a verification run, return its current checks/verdict; for a producer run, return current linked verifier verdicts, accepted check paths and numerical scope. Configuration/artifact/node changes can invalidate prior acceptance. Status is independent of execution completion.", ref("Verification"), errors={404: "NOT_FOUND"})
operation("post", "/api/verification/run", "Verification", "Enqueue a declared independent verification",
    "Require string project_id. Optional node_id must be a verification-kind node in that project. Exclude project_id/node_id/request_id from task config and reject reserved service-owned identity/verdict fields. Reused request_id must have the same verifier/node/contract. Return Run with provider_snapshot/env/remote config entries omitted.", ref("Run"), body="VerificationRequest", errors={404: "NOT_FOUND", 409: "REQUEST_ID_CONFLICT", 422: "INVALID_VERIFICATION or INVALID_CONFIGURATION"})


# Reporting DTOs are generated directly from the validated Pydantic models.
operation('get','/api/projects/{ident}/progress/artifacts/{file_id}','Progress','Open retained generation-bound artifact bytes',
    'Owner-only bounded artifact. Epoch, generation, scope ownership and current bytes must match. Changed/deleted sources return an error. Does not read unbounded binary content.',
    {'type':'string','format':'binary'},errors={404:'Source unavailable',409:'Source changed or not retained'})
for method, suffix, summary, response in [
    ('get','progress','Read a coherent deterministic progress projection','ProjectProgressSnapshot'),
    ('get','progress/scopes','List source scopes and coverage','ScopePage'),
    ('get','progress/sources','Page observed files in one authorized scope','FilePage'),
    ('post','progress/source','Resolve a generation-bound source without scientific side effects','SourceView'),
    ('get','reporter-settings','Read opt-in narration settings and reporting usage','ReporterSettingsView'),
    ('patch','reporter-settings','Save version-checked narration settings','ReporterSettingsView'),
    ('post','reports/refresh','Explicitly enqueue or coalesce optional narration','ReporterJob'),
    ('get','reports','Page persistent reporting jobs','ReportPage'),
    ('get','reports/latest','Read the latest report job','ReporterJob'),
]:
    schema = {'anyOf':[ref(response),{'type':'null'}]} if suffix=='reports/latest' else ref(response)
    operation(method,'/api/projects/{ident}/'+suffix,'Progress',summary,
        'Owner-scoped reporting domain. Reads do not invoke a model, verifier or remote host. Source offsets are UTF-8 bytes (end-exclusive); lines are one-based. Inventory and retained current text are bounded, with explicit coverage. Refresh may consume the authorized provider allowance; duplicate requests coalesce. Narration selects service-rendered facts and cannot assign status or scientific acceptance.',
        schema, errors={404:'Project/source unavailable',409:'Revision conflict, rebuilding or narration disabled'})


for method,path,summary,response,body in [
    ('get','/api/projects/{ident}/interventions','Read durable intervention receipts',array(ref('InterventionView')),None),
    ('get','/api/interventions/{ident}','Read the accepted intent and per-target application receipts',ref('InterventionView'),None),
    ('get','/api/runs/{ident}/applicability','Read goal applicability independently of computational verification',ref('ApplicabilityView'),None),
    ('get','/api/runs/{ident}/acceptance','Read source-bound scientific handoff acceptance for the configured use',ref('AcceptanceView'),None),
    ('post','/api/runs/{ident}/applicability/decisions','Record an evidence-use decision bound to the reviewed goal',ref('InterventionView'),'ApplicabilityDecision'),
    ('post','/api/projects/{ident}/instructions','Send an explicit scoped owner instruction',ref('InterventionView'),'InstructionRequest'),
    ('get','/api/projects/{ident}/decisions','Read durable human action decisions',array(ref('DecisionView')),None),
    ('post','/api/decisions/{ident}/answer','Accept, edit or reject the reviewed action',ref('DecisionView'),'DecisionAnswer'),
    ('post','/api/research/proposals/{ident}/reject','Reject a proposal with planning feedback',ref('ResourceRecord'),'RejectionRequest'),
]:
    description='Owner-only durable intent. Reviewed revisions and exact action identities are enforced; a stale action is marked stale and discarded rather than approved. Accepted is distinct from applied. Stop effects are reconciled outside graph transactions; uncertain effects remain visible and retryable. Instruction delivery is confirmed by actual prepared request and provider response receipts, not model agreement. Human decisions persist across worker restart and use the ordinary budget-checked resume path.'
    if path=='/api/projects/{ident}/interventions':
        description += ' The before timestamp cursor accepts before_id to disambiguate records sharing a timestamp; clients can page through all retained history.'
    operation(method,path,'Interventions',summary,
        description,
        response,body=body,errors={404:'Target unavailable',409:'Reviewed revision, request identity, decision or run state conflict'})

WEBSOCKETS = [{
    "path": "/ws/projects/{ident}/terminal",
    "description": "Reconnectable owner PTY in the project's main branch workspace, separate from worker experiment jobs.",
    "authentication": "Valid forest_owner cookie OR client peer 127.0.0.1/::1. Bearer headers are not read by this handler. A supplied Origin must have a trusted hostname or match the request hostname.",
    "client_messages": [
        {"type": "text", "description": "Raw UTF-8 shell input."},
        {"type": "text", "example": '{"resize":{"rows":40,"cols":120}}', "description": "Exact leading {\"resize\": selects JSON resize control; rows/cols are clamped to 1..1000."},
        {"type": "text", "example": '{"action":"close"}', "description": "Exact string closes the shared terminal session."},
    ],
    "server_messages": "Raw UTF-8 terminal output; reconnect replays at most the last 131072 characters.",
    "close_codes": {"1008": "Origin/authentication denied or project absent.", "1013": "32 terminal sessions already occupied without an idle session to evict."},
    "lifecycle": "One session per project/main-branch key; a disconnected idle session expires after approximately 1800 seconds. Output is not an SSE or JSON message envelope.",
}]


def enhance_openapi(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of generated OpenAPI enriched with accurate wire docs.

    Typed request schemas generated by FastAPI are preserved byte-for-byte.
    Untyped JSON object bodies gain properties without adding runtime validation.
    This function neither imports handlers nor mutates routes.
    """
    result = deepcopy(schema)
    components = result.setdefault("components", {})
    components.setdefault("schemas", {}).update(deepcopy(SCHEMAS))
    components.setdefault("securitySchemes", {}).update({
        "ForestOwnerBearer": {"type": "http", "scheme": "bearer",
            "description": "The FOREST owner token. Use Authorization: Bearer <token>. Not a model API key."},
        "ForestOwnerCookie": {"type": "apiKey", "in": "cookie", "name": "forest_owner",
            "description": "Owner session set by /api/auth/login or trusted-loopback access to a protected API endpoint."},
    })
    tags = {contract["tag"] for contract in OPERATION_CONTRACTS.values()}
    result["tags"] = [{"name": name} for name in sorted(tags)]
    result["x-forest-websockets"] = deepcopy(WEBSOCKETS)
    result["info"]["description"] = (
        "FOREST's current same-origin HTTP API. Bare-array lists, endpoint-specific envelopes, "
        "project/node/resource/file revision domains, queued task results and binary/SSE transports "
        "are explicit in each operation. Untyped configuration/scientific data remains extensible. "
        "These schemas describe the existing handlers; they do not introduce new runtime validation. "
        "Protected /api operations require owner bearer/cookie authentication except trusted loopback "
        "peers on trusted hostnames, which receive a session cookie. See docs/API_REFERENCE.md."
    )
    for (method, path), contract in OPERATION_CONTRACTS.items():
        current = result.get("paths", {}).get(path, {}).get(method)
        if current is None:
            continue
        current.update(summary=contract["summary"], description=contract["description"],
                       tags=[contract["tag"]])
        # A conditional localhost bypass cannot be fully expressed by standard
        # OpenAPI security. The empty alternative is limited by this extension.
        current["security"] = ([] if contract["public"] else
            [{"ForestOwnerBearer": []}, {"ForestOwnerCookie": []}] +
            ([] if contract["owner_only"] else [{}]))
        if not contract["public"] and not contract["owner_only"]:
            current["x-forest-local-owner-access"] = {
                "client_hosts": ["127.0.0.1", "::1", "testclient"],
                "request_hostnames": ["localhost", "127.0.0.1", "::1", "testserver"],
                "effect": "No prior token required only when both conditions match; response sets owner cookie.",
            }
        body_name = contract["body"]
        if body_name and "requestBody" in current:
            content = current["requestBody"].get("content", {})
            if "application/json" in content:
                original = content["application/json"].get("schema", {})
                # Existing typed GraphCommand/ProjectCreate/RunRequest/FileWrite/
                # ResourceCreate schemas remain the source of runtime field rules.
                if "$ref" not in original:
                    content["application/json"]["schema"] = ref(body_name)
        responses = current.setdefault("responses", {})
        media = contract["media"]
        if media:
            responses["200"] = {"description": "Successful stream or file response.",
                "content": {mime: {"schema": {"type": "string", **({} if mime == "text/event-stream" else {"format": "binary"})}}
                            for mime in media}}
        else:
            responses["200"] = {"description": "Successful response; queued work may still be incomplete.",
                "content": {"application/json": {"schema": deepcopy(contract["response"])}}}
        known_errors = {500: "INTERNAL_ERROR: unexpected handler, transport or persistence failure.", **contract["errors"]}
        if not contract["public"]:
            known_errors.setdefault(401, "UNAUTHORIZED: owner authentication required.")
            if not contract["owner_only"]:
                known_errors.setdefault(403, "ORIGIN_DENIED: nonmatching nontrusted Origin denied; PATH_ESCAPE where workspace paths are accepted.")
        # Every operation with parsed query/path/body fields can yield framework
        # 422. Explicit domain 422 failures use the same broad error envelope.
        if "422" in responses or "requestBody" in current or current.get("parameters"):
            known_errors.setdefault(422, "Request validation or documented domain field validation failed.")
        for status, description in known_errors.items():
            responses[str(status)] = {"description": description,
                "content": {"application/json": {"schema": ref("ApiError")}}}
        for parameter in current.get("parameters", []):
            name = parameter.get("name")
            if name in contract["parameters"]:
                parameter["description"] = contract["parameters"][name]
            if name == "action" and contract["actions"]:
                parameter.setdefault("schema", {})["enum"] = list(contract["actions"])
        if path.endswith("/events"):
            parameters = current.setdefault("parameters", [])
            if not any(item.get("name", "").lower() == "last-event-id" and item.get("in") == "header" for item in parameters):
                parameters.append({"name": "Last-Event-ID", "in": "header", "required": False,
                    "schema": {"type": "string"}, "description": "Last delivered integer sequence, encoded as a string. Missing/invalid values replay from zero; if retained events begin later or the cursor is ahead of the latest event, the stream emits cursor_reset and a resynchronization cursor."})
    return result


REFERENCE_INTRODUCTION = """# FOREST HTTP API reference

The dashboard and CLI use the same `/api` endpoints and saved project state.
Use this reference to replace frontend screens without duplicating research
logic, execution state or manuscript data in the UI. The live contract is
available at `/openapi.json`, with interactive documentation at `/docs` and
`/redoc`. Checked-in contracts and the TypeScript client are described in
[API contract maintenance](API_CONTRACTS.md) and the
[frontend integration guide](FRONTEND_API_GUIDE.md).

## Transport and authentication

- The default local API origin is `http://127.0.0.1:8000`. A development frontend
  proxies `/api` and `/ws` to the API; production serves the built frontend from
  the same origin. There is no version prefix or universal `{data: ...}` wrapper.
- JSON operations use `Content-Type: application/json`. Upload and project import
  use `multipart/form-data` with a `file` field; let the browser set its boundary.
- Every existing successful HTTP operation returns `200`, including creation and
  task submission. A returned run is a queued/persisted execution record, not its
  eventual result. Lists are bare arrays unless the individual endpoint says
  otherwise. Binary exports and SSE must not be read with `response.json()`.
- Protected `/api` requests accept `Authorization: Bearer <owner-token>` or the
  `forest_owner` cookie. The owner token is a FOREST credential, not a model API
  key. `/api/auth/login` sets an HttpOnly, SameSite=Strict cookie for 14 days;
  HTTPS adds Secure. API requests from a real loopback peer to a trusted hostname
  can establish the owner cookie without an existing token. Both the peer and
  hostname conditions must match; the OpenAPI empty security alternative is
  restricted by `x-forest-local-owner-access`.
- `/api/health`, `/api/auth/login`, `/api/auth/status` and GET share snapshots are
  public. DELETE share revocation still requires an existing valid owner cookie
  or bearer token. Public share snapshots expose only the selected display/status
  fields. Public-prefix middleware behavior does not authorize writes.
- On protected endpoints a supplied Origin must have a trusted hostname or match
  the request hostname; otherwise the API returns `ORIGIN_DENIED`. This is the
  existing same-origin policy, not a configurable permissive CORS layer.
- Provider/host/Agent configuration and scientific `data` fields are extensible
  objects. A component's documented properties are the known fields, not a list
  of every possible experimental payload. Persisted status/type strings remain
  open unless the existing validator explicitly defines an enum.

## Revision domains and editable paths

| Operation | `expected_revision` compares | When omitted |
| --- | --- | --- |
| Graph preview/commands | Project graph revision | Required by GraphCommand |
| Graph batch | Project graph revision | Rejected by revision comparison |
| Project PATCH, node PATCH, idea adoption, proposal application | Project graph revision | Use the current revision |
| Generic resource PATCH, figure revision | That resource's revision | Use the current revision |
| Manuscript PATCH, compile, revise, export | PaperDocument revision | Use the current revision |
| Manuscript figure insertion, layout, review application | PaperDocument revision | Rejected by revision comparison |
| File PUT | That path's FileRevision | Accept current file revision |

Node revision identifies the version used by a run; it is not the graph edit
guard. Keep project, node, resource, paper and file revision values separate in
frontend state. On `409`, refresh the applicable record, preserve unsaved edits,
and let the user merge/reapply them. Do not silently overwrite the refreshed
state or replay a rejected semantic edit against an unrelated revision.

Graph commands support `add_node`, `edit_node`, `delete_node`, `add_dependency`,
`remove_dependency`, `fork_branch`, `clone_subtree`, `insert_before`,
`insert_after`, `reparent_subtree`, `merge_branches`, `split_node`, `group_nodes`,
`prune_branch`, `restore_branch`, `set_main_branch`, `apply_instruction_patch`,
`undo` and `redo`. `targets` contains existing node IDs; branch operations can
select `params.branch_id`. `params` is operation-specific. Preview returns impact
only; command application returns the updated graph, impact and scheduling IDs.
Inspect `conflicts`, `input_bindings_required`, `actions`, `rerun_nodes` and
`refresh_nodes` before presenting an edit as complete. `run:true` requests
scheduling, not a synchronous experiment.

| Graph operation | Target / main `params` fields | Resulting editable operation |
| --- | --- | --- |
| `add_node` | Node fields directly or `node:{...}`, optional branch_id/parent_id | Add a node and optional execution parent |
| `edit_node` | targets: node IDs; fields directly or `patch:{...}`; optional stop_current_run; optional `positions:{node_id:{x,y}}` | Deep-merge editable fields, apply per-node layout positions atomically, analyze impact, optionally cancel the current run |
| `apply_instruction_patch` | targets: node IDs; instructions or old_text/new_text | Replace instructions or one exact matching passage |
| `delete_node` | targets; strategy subtree/reconnect/visual_only when descendants exist | Remove a subtree, reconnect execution reachability, or remove visual grouping |
| `add_dependency`, `remove_dependency` | source/target or two targets; relation; add supports input_mapping | Add/remove the specified typed edge |
| `fork_branch`, `clone_subtree` | origin target; optional name/node_ids/include_descendants/destination_branch_id/copy_policy | Clone selected nodes into a new or existing branch; create workspace only for a new branch |
| `insert_before`, `insert_after` | pivot target; node fields in node or selected direct fields | Insert a step and reconnect execution edges |
| `reparent_subtree` | pivot target; parent_id or new_parent_id; move_to_branch/input_map | Move execution parent and explicitly repair/review affected input bindings |
| `merge_branches` | left/right or left_branch_id/right_branch_id; name/resolution/dependencies | Compare and explicitly merge source workspaces/configuration into a new branch |
| `split_node` | original target; parts array of at least two node objects | Create a sequence, archive the original and flag dependent output rebinding |
| `group_nodes` | selected targets; optional group_id/title/collapsed | Create an editable display group without an experiment |
| `prune_branch`, `restore_branch`, `set_main_branch` | params.branch_id or a node target's branch | Change branch scheduling/active/main state |
| `undo`, `redo` | Current expected_revision; no target needed | Restore an editable graph history snapshot |

Fork copy_policy supports code/data/results strategies copy/reference/exclude
(booleans map to copy/exclude) and max_copy_bytes, default 100 MiB. Default file
policy copies code, references data and excludes results. Saved node result
handling also depends on the requested result copy policy; copied/reference
results do not establish fresh execution. Merge resolution.files maps paths to
left/right/base/delete or `{content:"..."}`; resolution.config is left/right or
an explicit configuration object. Missing merge choices return conflicts. A
missing editable fork base uses two-way comparison instead of inventing a base.

`depends_on` and `consumes` are execution relations. Typed evidence/citation/history
relations have different propagation duties. Kernel input bindings can create
execution dependencies even when no explicit canvas edge is drawn; the frontend
must preserve `inputs`, verification configuration and edge `input_mapping`.
Graph/branch/history edits remain editable. Undo changes graph content; it does
not undo completed processes, filesystem writes or external effects.

API file/artifact views use paths relative to their PROJECT root. Explicit node
`InputReference.path` instead uses its source BRANCH workspace unless
`project_scope:true`; `branch_id`/`node_id` select that actual source. Its
`destination` is relative to the receiving run workspace. Preserve these path
coordinates when moving a node or designing a file picker. `Branch.workspace` is
a project-relative directory; task `output_path` points to the run folder. Download
paths must be URL-encoded, not joined to a frontend filesystem directory.
`/projects/{id}/file` edits UTF-8 text; `/download` handles images/PDFs/binary files.
Working `paper/paper.tex` and `paper/references.bib` edits synchronize the saved
manuscript and invalidate its previous layout/compilation state.

## Submission IDs and state refresh

Graph command receipts are deduplicated by `(project_id, request_id)` before a
new command is applied. Batch commands use the same receipt namespace. Generic
scheduler submissions also use project request IDs. Keep an ID for transport
retries of the same submission and use a fresh ID for changed intent. Some
handlers compare payloads on replay (repository, verifier and layout submission);
do not assume every generic task checks payload equality. Idea adoption uses an
adoption receipt prefix. Node PATCH creates its own request ID and is not a
client-controlled idempotent graph-command replay.

Subscribe to project events after loading authoritative state. A notification is
a refresh/invalidation signal, not a complete replacement for the project/run/
resource. Re-fetch the affected records. Poll or reconnect for runs even if a
frontend component is remounted; never infer completion from a closed terminal.
Null timestamps, PID, exit code, error, session or spending limit mean no current
value. An unmeasured/null trial value is not a zero score.

## Errors

Application errors normally have:

```json
{"detail":{"code":"REVISION_CONFLICT","message":"Project changed","retryable":false,"suggestion":""}}
```

`suggestion` and context fields are optional. Graph kernel codes use lower case
(for example `revision_conflict`) and can include `expected_revision` and
`current_revision` directly in `detail`. Request validation uses FastAPI's array:

```json
{"detail":[{"loc":["body","expected_revision"],"msg":"Field required","type":"missing"}]}
```

Read either shape. Use HTTP status plus `detail.code` when it exists; do not
assume every `422` is a Pydantic array or every error code is upper case.
`retryable:true` describes a returned error, not blanket permission to repeat
state-changing requests with new IDs. Endpoint sections list their known domain
statuses. Unexpected handler/persistence/transport failures can return structured
`500 INTERNAL_ERROR`; documentation does not claim to replace runtime failures
with successful values.

## Scientific records and workbench fields

Generic resources are `library`, `experiments`, `datasets`, `ideas`, `theories`,
`claims`, `figures`, `analyses` and `reviews`. They share id/project_id/title/
revision/status/data/created_at/updated_at. Generic PATCH **shallow-merges** data;
an included nested value replaces that value. Generic POST creates metadata,
not execution. POST `/reviews` instead queues review work; there is no generic
review-create operation. Deleting a resource does not remove all its artifact
files. POST scientific actions return runs whose outputs create/update records.

### Figures and statistical presentation

`FigureRecord.data` separates `kind`, `run_ids`/`source_run_ids`, `metric`,
`purpose`, `caption`, `style`, actual `data`, `code`/`code_origin`, `outputs` and
`visual_selection`/`visual_review_status`. Preserve the complete object during
editing. The display style/code is not the statistical source data. `data` can
be a method topology/scene, per-object calibration predictions, or identified
statistical observations. The renderer chooses the applicable source and keeps
the result paths. The `outputs` object is a map of available export keys to
project-relative files; absent outputs are not downloadable formats.

A method `data` object retains supplied `nodes`/`edges`, optional `storyboard`/
`story_context`, and editable `production_scene`/`production_composition` plus
their source contracts and assets. Native scene objects include panels, objects,
connections and annotations. Pixel generation is a conceptual illustration path;
statistical plots/tables stay grounded in actual observations. Rendering does
not turn a schematic into measured evidence.

Figure revisions use `instruction` (singular), optional normalized
`region:{x,y,width,height}`, and the Figure resource revision. A selection made
against an older render must be refreshed before applying its revision request.
Image candidate reuse refers to a saved terminal figure run and real iteration
manifest; it is not a new invented image list. `visual_selection` can be null;
when present it identifies a candidate, complete ranking, reviewers and optional
placement. A whole-image raster completion can report `quality_status` and
`publication_gate_passed`; do not hide these behind a generic success label.

The statistical workflow preserves dataset/method/metric/condition/checkpoint
and independent seed/unit identities. Matching experiment declarations do not
establish source-bound verification. `comparison_eligible`, verification status
and numerical coverage stay distinct in trial displays. Paired inference is
computed by a queued analysis task over explicit real-data columns. Field units,
uncertainty definitions and publication table/curve output details are in
[statistical presentation](STATISTICAL_PRESENTATION.md).

### Manuscripts, layout and review proposals

The paper API accepts a PaperDocument ID or a project ID. GET can initialize the
first draft. `PaperRecord.data.source` and `bibtex` are the editable source;
`bindings`, `numeric_bindings`, `source_run_ids` and `figure_bindings` connect
scientific content to real evidence/artifacts. `pdf_path`/`source_dir` point to
actual files. `compiled_revision` must equal the enclosing paper revision for
current PDF export. Editing source invalidates `layout_plan`/`layout_preflight`,
which can be null. A previous PDF is not automatically the current draft.

The existing layout fields are columns, significant_digits, scientific_notation,
table_font_pt, min_font_pt, max_table_rows, float_placement, figure_span and
table_span. The component below gives their current values/bounds; the normalized
layout rejects unknown keys. `article` supports one/two columns; `iclr2027` uses
the template's single-column layout. Preserve layout decisions, visual placement
anchors and local references rather than regenerating a paper for UI changes.

Generated work can be automatically installed only when its saved paper
snapshot still matches and the manuscript has not been manually edited.
Otherwise a `ReviewRecord` with `kind:paper_generation` stores the entire
proposed bundle, diff and queued snapshot. Accepting it requires `indices:[0]`
and the current PaperDocument revision. Ordinary revision reviews contain exact
original/replacement spans; each original must occur once. A writing proposal
with `replacement:null` needs an evidence judgment and must not be applied as
deletion. Scientific review, literal writing review and compilation are distinct
operations.

### Repository and verification inputs

Repository input accepts **only** `url`, `ref`, `directory`, `transport` and
`credential`. It supports uncredentialed github.com HTTPS or git SSH URLs.
`ref` defaults HEAD, directory defaults source and transport defaults auto.
`credential` is a named worker profile, never an inline token, SSH key or host
credential path. SSH preparation requires a worker-side identity/known_hosts
profile. Submodule/depth/LFS fields are not accepted by the existing input.
Clone submission queues worker work; `/runs/{id}/repository` reports null until
the real provenance manifest exists. See [repository inputs](GITHUB_REPOSITORIES.md).

Verification submission accepts project_id, optional verification node/request
ID and the declared verification task configuration. Source identity/binding/
contract/verdict fields are service-owned. Producer verification views aggregate
current linked verifiers; changed files/configuration/node versions can revoke
prior acceptance. A completed run with `verification_status:unverified` is not
an accepted independent handoff. See [research workflow](RESEARCH_WORKFLOW.md).

### Protocol and publication fields

Base protocol validation requires research_question, hypothesis, baseline,
candidate, metric, statistical_unit, split_policy, selection_policy, decision_rule,
argumentative_duty, meaningful_effect and phase. Confirmatory phase requires
test_used_for_selection:false. A full_submission design also requires target_venue,
dataset_scale_rationale, baseline_selection_rationale, replicate_justification,
fair_compute_policy, leakage_checks, uncertainty_analysis, multiplicity_policy,
confirmation_policy, datasets/baselines/ablations with distinct IDs, distinct
integer seeds, studies covering all four argumentative duties and distinct
accepted_source_ids. The current editable profile determines coverage counts.
Structural validation does not run experiments or verify literature/claims.

Publication audit preserves ready/status/gaps, count/target/remaining-cell data
when available, and current independent verification. operational profile may
have no submission matrix. Route-health signals cover actual recorded repetition;
semantic scientific direction belongs to the queued full-history route review.
These views should stay independently inspectable after the UI is redesigned.

## Events and terminal transport

SSE endpoint `/api/projects/{id}/events` returns `text/event-stream`. It starts
with `event: connected` and `data: {}`. Persisted events use an integer sequence
as `id`, their saved type as the named event, and JSON `data`. Replay later
sequences with the `Last-Event-ID` header; invalid strings fall back to zero.
If the requested cursor predates retained history or is ahead of the latest
saved event, the stream emits a synthetic
`cursor_reset` control event with `id` set to `resume_after_sequence` and JSON
fields `requested_after_sequence`, `oldest_available_sequence`,
`latest_available_sequence`, and `resume_after_sequence`. Reread authoritative
REST state after this event; the stream then continues after the resume cursor.
With no saved events, an ahead cursor resets to checkpoint `0`.
Heartbeats are SSE comments, not
data events. Use EventSource with same-origin
cookies or a fetch-based streaming client capable of setting authentication and
cursor headers. Do not put owner tokens into query strings.

Terminal WebSocket `/ws/projects/{id}/terminal` authenticates with an owner
**cookie** or a real loopback peer; its handler does not read bearer headers.
A supplied Origin must be trusted or have the request hostname. Incoming/outgoing
messages are raw UTF-8 PTY text. Exact controls are
`{\"resize\":{\"rows\":40,\"cols\":120}}` (rows/cols clamped 1..1000) and
`{\"action\":\"close\"}`. The resize dispatcher uses the exact leading
`{\"resize\":`; preserve compact serialization. A reconnect can replay up to
131072 output characters. Session key is project/main branch; idle disconnected
sessions expire after approximately 1800 seconds. Close 1008 means auth/origin/
project rejection; 1013 means all 32 sessions are occupied. Terminal output is
separate from run stdout, process diagnostics and authoritative saved run state.

## Complete HTTP operation reference

Request schemas below retain the existing FastAPI/Pydantic rules for typed
bodies. Named schemas for untyped dictionaries describe intended successful
payloads and known keys; they do not add runtime validators. Optional-body
defaults and path/query defaults come from the actual registered route.
"""


def _schema_label(schema: dict[str, Any]) -> str:
    """Compact readable type name for generated reference tables."""
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in schema or "oneOf" in schema:
        return " or ".join(_schema_label(item) for item in schema.get("anyOf", schema.get("oneOf", [])))
    if "allOf" in schema:
        return " & ".join(_schema_label(item) for item in schema["allOf"])
    kind = schema.get("type")
    if isinstance(kind, list):
        return " or ".join(kind)
    if kind == "array":
        return _schema_label(schema.get("items", {})) + "[]"
    return str(kind or "any JSON")


def _table_text(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_api_reference(schema: dict[str, Any]) -> str:
    """Render the full reference from an enriched schema and this registry."""
    lines = [REFERENCE_INTRODUCTION.rstrip(), ""]
    groups = sorted({item["tag"] for item in OPERATION_CONTRACTS.values()})
    for group in groups:
        lines.extend([f"### {group}", ""])
        for (method, path), contract in sorted(OPERATION_CONTRACTS.items(), key=lambda item: (item[0][1], item[0][0])):
            if contract["tag"] != group:
                continue
            actual = schema.get("paths", {}).get(path, {}).get(method, {})
            lines.extend([f"#### `{method.upper()} {path}`", "", contract["description"], ""])
            parameters = actual.get("parameters", [])
            if parameters:
                lines.extend(["| Parameter | Location | Type | Required | Default / description |",
                              "| --- | --- | --- | --- | --- |"])
                for parameter in parameters:
                    declared = parameter.get("schema", {})
                    notes = parameter.get("description", declared.get("description", ""))
                    if "default" in declared:
                        notes = f"Default `{declared['default']}`. " + notes
                    bounds = [f"{key}={declared[key]}" for key in ("minimum", "maximum", "minLength", "maxLength") if key in declared]
                    if bounds:
                        notes += " " + ", ".join(bounds) + "."
                    if "enum" in declared:
                        notes += " Values: " + ", ".join(map(str, declared["enum"])) + "."
                    lines.append("| " + " | ".join(map(_table_text, (
                        f"`{parameter['name']}`", parameter["in"], _schema_label(declared),
                        "yes" if parameter.get("required") else "no", notes))) + " |")
                lines.append("")
            request = actual.get("requestBody")
            if request:
                request_types = [f"`{mime}`: `{_schema_label(value.get('schema', {}))}`" for mime, value in request.get("content", {}).items()]
                lines.extend(["Body: " + "; ".join(request_types) + ("; required." if request.get("required") else "; optional."), ""])
            response = actual.get("responses", {}).get("200", {}).get("content", {})
            response_types = [f"`{mime}`: `{_schema_label(value.get('schema', {}))}`" for mime, value in response.items()]
            lines.extend(["Success `200`: " + "; ".join(response_types) + ".", ""])
            if contract["errors"]:
                lines.extend(["Known domain failures: " + "; ".join(f"`{status}` {_table_text(reason)}" for status, reason in sorted(contract["errors"].items())) + ".", ""])
    lines.extend(["## Request and response field definitions", "",
        "Fields marked optional may be absent. A nullable field may be present with",
        "`null`; absence and null are separate states. Extensible objects admit",
        "additional scientific/configuration keys. Timestamps are ordinary strings",
        "because saved receipts do not all use one strict RFC 3339 format.", ""])
    for name, definition in sorted(schema.get("components", {}).get("schemas", {}).items()):
        lines.extend([f"### `{name}`", ""])
        if definition.get("description"):
            lines.extend([definition["description"], ""])
        properties = definition.get("properties")
        if not properties:
            lines.extend(["Type: `" + _schema_label(definition) + "`.", ""])
            continue
        required = set(definition.get("required", []))
        lines.extend(["| Field | Type | Required | Details |", "| --- | --- | --- | --- |"])
        for key, value in properties.items():
            details = value.get("description", "")
            if "default" in value:
                details += f" Default: `{value['default']}`."
            if "enum" in value:
                details += " Values: " + ", ".join(f"`{choice}`" for choice in value["enum"]) + "."
            bounds = [f"{bound}={value[bound]}" for bound in ("minimum", "maximum", "minLength", "maxLength") if bound in value]
            if bounds:
                details += " " + ", ".join(bounds) + "."
            lines.append("| " + " | ".join(map(_table_text, (f"`{key}`", _schema_label(value),
                "yes" if key in required else "no", details))) + " |")
        if definition.get("additionalProperties") is False:
            lines.extend(["", "Additional properties: rejected by the existing typed/schema-specific validator."])
        elif definition.get("additionalProperties", True):
            lines.extend(["", "Additional properties: extensible JSON."])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
