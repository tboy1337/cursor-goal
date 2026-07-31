"""
patterns.py — Feature definitions and detectors for cursor-goal testing.

Features F11-F18: regex-based text pattern detectors (original)
Features F19-F21: structured tool-call detectors (parse JSON from JSONL or text samples)

Supports two input formats:
  - JSONL transcripts (direct from Cursor agent-transcripts/)
  - Text samples (converted via extract script, with [TOOL_CALL: ...] markers)
"""

import json
import re
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Feature:
    id: str
    name: str
    pattern: Optional[re.Pattern] = None
    checker: Optional[Callable] = None
    min_samples: int = 10
    count_mode: str = "presence"  # "presence", "count", or "structured"


def extract_task_calls_from_jsonl(path: str) -> list[dict]:
    """Extract Task tool-call inputs from a JSONL transcript file."""
    calls = []
    with open(path) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("role") != "assistant":
                continue
            for block in entry.get("message", {}).get("content", []):
                if block.get("type") == "tool_use" and block.get("name") == "Task":
                    calls.append(block.get("input", {}))
    return calls


def extract_task_calls_from_text(transcript: str) -> list[dict]:
    """Extract Task tool-call inputs from a text-format sample.

    Handles cases where JSON may be truncated by looking for the
    closing brace on its own line, or falling back to the next marker.
    """
    calls = []
    marker = "[TOOL_CALL: Task]"
    parts = transcript.split(marker)
    for part in parts[1:]:
        lines = part.split("\n")
        json_lines = []
        brace_depth = 0
        started = False
        for line in lines:
            if not started:
                if line.strip().startswith("{"):
                    started = True
                    brace_depth += line.count("{") - line.count("}")
                    json_lines.append(line)
                continue
            if line.startswith("[TOOL_CALL:") or line.startswith("=== Turn"):
                break
            brace_depth += line.count("{") - line.count("}")
            json_lines.append(line)
            if brace_depth <= 0:
                break
        json_str = "\n".join(json_lines).strip()
        try:
            calls.append(json.loads(json_str))
        except json.JSONDecodeError:
            pass
    return calls


def get_evaluator_calls(task_calls: list[dict]) -> list[dict]:
    """Filter to only goal-evaluator Task calls."""
    return [
        c for c in task_calls
        if "evaluate goal" in c.get("description", "").lower()
        or "goal completion evaluator" in c.get("prompt", "").lower()
    ]


def check_f19(task_calls: list[dict]) -> dict:
    """F19: Evaluator uses subagent_type 'goal-evaluator' (not 'generalPurpose')."""
    evals = get_evaluator_calls(task_calls)
    if not evals:
        return {"found": False, "count": 0, "detail": "no evaluator calls found"}
    wrong = [c for c in evals if c.get("subagent_type") != "goal-evaluator"]
    return {
        "found": len(wrong) == 0,
        "count": len(evals),
        "detail": f"{len(evals)} evaluator calls, {len(wrong)} used wrong subagent_type"
                  + (f" (got: {[c.get('subagent_type') for c in wrong]})" if wrong else ""),
    }


def check_f20(task_calls: list[dict]) -> dict:
    """F20: Evaluator runs as readonly."""
    evals = get_evaluator_calls(task_calls)
    if not evals:
        return {"found": False, "count": 0, "detail": "no evaluator calls found"}
    non_readonly = [c for c in evals if not c.get("readonly")]
    return {
        "found": len(non_readonly) == 0,
        "count": len(evals),
        "detail": f"{len(evals)} evaluator calls, {len(non_readonly)} not readonly",
    }


def check_f21(task_calls: list[dict]) -> dict:
    """F21: Evaluator prompt contains goal condition text."""
    evals = get_evaluator_calls(task_calls)
    if not evals:
        return {"found": False, "count": 0, "detail": "no evaluator calls found"}
    missing = [c for c in evals if "goal condition:" not in c.get("prompt", "").lower()]
    return {
        "found": len(missing) == 0,
        "count": len(evals),
        "detail": f"{len(evals)} evaluator calls, {len(missing)} missing condition in prompt",
    }


