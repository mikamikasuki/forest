"""Submission scope is a default, never inferred from a small compute budget."""
from copy import deepcopy
import json
from pathlib import PurePath

FULL_SUBMISSION = {
    'id': 'full_submission', 'version': 1, 'manuscript_type': 'full_paper',
    'target': 'A complete research article for a leading journal or conference',
    'accepted_papers': 15, 'datasets': 3, 'baselines': 5, 'seeds': 5,
    'ablations': 4,
    'experiment_duties': ['effectiveness', 'mechanism', 'scenario_value', 'alternative_explanation'],
    'evidence_manifest': 'submission_evidence.json',
    'scale_policy': 'Use the larger of these starting targets and the median scale of relevant accepted papers; justify field-specific designs in an editable protocol.',
    'budget_policy': 'Keep the full deliverable incomplete when resources run out; preserve real progress and the remaining experiment matrix. Never downgrade to a demo.',
    'writing_policy': 'Direct evidence-calibrated prose; no defensive writing or unsolicited AI-writing statements; preserve core negative results, required comparators, primary outcomes and metric definitions.',
    'architecture_figure_policy': 'Scientific problem -> specific mechanism zoom -> grounded consequence/testable expectation; explicit individual method_only requests retain only their supplied topology.',
}


def publication_profile(config=None):
    """Resolve an explicit editable profile while keeping absent defaults full."""
    config = config or {}
    value = config.get('publication_profile', config.get('publication', {}))
    if isinstance(value, str):
        value = {'id': value}
    if not isinstance(value, dict):
        raise ValueError('publication_profile must be an object or profile ID')
    if value.get('id') == 'operational':
        return {'id': 'operational', 'version': 1, 'target': 'Explicitly requested operational task'}
    if value.get('id', 'full_submission') != 'full_submission':
        raise ValueError('Choose full_submission or an explicitly requested operational profile')
    result = {**deepcopy(FULL_SUBMISSION), **deepcopy(value), 'id': 'full_submission'}
    for field in ('accepted_papers', 'datasets', 'baselines', 'seeds', 'ablations'):
        n = result[field]
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(field + ' must be a positive integer')
    # The reference corpus remains substantial even for field-specific designs.
    if result['accepted_papers'] < 12:
        raise ValueError('A full submission requires at least twelve accepted comparable papers')
    if result['manuscript_type']!='full_paper':
        raise ValueError('A full_submission profile requires full_paper; a demo or excerpt is a different explicit task')
    if not isinstance(result['experiment_duties'],list) or any(not isinstance(d,str) for d in result['experiment_duties']) or set(result['experiment_duties'])!=set(FULL_SUBMISSION['experiment_duties']):
        raise ValueError('A full submission requires all four scientific experiment duties')
    manifest=result['evidence_manifest']
    if not isinstance(manifest,str) or not manifest or PurePath(manifest).is_absolute() or '..' in PurePath(manifest).parts:
        raise ValueError('evidence_manifest must be an editable project-relative path')
    return result


