#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""compute_auxiliary_sdg_diff.py

Extracts keywords and alternatives from UoA-SDG-Keyword-List-Ver.-1.1.xlsx,
filters out terms already present in Elsevier V4 and AUXILIARY_SDG_TERMS,
and formats the new candidate terms for inclusion in AUXILIARY_SDG_TERMS.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import pandas as pd

# 1. Attempt to load existing Elsevier V4 terms
try:
    import Elsevier_Data_Repository_V4_SDG_terms as sdg_dict
except ImportError:
    sdg_dict = None

# 2. Existing auxiliary terms from sdg_classifier.py
EXISTING_AUXILIARY_SDG_TERMS: dict[int, list[str]] = {
    3: [
        "cardiac arrest", "cardiac", "electrocardiogram", "ecg", "ecgi",
        "arrhythmia", "healthcare", "health care", "clinical adoption",
        "clinical", "clinicians", "medical imaging", "biomedical",
        "cardiovascular", "heart disease", "patient monitoring",
        "hospitalized patients", "vital signs", "e-health", "mhealth",
        "prosthesis", "prostheses", "prosthetic", "prosthetic foot",
        "prosthetics", "prosthetic users", "transtibial", "transfemoral",
        "amputee", "amputees", "amputation", "orthosis", "orthoses",
        "orthotics", "biomechanics", "biomechanical", "gait",
        "gait analysis", "motion capture", "rehabilitation",
        "assistive technology", "assistive devices", "force plate",
        "force plates", "heel strike",
    ],
    9: [
        "artificial intelligence", "machine learning", "deep learning",
        "federated learning", "neural network", "neural networks",
        "distributed computing", "edge computing", "edge devices",
        "internet of things", "sensor network", "telecommunications",
        "cloud computing", "data infrastructure", "anomaly detection",
        "power converter", "power converters", "traction inverter",
        "inverter", "inverters", "power electronics", "gate driver",
        "gate drivers", "silicon carbide", "mosfet", "mosfets",
        "semiconductor", "semiconductors", "soft switching",
        "printed circuit board", "pcb", "hardware prototype",
        "circuit design", "finite element method",
    ],
    10: [
        "assistive technology", "prosthetic users", "physical rehabilitation",
    ],
    11: [
        "municipalities", "municipality", "municipal governance",
        "regional governance", "local food systems", "social-ecological systems",
        "governing for resilience", "resilience capacities", "local produce",
        "urban and regional", "community resilience",
    ],
    12: [
        "production to consumption", "production and consumption",
        "food supply systems", "food supply chain", "food governance",
        "food systems governance", "sustainable supply", "sustainable food",
        "missing middle", "circular food",
    ],
}

# Generic terms to avoid introducing broad noise
EXCLUDED_TERMS = {
    "sdg", "sdgs", "sustainable development", "sustainable development goals",
    "development goals", "agenda 2030", "indicator", "indicators", "target", "targets"
}

# Specific short technical acronyms safe to keep
SAFE_SHORT_ACRONYMS = {"ecg", "pcb", "sic", "ev", "ghg", "hiv", "sdg"}

def extract_terms_from_cell(cell_value: object) -> list[str]:
    """Splits semicolon-separated alternatives, filters booleans and ambiguous short acronyms."""
    if pd.isna(cell_value):
        return []

    results = []
    raw_str = str(cell_value)
    sub_phrases = raw_str.split(";")

    for sp in sub_phrases:
        # Ignore boolean search syntax
        if re.search(r"\b(AND|OR|NOT)\b", sp):
            continue

        cleaned = clean_phrase(sp)
        if not cleaned or cleaned in EXCLUDED_TERMS:
            continue

        # If it's a single word with 4 or fewer characters, ensure it's not an ambiguous acronym
        if " " not in cleaned and len(cleaned) <= 4:
            if cleaned not in SAFE_SHORT_ACRONYMS:
                continue

        if len(cleaned) >= 3:
            results.append(cleaned)

    return results

def clean_phrase(phrase: str) -> str:
    """Normalizes whitespace and characters for phrase matching."""
    phrase = phrase.strip().lower()
    # Strip wildcards (* and ?) if used as simple suffix matchers
    phrase = phrase.replace("*", "").replace("?", "")
    phrase = re.sub(r"\s+", " ", phrase)
    return phrase.strip()


