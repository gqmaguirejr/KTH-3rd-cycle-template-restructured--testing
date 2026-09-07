#!/usr/bin/python3.11
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-

"""
Merge references.bib and references-annotations.bib based on BibTeX citation keys.

Maintains standard bibliographic field order and enforces canonical ordering
of Biber data annotations (identifiers first, followed by CReDiT taxonomy).
"""

import argparse
from pathlib import Path
import re
import sys
import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter

testing = True
if testing:
    Basefile_name = "test"
    Basefile_name_annotations = "test-annotations"
    Basefile_name_merged = "test-merged"
else:
    Basefile_name = "references"
    Basefile_name_annotations = "references-annotations"
    Basefile_name_merged = "references-merged"

# Canonical field order for entries
FIELD_DISPLAY_ORDER = [
    "author",
    "author+an",
    "title",
    "title+an",
    "booktitle",
    "journal",
    "journal+an",
    "year",
    "volume",
    "number",
    "pages",
    "pages+an",
    "doi",
    "doi+an",
    "url",
    "url+an",
    "eprint",
    "eprint+an",
    "note",
    "note+an",
    "entry+an",
]

# Canonical CReDiT taxonomy order
CREDIT_ROLES_ORDER = [
    "conceptualization",
    "data-curation",
    "formal-analysis",
    "funding-acquisition",
    "investigation",
    "methodology",
    "project-administration",
    "resources",
    "software",
    "supervision",
    "validation",
    "visualization",
    "writing-original-draft",
    "writing-review-editing",
]

# Priority mapping for part annotation keys within an author index
ANNOTATION_KEY_PRIORITY = {
    "orcid": 1,
    "kthid": 2,
    "equal": 3,
    "specific-contributor": 4,
    "affiliation": 5,
    "org": 6,
    "school": 7,
}
# Map the 14 CReDiT roles starting after priority keys
for _idx, _role in enumerate(CREDIT_ROLES_ORDER, start=10):
    ANNOTATION_KEY_PRIORITY[_role] = _idx


def sort_annotation_content(raw_an_text: str) -> str:
    """Sort individual clauses within an annotation field (e.g., author+an).

    Ensures author indices (1:, 2:, ...) are sorted ascending, and keys within each
    author follow ANNOTATION_KEY_PRIORITY.
    """
    clauses = [c.strip() for c in raw_an_text.strip().split(";") if c.strip()]
    if not clauses:
        return raw_an_text

    parsed_clauses = []
    for clause in clauses:
        # Match pattern: <index>:<sub_key>=<value> or <key>=<value>
        m = re.match(r"^(\d+):([a-zA-Z0-9_\-]+)\s*=\s*(.*)$", clause)
        if m:
            idx = int(m.group(1))
            sub_key = m.group(2).lower()
            val = m.group(3)
            prio = ANNOTATION_KEY_PRIORITY.get(sub_key, 99)
            parsed_clauses.append((idx, prio, f"{idx}:{sub_key}={val}"))
        else:
            # Field-level or entry-level key without author index prefix
            m_entry = re.match(r"^([a-zA-Z0-9_\-]+)\s*=\s*(.*)$", clause)
            if m_entry:
                key = m_entry.group(1).lower()
                val = m_entry.group(2)
                prio = ANNOTATION_KEY_PRIORITY.get(key, 99)
                parsed_clauses.append((0, prio, f"{key}={val}"))
            else:
                parsed_clauses.append((999, 999, clause))

    # Sort by author index, then by defined key priority
    parsed_clauses.sort(key=lambda item: (item[0], item[1]))

    # Format cleanly with 4-space indentation and trailing semicolon
    formatted = (
        "\n"
        + ";\n".join("    " + item[2] for item in parsed_clauses)
        + ";\n  "
    )
    return formatted


