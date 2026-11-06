#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""sdg_classifier.py

Audits and classifies research text against the UN SDGs using
Elsevier V4 and UN DESA keyword sets.

Supports:
  - Direct ingestion of fordiva.json (extracting Title, Abstract, and Keywords).
  - Standalone PDF or text file ingestion.
  - Updating fordiva.json directly with recommended SDGs.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

import Elsevier_Data_Repository_V4_SDG_terms as sdg_dict


# Generic meta-terms that falsely inflate SDG 8 across unrelated abstracts
GENERIC_SDG_META_TERMS = {
    "sdg", "sdgs", "sustainable development",
    "sustainable development goals", "sustainable development goals (sdgs)",
    "development goals", "agenda 2030", "the concept of sustainable development",
    "concept of sustainable development", "a sustainable development"
}

# Auxiliary terms for modern ICT, Biomedical Engineering, and Health Informatics
# to bridge the gap between pure clinical terms and computational engineering.
AUXILIARY_SDG_TERMS = {
    3: [
        # Clinical & Cardiovascular (previously added)
        "cardiac arrest", "cardiac", "electrocardiogram", "ecg", "ecgi",
        "arrhythmia", "healthcare", "health care", "clinical adoption",
        "clinical", "clinicians", "medical imaging", "biomedical",
        "cardiovascular", "heart disease", "patient monitoring",
        "hospitalized patients", "vital signs", "e-health", "mhealth",

        # Biomechanics, Assistive Technology & Rehabilitation
        "prosthesis", "prostheses", "prosthetic", "prosthetic foot",
        "prosthetics", "prosthetic users", "transtibial", "transfemoral",
        "amputee", "amputees", "amputation", "orthosis", "orthoses",
        "orthotics", "biomechanics", "biomechanical", "gait",
        "gait analysis", "motion capture", "rehabilitation",
        "assistive technology", "assistive devices", "force plate",
        "force plates", "heel strike"
    ],
    9: [
        # AI / Software / Digital Infrastructure
        "artificial intelligence", "machine learning", "deep learning",
        "federated learning", "neural network", "neural networks",
        "distributed computing", "edge computing", "edge devices",
        "internet of things", "sensor network", "telecommunications",
        "cloud computing", "data infrastructure", "anomaly detection",

        # Electrical, Electronic & Semiconductor Engineering
        "power converter", "power converters", "traction inverter",
        "inverter", "inverters", "power electronics", "gate driver",
        "gate drivers", "silicon carbide", "mosfet", "mosfets",
        "semiconductor", "semiconductors", "soft switching",
        "printed circuit board", "pcb", "hardware prototype",
        "circuit design", "finite element method"
    ],
    10: [
        # Target 10.2 (empowerment and social/physical inclusion of persons with disabilities) and Target 10.3 (equal opportunity)
        "assistive technology", "prosthetic users", "physical rehabilitation"
        ],
    11: [
        # Regional/Municipal governance and local resilience
        "municipalities", "municipality", "municipal governance",
        "regional governance", "local food systems", "social-ecological systems",
        "governing for resilience", "resilience capacities", "local produce",
        "urban and regional", "community resilience"
    ],
    12: [
        # Sustainable Consumption and Production (SCP) & Supply Chains
        "production to consumption", "production and consumption",
        "food supply systems", "food supply chain", "food governance",
        "food systems governance", "sustainable supply", "sustainable food",
        "missing middle", "circular food"
    ]
}

def clean_latex_artifacts(text: str) -> str:
    """Strips basic TeX macros, braces, and formatting noise from abstract strings."""
    if not text:
        return ""
    # Strip TeX comments only if % is NOT preceded by a backslash
    t = re.sub(r"(?<!\\)%.*?\n", "\n", text)
    
    # Replace literal escaped percent signs with standard percent signs (or a space)
    t = t.replace(r"\%", "%")

    # Strip macro commands like \linebreak[4], \item, \par, \engExpl{...}
    t = re.sub(r"\\[a-zA-Z]+(\[[^\]]*\])?(\{([^}]*)\})?", r" \3 ", t)
    
    # Strip braces, hyphens, and discretionary breaks
    t = re.sub(r"[{}]", " ", t)
    t = t.replace("---", " ").replace("--", " ")
    return " ".join(t.split()).lower()

def extract_explicit_sdg_mentions(text: str) -> set[int]:
    """Extracts explicitly stated goals such as 'SDG 3, 4, 7, 8 and 13' or 'SDGs 3 and 13'."""
    explicit_goals = set()
    
    # Normalize punctuation around words to spaces, keeping hyphens/commas with numbers
    # Tokenize by whitespace
    tokens = text.split()
    
    i = 0
    while i < len(tokens):
        raw_tok = tokens[i].strip("()[]{}:;.\"").lower()
        # Check if current token is an SDG anchor
        if raw_tok in ("sdg", "sdgs", "goal", "goals"):
            j = i + 1
            # Scan forward through the arguments
            while j < len(tokens):
                t = tokens[j].strip("()[]{};.\"").lower()
                
                # If it's a conjunction, continue scanning
                if t in ("and", "&", "or", ","):
                    j += 1
                    continue
                
                # Check for integers in this token (e.g. '3,', '13', '3-5')
                found_digits = re.findall(r"\b\d+\b", t)
                if found_digits:
                    for d in found_digits:
                        val = int(d)
                        if 1 <= val <= 17:
                            explicit_goals.add(val)
                    j += 1
                else:
                    # Hit a normal word like 'indicators', stop scanning this group
                    break
            i = j
        else:
            i += 1
            
    print(f"extract_explicit_sdg_mentions: {explicit_goals=}")
    return explicit_goals