def build_existing_index() -> dict[int, set[str]]:
    """Builds a lookup index of terms already present in Elsevier V4 and AUXILIARY."""
    existing_index: dict[int, set[str]] = {i: set() for i in range(1, 18)}

    # Add Elsevier V4 terms
    if sdg_dict is not None:
        for i in range(1, 17):
            var_name = f"sdg{i}_terms"
            terms = getattr(sdg_dict, var_name, [])
            for t in terms:
                cleaned = clean_phrase(t)
                if cleaned:
                    existing_index[i].add(cleaned)

    # Add current AUXILIARY terms
    for sdg_num, terms in EXISTING_AUXILIARY_SDG_TERMS.items():
        for t in terms:
            cleaned = clean_phrase(t)
            if cleaned:
                existing_index[sdg_num].add(cleaned)

    return existing_index


def compute_differences(excel_path: Path) -> dict[int, list[str]]:
    """Reads UoA spreadsheet and extracts novel terms per SDG."""
    xls = pd.ExcelFile(excel_path)
    sheet_name = "SDG Keywords (initial ver.)"
    if sheet_name not in xls.sheet_names:
        sheet_name = xls.sheet_names[0]

    df = pd.read_excel(excel_path, sheet_name=sheet_name)
    existing_index = build_existing_index()
    novel_terms_per_sdg: dict[int, set[str]] = {i: set() for i in range(1, 17)}

    for _, row in df.iterrows():
        sdg_str = str(row.get("SDG", "")).strip().upper()
        sdg_match = re.search(r"SDG\s*(\d+)", sdg_str)
        if not sdg_match:
            continue

        sdg_num = int(sdg_match.group(1))
        if not (1 <= sdg_num <= 16):
            continue

        # Extract both main keywords and alternatives
        candidates = []
        candidates.extend(extract_terms_from_cell(row.get("SDG Keywords")))
        candidates.extend(extract_terms_from_cell(row.get("Alternatives")))

        for term in candidates:
            if term not in existing_index[sdg_num]:
                novel_terms_per_sdg[sdg_num].add(term)

    # Sort descending by length so compound phrases take precedence
    return {
        i: sorted(list(novel_terms_per_sdg[i]), key=lambda x: (-len(x), x))
        for i in range(1, 17)
    }


def main():
    parser = argparse.ArgumentParser(
        description="Extract novel SDG keywords from UoA Excel list."
    )
    parser.add_argument(
        "excel_file",
        type=Path,
        nargs="?",
        default=Path("/tmp/UoA-SDG-Keyword-List-Ver.-1.1.xlsx"),
        help="Path to UoA-SDG-Keyword-List-Ver.-1.1.xlsx",
    )
    parser.add_argument(
        "--format",
        choices=["python", "json", "stats"],
        default="python",
        help="Output format: 'python' (dictionary code), 'json', or 'stats'",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional destination file to save the result",
    )

    args = parser.parse_args()

    if not args.excel_file.is_file():
        sys.exit(f"Error: Excel file '{args.excel_file}' not found.")

    diff_map = compute_differences(args.excel_file)

    if args.format == "stats":
        print(f"{'SDG':<10} | {'Novel Terms Added':<18}")
        print("-" * 32)
        total = 0
        for i in range(1, 17):
            cnt = len(diff_map[i])
            total += cnt
            print(f"SDG {i:<6} | {cnt:<18}")
        print("-" * 32)
        print(f"{'Total':<10} | {total:<18}")
        return

    if args.format == "json":
        formatted_output = json.dumps(diff_map, indent=2, ensure_ascii=False)
    else:
        # Generate valid Python dictionary representation
        lines = ["# Additional SDG terms extracted from UoA Keyword List Ver 1.1", "UOA_ADDITIONAL_SDG_TERMS = {"]
        for sdg in range(1, 17):
            terms = diff_map[sdg]
            if not terms:
                continue
            lines.append(f"    {sdg}: [")
            for t in terms:
                lines.append(f'        "{t}",')
            lines.append("    ],")
        lines.append("}")
        formatted_output = "\n".join(lines)

    if args.output:
        args.output.write_text(formatted_output, encoding="utf-8")
        print(f"Results written to {args.output}")
    else:
        print(formatted_output)


if __name__ == "__main__":
    main()