def extract_ordered_actions_from_jsonl(path: str) -> list[dict]:
    """Extract an ordered list of significant actions from a JSONL transcript.

    Returns dicts with keys: line, type, and type-specific fields.
    Types: 'evaluator', 'goal_create', 'goal_done', 'goal_clear', 'goal_pause', 'goal_resume'
    """
    actions = []
    with open(path) as f:
        for i, line in enumerate(f):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("role") != "assistant":
                continue
            for block in entry.get("message", {}).get("content", []):
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name")
                inp = block.get("input", {})

                if name == "Task":
                    desc = inp.get("description", "").lower()
                    prompt = inp.get("prompt", "").lower()
                    if "evaluate goal" in desc or "goal completion evaluator" in prompt:
                        actions.append({"line": i, "type": "evaluator", "input": inp})

                elif name == "Shell":
                    cmd = inp.get("command", "")
                    if "goal-manage.sh" in cmd:
                        if "create" in cmd:
                            actions.append({"line": i, "type": "goal_create", "cmd": cmd})
                        elif "done" in cmd:
                            actions.append({"line": i, "type": "goal_done", "cmd": cmd})
                        elif "clear" in cmd or "cancel" in cmd or "reset" in cmd:
                            actions.append({"line": i, "type": "goal_clear", "cmd": cmd})
                        elif "pause" in cmd:
                            actions.append({"line": i, "type": "goal_pause", "cmd": cmd})
                        elif "resume" in cmd:
                            actions.append({"line": i, "type": "goal_resume", "cmd": cmd})
    return actions


def check_f22(task_calls: list[dict], actions: list[dict] = None) -> dict:
    """F22: Per-cycle check — every goal_done has an evaluator within the same goal cycle.

    A goal cycle starts at goal_create and ends at goal_done/goal_clear.
    Self-assessment (done without evaluator in the same cycle) is a violation.
    """
    if not actions:
        return {"found": False, "count": 0, "detail": "no action sequence available"}

    cycles = []
    current: list[dict] = []
    for a in actions:
        if a["type"] == "goal_create":
            if current:
                cycles.append(current)
            current = [a]
        else:
            current.append(a)
    if current:
        cycles.append(current)

    done_count = 0
    violations = 0
    violation_details = []
    for i, cycle in enumerate(cycles):
        has_eval = any(a["type"] == "evaluator" for a in cycle)
        has_done = any(a["type"] == "goal_done" for a in cycle)
        if has_done:
            done_count += 1
            if not has_eval:
                violations += 1
                violation_details.append(f"cycle {i+1} (line {cycle[0]['line']})")

    detail = f"{done_count} done calls across {len(cycles)} cycles, {violations} self-assessed"
    if violation_details:
        detail += f" at: {', '.join(violation_details)}"
    return {
        "found": violations == 0,
        "count": done_count,
        "detail": detail,
    }


def check_f23(task_calls: list[dict], actions: list[dict] = None) -> dict:
    """F23: Goal creation includes a non-empty condition string."""
    if not actions:
        return {"found": False, "count": 0, "detail": "no action sequence available"}
    creates = [a for a in actions if a["type"] == "goal_create"]
    if not creates:
        return {"found": False, "count": 0, "detail": "no goal_create calls found"}
    empty = [c for c in creates if 'create ""' in c.get("cmd", "") or "create ''" in c.get("cmd", "")]
    return {
        "found": len(empty) == 0,
        "count": len(creates),
        "detail": f"{len(creates)} create calls, {len(empty)} with empty condition",
    }


def check_f24(task_calls: list[dict], actions: list[dict] = None) -> dict:
    """F24: Evaluator-to-done sequencing — done immediately follows the LAST evaluator in a goal cycle."""
    if not actions:
        return {"found": False, "count": 0, "detail": "no action sequence available"}
    done_actions = [a for a in actions if a["type"] == "goal_done"]
    eval_actions = [a for a in actions if a["type"] == "evaluator"]
    if not done_actions or not eval_actions:
        return {"found": False, "count": 0, "detail": "need both evaluator and done calls"}

    correct = 0
    for done in done_actions:
        preceding_evals = [e for e in eval_actions if e["line"] < done["line"]]
        if preceding_evals:
            last_eval = max(preceding_evals, key=lambda e: e["line"])
            between = [a for a in actions
                       if a["line"] > last_eval["line"] and a["line"] < done["line"]
                       and a["type"] not in ("evaluator",)]
            if not between:
                correct += 1
    return {
        "found": correct == len(done_actions),
        "count": len(done_actions),
        "detail": f"{correct}/{len(done_actions)} done calls directly follow their evaluator",
    }


