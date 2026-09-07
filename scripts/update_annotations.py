#!/usr/bin/python3.11
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-

"""
Update references-annotations.bib using metadata from publications_map.json and references.bib.

Resolves contributor names in credit_contributions to numeric author indices in references.bib,
handles virtual indices for unlisted specific contributors, normalizes CReDiT role strings into
standard slugs, and merges author identifiers, CReDiT roles, and entry-level identifiers into
canonically ordered Biber annotations.
"""

import argparse
from pathlib import Path
import re
import sys
import json
import unicodedata
import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter

DEFAULT_MAP_FILE = "publications_map.json"
DEFAULT_BIB_FILE = "references.bib"
DEFAULT_ANN_FILE = "references-annotations.bib"

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

ANNOTATION_KEY_PRIORITY = {
    "orcid": 1,
    "kthid": 2,
    "equal": 3,
    "specific-contributor": 4,
    "affiliation": 5,
    "org": 6,
    "school": 7,
}
for _idx, _role in enumerate(CREDIT_ROLES_ORDER, start=10):
    ANNOTATION_KEY_PRIORITY[_role] = _idx


def normalize_string(s: str) -> str:
    """Normalize text for name matching: lowercase, ASCII, alphanumeric only."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("utf-8")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def normalize_credit_slug(role: str) -> str:
    """Convert human-readable CReDiT role strings into standard hyphenated slugs."""
    r = role.lower()
    r = r.replace("–", "-").replace("—", "-")  # Replace en/em dashes
    r = r.replace("&", "and")
    r = re.sub(r"\s+", "-", r)
    r = re.sub(r"[^a-z0-9\-]", "", r)
    r = re.sub(r"-+", "-", r).strip("-")
    # Standardize 'writing-review-and-editing' to 'writing-review-editing'
    if r == "writing-review-and-editing":
        r = "writing-review-editing"
    return r


def split_authors(author_str: str) -> list[str]:
    """Split BibTeX author field into individual author names, honoring braces."""
    if not author_str:
        return []
    raw_authors = re.split(r"\s+and\s+", author_str.strip(), flags=re.IGNORECASE)
    return [a.strip() for a in raw_authors if a.strip()]


def format_specific_contributor_name(contrib: dict) -> str:
    """Format a specific contributor dictionary into standard BibTeX 'Last, First'."""
    lname = contrib.get("lname", "").strip()
    fname = contrib.get("fname", "").strip()
    if lname and fname:
        return f"{lname}, {fname}"
    return lname or fname


def match_contributor_to_index(contrib_name: str, bib_authors: list[str]) -> int | None:
    """Match a contributor name from the JSON map to a 1-based author index."""
    norm_contrib = normalize_string(contrib_name)
    contrib_last = normalize_string(contrib_name.split(",")[0])

    # 1. Exact normalized match
    for idx, author in enumerate(bib_authors, start=1):
        if norm_contrib == normalize_string(author):
            return idx

    # 2. Match by family name plus first initial
    for idx, author in enumerate(bib_authors, start=1):
        auth_last = normalize_string(author.split(",")[0])
        if contrib_last and auth_last and (contrib_last == auth_last):
            c_parts = contrib_name.split(",")
            a_parts = author.split(",")
            if len(c_parts) > 1 and len(a_parts) > 1:
                c_init = normalize_string(c_parts[1])[:1]
                a_init = normalize_string(a_parts[1])[:1]
                if c_init and a_init and c_init != a_init:
                    continue
            return idx

    return None


def parse_annotation_field(raw_text: str) -> dict[str, dict[str, str]]:
    """Parse a Biber annotation string into a structured dictionary."""
    data = {}
    if not raw_text:
        return data

    clauses = [c.strip() for c in raw_text.strip().split(";") if c.strip()]
    for clause in clauses:
        m = re.match(r"^(\d+):([a-zA-Z0-9_\-]+)\s*=\s*[\"{]?(.*?)[\"}]?$", clause)
        if m:
            idx = m.group(1)
            key = m.group(2).lower()
            val = m.group(3)
            data.setdefault(idx, {})[key] = val
        else:
            m_entry = re.match(r"^([a-zA-Z0-9_\-]+)\s*=\s*[\"{]?(.*?)[\"}]?$", clause)
            if m_entry:
                key = m_entry.group(1).lower()
                val = m_entry.group(2)
                data.setdefault("entry", {})[key] = val
    return data


def format_annotation_dict(data: dict[str, dict[str, str]]) -> str:
    """Serialize structured annotation data back into a canonically sorted Biber string."""
    clauses = []
    for target, pairs in data.items():
        if target.isdigit():
            idx = int(target)
            for k, v in pairs.items():
                prio = ANNOTATION_KEY_PRIORITY.get(k.lower(), 99)
                clauses.append((idx, prio, f'{idx}:{k}="{v}"'))
        else:
            for k, v in pairs.items():
                prio = ANNOTATION_KEY_PRIORITY.get(k.lower(), 99)
                clauses.append((0, prio, f'{k}="{v}"'))

    clauses.sort(key=lambda item: (item[0], item[1]))
    if not clauses:
        return ""

    return "\n" + ";\n".join("    " + c[2] for c in clauses) + ";\n  "


def load_bib_file(file_path: Path) -> bibtexparser.bibdatabase.BibDatabase:
    """Load and parse a .bib file into a BibDatabase object."""
    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    parser.homogenise_fields = False

    if not file_path.exists():
        return bibtexparser.bibdatabase.BibDatabase()

    with open(file_path, "r", encoding="utf-8") as f:
        return bibtexparser.load(f, parser=parser)


def write_bib_file(db: bibtexparser.bibdatabase.BibDatabase, output_path: Path):
    """Write BibDatabase to a .bib file cleanly."""
    writer = BibTexWriter()
    writer.indent = "  "
    writer.order_entries_by = None
    writer.add_trailing_comma = True

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(writer.write(db))


def update_annotations(map_file: Path, bib_file: Path, ann_file: Path):
    if not map_file.exists():
        print(f"Error: Map file not found: {map_file}", file=sys.stderr)
        sys.exit(1)

    with open(map_file, "r", encoding="utf-8") as f:
        pub_map = json.load(f)

    base_db = load_bib_file(bib_file)
    base_entries_map = {entry["ID"]: entry for entry in base_db.entries}

    ann_db = load_bib_file(ann_file)
    ann_entries_map = {entry["ID"]: entry for entry in ann_db.entries}
    ann_order = [entry["ID"] for entry in ann_db.entries]

    modified_count = 0
    created_count = 0

    for diva_id, item in pub_map.items():
        bib_key = item.get("bib_key")
        if not bib_key or not item.get("in_bib", False):
            continue

        base_entry = base_entries_map.get(bib_key, {})
        raw_bib_authors = split_authors(base_entry.get("author", ""))
        combined_authors = list(raw_bib_authors)

        # Append specific contributors to create virtual author indices
        specific_contributors = item.get("specific_contributors", [])
        specific_indices = {}

        for spec in specific_contributors:
            spec_name = format_specific_contributor_name(spec)
            if spec_name:
                combined_authors.append(spec_name)
                assigned_idx = len(combined_authors)
                specific_indices[assigned_idx] = spec_name

        author_identifiers = item.get("author_identifiers", {})
        credit_contributions = (
            item.get("credit_contributions")
            or item.get("credit_roles")
            or item.get("credit")
            or {}
        )
        equal_contributors = item.get("equal_contributors", [])
        identifiers = item.get("identifiers", {})

        if bib_key in ann_entries_map:
            ann_entry = ann_entries_map[bib_key]
            is_new = False
        else:
            ann_entry = {
                "ID": bib_key,
                "ENTRYTYPE": base_entry.get("ENTRYTYPE", "article"),
            }
            ann_entries_map[bib_key] = ann_entry
            ann_order.append(bib_key)
            is_new = True

        # --- 1. Merge Author Annotations (author+an) ---
        current_author_an = parse_annotation_field(ann_entry.get("author+an", ""))

        # 1a. Inject / Update Harvested Identifiers (ORCID, KTHID)
        for idx_str, harvested_ids in author_identifiers.items():
            author_data = current_author_an.setdefault(str(idx_str), {})
            for id_key, id_val in harvested_ids.items():
                if id_val:
                    author_data[id_key] = id_val

        # 1b. Mark Specific Contributors with explicit identification
        for s_idx, s_name in specific_indices.items():
            author_data = current_author_an.setdefault(str(s_idx), {})
            author_data["specific-contributor"] = s_name

        # 1c. Map and Inject CReDiT Contributions
        for contrib_key, roles in credit_contributions.items():
            target_idx = None
            if str(contrib_key).isdigit():
                target_idx = int(contrib_key)
            elif combined_authors:
                target_idx = match_contributor_to_index(contrib_key, combined_authors)

            if target_idx is None:
                print(
                    f"Warning: Could not resolve contributor '{contrib_key}' "
                    f"to an author index for key '{bib_key}'",
                    file=sys.stderr,
                )
                continue

            author_data = current_author_an.setdefault(str(target_idx), {})

            if isinstance(roles, list):
                for role_str in roles:
                    role_slug = normalize_credit_slug(role_str)
                    author_data[role_slug] = "lead"
            elif isinstance(roles, dict):
                for role_str, degree in roles.items():
                    role_slug = normalize_credit_slug(role_str)
                    if degree:
                        author_data[role_slug] = str(degree)

        # 1d. Inject Equal Contribution Flags
        for eq_contrib in equal_contributors:
            eq_idx = None
            if str(eq_contrib).isdigit():
                eq_idx = int(eq_contrib)
            elif combined_authors:
                eq_idx = match_contributor_to_index(eq_contrib, combined_authors)
            if eq_idx is not None:
                current_author_an.setdefault(str(eq_idx), {})["equal"] = "true"

        formatted_author_an = format_annotation_dict(current_author_an)
        if formatted_author_an:
            ann_entry["author+an"] = formatted_author_an

        # --- 2. Merge Entry Annotations (entry+an) ---
        current_entry_an = parse_annotation_field(ann_entry.get("entry+an", ""))
        entry_data = current_entry_an.setdefault("entry", {})

        entry_data["diva_id"] = diva_id

        if item.get("contribution_note"):
            entry_data["contribution_note"] = item["contribution_note"]
        if specific_contributors or item.get("specific_contributors"):
            entry_data["has_specific_contributors"] = "true"

        # Inject external PIDs (skipping standard DOI/URL handled in base bib)
        for id_type, id_val in identifiers.items():
            if id_type.lower() not in ["doi", "url"]:
                entry_data[id_type.lower()] = id_val

        formatted_entry_an = format_annotation_dict(current_entry_an)
        if formatted_entry_an:
            ann_entry["entry+an"] = formatted_entry_an

        if is_new:
            created_count += 1
        else:
            modified_count += 1

    ann_db.entries = [ann_entries_map[k] for k in ann_order]
    write_bib_file(ann_db, ann_file)
    print(
        f"Update complete: {created_count} created, {modified_count} updated in {ann_file}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Update references-annotations.bib with PIDs and CReDiT roles from publications_map.json."
    )
    parser.add_argument(
        "--map",
        type=Path,
        default=Path(DEFAULT_MAP_FILE),
        help=f"Publications map JSON file (default: {DEFAULT_MAP_FILE})",
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=Path(DEFAULT_BIB_FILE),
        help=f"Canonical references .bib file (default: {DEFAULT_BIB_FILE})",
    )
    parser.add_argument(
        "--annotations",
        type=Path,
        default=Path(DEFAULT_ANN_FILE),
        help=f"Output/updated annotations .bib file (default: {DEFAULT_ANN_FILE})",
    )

    args = parser.parse_args()
    update_annotations(args.map, args.base, args.annotations)


if __name__ == "__main__":
    main()
