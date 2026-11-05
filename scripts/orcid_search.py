#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""orcid_search.py

Looks up researchers in the ORCID Public API v3.0 by:
  - Email address (e.g. user@kth.se)
  - Full name (e.g. "First Last", "Last, First") or family name alone.

Includes polite User-Agent headers, checksum validation, and detailed
biographical / affiliation record fetching.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import requests

ORCID_SEARCH_URL = "https://pub.orcid.org/v3.0/search/"
ORCID_RECORD_URL = "https://pub.orcid.org/v3.0/{orcid}/person"

# Regional institutions common to KTH workflows
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


def get_git_email() -> str | None:
    """Retrieves global or local git user email as a polite default."""
    try:
        return subprocess.check_output(["git", "config", "user.email"]).decode().strip()
    except Exception:
        return None


def validate_orcid_checksum(orcid_id: str) -> bool:
    """Validates the MOD 11-2 ISO/IEC 7064 checksum on an ORCID string."""
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
    solr_query: str,
    headers: dict[str, str],
    max_results: int = 5,
    timeout: int = 15,
    verbose: bool = False,
) -> list[dict]:
    """Executes a GET query against the ORCID v3.0 Solr endpoint."""
    if verbose:
        print(f"  [Solr Query] {solr_query}")

    params = {"q": solr_query, "rows": max_results}
    try:
        response = requests.get(
            ORCID_SEARCH_URL,
            headers=headers,
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        if verbose:
            print(f"  [!] Query failed: {e}")
        return []

    # Null-safe extraction
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


def search_by_email(
    email: str,
    headers: dict[str, str],
    affil_clause: str = "",
    max_results: int = 5,
    verbose: bool = False,
) -> tuple[list[dict], str]:
    clean_email = email.strip().lower()

    if "@" in clean_email:
        user_part, domain_part = clean_email.split("@", 1)
    else:
        user_part, domain_part = clean_email, ""

    # Strategy 1: Wildcard on email field
    if user_part and domain_part:
        q_wildcard = f'email:*{user_part}* AND email:*{domain_part}*'
        hits = _execute_query(q_wildcard, headers, max_results=max_results, verbose=verbose)
        if hits:
            return hits, "wildcard email (user + domain)"

    # Strategy 2: Quoted email in generic text index
    q_text = f'text:"{clean_email}"'
    hits = _execute_query(q_text, headers, max_results=max_results, verbose=verbose)
    if hits:
        return hits, "text search on full email"

    # Strategy 3: Solr-escaped exact email
    escaped_email = clean_email.replace("@", r"\@").replace(".", r"\.")
    q_escaped = f'email:{escaped_email}'
    hits = _execute_query(q_escaped, headers, max_results=max_results, verbose=verbose)
    if hits:
        return hits, "escaped exact email"

    # Strategy 4: Fallback to username token combined with the active affiliation clause
    if user_part and affil_clause:
        q_fallback = f'(family-name:{user_part} OR text:{user_part}) AND {affil_clause}'
        hits = _execute_query(q_fallback, headers, max_results=max_results, verbose=verbose)
        if hits:
            return hits, f"email user-token fallback ('{user_part}' + affiliation)"

    return [], "no matching records found"

def search_by_name(
    given_name: str | None,
    family_name: str,
    headers: dict[str, str],
    affil_clause: str = "",
    max_results: int = 5,
    verbose: bool = False,
) -> tuple[list[dict], str]:
    clean_family = family_name.strip()
    clean_given = given_name.strip() if given_name else None

    is_initials = bool(
        clean_given
        and re.fullmatch(r"^[A-Za-z](\.|\s+[A-Za-z]\.?|\s+[A-Za-z])*$", clean_given)
    )

    # Strategy 1: Strict name + Affiliation (only if affil_clause exists)
    if clean_given and not is_initials and affil_clause:
        q = f'family-name:"{clean_family}" AND given-names:"{clean_given}" AND {affil_clause}'
        hits = _execute_query(q, headers, max_results, verbose=verbose)
        if hits:
            return hits, "strict name + specified affiliation"

    # Strategy 2: Initials prefix + Affiliation (only if affil_clause exists)
    first_initial = ""
    if clean_given and affil_clause:
        letters = re.findall(r"[A-Za-z]", clean_given)
        first_initial = letters[0] if letters else ""
        q = (
            f'family-name:"{clean_family}" '
            f'AND (given-names:{first_initial}* OR credit-name:{first_initial}* OR other-names:{first_initial}*) '
            f"AND {affil_clause}"
        )
        hits = _execute_query(q, headers, max_results, verbose=verbose)
        if hits:
            return hits, f"prefix '{first_initial}*' + specified affiliation"

    # Strategy 3: Global strict match (unconstrained)
    if clean_given and not is_initials:
        q = f'family-name:"{clean_family}" AND given-names:"{clean_given}"'
        hits = _execute_query(q, headers, max_results, verbose=verbose)
        if hits:
            return hits, "global strict name (no affiliation filter)"

    # Strategy 4: Global prefix match (unconstrained)
    if first_initial:
        q = (
            f'family-name:"{clean_family}" '
            f'AND (given-names:{first_initial}* OR credit-name:{first_initial}* OR other-names:{first_initial}*)'
        )
        hits = _execute_query(q, headers, max_results, verbose=verbose)
        if hits:
            return hits, f"global prefix '{first_initial}*'"

    # Strategy 5: Family name + affiliation
    if affil_clause:
        q = f'family-name:"{clean_family}" AND {affil_clause}'
        hits = _execute_query(q, headers, max_results, verbose=verbose)
        if hits:
            return hits, "family-name only + specified affiliation"

    return [], "no matching records found"


def fetch_person_summary(orcid_id: str, headers: dict[str, str], timeout: int = 10) -> dict:
    """Fetches full name, credit name, aliases, and biography from ORCID."""
    url = ORCID_RECORD_URL.format(orcid=orcid_id)
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code != 200:
            return {}
        data = r.json()
    except Exception:
        return {}

    name_block = data.get("name", {}) or {}
    given = name_block.get("given-names", {}).get("value") if name_block.get("given-names") else ""
    family = name_block.get("family-name", {}).get("value") if name_block.get("family-name") else ""
    credit_name = name_block.get("credit-name", {}).get("value") if name_block.get("credit-name") else ""

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


def parse_query_input(tokens: list[str]) -> tuple[str, str | None, str | None]:
    """
    Parses positional CLI arguments into:
      ('email', email_address, None)
      or
      ('name', given_name, family_name)
    """
    full_str = " ".join(tokens).strip()

    # Case 1: Email Address
    if "@" in full_str:
        return "email", full_str, None

    # Case 2: "Family, Given"
    if "," in full_str:
        parts = [p.strip() for p in full_str.split(",", 1)]
        family = parts[0]
        given = parts[1] if parts[1] else None
        return "name", given, family

    # Case 3: "Given Family" or single name
    parts = full_str.split()
    if len(parts) == 1:
        return "name", None, parts[0]
    else:
        given = " ".join(parts[:-1])
        family = parts[-1]
        return "name", given, family

def build_affiliation_clause(
    custom_orgs: list[str] | None = None,
    clear_defaults: bool = False,
    include_email_domains: bool = True,
) -> str:
    """Builds a Solr boolean clause for institutional affiliation."""
    clauses = []

    if custom_orgs:
        for org in custom_orgs:
            clean = org.strip().replace('"', "")
            clauses.append(f'affiliation-org-name:"{clean}"')

    if not clear_defaults:
        clauses.append(
            'affiliation-org-name:"KTH" '
            'OR affiliation-org-name:"Royal Institute of Technology" '
            'OR affiliation-org-name:"Kungliga Tekniska högskolan" '
            'OR affiliation-org-name:"Karolinska Institutet" '
            'OR affiliation-org-name:"Karolinska" '
            'OR affiliation-org-name:"Stockholm University" '
            'OR affiliation-org-name:"Stockholms universitet"'
        )
        if include_email_domains:
            clauses.append("email:*@kth.se OR email:*@ki.se OR email:*@su.se")

    if not clauses:
        return ""

    return f"({' OR '.join(clauses)})"

def main():
    default_email = os.environ.get("USER_EMAIL") or get_git_email() or "user@kth.se"

    parser = argparse.ArgumentParser(
        description="Search ORCID for researchers by name or email address."
    )
    
    # Optional flags defined first
    parser.add_argument(
        "--org",
        action="append",
        default=[],
        help="Specify an affiliation organization name to filter by (can be used multiple times).",
    )
    parser.add_argument(
        "--clear-default-orgs",
        action="store_true",
        help="Clear default Stockholm/KTH institutions and only match against explicitly supplied --org values.",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=default_email,
        help=f"Contact email for the polite API User-Agent header (default: {default_email})",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=5,
        help="Maximum candidate results to fetch (default: 5)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print raw Solr queries and intermediate lookup stages",
    )

    # Positional target query
    parser.add_argument(
        "query",
        nargs="+",
        help="Target user query: 'First Last', 'Last, First', or 'user@domain.se'",
    )

    args = parser.parse_args()

    headers = {
        "Accept": "application/json",
        "User-Agent": f"KTH-Thesis-Automation/1.0 (mailto:{args.email})",
    }

    if args.verbose:
        print(f"[*] Contact email (User-Agent): {args.email}")

    mode, val1, val2 = parse_query_input(args.query)

    # Build dynamic Solr affiliation clause based on CLI args
    affil_clause = build_affiliation_clause(
        custom_orgs=args.org,
        clear_defaults=args.clear_default_orgs,
        include_email_domains=not args.clear_default_orgs,
    )

    if args.verbose and affil_clause:
        print(f"[*] Active Affiliation Filter: {affil_clause}")

    if mode == "email":
        target_email = val1
        print(f"==> Searching ORCID by email: '{target_email}'")
        hits, strategy = search_by_email(
            target_email,
            headers,
            affil_clause=affil_clause,
            max_results=args.max_results,
            verbose=args.verbose,
        )
    else:
        given, family = val1, val2
        name_display = f"{given} {family}".strip() if given else family
        print(f"==> Searching ORCID by name: '{name_display}'")
        hits, strategy = search_by_name(
            given,
            family,
            headers,
            affil_clause=affil_clause,
            max_results=args.max_results,
            verbose=args.verbose,
        )

    print(f"Strategy used: {strategy}")
    print(f"Found {len(hits)} matching record(s):\n")

    if not hits:
        sys.exit(0)

    for hit in hits:
        orcid_id = hit["orcid"]
        details = fetch_person_summary(orcid_id, headers)

        display_name = (
            details.get("credit_name")
            or f"{details.get('given_name')} {details.get('family_name')}".strip()
            or "Name not publicly visible"
        )

        chk_str = "valid" if hit["valid_checksum"] else "INVALID"
        print(f"  * Name:    {display_name}")
        print(f"    ORCID:   {orcid_id} (checksum: {chk_str})")
        print(f"    URL:     {hit['url']}")
        if details.get("other_names"):
            print(f"    Aliases: {', '.join(details['other_names'])}")
        print()


if __name__ == "__main__":
    main()