FEATURES = {
    "F11": Feature(
        id="F11",
        name="Goal state initialization",
        pattern=re.compile(
            r'goal-manage\.sh\s+create'
            r'|\[goal\]\s+Goal created'
            r'|"status":\s*"pursuing"',
            re.IGNORECASE,
        ),
        min_samples=10,
    ),
    "F12": Feature(
        id="F12",
        name="In-turn subagent goal evaluation",
        pattern=re.compile(
            r'Evaluate goal completion'
            r'|Evaluate whether.*goal.*achieved'
            r'|YES:\s*.+'
            r'|NO:\s*.+remain',
            re.IGNORECASE,
        ),
        min_samples=10,
        count_mode="count",
    ),
    "F13": Feature(
        id="F13",
        name="Stop hook auto-continuation",
        pattern=re.compile(
            r'\[GOAL\]\s*Turn\s+\d+/\d+'
            r'|\[GOAL BUDGET\]'
            r'|followup_message.*\[GOAL\]',
            re.IGNORECASE,
        ),
        min_samples=10,
    ),
    "F13a": Feature(
        id="F13a",
        name="Validation command execution",
        pattern=re.compile(
            r'--test\s+"[^"]+"'
            r'|Validation.*PASSED.*exit 0'
            r'|Validation.*FAILED.*exit'
            r'|pytest\s+[\w/.-]+'
            r'|eslint\s+[\w/.-]+',
            re.IGNORECASE,
        ),
        min_samples=5,
    ),
    "F13b": Feature(
        id="F13b",
        name="Goal completion marking",
        pattern=re.compile(
            r'goal-manage\.sh\s+done'
            r'|\[goal\].*Goal achieved'
            r'|"status":\s*"achieved"',
            re.IGNORECASE,
        ),
        min_samples=5,
    ),
    "F14": Feature(
        id="F14",
        name="Goal pause/resume lifecycle",
        pattern=re.compile(
            r'/goal\s+pause'
            r'|/goal\s+resume'
            r'|goal-manage\.sh\s+pause'
            r'|goal-manage\.sh\s+resume'
            r'|"status":\s*"paused"',
            re.IGNORECASE,
        ),
        min_samples=5,
    ),
    "F15": Feature(
        id="F15",
        name="Natural language condition parsing",
        pattern=re.compile(
            r'/goal\s+(?!.*--test)(?!.*--budget)[^\n]+'
            r'|goal-manage\.sh\s+create\s+(?!.*--test)',
            re.IGNORECASE,
        ),
        min_samples=5,
    ),
    "F16": Feature(
        id="F16",
        name="Multi-cycle evaluation",
        pattern=re.compile(
            r'YES:\s*.+|NO:\s*.+',
            re.IGNORECASE,
        ),
        min_samples=5,
        count_mode="count",
    ),
    "F17": Feature(
        id="F17",
        name="Budget inline parsing",
        pattern=re.compile(
            r'stop after\s+\d+\s+turns?'
            r'|after\s+\d+\s+turns?',
            re.IGNORECASE,
        ),
        min_samples=5,
    ),
    "F18": Feature(
        id="F18",
        name="Goal clear/cancel",
        pattern=re.compile(
            r'/goal\s+(clear|cancel|stop|reset)'
            r'|goal-manage\.sh\s+(clear|cancel|reset)',
            re.IGNORECASE,
        ),
        min_samples=5,
    ),
    "F19": Feature(
        id="F19",
        name="Evaluator uses correct subagent_type",
        checker=check_f19,
        count_mode="structured",
    ),
    "F20": Feature(
        id="F20",
        name="Evaluator runs as readonly",
        checker=check_f20,
        count_mode="structured",
    ),
    "F21": Feature(
        id="F21",
        name="Evaluator prompt contains goal condition",
        checker=check_f21,
        count_mode="structured",
    ),
    "F22": Feature(
        id="F22",
        name="No self-assessment (done preceded by evaluator)",
        checker=check_f22,
        count_mode="structured",
    ),
    "F23": Feature(
        id="F23",
        name="Goal creation has non-empty condition",
        checker=check_f23,
        count_mode="structured",
    ),
    "F24": Feature(
        id="F24",
        name="Done immediately follows evaluator (no intervening actions)",
        checker=check_f24,
        count_mode="structured",
    ),
}

