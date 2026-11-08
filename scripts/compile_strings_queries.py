#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""compile_strings_queries.py

Compiles STRINGS_SDG_queries_FINAL.xlsx into structured boolean rules.
Output format: {
  "<sdg>": [
    {
      "raw": "hazardous chemical AND (\"consumption\" OR \"production\")",
      "clauses": [
        ["\\bhazardous\\s+chemical\\w*"],
        ["\\bconsumption\\w*", "\\bproduction\\w*"]
      ]
    }
  ]
}
"""

import json
from pathlib import Path
import re
import pandas as pd


def token_to_regex(token: str) -> str:
    """Converts a token or phrase with wildcards into a regex pattern."""
    t = token.strip().strip('"').strip("'").lower()
    t = re.sub(r"\s+", r"\\s+", t)
    if t.endswith("*"):
        t = re.escape(t[:-1]) + r"\w*"
    else:
        t = re.escape(t)
    return rf"\b{t}\b"


def parse_clause(clause_str: str) -> list[str]:
    """Extracts OR-separated terms from a clause."""
    c = clause_str.strip().strip("()")
    parts = re.split(r"\s+\bOR\b\s+", c, flags=re.IGNORECASE)
    patterns = []
    for p in parts:
        p_clean = p.strip().strip('"').strip("'")
        if p_clean:
            patterns.append(token_to_regex(p_clean))
    return patterns


def compile_strings_file(
    excel_path: Path, output_json: Path = Path("STRINGS_rules.json")
):
    df = pd.read_excel(excel_path, sheet_name=0)
    compiled_by_sdg = {i: [] for i in range(1, 18)}

    for _, row in df.iterrows():
        sdg = int(row["SDG"])
        raw_query = str(row["full search string"]).strip()
        if not raw_query:
            continue

        # Split on top-level AND
        raw_clauses = re.split(r"\s+\bAND\b\s+", raw_query, flags=re.IGNORECASE)
        rule_clauses = []
        for rc in raw_clauses:
            patterns = parse_clause(rc)
            if patterns:
                rule_clauses.append(patterns)

        if rule_clauses:
            compiled_by_sdg[sdg].append(
                {"raw": raw_query, "clauses": rule_clauses}
            )

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(compiled_by_sdg, f, ensure_ascii=False, indent=2)

    total_rules = sum(len(v) for v in compiled_by_sdg.values())
    print(
        f"Compiled {total_rules} STRINGS rules across {len(compiled_by_sdg)} SDGs into '{output_json}'."
    )


if __name__ == "__main__":
    compile_strings_file(Path("STRINGS_SDG_queries_FINAL.xlsx"))
