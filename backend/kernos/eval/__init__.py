"""``kernos.eval``: cases, graders as plugins, the runner, the identity a run is keyed by
(design §5.5; plan Task 4.2), gate 4 (``kernos.eval.gate``, Task 4.3). The eval tables
live in ``kernos.content``."""
from kernos.eval.case import RECORD_VERSION, EvalCase, spec_sha  # noqa: F401
from kernos.eval.graders import (  # noqa: F401
    DEFAULT_TOOL_SELECTION, Grader, GraderRegistry, Prose, ToolSelection, Verdict, _Invocation,
    _ok_results, summarize_cost_latency,
)
from kernos.eval.runner import Runner, grade_record, invocation_dicts, summarize  # noqa: F401
from kernos.eval.gate import eval_gate, latest_matching_run  # noqa: F401
