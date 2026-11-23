"""Purpose-specific evidence admission using service-bound actual checks."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from services.api.common import get
from services.api.db import TaskRun, Project


class AcceptanceContract(BaseModel):
    model_config = ConfigDict(extra='forbid')
    purpose: Literal['exploratory', 'raw_data', 'comparison', 'major_claim']
    artifact_paths: list[str] = Field(default_factory=list)
    confirmation_run_ids: list[str] = Field(default_factory=list)
    comparison_fields: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def complete(self):
        from research.validation.handoff import _relative, _names
        self.artifact_paths = [_relative(path, 'Acceptance artifact') for path in self.artifact_paths]
        for name in ('artifact_paths', 'confirmation_run_ids', 'comparison_fields'):
            values = getattr(self, name)
            if values: _names(values, name)
        if self.purpose == 'raw_data' and not self.artifact_paths:
            raise ValueError('Raw-data admission requires explicit artifact_paths')
        if self.purpose == 'major_claim' and not self.confirmation_run_ids:
            raise ValueError('Major-claim admission requires distinct confirmation_run_ids')
        return self


def acceptance_contract(config):
    value = config.get('acceptance_contract')
    return AcceptanceContract.model_validate(value) if value is not None else None


def acceptance_gate(session, run, sources):
    from services.api.verification import verification_for_run, numerical_coverage_for_run
    from .applicability import goal_applicability
    from research.validation.comparability import assess_comparability, evaluation_classification
    from services.api.db import asdict
    contract = acceptance_contract(run.config)
    if contract is None or contract.purpose == 'exploratory':
        return {'ready': True, 'purpose': 'exploratory', 'scope': 'Exploration has no scientific acceptance claim'}
    failures = []; checks = []
    if not sources: failures.append('Select actual evidence runs for this handoff')
    for source in sources:
        if source.project_id != run.project_id:
            raise ValueError('Evidence must belong to this project')
        applicable = goal_applicability(session, source)
        observed = verification_for_run(session, source)
        checks.append({**observed, 'goal_applicability': applicable})
        if source.status != 'completed': failures.append('Completed actual evidence is required: '+source.id)
        if not applicable['ready']: failures.append('Evidence requires a current goal-use decision: '+source.id)
        if observed['verification_status'] != 'accepted': failures.append('Independent source-bound verification is required: '+source.id)
        if contract.purpose == 'raw_data':
            admitted = {check['source'] for check in observed['checks']
                        if check.get('kind') == 'data_contract' and check.get('status') == 'accepted'}
            for path in contract.artifact_paths:
                if path not in admitted: failures.append('Raw-data schema, units and sample partition checks are missing: '+source.id+'/'+path)
        else:
            numeric = numerical_coverage_for_run(session, source)
            if not numeric['ready']: failures.append('Complete numerical handoff coverage is required: '+source.id)
    comparison = None
    if contract.purpose in ('comparison', 'major_claim') and sources:
        comparison = assess_comparability([asdict(source) for source in sources],
            {'comparison_fields': contract.comparison_fields})
        if not comparison['directly_comparable']: failures.append(comparison['reason'])
    confirmations = []
    if contract.purpose == 'major_claim':
        for identifier in contract.confirmation_run_ids:
            source = get(session, TaskRun, identifier)
            if source.project_id != run.project_id: raise ValueError('Confirmation must belong to this project')
            observed = verification_for_run(session, source)
            numerical = numerical_coverage_for_run(session, source)
            applicable = goal_applicability(session, source)
            phase = source.config.get('research_phase')
            confirmation = asdict(source)
            compatible = assess_comparability([asdict(sources[0]), confirmation],
                {'comparison_fields': contract.comparison_fields}) if sources and source.id != sources[0].id else None
            if (source.id in {item.id for item in sources} or source.status != 'completed' or phase != 'confirmation'
                    or observed['verification_status'] != 'accepted' or not numerical['ready'] or not applicable['ready']
                    or compatible is None or not compatible['directly_comparable']):
                failures.append('A distinct actual, independently checked confirmation-phase run is required: '+identifier)
            elif evaluation_classification([confirmation]) != 'independent_confirmation':
                failures.append('Confirmation requires an independent held-out evaluation without feedback: '+identifier)
            confirmations.append({'run_id': identifier, 'research_phase': phase,
                'verification_status': observed['verification_status'], 'numerical_coverage': numerical,
                'comparison': compatible, 'goal_applicability': applicable})
    return {'ready': not failures, 'purpose': contract.purpose, 'contract': contract.model_dump(),
        'failures': failures, 'source_checks': checks, 'comparison': comparison, 'confirmations': confirmations,
        'scope': 'Declared raw-data properties, comparison conditions and independently checked numerical/confirmation execution; scientific validity and importance require scientific review'}
