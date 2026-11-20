#!/usr/bin/env python3.11
"""
audit_SDGs_from_JSON.py

Audit SDG assignment and classifier error frequencies across all unique
divaID records in all-with-SDGs.json by invoking sdg_classifier_dual_language.py.
It can even be applied to a single JSON file.
"""
import ast
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path


def load_unique_records(path: Path) -> list[dict]:
    """Load concatenated JSON objects or a JSON list and deduplicate by divaID."""
    raw = path.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    idx, n = 0, len(raw)
    records = []
    while idx < n:
        while idx < n and raw[idx].isspace():
            idx += 1
        if idx >= n:
            break
        obj, idx = decoder.raw_decode(raw, idx)
        if isinstance(obj, list):
            records.extend(obj)
        elif isinstance(obj, dict):
            records.append(obj)

    unique = {}
    for rec in records:
        did = rec.get("divaID")
        if did and did not in unique:
            unique[did] = rec
    return list(unique.values())


def parse_sdg_set(sdg_str: str) -> set[int]:
    if not sdg_str:
        return set()
    return {int(x.strip()) for x in sdg_str.split(",") if x.strip().isdigit()}


def check_artifacts(rec: dict) -> list[str]:
    flags = []
    title = rec.get("Title", {}).get("Main title", "")
    eng = rec.get("abstracts", {}).get("eng", "") or rec.get("eng", "")
    swe = rec.get("abstracts", {}).get("swe", "") or rec.get("swe", "")
    full_text = f"{title} {eng} {swe}"

    if not eng.strip() and not swe.strip():
        flags.append("no_abstract")
    elif len(full_text.split()) < 180:
        flags.append("short_text_lt_180w")
    if "\u037e" in full_text or "Å…" in full_text or "\ufffd" in full_text:
        flags.append("encoding_or_greek_semicolon")
    if re.search(r"[a-zåäö]{4,}[A-ZÅÄÖ][a-zåäö]{3,}", re.sub(r"<[^>]+>", " ", full_text)):
        flags.append("glued_tokens_or_page_header")
    return flags


def run_classifier(script_path: Path, rec: dict, tmp_dir: Path) -> dict[int, int]:
    """Write record to a temp JSON file, run classifier CLI, and parse Match Vector."""
    tmp_file = tmp_dir / "record.json"
    tmp_file.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(script_path), str(tmp_file)],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            vec = ast.literal_eval(line)
            if isinstance(vec, list) and len(vec) == 17:
                return {i + 1: vec[i] for i in range(17)}
    raise RuntimeError(f"Could not parse Match Vector for {rec.get('divaID')}:\n{proc.stdout}")


def main():
    json_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("all-with-SDGs.json")
    script_path = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else Path(__file__).resolve().parent / "sdg_classifier_dual_language.py"
    )

    records = load_unique_records(json_path)
    print(f"Loaded {len(records)} unique divaID records.\n")

    counts = Counter()
    examples = defaultdict(list)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for rec in records:
            did = rec["divaID"]
            assigned = parse_sdg_set(rec.get("SDGs", ""))
            scores = run_classifier(script_path, rec, tmp_dir)

            inferred_ge2 = {g for g, c in scores.items() if c >= 2}
            strong_goals = {g for g, c in scores.items() if c >= 5}
            trace_goals = {g for g, c in scores.items() if c == 1}

            # 1. Source/structural artifacts
            for flag in check_artifacts(rec):
                counts[flag] += 1
                if len(examples[flag]) < 8:
                    examples[flag].append(did)

            # 2. Metadata vs. lexical evidence diagnostics
            zero_hit_assigned = assigned - {g for g, c in scores.items() if c > 0}
            trace_only_assigned = assigned & trace_goals
            omitted_strong = strong_goals - assigned

            if zero_hit_assigned:
                counts["author_zero_hit_assigned_goal"] += 1
                if len(examples["author_zero_hit_assigned_goal"]) < 8:
                    examples["author_zero_hit_assigned_goal"].append(
                        f"{did} (assigned={sorted(assigned)}, zero={sorted(zero_hit_assigned)})"
                    )

            if trace_only_assigned:
                counts["author_trace_only_assigned_goal"] += 1
                if len(examples["author_trace_only_assigned_goal"]) < 8:
                    examples["author_trace_only_assigned_goal"].append(
                        f"{did} (trace={sorted(trace_only_assigned)})"
                    )

            if omitted_strong:
                counts["author_omitted_strong_goal_ge5"] += 1
                if len(examples["author_omitted_strong_goal_ge5"]) < 8:
                    examples["author_omitted_strong_goal_ge5"].append(
                        f"{did} (assigned={sorted(assigned)}, omitted={sorted(omitted_strong)})"
                    )

            if not inferred_ge2:
                counts["classifier_no_goal_ge2"] += 1
                if len(examples["classifier_no_goal_ge2"]) < 8:
                    examples["classifier_no_goal_ge2"].append(
                        f"{did} (assigned={sorted(assigned)})"
                    )

            if trace_goals - assigned:
                counts["trace_noise_suppressed_by_min_hits_2"] += 1

            if assigned and assigned.issubset(inferred_ge2):
                counts["exact_or_superset_lexical_confirmation"] += 1

    print(f"{'Category':<44} {'Count':>6} {'Pct':>8}")
    print("-" * 62)
    for k, v in counts.most_common():
        print(f"{k:<44} {v:>6} {100.0 * v / len(records):>7.1f}%")

    print("\nSample divaIDs per category:")
    for k, ex in examples.items():
        print(f"  * {k}: {', '.join(ex)}")


if __name__ == "__main__":
    main()
