#!/usr/bin/env python3
"""check_trita_collision.py

Audits TRITA and ISBN assignments by inspecting either:
  1. generated/assigned-TRITA.tex (and generated/assigned-ISBN.tex)
  2. fordiva.json
  3. Direct CLI argument (e.g. TRITA-EECS-AVL-2026:10)

Cross-checks DiVA MODS records for exact collisions and performs
high-water-mark (HWM) sequence analysis.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

SCHOOLS_INFO = {
    "ABE": {"L1": "5850"},
    "ITM": {"L1": "6023"},
    "SCI": {"L1": "6091"},
    "CBH": {"L1": "879224"},
    "EECS": {"L1": "879223"},
}

TRITA_RE = re.compile(
    r"^TRITA-(?P<school>CBH|EECS|ITM|ABE|SCI)-AVL-(?P<year>\d{4}):(?P<num>\d+)$"
)

DOCTORAL_PUB_TYPES = ["comprehensiveDoctoralThesis", "doctoralThesis"]


def parse_trita(trita_str: str) -> dict | None:
    """Normalizes and parses a TRITA string."""
    clean = trita_str.strip().replace(" ", "").replace("--", "-")
    m = TRITA_RE.match(clean)
    if not m:
        return None
    return {
        "school": m.group("school"),
        "year": int(m.group("year")),
        "num": int(m.group("num")),
        "canonical": (
            f"TRITA-{m.group('school')}-AVL-{m.group('year')}:{m.group('num')}"
        ),
        "num_str": f"{m.group('year')}:{m.group('num')}",
    }


def is_placeholder(val: str) -> bool:
    """Detects whether a TRITA or ISBN value is an unassigned template placeholder."""
    val_upper = val.upper()
    return any(
        x in val_upper
        for x in ["XXX", "0000", "***", "PLACEHOLDER", "ASSIGNED"]
    )


def extract_trita_from_tex(tex_path: str) -> str | None:
    """Extracts the TRITA string from an assigned-TRITA.tex file."""
    if not os.path.exists(tex_path):
        return None
    with open(tex_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Matches \trita{TRITA-EECS-AVL}{2026:10} or \trita{TRITA-EECS-AVL-2026:10}
    m = re.search(r"\\trita\s*\{([^}]+)\}(?:\{([^}]+)\})?", content)
    if not m:
        return None
    part1 = m.group(1).strip()
    part2 = m.group(2).strip() if m.group(2) else ""

    if part2:
        return f"{part1}-{part2}"
    return part1


def extract_isbn_from_tex(tex_path: str) -> str | None:
    """Extracts the ISBN from an assigned-ISBN.tex file."""
    if not os.path.exists(tex_path):
        return None
    with open(tex_path, "r", encoding="utf-8") as f:
        content = f.read()
    m = re.search(r"\\thesisISBN\s*\{([^}]+)\}", content)
    return m.group(1).strip() if m else None


def extract_metadata_from_fordiva(json_path: str) -> tuple[str | None, str | None]:
    """
    Extracts (canonical_trita, isbn) from fordiva.json using the exact
    DiVA ingest schema.
    """
    if not os.path.exists(json_path):
        return None, None
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 1. Extract TRITA from "Series" object
        series_obj = data.get("Series", {})
        title_of_series = (series_obj.get("Title of series") or "").strip()
        no_in_series = (series_obj.get("No. in series") or "").strip()

        trita = None
        if title_of_series and no_in_series:
            # Normalize "TRITA -- EECS-AVL" -> "TRITA-EECS-AVL"
            norm_title = re.sub(r"\s*--\s*|\s+-\s*", "-", title_of_series).strip()
            # Combine to form canonical "TRITA-EECS-AVL-2025:0001"
            trita = f"{norm_title}-{no_in_series}"

        # 2. Extract ISBN (search known locations: top-level, Identifiers, or Other information)
        isbn = None
        if "ISBN" in data:
            isbn = str(data["ISBN"]).strip()
        elif "isbn" in data:
            isbn = str(data["isbn"]).strip()
        elif "Identifiers" in data and isinstance(data["Identifiers"], dict):
            isbn = data["Identifiers"].get("ISBN") or data["Identifiers"].get("isbn")
        elif "Other information" in data and isinstance(data["Other information"], dict):
            isbn = data["Other information"].get("ISBN") or data["Other information"].get("isbn")

        return trita, isbn
    except Exception as e:
        print(f"::warning::Failed to parse {json_path}: {e}")
        return None, None

def fetch_school_mods(
    school: str, year: int, max_cache_hours: float = 24.0, refresh: bool = False
) -> str:
    cache_file = f"./school-{school}-{year}.mods"
    if not refresh and os.path.exists(cache_file):
        age_hours = (time.time() - os.path.getmtime(cache_file)) / 3600.0
        if age_hours < max_cache_hours:
            with open(cache_file, "r", encoding="utf-8") as f:
                return f.read()

    org_id = SCHOOLS_INFO.get(school, {}).get("L1")
    if not org_id:
        return ""

    aq2 = [
        [
            {"dateIssued": {"from": str(year), "to": str(year)}},
            {"organisationId": org_id, "organisationId-Xtra": True},
            {"publicationTypeCode": DOCTORAL_PUB_TYPES},
        ]
    ]
    query_params = {
        "format": "mods",
        "addFilename": "true",
        "aq": "[[]]",
        "aqe": "[]",
        "aq2": json.dumps(aq2),
        "onlyFullText": "false",
        "noOfRows": "5000",
        "sortOrder": "title_sort_asc",
    }
    url = (
        "https://kth.diva-portal.org/smash/export.jsf?"
        + urllib.parse.urlencode(query_params)
    )

    req = urllib.request.Request(
        url, headers={"User-Agent": "KTH-Thesis-Automation/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        content = resp.read().decode("utf-8")

    with open(cache_file, "w", encoding="utf-8") as f:
        f.write(content)
    return content


def extract_thesis_records(mods_xml: str) -> list[dict]:
    if not mods_xml.strip():
        return []

    root = ET.fromstring(mods_xml)
    ns = {"mods": "http://www.loc.gov/mods/v3"}
    records = []

    for entry in root.findall(".//mods:mods", ns):
        # 1. PID
        pid = ""
        for rec_id in entry.findall(
            ".//mods:recordInfo/mods:recordIdentifier", ns
        ):
            if rec_id.text and "diva2" in rec_id.text:
                pid = rec_id.text.strip()
                break

        # 2. Title
        title_el = entry.find(".//mods:titleInfo/mods:title", ns)
        sub_el = entry.find(".//mods:titleInfo/mods:subTitle", ns)
        title = (
            title_el.text.strip()
            if (title_el is not None and title_el.text)
            else "Untitled"
        )
        if sub_el is not None and sub_el.text:
            title += f": {sub_el.text.strip()}"

        # 3. Authors
        authors = []
        for name_el in entry.findall("./mods:name[@type='personal']", ns):
            is_aut = any(
                (r.text or "").strip().lower() == "aut"
                for r in name_el.findall("./mods:role/mods:roleTerm", ns)
            )
            if is_aut:
                g = (name_el.findtext("./mods:namePart[@type='given']", default="", namespaces=ns)).strip()
                f = (name_el.findtext("./mods:namePart[@type='family']", default="", namespaces=ns)).strip()
                if g or f:
                    authors.append(f"{g} {f}".strip())

        if not authors:
            for name_el in entry.findall("./mods:name[@type='personal']", ns):
                g = (name_el.findtext("./mods:namePart[@type='given']", default="", namespaces=ns)).strip()
                f = (name_el.findtext("./mods:namePart[@type='family']", default="", namespaces=ns)).strip()
                if g or f:
                    authors.append(f"{g} {f}".strip())

        # 4. TRITA / Series
        tritas = set()
        for ident in entry.findall("./mods:identifier", ns):
            text = (ident.text or "").strip()
            if "TRITA" in text:
                tritas.add(text)

        for series in entry.findall("./mods:relatedItem[@type='series']", ns):
            s_title = (series.findtext("./mods:titleInfo/mods:title", default="", namespaces=ns)).strip()
            s_num = (
                series.findtext("./mods:identifier[@type='issue number']", default="", namespaces=ns)
                or series.findtext("./mods:part//mods:number", default="", namespaces=ns)
            ).strip()
            if "TRITA" in s_title and s_num:
                tritas.add(f"{s_title}-{s_num}")
                tritas.add(f"{s_title}:{s_num}")

        # 5. ISBNs
        isbns = set()
        for ident in entry.findall("./mods:identifier[@type='isbn']", ns):
            text = (ident.text or "").strip().replace("-", "")
            if text:
                isbns.add(text)

        records.append(
            {
                "pid": pid,
                "title": title,
                "authors": ", ".join(authors) if authors else "Unknown Author",
                "tritas": list(tritas),
                "isbns": list(isbns),
            }
        )

    return records


def audit_thesis_identifiers(
    candidate_trita: str | None,
    candidate_isbn: str | None = None,
    refresh_cache: bool = False,
):
    print("=" * 65)
    print("==> AUDITING THESIS METADATA IDENTIFIERS")

    # --- 1. Audit TRITA ---
    if not candidate_trita or is_placeholder(candidate_trita):
        print("ℹ TRITA: Placeholder active (skipped verification).")
    else:
        parsed = parse_trita(candidate_trita)
        if not parsed:
            print(
                f"❌ SYNTAX ERROR: '{candidate_trita}' violates post-2018 KTH TRITA standards."
            )
            print("   Expected: TRITA-<SCHOOL>-AVL-<YEAR>:<NUMBER>")
            sys.exit(1)

        canonical = parsed["canonical"]
        school = parsed["school"]
        year = parsed["year"]
        candidate_num = parsed["num"]
        target_num_str = f"{year}:{candidate_num}"

        print(f"==> Verifying TRITA: {canonical}")
        mods_data = fetch_school_mods(school, year, refresh=refresh_cache)
        records = extract_thesis_records(mods_data)

        collision = None
        for rec in records:
            for t in rec["tritas"]:
                if (
                    canonical.lower() in t.lower()
                    or (
                        f"TRITA-{school}-AVL".lower() in t.lower()
                        and target_num_str in t
                    )
                ):
                    collision = rec
                    break
            if collision:
                break

        if collision:
            print(f"\n❌ FATAL COLLISION: {canonical} is ALREADY published in DiVA!")
            print(f"   Title:   {collision['title']}")
            print(f"   Authors: {collision['authors']}")
            if collision.get("pid"):
                print(
                    f"   DiVA:    https://kth.diva-portal.org/smash/record.jsf?pid={collision['pid']}"
                )
            print("   Contact your school DiVA administrator immediately.\n")
            sys.exit(2)

        print("✔ TRITA: No duplicate found in DiVA.")

        # HWM Check
        assigned_numbers = []
        for rec in records:
            for t in rec["tritas"]:
                m = TRITA_RE.search(t.replace(" ", ""))
                if (
                    m
                    and m.group("school") == school
                    and int(m.group("year")) == year
                ):
                    assigned_numbers.append(int(m.group("num")))

        assigned_numbers = sorted(set(assigned_numbers))
        if assigned_numbers:
            hwm = max(assigned_numbers)
            print(f"✔ DiVA Sequence Stats for {school} ({year}):")
            print(f"   * Total registered in DiVA: {len(assigned_numbers)}")
            print(f"   * Highest published sequence number (HWM): :{hwm}")

            if candidate_num < (hwm - 20):
                print(
                    f"⚠️  ANOMALY: Assigned :{candidate_num} is far below current HWM (:{hwm})."
                )
            elif candidate_num > (hwm + 80):
                print(
                    f"⚠️  VELOCITY: Assigned :{candidate_num} is unexpectedly high above HWM (:{hwm})."
                )
            else:
                print(f"✔ Assigned :{candidate_num} is in expected sequence window.")

    # --- 2. Audit ISBN ---
    if not candidate_isbn or is_placeholder(candidate_isbn):
        print("ℹ ISBN:  Placeholder active (skipped verification).")
    else:
        clean_isbn = candidate_isbn.replace("-", "").strip()
        print(f"==> Verifying ISBN:  {candidate_isbn}")
        if len(clean_isbn) != 13 or not clean_isbn.isdigit():
            print(f"⚠️  ISBN WARNING: '{candidate_isbn}' is not a valid 13-digit ISBN.")
        else:
            # Check collision across extracted records
            isbn_collision = None
            if "records" in locals() and records:
                for rec in records:
                    if clean_isbn in rec["isbns"]:
                        isbn_collision = rec
                        break

            if isbn_collision:
                print(f"\n❌ FATAL COLLISION: ISBN {candidate_isbn} is already registered!")
                print(f"   Title:   {isbn_collision['title']}")
                print(f"   Authors: {isbn_collision['authors']}")
                sys.exit(3)
            else:
                print("✔ ISBN:  No collision found in school records.")


def main():
    parser = argparse.ArgumentParser(
        description="Audit TRITA and ISBN assignments against DiVA."
    )
    parser.add_argument(
        "trita",
        nargs="?",
        help="Optional TRITA string (e.g. TRITA-EECS-AVL-2026:10)",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="Force refresh of DiVA MODS cache"
    )
    parser.add_argument(
        "--fordiva",
        default="fordiva.json",
        help="Path to fordiva.json metadata",
    )
    parser.add_argument(
        "--trita-file",
        default="generated/assigned-TRITA.tex",
        help="Path to assigned-TRITA.tex",
    )
    parser.add_argument(
        "--isbn-file",
        default="generated/assigned-ISBN.tex",
        help="Path to assigned-ISBN.tex",
    )

    args = parser.parse_args()

    trita = None
    isbn = None

    # Priority 1: Direct CLI argument
    if args.trita:
        trita = args.trita

    # Priority 2: generated/assigned-TRITA.tex & assigned-ISBN.tex
    if not trita and os.path.exists(args.trita_file):
        trita = extract_trita_from_tex(args.trita_file)
    if os.path.exists(args.isbn_file):
        isbn = extract_isbn_from_tex(args.isbn_file)

    # Priority 3: fordiva.json
    if (not trita or not isbn) and os.path.exists(args.fordiva):
        f_trita, f_isbn = extract_metadata_from_fordiva(args.fordiva)
        trita = trita or f_trita
        isbn = isbn or f_isbn

    if not trita and not isbn:
        print("ℹ No TRITA or ISBN assignments found (or files do not exist yet).")
        print(f"  Looked in: {args.trita_file}, {args.isbn_file}, {args.fordiva}")
        sys.exit(0)

    audit_thesis_identifiers(trita, isbn, refresh_cache=args.refresh)


if __name__ == "__main__":
    main()
