#!/usr/bin/env python3
"""
check_trita_collision.py

Audits TRITA allocation validity, checks DiVA for public collisions,
and calculates the high-water mark for the given school and year
using the authoritative DiVA MODS export URL pattern.
"""

import sys
import os
import re
import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# KTH School L1 organization IDs in DiVA
SCHOOLS_INFO = {
    'ABE': {'L1': "5850"},
    'ITM': {'L1': "6023"},
    'SCI': {'L1': "6091"},
    'CBH': {'L1': "879224"},
    'EECS': {'L1': "879223"},
}

TRITA_RE = re.compile(
    r"^TRITA-(?P<school>CBH|EECS|ITM|ABE|SCI)-AVL-(?P<year>\d{4}):(?P<num>\d+)$"
)

DOCTORAL_PUB_TYPES = ["comprehensiveDoctoralThesis", "doctoralThesis"]


def parse_trita(trita_str: str) -> dict | None:
    m = TRITA_RE.match(trita_str.strip())
    if not m:
        return None
    return {
        "school": m.group("school"),
        "year": int(m.group("year")),
        "num": int(m.group("num")),
        "canonical": f"TRITA-{m.group('school')}-AVL-{m.group('year')}:{m.group('num')}",
        "num_str": f"{m.group('year')}:{m.group('num')}",
    }


def fetch_school_mods(school: str, year: int, max_cache_hours: float = 24.0) -> str:
    """Fetches or reads cached DiVA MODS XML for a given school and calendar year."""
    cache_file = f"./school-{school}-{year}.mods"

    if os.path.exists(cache_file):
        age_hours = (time.time() - os.path.getmtime(cache_file)) / 3600.0
        if age_hours < max_cache_hours:
            with open(cache_file, "r", encoding="utf-8") as f:
                return f.read()

    org_id = SCHOOLS_INFO.get(school, {}).get("L1")
    if not org_id:
        return ""

    aq2 = [[
        {"dateIssued": {"from": str(year), "to": str(year)}},
        {"organisationId": org_id, "organisationId-Xtra": True},
        {"publicationTypeCode": DOCTORAL_PUB_TYPES}
    ]]

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

    url = "https://kth.diva-portal.org/smash/export.jsf?" + urllib.parse.urlencode(query_params)

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "KTH-Thesis-Automation/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        content = resp.read().decode("utf-8")

    with open(cache_file, "w", encoding="utf-8") as f:
        f.write(content)

    return content


def extract_thesis_records(mods_xml: str) -> list[dict]:
    """Parses MODS XML and extracts canonical metadata for each thesis."""
    if not mods_xml.strip():
        return []

    root = ET.fromstring(mods_xml)
    ns = {"mods": "http://www.loc.gov/mods/v3"}
    records = []

    for entry in root.findall(".//mods:mods", ns):
        # 1. PID
        pid = ""
        for rec_id in entry.findall(".//mods:recordInfo/mods:recordIdentifier", ns):
            if rec_id.text and "diva2" in rec_id.text:
                pid = rec_id.text.strip()
                break

        # 2. Title
        title_el = entry.find(".//mods:titleInfo/mods:title", ns)
        sub_el = entry.find(".//mods:titleInfo/mods:subTitle", ns)
        title = title_el.text.strip() if (title_el is not None and title_el.text) else "Untitled"
        if sub_el is not None and sub_el.text:
            title += f": {sub_el.text.strip()}"

        # 3. Authors (Filtered safely without nested XPath predicates)
        authors = []
        for name_el in entry.findall("./mods:name[@type='personal']", ns):
            is_aut = False
            for r_node in name_el.findall("./mods:role/mods:roleTerm", ns):
                if (r_node.text or "").strip().lower() == "aut":
                    is_aut = True
                    break
            if is_aut:
                given = name_el.find("./mods:namePart[@type='given']", ns)
                family = name_el.find("./mods:namePart[@type='family']", ns)
                g = given.text.strip() if (given is not None and given.text) else ""
                f = family.text.strip() if (family is not None and family.text) else ""
                if g or f:
                    authors.append(f"{g} {f}".strip())

        # Fallback if no explicit 'aut' role attribute
        if not authors:
            for name_el in entry.findall("./mods:name[@type='personal']", ns):
                given = name_el.find("./mods:namePart[@type='given']", ns)
                family = name_el.find("./mods:namePart[@type='family']", ns)
                g = given.text.strip() if (given is not None and given.text) else ""
                f = family.text.strip() if (family is not None and family.text) else ""
                if g or f:
                    authors.append(f"{g} {f}".strip())

        # 4. Extract all associated TRITA / Series strings
        tritas = set()

        # Check explicit identifier elements
        for ident in entry.findall("./mods:identifier", ns):
            text = (ident.text or "").strip()
            if "TRITA" in text:
                tritas.add(text)

        # Check series elements: <title>TRITA-EECS-AVL</title> ... <identifier type="issue number">2026:15</identifier> or <number>2026:15</number>
        for series in entry.findall("./mods:relatedItem[@type='series']", ns):
            title_node = series.find("./mods:titleInfo/mods:title", ns)
            s_title = (title_node.text or "").strip() if title_node is not None else ""

            # Check issue number in identifier or part/number
            issue_node = series.find("./mods:identifier[@type='issue number']", ns)
            if issue_node is None:
                issue_node = series.find("./mods:part//mods:number", ns)

            s_num = (issue_node.text or "").strip() if issue_node is not None else ""

            if "TRITA" in s_title and s_num:
                tritas.add(f"{s_title}-{s_num}")
                tritas.add(f"{s_title}:{s_num}")

        records.append({
            "pid": pid,
            "title": title,
            "authors": ", ".join(authors) if authors else "Unknown Author",
            "tritas": list(tritas),
        })

    return records