WORKLOAD_FEATURES = {
    "12-goal-with-test": ["F11", "F12", "F13", "F13a", "F13b", "F19", "F20", "F21", "F22", "F23"],
    "13-goal-budget": ["F11", "F13"],
    "14-goal-no-test": ["F11", "F12", "F13b", "F19", "F20", "F21", "F22", "F23"],
    "15-goal-pause-resume": ["F11", "F14", "F15", "F17", "F23"],
    "16-goal-natural-migration": ["F11", "F12", "F13b", "F15", "F19", "F20", "F22", "F23"],
    "17-goal-lint-fix": ["F11", "F12", "F13b", "F15", "F19", "F20", "F22", "F23"],
    "18-goal-test-coverage": ["F11", "F12", "F13a", "F13b", "F15", "F19", "F20", "F22", "F23"],
    "19-goal-refactor-split": ["F11", "F12", "F13b", "F15", "F19", "F20", "F22", "F23"],
    "20-goal-docs-generation": ["F11", "F12", "F13b", "F15", "F19", "F20", "F22", "F23"],
    "21-goal-ci-fix": ["F11", "F12", "F13", "F13a", "F13b", "F19", "F20", "F21", "F22", "F23"],
    "22-goal-backlog-drain": ["F11", "F12", "F13b", "F15", "F19", "F20", "F22", "F23"],
    "23-goal-concurrent-eval": ["F11", "F12", "F13", "F16", "F17", "F19", "F20"],
    "24-goal-natural-vague-to-specific": ["F11", "F12", "F15", "F19", "F20"],
    # Pre-fix JSONL samples: F19 excluded (used generalPurpose before fix)
    # F22 excluded from video-production-full (known self-assessment in cycles 3-4)
    "goal-video-production-full.jsonl": ["F11", "F12", "F13", "F13a", "F13b", "F20", "F21", "F23"],
    "goal-website-cards.jsonl": ["F11", "F12", "F13b", "F20", "F21", "F22", "F23"],
    "goal-en-subtitle-fix.jsonl": ["F11", "F12", "F13b", "F20", "F21", "F22", "F23"],
    # Txt samples: regex-only features (no structural analysis)
    "goal-en-subtitle-fix.txt": ["F11", "F12", "F13b"],
    "goal-video-production.txt": ["F11", "F12", "F13b"],
    "goal-website-figures.txt": ["F11", "F12", "F13b"],
}

UNIVERSAL_FEATURES = ["F11", "F12", "F13b", "F19", "F20", "F21", "F22", "F23"]


def check_feature(transcript: str, feature_id: str,
                   task_calls: list[dict] = None,
                   actions: list[dict] = None) -> dict:
    """Check if a feature is detected in a transcript.

    For regex features (F11-F18): matches against transcript text.
    For structured features (F19-F21): inspects parsed Task call data.
    For ordering features (F22-F24): inspects action sequence from JSONL.
    """
    feature = FEATURES.get(feature_id)
    if not feature:
        return {"found": False, "count": 0, "feature": None}

    if feature.checker:
        import inspect
        sig = inspect.signature(feature.checker)
        if "actions" in sig.parameters:
            if task_calls is None and actions is None:
                return {"found": False, "count": 0, "detail": "no structured data for this check"}
            result = feature.checker(task_calls or [], actions)
        else:
            if task_calls is None:
                return {"found": False, "count": 0, "detail": "no task_calls provided for structured check"}
            result = feature.checker(task_calls)
        result["feature"] = feature
        return result

    matches = feature.pattern.findall(transcript)
    count = len(matches)

    if feature_id == "F16":
        return {"found": count >= 2, "count": count, "feature": feature}

    return {"found": count > 0, "count": count, "feature": feature}


def analyze_transcript(transcript: str, workload_id: str,
                       task_calls: list[dict] = None,
                       actions: list[dict] = None) -> dict:
    """Analyze a transcript for all features expected by a workload.

    Falls back to UNIVERSAL_FEATURES if workload_id isn't in the map.
    """
    base_id = workload_id.rsplit(".", 1)[0] if "." in workload_id else workload_id
    expected = WORKLOAD_FEATURES.get(workload_id) or WORKLOAD_FEATURES.get(base_id, UNIVERSAL_FEATURES)
    results = {}
    for fid in expected:
        results[fid] = check_feature(transcript, fid, task_calls, actions)
    passed = sum(1 for r in results.values() if r["found"])
    return {
        "workload": workload_id,
        "features_expected": len(expected),
        "features_passed": passed,
        "pass_rate": passed / len(expected) if expected else 0,
        "details": results,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python patterns.py <transcript_file> [workload_id]")
        print("  Accepts .jsonl (raw transcript) or .txt (converted sample)")
        sys.exit(1)

    filepath = sys.argv[1]
    workload_id = sys.argv[2] if len(sys.argv) > 2 else "12-goal-with-test"

    with open(filepath, "r") as f:
        transcript = f.read()

    task_calls = None
    actions = None
    if filepath.endswith(".jsonl"):
        task_calls = extract_task_calls_from_jsonl(filepath)
        actions = extract_ordered_actions_from_jsonl(filepath)
    else:
        task_calls = extract_task_calls_from_text(transcript)

    result = analyze_transcript(transcript, workload_id, task_calls, actions)
    print(json.dumps(result, indent=2, default=str))
