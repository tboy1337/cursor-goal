#!/usr/bin/env python3
"""
Run subagent-based tests for cursor-goal features.

This script is meant to be called by the main agent, which launches
Task subagents for each workload and collects the results.

Usage (from within an agent context):
  1. Import workloads from workloads.py
  2. For each workload, run setup, launch Task subagent, run teardown
  3. Analyze the subagent transcript with patterns.py
  4. Save results

Standalone usage (analysis only — requires pre-collected transcripts):
  python3 run-subagent-tests.py analyze <results_dir>
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from patterns import (
    FEATURES,
    check_feature,
    extract_task_calls_from_text,
)


def analyze_subagent_response(response: str, workload: dict) -> dict:
    """Analyze a subagent's response text for expected features.

    Uses regex patterns (F11-F18) against the response text,
    and structured analysis (F19-F24) against extracted Task calls.
    """
    task_calls = extract_task_calls_from_text(response)

    results = {}
    for fid in workload["features"]:
        feature = FEATURES.get(fid)
        if not feature:
            results[fid] = {"found": False, "count": 0, "detail": f"unknown feature {fid}"}
            continue

        result = check_feature(response, fid, task_calls)
        results[fid] = {
            "found": result["found"],
            "count": result.get("count", 0),
            "detail": result.get("detail", ""),
        }

    passed = sum(1 for r in results.values() if r["found"])
    return {
        "workload_id": workload["id"],
        "workload_name": workload["name"],
        "features_expected": len(workload["features"]),
        "features_passed": passed,
        "pass_rate": passed / len(workload["features"]) if workload["features"] else 0,
        "details": results,
    }


def save_results(results: list[dict], results_dir: str):
    """Save analysis results to JSON and generate a report."""
    os.makedirs(results_dir, exist_ok=True)

    with open(os.path.join(results_dir, "results.json"), "w") as f:
        json.dump(results, f, indent=2)

    lines = ["# Subagent Test Results", ""]
    lines.append(f"Run: {datetime.now().isoformat()}")
    lines.append("")

    total_features = sum(r["features_expected"] for r in results)
    total_passed = sum(r["features_passed"] for r in results)
    lines.append(f"**Overall: {total_passed}/{total_features} features passed**")
    lines.append("")

    lines.append("## Per-Workload Results")
    lines.append("")
    lines.append("| Workload | Pass Rate | Passed | Expected |")
    lines.append("|----------|-----------|--------|----------|")
    for r in results:
        status = "PASS" if r["pass_rate"] == 1.0 else "PARTIAL"
        lines.append(
            f"| {r['workload_id']} | {status} ({r['pass_rate']:.0%}) | "
            f"{r['features_passed']} | {r['features_expected']} |"
        )

    lines.append("")
    lines.append("## Feature Coverage")
    lines.append("")

    feature_status = {}
    for r in results:
        for fid, detail in r["details"].items():
            if fid not in feature_status:
                feature_status[fid] = {"tested": 0, "passed": 0}
            feature_status[fid]["tested"] += 1
            if detail["found"]:
                feature_status[fid]["passed"] += 1

    lines.append("| Feature | Name | Tested | Passed |")
    lines.append("|---------|------|--------|--------|")
    for fid in sorted(feature_status.keys()):
        feat = FEATURES.get(fid)
        name = feat.name if feat else "?"
        s = feature_status[fid]
        lines.append(f"| {fid} | {name} | {s['tested']} | {s['passed']} |")

    lines.append("")

    failed_details = []
    for r in results:
        for fid, detail in r["details"].items():
            if not detail["found"]:
                failed_details.append(
                    f"- **{r['workload_id']}** / {fid}: {detail.get('detail', 'no detail')}"
                )

    if failed_details:
        lines.append("## Failures")
        lines.append("")
        lines.extend(failed_details)
        lines.append("")

    report = "\n".join(lines)
    with open(os.path.join(results_dir, "report.md"), "w") as f:
        f.write(report)

    return report


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "analyze":
        print("Usage: python3 run-subagent-tests.py analyze <results_dir>")
        print("  Reads results.json from <results_dir> and regenerates report.md")
        sys.exit(1)

    results_dir = sys.argv[2]
    results_file = os.path.join(results_dir, "results.json")
    if not os.path.exists(results_file):
        print(f"No results.json found in {results_dir}")
        sys.exit(1)

    with open(results_file) as f:
        results = json.load(f)

    report = save_results(results, results_dir)
    print(report)