def audit_candidate_trita(trita_input: str):
    print(f"==> Auditing TRITA assignment: '{trita_input}'")
    parsed = parse_trita(trita_input)
    if not parsed:
        print(f"❌ SYNTAX ERROR: '{trita_input}' violates post-2018 KTH TRITA standards.")
        print("   Expected format: TRITA-<SCHOOL>-AVL-<YEAR>:<NUMBER>")
        print(f"   Schools permitted: {', '.join(SCHOOLS_INFO.keys())}")
        sys.exit(1)

    canonical = parsed["canonical"]
    school = parsed["school"]
    year = parsed["year"]
    candidate_num = parsed["num"]

    mods_data = fetch_school_mods(school, year)
    if not mods_data:
        print(f"⚠️ Could not retrieve DiVA records for school {school} in {year}.")
        sys.exit(1)

    records = extract_thesis_records(mods_data)

    # 1. Exact Collision Check
    collision = None
    target_num_str = f"{year}:{candidate_num}"

    for rec in records:
        for t in rec["tritas"]:
            if (
                canonical.lower() in t.lower()
                or (f"TRITA-{school}-AVL".lower() in t.lower() and target_num_str in t)
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
            print(f"   DiVA:    https://kth.diva-portal.org/smash/record.jsf?pid={collision['pid']}")
        print("   This number cannot be reused. Contact your school DiVA administrator immediately.\n")
        sys.exit(2)

    print("✔ No exact public collision detected in DiVA.")

    # 2. Numerical Sequence & High-Water Mark (HWM) Analysis
    published_nums = []
    for rec in records:
        for t in rec["tritas"]:
            m = TRITA_RE.search(t.replace(" ", ""))
            if m and m.group("school") == school and int(m.group("year")) == year:
                published_nums.append(int(m.group("num")))

    assigned_numbers = sorted(set(published_nums))
    if not assigned_numbers:
        print(f"ℹ No previous published theses found in DiVA for {school} in {year}.")
        sys.exit(0)

    hwm = max(assigned_numbers)
    total_published = len(assigned_numbers)

    print(f"✔ DiVA Current Stats for {school} ({year}):")
    print(f"   * Total published theses in DiVA: {total_published}")
    print(f"   * Highest published sequence number (HWM): :{hwm}")

    if candidate_num < (hwm - 20):
        print(f"\n⚠️  ANOMALY WARNING: Assigned number :{candidate_num} is far below current DiVA HWM (:{hwm}).")
        print("   This suggests a recycled, cancelled, or mistyped number in the administrator's spreadsheet.")
        print(f"   Verify with the administrator that :{candidate_num} was not assigned to an earlier defense.")
    elif candidate_num > (hwm + 80):
        print(f"\n⚠️  VELOCITY WARNING: Assigned number :{candidate_num} is unusually high compared to current HWM (:{hwm}).")
        print("   Confirm whether a digit was inadvertently prepended or mistyped.")
    else:
        print(f"✔ Sequence assignment :{candidate_num} falls within an expected temporal window relative to HWM :{hwm}.")


if __name__ == "__main__":
    test_trita = sys.argv[1] if len(sys.argv) > 1 else "TRITA-EECS-AVL-2026:10"
    audit_candidate_trita(test_trita)