class CustomOrderBibTexWriter(BibTexWriter):
    """Custom BibTexWriter that formats entry fields according to FIELD_DISPLAY_ORDER."""

    def _entry_to_bibtex(self, entry):
        bibtex = f"@{entry['ENTRYTYPE']}{{{entry['ID']},\n"

        # Separate known display fields from arbitrary leftover fields
        used_fields = set()

        for field in FIELD_DISPLAY_ORDER:
            if field in entry:
                bibtex += f"  {field:<10} = {{{entry[field]}}},\n"
                used_fields.add(field)

        # Append any remaining fields not in FIELD_DISPLAY_ORDER (sorted)
        for field in sorted(entry.keys()):
            if (
                field not in used_fields
                and field not in ["ENTRYTYPE", "ID"]
            ):
                bibtex += f"  {field:<10} = {{{entry[field]}}},\n"

        bibtex += "}\n\n"
        return bibtex


def load_bib_file(file_path: Path) -> bibtexparser.bibdatabase.BibDatabase:
    """Load and parse a .bib file into a BibDatabase object."""
    if not file_path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    parser.homogenise_fields = False

    with open(file_path, "r", encoding="utf-8") as f:
        return bibtexparser.load(f, parser=parser)


def merge_bib_databases(
    base_db: bibtexparser.bibdatabase.BibDatabase,
    ann_db: bibtexparser.bibdatabase.BibDatabase,
) -> bibtexparser.bibdatabase.BibDatabase:
    """Merge annotation database entries into base database entries matching on citation key."""
    merged_entries_map = {
        entry["ID"]: dict(entry) for entry in base_db.entries
    }
    merged_order = [entry["ID"] for entry in base_db.entries]
    unmatched_annotations = []

    for ann_entry in ann_db.entries:
        key = ann_entry.get("ID")
        if not key:
            continue

        if key in merged_entries_map:
            base_entry = merged_entries_map[key]
            for field, value in ann_entry.items():
                if field == "ENTRYTYPE" and base_entry.get("ENTRYTYPE"):
                    continue
                # Normalize and sort annotation fields
                if field.endswith("+an"):
                    value = sort_annotation_content(value)
                base_entry[field] = value
        else:
            unmatched_annotations.append(key)
            cleaned_ann = dict(ann_entry)
            for f, v in cleaned_ann.items():
                if f.endswith("+an"):
                    cleaned_ann[f] = sort_annotation_content(v)
            merged_entries_map[key] = cleaned_ann
            merged_order.append(key)

    if unmatched_annotations:
        print(
            f"Warning: Found {len(unmatched_annotations)} annotation entries with no matching "
            f"key in primary bibliography: {', '.join(unmatched_annotations)}",
            file=sys.stderr,
        )

    merged_db = bibtexparser.bibdatabase.BibDatabase()
    merged_db.entries = [merged_entries_map[k] for k in merged_order]
    return merged_db


def write_bib_file(
    db: bibtexparser.bibdatabase.BibDatabase, output_path: Path
):
    """Write BibDatabase using the ordered writer."""
    writer = CustomOrderBibTexWriter()
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(writer.write(db))


def main():
    parser = argparse.ArgumentParser(
        description=f"Merge {Basefile_name}.bib and {Basefile_name_annotations}.bib on citation keys."
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=Path(f"{Basefile_name}.bib"),
        help=f"Primary BibTeX file from reference manager (default: {Basefile_name}.bib)",
    )
    parser.add_argument(
        "--annotations",
        type=Path,
        default=Path(f"{Basefile_name_annotations}.bib"),
        help=f"Biber annotation overlay file (default: {Basefile_name_annotations}.bib)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(f"{Basefile_name_merged}.bib"),
        help=f"Destination merged BibTeX file (default: {Basefile_name_merged}.bib)",
    )

    args = parser.parse_args()

    print(f"Loading primary entries from: {args.base}")
    base_db = load_bib_file(args.base)

    if args.annotations.exists():
        print(f"Loading annotations from: {args.annotations}")
        ann_db = load_bib_file(args.annotations)
        merged_db = merge_bib_databases(base_db, ann_db)
    else:
        print(
            f"Note: {args.annotations} not found. Outputting unannotated base bibliography."
        )
        merged_db = base_db

    print(
        f"Writing {len(merged_db.entries)} unified entries to: {args.output}"
    )
    write_bib_file(merged_db, args.output)
    print("Merge complete.")


if __name__ == "__main__":
    main()
