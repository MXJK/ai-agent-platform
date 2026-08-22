"""Load and validate the trajectory case suite."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


DEFAULT_SUITE_PATH = Path(__file__).with_name("trajectory_cases.json")

# Tolerances used when a run is compared against a baseline. A real model does
# not repeat itself exactly, so a small drift is noise rather than a regression.
DEFAULT_REGRESSION_TOLERANCE: dict[str, float] = {
    "pass_rate": 0.0,
    "invalid_action_rate": 0.05,
    "mean_step_efficiency": 0.30,
    "budget_cap_rate": 0.15,
    "failure_recovery_rate": 0.0,
    "citation_accuracy": 0.0,
}


@dataclass(frozen=True)
class EvalSuite:
    suite_id: str
    workspace_id: str
    fixtures: tuple[dict[str, Any], ...]
    cases: tuple[dict[str, Any], ...]
    metric_thresholds: dict[str, float]
    regression_tolerance: dict[str, float]

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(str(case.get("id") or "") for case in self.cases)

    def materialize(self, workspace_root: Path) -> None:
        """Write the fixture workspace.

        ``padding``/``padding_lines`` let a case drive the agent into its
        context-size budget without carrying kilobytes of filler in the JSON.
        The padding carries no searchable identifier, so it grows a file without
        adding search matches.
        """

        for fixture in self.fixtures:
            target = workspace_root / str(fixture["filename"])
            target.parent.mkdir(parents=True, exist_ok=True)
            padding = str(fixture.get("padding") or "")
            target.write_text(
                str(fixture["content"])
                + padding * int(fixture.get("padding_lines") or 0),
                encoding="utf-8",
            )


def load_suite(path: Path | str = DEFAULT_SUITE_PATH) -> EvalSuite:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = tuple(payload.get("cases") or ())
    if not cases:
        raise ValueError("eval suite declares no cases")
    seen: set[str] = set()
    for case in cases:
        case_id = str(case.get("id") or "")
        if not case_id:
            raise ValueError("every eval case needs an id")
        if case_id in seen:
            raise ValueError(f"duplicate eval case id: {case_id}")
        if not str(case.get("message") or ""):
            raise ValueError(f"eval case {case_id} needs a message")
        seen.add(case_id)
    tolerance = dict(DEFAULT_REGRESSION_TOLERANCE)
    tolerance.update(
        {
            str(key): float(value)
            for key, value in (payload.get("regression_tolerance") or {}).items()
        }
    )
    return EvalSuite(
        suite_id=str(payload.get("suite_id") or "l1_trajectory"),
        workspace_id=str(payload.get("workspace_id") or "workspace_l1"),
        fixtures=tuple(payload.get("fixtures") or ()),
        cases=cases,
        metric_thresholds={
            str(key): float(value)
            for key, value in (payload.get("metric_thresholds") or {}).items()
        },
        regression_tolerance=tolerance,
    )
