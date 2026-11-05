#!/usr/bin/env python3
"""orcid_kth_search.py

Searches the ORCID Public API v3.0 for researchers by name and institutional affiliation.
Supports:
  - Resilient null-safe parsing of empty Solr result sets.
  - Multi-institution matching (KTH, Karolinska Institutet, Stockholm University, etc.).
  - Two-stage fallback: Strict name -> Name prefix -> Unconstrained global search.

Requires: requests
"""

import re
import requests

ORCID_SEARCH_URL = "https://pub.orcid.org/v3.0/search/"
ORCID_RECORD_URL = "https://pub.orcid.org/v3.0/{orcid}/person"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "KTH-Thesis-Automation/1.0 (mailto:admin@example.se)",
}

# Standard regional institutions common in KTH co-authorships
DEFAULT_ORG_CLAUSE = (
    '('
    'affiliation-org-name:"KTH" '
    'OR affiliation-org-name:"Royal Institute of Technology" '
    'OR affiliation-org-name:"Kungliga Tekniska högskolan" '
    'OR affiliation-org-name:"Karolinska Institutet" '
    'OR affiliation-org-name:"Karolinska" '
    'OR affiliation-org-name:"Stockholm University" '
    'OR affiliation-org-name:"Stockholms universitet"'
    ')'
)


def validate_orcid_checksum(orcid_id: str) -> bool:
    """Validates the MOD 11-2 ISO/IEC 7064 checksum on a 16-character ORCID string."""
    clean = orcid_id.replace("-", "").strip()
    if len(clean) != 16:
        return False

    base = clean[:15]
    check_char = clean[15].upper()

    if not base.isdigit():
        return False

    total = 0
    for digit in base:
        total = (total + int(digit)) * 2

    remainder = total % 11
    result = (12 - remainder) % 11
    expected_char = "X" if result == 10 else str(result)

    return check_char == expected_char


