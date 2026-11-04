"""The ordered list of pipeline steps."""

from lane.pipeline.actions_step import actions_step
from lane.pipeline.analyze_step import analyze_step
from lane.pipeline.classify_step import classify_step
from lane.pipeline.extract_step import extract_step
from lane.pipeline.resolve_step import resolve_step
from lane.pipeline.rules_step import rules_step
from lane.pipeline.runner import StepDef
from lane.pipeline.triage_step import triage_step

PIPELINE: list[StepDef] = [
    StepDef(name="triage", version="1", fn=triage_step),
    StepDef(name="classify", version="1", fn=classify_step),
    StepDef(name="extract", version="1", fn=extract_step),
    StepDef(name="resolve", version="1", fn=resolve_step),
    StepDef(name="rules", version="1", fn=rules_step),
    StepDef(name="analyze", version="1", fn=analyze_step, required=False),
    StepDef(name="actions", version="1", fn=actions_step),
]