def publication_instructions(profile=None):
    profile = profile or publication_profile()
    if profile['id'] == 'operational':
        return 'The user explicitly selected operational scope. Report the actual task outcome; do not describe it as a submission-ready research paper.'
    from research.figures.narrative import STORY_CONTRACT
    return '''FULL SUBMISSION CONTRACT (applies to every role)
Default to a complete leading-journal/conference submission, with its actual venue length, real data volume and experimental workload. A pilot is only a preliminary discriminating step and cannot satisfy the delivery goal. Use at least twelve, normally fifteen, closely related accepted papers from official proceedings or journal records. Import and read their full text; arXiv alone does not establish acceptance. Record source IDs and exact passages/pages supporting experimental scale, main comparison coverage, ablations, statistics, introduction figures, analysis figures, table design and placement. Infer the necessary workload from these papers, not from the cheapest available execution or an arbitrary page count. Reproduce strong recent and standard baselines under comparable data, tuning and compute conditions. Design substantial real benchmark coverage, multiple independent seeds/replicates, mechanism ablations, stress/subgroup/scenario studies, leakage controls and separately evaluated confirmation. Match or exceed relevant accepted-paper scale; explain justified domain-specific protocols with primary-source evidence.
Execute purpose-bound handoffs using acceptance_contract with purpose raw_data (explicit artifact_paths checked by data_contract), comparison (complete matching declarations and independently checked numerical evidence), and major_claim (a distinct actual research_phase=confirmation run, independently checked under comparable conditions). List the completed handoff run IDs in submission_evidence.json acceptance_handoffs. Final delivery requires current Goal applicability and current source-bound numerical acceptance even if exploratory task verification was optional. These checks establish their declared mechanical scope; independent scientific review remains required.
Construct real executable dependent agents: Literature Scout -> Benchmark Curator -> Experiment Designer -> Baseline Reproducer/Engineer -> Experimenter -> Analyst -> Figure Designer -> visual selection/layout reviewers -> Writer -> independent Submission Reviewer. These are substantive roles, not empty graph nodes. Run experiment batches and independently recompute intervals/effects from raw observations. A chart with a few model-authored values is not evidence. Introductory diagrams explain the actual mechanism; empirical plots preserve comparator coverage, uncertainty and sample identity. Compare multiple visual candidates and plan their article anchors before rendering. Inspect the compiled paper at final column width.
Keep submission_evidence.json (or the explicitly configured evidence_manifest) editable. Include target_venue, central_claim, candidate, statistical_unit, comparable_papers, datasets, baselines, seeds, ablations, studies, measured_cells, analysis_run_ids, reviewer_run_ids, statistics and manuscript. Each comparable paper records source_id, acceptance_url, acceptance_kind (official_proceedings or official_decision), acceptance_passage_id for a decision, relevance, experiment_design, figures, tables and adopted_changes as finding/passages records. Its scale records datasets, baselines, seeds and independent_units; unreported values are null with source-backed reasons in scale_unknowns. Never guess unreported peer counts. Mark each genuinely comparable dimension true in scale_comparability and state unit_type for independent_units. Derive a numeric scale median only when at least three peers report comparable units; benchmark-task counts, evaluation observations, training samples and retrieval-library inventory are different units. Main-paper pages and words are read from actual full text/PDF, not self-reported length claims. Datasets record id, source, split_policy and actual evaluation sample_size; baselines record id, source and fair_budget; ablations record id and mechanism. Main effectiveness studies cover every declared benchmark/baseline/seed. Other studies record explicit datasets, methods, seeds and scope_rationale, and target only scientific questions and components relevant to that study. Each ablation belongs to a concrete mechanism study. Each measured cell records dataset, method, seed, study, run_id and raw_path relative to that completed run's workspace; raw CSV/JSON rows retain dataset, method, seed, study, unit_id/sample_id and numeric observations. List at least two distinct completed analysis runs in analysis_run_ids, separate from the measured runs. Statistics record effect, ci_low, ci_high, analysis_run_id and optional JSON pointers for their actual recomputed metrics (defaults /improvement, /ci_low and /ci_high). manuscript records source_path, pdf_path, layout_report, placement_report and compile_run_id as project-relative paths/actual run ID. Figure/table blocks have argumentative_duty and actual paragraph anchors. The substantive Results comparison table, mechanism diagram near Intro/Method, and Results analysis figure fulfill their scientific roles; no arbitrary universal image count establishes quality. Completed executions and current artifacts, rather than model declarations, satisfy the delivery audit.
After each measured batch and manuscript draft, compare actual evidence/design/layout with the accepted corpus, record exact gaps and applied changes, schedule repairs and rerun affected experiments. Keep cycling until the delivery audit and independent review support completion. The independent submission_review.json records verdict ready|revise, unresolved_issues, reviewed_run_ids covering every measured/analysis/final compile run, and evidence consisting of actual project-relative file paths or source_id/passage_id pairs. A review predating current manuscript/measurement changes must be refreshed. Resource exhaustion preserves an unfinished full submission with its remaining work; never manufacture observations, shrink to a demo or report acceptance as guaranteed. Do not freeze research parameters or use SHA/content hashes. All files and decisions remain editable. No unsolicited AI-writing statement belongs in a manuscript; required venue declarations follow the user's explicit instructions. Write direct, precise evidence-calibrated prose and preserve evidence that changes the central conclusion.
Press-Release style cannot remove a core negative result, decisive contrary comparison, required comparator or relevant uncertainty. Preserve declared primary outcomes, metric definitions, units and fair budgets; explicitly justify exploratory changes and rerun affected comparisons before drawing revised conclusions. Never change a metric or comparator to manufacture a win. All paid design, image, review and repair requests share the owner's authorized outer allowance through the saved provider and project ledger. Do not copy providers or split projects to evade it; uncertain requests retain their reserved bounds. An empty queue cannot replace a current delivery audit and independent review.
Resolved editable publication profile: ''' + json.dumps(profile, ensure_ascii=False) + '\n' + STORY_CONTRACT