def build_sdg_term_index() -> dict[int, list[str]]:
    """Collects terms for SDG 1 through SDG 16, stripping meta-terms and adding auxiliary terms."""
    mapping = {}
    for i in range(1, 17):
        var_name = f"sdg{i}_terms"
        raw_terms = list(getattr(sdg_dict, var_name, []))

        # Merge auxiliary domain terms
        if i in AUXILIARY_SDG_TERMS:
            raw_terms.extend(AUXILIARY_SDG_TERMS[i])

        cleaned_terms = set()
        for t in raw_terms:
            t_clean = t.strip().lower()
            # Normalize leading conjunctions BEFORE filtering meta-terms
            t_clean = re.sub(r"^(and|the)\s+", "", t_clean).strip()
            if not t_clean or t_clean in GENERIC_SDG_META_TERMS:
                continue
            cleaned_terms.add(t_clean)

        # Sort descending by length so compound phrases match before sub-tokens
        mapping[i] = sorted(list(cleaned_terms), key=len, reverse=True)
    return mapping

def extract_from_fordiva(json_path: Path) -> tuple[str, dict]:
    """Extracts Title, Subtitle, Abstract, and Keywords from fordiva.json."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    title_block = data.get("Title", {})
    main_title = title_block.get("Main title", "")
    sub_title = title_block.get("Subtitle", "")

    abstract_block = data.get("abstracts", {})
    abstract_eng = abstract_block.get("eng", "")

    keywords_block = data.get("keywords", {})
    keywords_eng = keywords_block.get("eng", "")

    # Combine primary metadata fields
    raw_content = f"{main_title}. {sub_title}. {abstract_eng}. {keywords_eng}"
    cleaned_content = clean_latex_artifacts(raw_content)

    return cleaned_content, data


def extract_from_pdf(pdf_path: Path) -> str:
    """Extracts text from PDF while removing references."""
    if fitz is None:
        raise RuntimeError(
            "PyMuPDF ('fitz') is required for PDF parsing. Install with: pip install pymupdf"
        )
    doc = fitz.open(str(pdf_path))
    pages = [doc[i].get_text("text") for i in range(len(doc))]
    full_text = "\n".join(pages)

    # Reconnect line breaks
    full_text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", full_text)

    # Strip bibliography
    bib_marker = re.search(
        r"\n(References|Bibliography|Litteraturförteckning)\s*\n",
        full_text,
        flags=re.IGNORECASE,
    )
    if bib_marker:
        full_text = full_text[: bib_marker.start()]

    return full_text.lower()



def scan_text_for_sdgs(
    text: str,
) -> tuple[dict[int, int], dict[int, list[tuple[str, int]]], set[int]]:
    """Calculates keyword match frequencies and captures explicit in-text SDG mentions."""
    sdg_terms = build_sdg_term_index()
    counts: dict[int, int] = {i: 0 for i in range(1, 18)}
    matched_phrases: dict[int, list[tuple[str, int]]] = {i: [] for i in range(1, 18)}

    padded_text = f" {text} "

    # 1. Check for explicit author declarations (e.g., "towards SDG 3, 4, 7, 8 and 13")
    explicit_mentions = extract_explicit_sdg_mentions(text)

    # 2. Match SDGs 1–16
    for sdg_num, terms in sdg_terms.items():
        counter = Counter()
        for phrase in terms:
            if len(phrase) < 3:
                continue

            escaped = re.escape(phrase)
            pattern = rf"(?<!\w){escaped}(?!\w)"
            matches = len(re.findall(pattern, padded_text))
            if matches > 0:
                counter[phrase] += matches

        counts[sdg_num] = sum(counter.values())
        matched_phrases[sdg_num] = counter.most_common(10)

    # 3. Match SDG 17
    sdg17_counter = Counter()
    for pattern in getattr(sdg_dict, "SDG17_PATTERNS", []):
        hits = re.findall(pattern, padded_text, flags=re.IGNORECASE)
        if hits:
            clean_name = pattern.replace(r"\b", "").replace(r".*?", " ")
            sdg17_counter[clean_name] += len(hits)

    counts[17] = sum(sdg17_counter.values())
    matched_phrases[17] = sdg17_counter.most_common(10)

    return counts, matched_phrases, explicit_mentions

def main():
    parser = argparse.ArgumentParser(
        description="Audit and classify thesis metadata against UN SDGs."
    )
    parser.add_argument(
        "input_file",
        type=Path,
        help="Path to fordiva.json, compiled .pdf, or text file",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Maximum number of candidate SDGs to infer from keywords (default: 3)",
    )
    parser.add_argument(
        "--min-hits",
        type=int,
        default=2,
        help="Minimum keyword occurrences required to consider an SDG (default: 2)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="If input is fordiva.json, write recommended SDGs directly back to the file",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print top matched terms per SDG",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit raw vector output as JSON",
    )

    args = parser.parse_args()

    if not args.input_file.is_file():
        sys.exit(f"Error: File '{args.input_file}' not found.")

    is_fordiva = (
        args.input_file.name == "fordiva.json"
        or args.input_file.suffix.lower() == ".json"
    )
    fordiva_data = None

    if is_fordiva:
        text, fordiva_data = extract_from_fordiva(args.input_file)
        if args.verbose:
            print(f"is_fordiva: {text=}")
    elif args.input_file.suffix.lower() == ".pdf":
        text = extract_from_pdf(args.input_file)
    else:
        text = clean_latex_artifacts(
            args.input_file.read_text(encoding="utf-8")
        )

    counts, matched_phrases, explicit_mentions = scan_text_for_sdgs(text)
    vector = [counts[i] for i in range(1, 18)]

    # 1. Rank keyword hits descending by frequency
    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    top_by_keyword = [g for g, c in ranked if c >= args.min_hits][: args.top_k]

    # 2. Determine final suggested goals (Option A: Explicit author declarations take precedence)
    if explicit_mentions:
        combined_goals = sorted(list(explicit_mentions))
    else:
        combined_goals = sorted(top_by_keyword)

    top_candidates = [f"SDG {g}" for g in combined_goals]
    suggestion_str = ", ".join(top_candidates) if top_candidates else "None"

    # JSON output branch
    if args.json:
        out = {
            "file": args.input_file.name,
            "sdg_vector": vector,
            "explicit_mentions": sorted(list(explicit_mentions)),
            "ranked_matches": {f"SDG {g}": c for g, c in ranked if c > 0},
            "recommended": top_candidates,
        }
        print(json.dumps(out, indent=2))
        return

    # Standard CLI Report
    print("=" * 65)
    print(f"==> SDG Classification Report for: {args.input_file.name}")
    print(f"Match Vector (SDG 1 - 17):\n{vector}\n")

    if is_fordiva:
        current_sdgs = fordiva_data.get("SDGs", "")
        is_placeholder = bool(
            re.search(r"XXX|TODO|ddddd", current_sdgs, re.IGNORECASE)
            or not current_sdgs.strip()
        )
        status_symbol = "❌" if is_placeholder else "✔"
        print(
            f"Current value in fordiva.json: \"{current_sdgs}\" {status_symbol}"
        )
        if is_placeholder:
            print("  [!] WARNING: Unresolved placeholder detected in 'SDGs'!")
        print()

        f_abstracts = fordiva_data.get("abstracts", {})
        eng_abstract = f_abstracts.get("eng", "")
        if "Enter your abstract here and remove this line!" in eng_abstract:
            print("This looks like the abstract from the template! Try adding an actual abstract.")
            sys.exit(1)

    # Display Explicit In-Text Declarations
    if explicit_mentions:
        print("Explicit In-Text Goal Declarations:")
        for g in sorted(explicit_mentions):
            k_hits = counts[g]
            print(f"  [*] SDG {g:2d} (keyword occurrences: {k_hits})")
        print()

    # Display Ranked Topical Keyword Hits
    print("Top Keyword Matching Goals:")
    has_keyword_hits = False
    for rank, (goal, hits) in enumerate(ranked, 1):
        if hits == 0:
            continue
        has_keyword_hits = True
        flag = " [Explicitly cited]" if goal in explicit_mentions else ""
        print(f"  [{rank}] SDG {goal:2d} : {hits:4d} hit(s){flag}")
        if args.verbose:
            for phrase, count in matched_phrases[goal][:5]:
                print(f"       * \"{phrase}\": {count}")

    if not has_keyword_hits:
        print("  (No significant keyword matches found)")

    print("-" * 65)
    if explicit_mentions:
        print("Note: Suggested value is scoped to author's explicit in-text declarations.")
    print(f"Suggested 'SDGs' value: \"{suggestion_str}\"")

    # In-place update for fordiva.json
    if is_fordiva and args.update:
        if top_candidates:
            fordiva_data["SDGs"] = suggestion_str
            with open(args.input_file, "w", encoding="utf-8") as f:
                json.dump(fordiva_data, f, indent=2, ensure_ascii=False)
            print(
                f"\n✔ Successfully updated '{args.input_file.name}' with: \"{suggestion_str}\""
            )
        else:
            print("\n[!] No significant SDG terms found; file not updated.")
    print("=" * 65)


if __name__ == "__main__":
    main()