def _execute_query(
    solr_query: str, max_results: int, timeout: int = 15
) -> list[dict]:
    """Executes a GET search request against the ORCID v3.0 Solr endpoint."""
    params = {"q": solr_query, "rows": max_results}
    response = requests.get(
        ORCID_SEARCH_URL,
        headers=HEADERS,
        params=params,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()

    # Null-safe extraction: data.get("result") evaluates to None on 0 hits
    records = data.get("result") or []

    results = []
    for entry in records:
        orcid_id = entry.get("orcid-identifier", {}).get("path")
        if not orcid_id:
            continue

        results.append(
            {
                "orcid": orcid_id,
                "url": f"https://orcid.org/{orcid_id}",
                "valid_checksum": validate_orcid_checksum(orcid_id),
            }
        )
    return results


def search_orcid_author(
    family_name: str,
    given_name: str | None = None,
    include_email_domain: bool = True,
    max_results: int = 5,
    timeout: int = 15,
) -> tuple[list[dict], str]:
    """Queries ORCID for an author.

    Progression:
      1. Affiliation-constrained strict name match.
      2. Affiliation-constrained name-prefix (initials) match.
      3. Global unconstrained strict name match (for external co-authors).
      4. Global unconstrained prefix match.
    """
    affil_clause = (
        f"({DEFAULT_ORG_CLAUSE} OR email:*@kth.se OR email:*@ki.se OR email:*@su.se)"
        if include_email_domain
        else DEFAULT_ORG_CLAUSE
    )

    clean_family = family_name.strip()
    clean_given = given_name.strip() if given_name else None

    is_initials = bool(
        clean_given
        and re.fullmatch(r"^[A-Za-z](\.|\s+[A-Za-z]\.?|\s+[A-Za-z])*$", clean_given)
    )

    # Strategy 1: Strict name + Regional Institutional Affiliation
    if clean_given and not is_initials:
        strict_query = (
            f'family-name:"{clean_family}" '
            f'AND given-names:"{clean_given}" '
            f"AND {affil_clause}"
        )
        try:
            hits = _execute_query(strict_query, max_results, timeout=timeout)
            if hits:
                return hits, "strict (name + affiliation)"
        except (
            requests.exceptions.Timeout,
            requests.exceptions.RequestException,
        ):
            pass

    # Strategy 2: Fast initials prefix + Regional Institutional Affiliation
    first_initial = ""
    if clean_given:
        letters = re.findall(r"[A-Za-z]", clean_given)
        first_initial = letters[0] if letters else ""
        prefix_query = (
            f'family-name:"{clean_family}" '
            f'AND (given-names:{first_initial}* OR credit-name:{first_initial}* OR other-names:{first_initial}*) '
            f"AND {affil_clause}"
        )
        try:
            hits = _execute_query(prefix_query, max_results, timeout=timeout)
            if hits:
                return hits, f"prefix '{first_initial}*' + affiliation"
        except (
            requests.exceptions.Timeout,
            requests.exceptions.RequestException,
        ):
            pass

    # Strategy 3: Global unconstrained strict match (External / International Co-authors)
    if clean_given and not is_initials:
        global_strict = (
            f'family-name:"{clean_family}" AND given-names:"{clean_given}"'
        )
        try:
            hits = _execute_query(global_strict, max_results, timeout=timeout)
            if hits:
                return hits, "global strict name (no affiliation constraint)"
        except (
            requests.exceptions.Timeout,
            requests.exceptions.RequestException,
        ):
            pass

    # Strategy 4: Global unconstrained prefix match
    if first_initial:
        global_prefix = (
            f'family-name:"{clean_family}" '
            f'AND (given-names:{first_initial}* OR credit-name:{first_initial}* OR other-names:{first_initial}*)'
        )
        try:
            hits = _execute_query(global_prefix, max_results, timeout=timeout)
            if hits:
                return (
                    hits,
                    f"global prefix '{first_initial}*' (no affiliation constraint)",
                )
        except (
            requests.exceptions.Timeout,
            requests.exceptions.RequestException,
        ):
            pass

    # Strategy 5: Broadest fallback (Family name + Regional Affiliation only)
    broad_query = f'family-name:"{clean_family}" AND {affil_clause}'
    try:
        hits = _execute_query(broad_query, max_results, timeout=timeout)
        if hits:
            return hits, "family-name only + regional affiliation"
    except (requests.exceptions.Timeout, requests.exceptions.RequestException):
        pass

    return [], "no matching records found"


def fetch_person_summary(orcid_id: str, timeout: int = 10) -> dict:
    """Fetches public biographical names, credit name, and current affiliations."""
    person_url = ORCID_RECORD_URL.format(orcid=orcid_id)
    try:
        r = requests.get(person_url, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            return {}
        data = r.json()
    except (requests.exceptions.Timeout, requests.exceptions.RequestException):
        return {}

    name_block = data.get("name", {}) or {}

    given = (
        name_block.get("given-names", {}).get("value")
        if name_block.get("given-names")
        else ""
    )
    family = (
        name_block.get("family-name", {}).get("value")
        if name_block.get("family-name")
        else ""
    )
    credit_name = (
        name_block.get("credit-name", {}).get("value")
        if name_block.get("credit-name")
        else ""
    )

    other_names = [
        item.get("content")
        for item in (data.get("other-names", {}).get("other-name") or [])
        if item.get("content")
    ]

    return {
        "given_name": given,
        "family_name": family,
        "credit_name": credit_name,
        "other_names": other_names,
    }


if __name__ == "__main__":
    test_cases = [
        ("Maguire", "Gerald"),  # Hits Strategy 1 (Strict + KTH)
        ("Maguire", "G. Q."),  # Hits Strategy 2 (Prefix G* + KTH)
        (
            "Bruchfeld",
            "S.",
        ),  # Hits Strategy 2 (Prefix S* + Karolinska Institutet)
    ]

    for family, given in test_cases:
        print("=" * 60)
        print(f"==> Searching ORCID for: '{given} {family}'")
        hits, strategy = search_orcid_author(family, given)
        print(f"Strategy used: {strategy}")
        print(f"Found {len(hits)} record(s):\n")

        for hit in hits:
            details = fetch_person_summary(hit["orcid"])
            name_display = (
                details.get("credit_name")
                or f"{details.get('given_name')} {details.get('family_name')}".strip()
            )

            print(f"  * Name:    {name_display}")
            print(
                f"    ORCID:   {hit['orcid']} (Checksum valid:"
                f" {hit['valid_checksum']})"
            )
            print(f"    URL:     {hit['url']}")
            if details.get("other_names"):
                print(f"    Aliases: {', '.join(details['other_names'])}")
            print()
