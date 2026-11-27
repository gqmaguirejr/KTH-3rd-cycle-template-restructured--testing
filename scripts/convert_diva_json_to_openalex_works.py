#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
convert_diva_json_to_openalex_works.py

Loads concatenated JSON objects or a JSON list from a DiVA export, deduplicates
by divaID, cleans HTML/TeX artifacts, and writes OpenAlex-compatible JSONL
records (id, title, abstract) for `openalex-sdgs`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


def clean_text_artifacts(text: str) -> str:
    """Removes HTML and TeX artifacts and normalizes whitespace."""
    if not text:
        return ""
    t = re.sub(r"<[^>]+>", " ", text)
    t = re.sub(r"(?<!\\)%.*?\n", "\n", t)
    t = t.replace(r"\%", "%")
    t = re.sub(r"\\[a-zA-Z]+(\[[^\]]*\])?(\{([^}]*)\})?", r" \3 ", t)
    t = re.sub(r"[{}]", " ", t)
    t = t.replace("---", " ").replace("--", " ")
    return " ".join(t.split())


def load_unique_records(path: Path) -> list[dict]:
    """Load concatenated JSON objects or a JSON list and deduplicate by divaID."""
    raw = path.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    idx, n = 0, len(raw)
    records: list[dict] = []

    while idx < n:
        while idx < n and raw[idx].isspace():
            idx += 1
        if idx >= n:
            break
        obj, idx = decoder.raw_decode(raw, idx)
        if isinstance(obj, list):
            records.extend(obj)
        elif isinstance(obj, dict):
            # Support both individual record dicts and {divaID: record_dict} mappings
            if "divaID" in obj or "Title" in obj or "abstracts" in obj:
                records.append(obj)
            else:
                for k, v in obj.items():
                    if isinstance(v, dict):
                        v.setdefault("divaID", k)
                        records.append(v)

    unique: dict[str, dict] = {}
    for fallback_idx, rec in enumerate(records):
        did = rec.get("divaID") or rec.get("id") or f"record_{fallback_idx}"
        if did not in unique:
            unique[did] = rec
    return list(unique.values())


def pack_work(record_id: str, data: dict) -> dict:
    """Packs primary/alternative titles, abstracts, and keywords into an OpenAlex work dict."""
    # 1. Primary and alternative titles
    titles: list[str] = []
    for key in ("Title", "Alternative title"):
        t_obj = data.get(key, {})
        if isinstance(t_obj, dict):
            main_t = clean_text_artifacts(t_obj.get("Main title") or "")
            sub_t = clean_text_artifacts(t_obj.get("Subtitle") or "")
            if main_t and sub_t:
                titles.append(f"{main_t}: {sub_t}")
            elif main_t:
                titles.append(main_t)
        elif isinstance(t_obj, str) and t_obj.strip():
            titles.append(clean_text_artifacts(t_obj))
    title_str = " / ".join(titles)

    # 2. Abstracts (eng + swe)
    parts: list[str] = []
    abs_obj = data.get("abstracts", {})
    if isinstance(abs_obj, dict):
        for lang in ("eng", "swe"):
            cleaned = clean_text_artifacts(abs_obj.get(lang) or "")
            if cleaned:
                parts.append(cleaned)
    else:
        for lang in ("eng", "swe"):
            cleaned = clean_text_artifacts(data.get(lang) or "")
            if cleaned:
                parts.append(cleaned)

    # 3. Keywords appended into the abstract text
    kw_list: list[str] = []
    kw_obj = data.get("keywords", {})
    if isinstance(kw_obj, dict):
        for lang in ("eng", "swe"):
            cleaned = clean_text_artifacts(kw_obj.get(lang) or "")
            if cleaned:
                kw_list.append(cleaned)
    elif isinstance(kw_obj, str) and kw_obj.strip():
        kw_list.append(clean_text_artifacts(kw_obj))

    if kw_list:
        parts.append("Keywords: " + "; ".join(kw_list))

    return {
        "id": record_id,
        "title": title_str,
        "abstract": "\n\n".join(parts),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert DiVA JSON records into works.jsonl for openalex-sdgs."
    )
    parser.add_argument(
        "input_json",
        nargs="?",
        type=Path,
        default=Path("/tmp/all-with-SDGs.json"),
        help="Path to input DiVA JSON file (default: /tmp/all-with-SDGs.json)",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=Path("works.jsonl"),
        help="Path to output JSONL file (default: works.jsonl)",
    )
    parser.add_argument(
        "--skip-empty",
        action="store_true",
        help="Skip records where both title and abstract are empty",
    )
    args = parser.parse_args()

    records = load_unique_records(args.input_json)
    print(f"Loaded {len(records)} unique divaID records from {args.input_json}.")

    written = 0
    skipped = 0
    with args.out.open("w", encoding="utf-8") as out:
        for i, rec in enumerate(records):
            rec_id = str(rec.get("divaID") or rec.get("id") or f"record_{i}")
            work = pack_work(rec_id, rec)
            if args.skip_empty and not work["title"] and not work["abstract"]:
                skipped += 1
                continue
            out.write(json.dumps(work, ensure_ascii=False) + "\n")
            written += 1

    print(f"Wrote {written} works to {args.out}" + (f" (skipped {skipped} empty)." if skipped else "."))


if __name__ == "__main__":
    main()
