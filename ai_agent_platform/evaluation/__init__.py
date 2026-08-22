"""L1 trajectory evaluation: grade how an agent run reached its answer.

The analysis lives inside the package rather than under ``evals/`` because the
container image only carries ``ai_agent_platform``. That is what lets the eval
run in the app process, against the model registry and credentials the user
actually configured, instead of only offline against the fake provider.
"""

from ai_agent_platform.evaluation.citations import (
    CitationReport,
    CitationVerdict,
    answer_citation_paths,
    ungrounded_answer_paths,
    verify_citations,
    verify_context_source,
)
from ai_agent_platform.evaluation.faults import (
    FaultInjectingToolRegistry,
    ToolFaultController,
)
from ai_agent_platform.evaluation.models import (
    EvalAlert,
    EvalBaseline,
    EvalCaseRecord,
    EvalRunRecord,
    EvalSuiteMetrics,
)
from ai_agent_platform.evaluation.suite import (
    DEFAULT_SUITE_PATH,
    EvalSuite,
    load_suite,
)
from ai_agent_platform.evaluation.trajectory import (
    ConstraintVerdict,
    RunObservation,
    TrajectoryMetrics,
    aggregate_budget_cap_rate,
    aggregate_failure_recovery_rate,
    aggregate_invalid_action_rate,
    aggregate_step_efficiency,
    check_constraints,
    measure_trajectory,
)

__all__ = [
    "CitationReport",
    "CitationVerdict",
    "ConstraintVerdict",
    "DEFAULT_SUITE_PATH",
    "EvalAlert",
    "EvalBaseline",
    "EvalCaseRecord",
    "EvalRunRecord",
    "EvalSuite",
    "EvalSuiteMetrics",
    "FaultInjectingToolRegistry",
    "RunObservation",
    "ToolFaultController",
    "TrajectoryMetrics",
    "aggregate_budget_cap_rate",
    "aggregate_failure_recovery_rate",
    "aggregate_invalid_action_rate",
    "aggregate_step_efficiency",
    "answer_citation_paths",
    "check_constraints",
    "load_suite",
    "measure_trajectory",
    "ungrounded_answer_paths",
    "verify_citations",
    "verify_context_source",
]
