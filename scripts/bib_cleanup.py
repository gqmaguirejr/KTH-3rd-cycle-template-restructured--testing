#!/usr/bin/python3.11
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-
#
# The purpose of bib_cleanup.py is to check the existence of the entries in the references.bib file.
# If there is an output.aux or output.bcf file, then the checking is limited to the entries that have been used.
# Otherwise, all of the entries in the references.bib file are checked.
#
# Previously checked entries are stored in .bib_validator_cache.json to avoid repeated lookups.
#
# When compiling a document in Overleaf, the output.aux and output.bcf files are only in the container.
# To move them out of the container, download the file and then upload it to the project's root directory.
#

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import requests

import bibtexparser
from difflib import SequenceMatcher

CACHE_FILE = ".bib_validator_cache.json"

# Crossref uses the following DOIs for testing and internal use
test_DOI_prefixes_Crossref = [
    "10.18810",  # Test accounts
    "10.5555",   # Test accounts - used frequently in examples in their documentation
    "10.88888",  # Test accounts
    "10.30444",
    "10.30446",
    "10.30447",
    "10.30448",
    "10.30449",
    "10.64000",  # Crossref Blog
    "10.13003",
    "10.30443",
]

# Configuration
INPUT_BIB = 'references.bib'
OUTPUT_BIB = 'referencesUsed.bib'
STRIP_FIELDS = ['abstract', 'file', 'groups', 'mendeley-groups', 'keywords', 'annote', 'annotation']

# ----------------------------------------------------------------------
# Version-agnostic bibtexparser wrapper (supports both v1.x and v2.x)
# ----------------------------------------------------------------------
_IS_V2 = hasattr(bibtexparser, "__version__") and bibtexparser.__version__.startswith("2")

if _IS_V2:
    import bibtexparser as bp

    def parse_bib_file(file_path):
        library = bp.parse_file(str(file_path))
        entries = []
        for entry in library.entries:
            d = {"ENTRYTYPE": entry.entry_type, "ID": entry.key}
            for field in entry.fields:
                d[field.key.lower()] = field.value
            entries.append(d)
        return library, entries

    def write_bib_file(file_path, original_library, entries):
        # In v2, construct a new Library directly with the Entry models
        new_library = bp.Library()
        for e in entries:
            fields = [
                bp.model.Field(k, v)
                for k, v in e.items()
                if k not in {"ENTRYTYPE", "ID"}
            ]
            new_library.add(
                bp.model.Entry(e["ENTRYTYPE"], e["ID"], fields=fields)
            )
        bp.write_file(str(file_path), new_library)

else:
    from bibtexparser.bparser import BibTexParser
    from bibtexparser.bwriter import BibTexWriter

    def parse_bib_file(file_path):
        parser = BibTexParser(common_strings=True, ignore_nonstandard_types=False)
        with open(file_path, "r", encoding="utf-8") as f:
            db = bibtexparser.load(f, parser=parser)
        return db, db.entries

    def write_bib_file(file_path, original_library, entries):
        original_library.entries = entries
        writer = BibTexWriter()
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(writer.write(original_library))

# ----------------------------------------------------------------------

from isbnlib import canonical, is_isbn10, is_isbn13, meta


def get_git_email():
    """Retrieves the global or local git user email as a fallback."""
    try:
        return subprocess.check_output(['git', 'config', 'user.email']).decode().strip()
    except Exception:
        return None


def get_entry_hash(entry):
    """Computes a stable hash of the entry's key-value pairs."""
    relevant_data = {k: v for k, v in entry.items() if k not in STRIP_FIELDS}
    entry_str = json.dumps(relevant_data, sort_keys=True)
    return hashlib.sha256(entry_str.encode('utf-8')).hexdigest()


def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=4)


def get_cited_keys(artifact_path):
    """
    Supports:
      - BibLaTeX .aux: \\abx@aux@cite{section}{key}
      - Classic BibTeX .aux: \\citation{key1,key2}
      - Biber XML .bcf: <bcf:citekey>key</bcf:citekey>
    """
    path = Path(artifact_path)
    if not path.exists():
        raise FileNotFoundError(f"Could not find build artifact: {artifact_path}")
    
    content = path.read_text(encoding="utf-8")
    cited = set()
    
    if path.suffix == ".aux":
        blx_keys = re.findall(r'\\abx@aux@cite\{\d+\}\{([^}]+)\}', content)
        cited.update(k.strip() for k in blx_keys)
        
        bibtex_keys = re.findall(r'\\citation\{([^}]+)\}', content)
        for group in bibtex_keys:
            for k in group.split(","):
                cited.add(k.strip())
                
    elif path.suffix == ".bcf":
        bcf_keys = re.findall(r'<bcf:citekey[^>]*>([^<]+)</bcf:citekey>', content)
        cited.update(k.strip() for k in bcf_keys)
        
    return cited


def validate_isbn_metadata(isbn, email="unknown@example.com", verbose=False):
    """Tiered metadata check: Crossref -> Google (via isbnlib) -> Open Library."""
    isbn = canonical(isbn)
    if not (is_isbn10(isbn) or is_isbn13(isbn)):
        return None

    # 1. Try Crossref (Polite User-Agent)
    try:
        headers = {'User-Agent': f'BibCleanupScript/1.0 (mailto:{email})'}
        r = requests.get(f"https://api.crossref.org/works?filter=isbn:{isbn}", timeout=5, headers=headers)
        if r.status_code == 200 and r.json()['message']['total-results'] > 0:
            if verbose:
                print(f"{r.json()}")
            item = r.json()['message']['items'][0]

            def get_year(date_field):
                if date_field:
                    parts = date_field.get('date-parts', [])
                    if parts and len(parts[0]) >= 1:
                        return parts[0][0]
                return None

            final_year = (get_year(item.get('published-print')) or 
                          get_year(item.get('issued')) or 
                          get_year(item.get('published-online')))

            if verbose:
                print(f"  [+] Crossref Found: {final_year=}")

            return {
                "title": item.get("title", [None])[0],
                "year": str(final_year) if final_year else '',
                "source": "Crossref (ISBN)"
            }
    except Exception as e:
        if verbose:
            print(f"  [!] Error in validate_isbn_metadata (Crossref): {e}")

    # 2. Try Google Books (via isbnlib default service)
    try:
        data = meta(isbn, service='goob')
        if data:
            return {
                "title": data.get("Title"),
                "year": str(data.get("Year", "")),
                "source": "Google"
            }
    except Exception as e:
        if verbose:
            print(f"  [!] Error in validate_isbn_metadata (Google Books): {e}")

    # 3. Try Open Library
    try:
        data = meta(isbn, service='openl')
        if data:
            return {
                "title": data.get("Title"),
                "year": str(data.get("Year", "")),
                "source": "Open Library"
            }
    except Exception as e:
        if verbose:
            print(f"  [!] Error in validate_isbn_metadata (Open Library): {e}")

    return None


def validate_doi_metadata(doi, email="unknown@example.com", verbose=False):
    """
    Checks DOI validity via Crossref first (with retraction/update checks),
    falling back to DataCite REST API.
    """
    try:
        # Strip common URL prefixes and leading schemes
        clean_doi = re.sub(r'^(https?://(?:dx\.)?doi\.org/|doi:)', '', doi.strip(), flags=re.IGNORECASE)

        # Filter out Crossref test accounts
        for doi_prefix in test_DOI_prefixes_Crossref:
            if clean_doi.startswith(doi_prefix + '/'):
                return None

        # -------------------------------------------------------------
        # 1. Primary Check: Crossref REST API
        # -------------------------------------------------------------
        headers = {'User-Agent': f'BibCleanupScript/1.0 (mailto:{email})'}
        url = f"https://api.crossref.org/works/{clean_doi}"
        r = requests.get(url, timeout=5, headers=headers)

        if r.status_code == 200:
            item = r.json().get('message', {})

            # --- Year Resolution ---
            def get_cr_year(date_field):
                if date_field:
                    parts = date_field.get('date-parts', [])
                    if parts and len(parts[0]) >= 1:
                        return parts[0][0]
                return None

            final_year = get_cr_year(item.get('published-print')) or get_cr_year(item.get('issued'))

            # --- Retraction and Update Detection ---
            updates = []
            is_retracted = False

            # Check Crossref relation field: is-updated-by
            relations = item.get('relation', {})
            updated_by_list = relations.get('is-updated-by', [])
            for upd in updated_by_list:
                upd_doi = upd.get('id')
                if upd_doi:
                    updates.append(upd_doi)

            # Check Crossmark updates array (contains explicit update types)
            crossmark_updates = item.get('update-to', []) or item.get('updates', [])
            for entry in crossmark_updates:
                upd_type = entry.get('type', '').lower()
                label = entry.get('label', upd_type)
                upd_doi = entry.get('doi') or entry.get('id')
                
                if 'retract' in upd_type or 'retract' in label.lower():
                    is_retracted = True

                if upd_doi and upd_doi not in updates:
                    updates.append(upd_doi)

            # If there are updates, check if any updating entity declares a retraction
            if updated_by_list and not is_retracted:
                for upd in updated_by_list:
                    sub_doi = upd.get('id')
                    # If the relation asserts retraction explicitly
                    rel_type = str(upd.get('relationship-type', '')).lower()
                    if 'retract' in rel_type:
                        is_retracted = True
                        break

            if verbose:
                print(f"  [+] Crossref Found: {final_year=}")
                if is_retracted:
                    print(f"  [!] CRITICAL: {clean_doi} has been RETRACTED by: {updates}")
                elif updates:
                    print(f"  [*] Notice: {clean_doi} has updates/errata: {updates}")

            result = {
                "title": item.get("title", [None])[0],
                "year": str(final_year) if final_year else '',
                "source": "Crossref (DOI)",
                "updates": updates,
                "retracted": is_retracted
            }
            return result

        elif verbose and r.status_code != 404:
            print(f"  [!] Crossref lookup returned status {r.status_code} for {clean_doi}")

        # -------------------------------------------------------------
        # 2. Fallback: DataCite REST API (Datasets, Software, Zenodo)
        # -------------------------------------------------------------
        datacite_url = f"https://api.datacite.org/dois/{clean_doi}"
        dc_headers = {'User-Agent': f'BibCleanupScript/1.0 (mailto:{email})'}
        r_dc = requests.get(datacite_url, timeout=5, headers=dc_headers)

        if r_dc.status_code == 200:
            dc_data = r_dc.json().get('data', {}).get('attributes', {})
            titles = dc_data.get('titles', [])
            dc_title = titles[0].get('title') if titles else None
            dc_year = dc_data.get('publicationYear')

            if verbose:
                print(f"  [+] DataCite Found: publicationYear={dc_year}")

            return {
                "title": dc_title,
                "year": str(dc_year) if dc_year else '',
                "source": "DataCite (DOI)",
                "updates": [],
                "retracted": False
            }
        elif verbose:
            print(f"  [!] Both Crossref and DataCite failed for {clean_doi} (DataCite Status: {r_dc.status_code})")

    except Exception as e:
        if verbose:
            print(f"  [!] Error in validate_doi_metadata: {e}")

    return None

def validate_patent_url(patent_id):
    """Checks if a Google Patents page exists and returns the URL."""
    clean_id = patent_id.replace(" ", "").upper()
    url = f"https://patents.google.com/patent/{clean_id}/en"
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.head(url, headers=headers, timeout=5, allow_redirects=True)
        if r.status_code == 200:
            return {"title": f"Patent {clean_id}", "url": url, "source": "Google Patents"}
    except Exception:
        pass
    return None


def main():
    git_fallback = get_git_email() or "your-backup-contact@example.com"
    default_email = os.environ.get('USER_EMAIL', git_fallback)

    # 1. Argument parsing
    arg_parser = argparse.ArgumentParser(description="Clean and validate BibTeX based on used citations.")
    arg_parser.add_argument("--artifact", default="output.aux", help="Path to .aux or .bcf file")
    arg_parser.add_argument("--verbose", action='store_true', help="Print lots of output to stdout")
    arg_parser.add_argument(
        "--email", 
        type=str, 
        default=default_email,
        help="Contact email for Crossref 'Polite' API pool"
    )

    args = arg_parser.parse_args()
    print(f"{args.artifact=}")

    if args.verbose:
        print(f"Using {args.email} as the polite e-mail address for Crossref")

    # 2. Extract cited keys from build artifact if available; otherwise process all entries
    artifact_arg = getattr(args, "artifact", "output.aux")
    base_artifact = Path(artifact_arg)
    
    cited_keys = set()
    
    if base_artifact.suffix in {".aux", ".bcf"}:
        candidates = [base_artifact.with_suffix(".bcf"), base_artifact.with_suffix(".aux")]
    else:
        candidates = [base_artifact.with_suffix(".bcf"), base_artifact.with_suffix(".aux"), base_artifact]

    active_artifact = next((p for p in candidates if p.is_file()), None)

    if active_artifact:
        try:
            cited_keys = get_cited_keys(active_artifact)
            if not cited_keys and active_artifact.suffix == ".aux":
                alt_bcf = active_artifact.with_suffix(".bcf")
                if alt_bcf.is_file():
                    cited_keys = get_cited_keys(alt_bcf)
                    if cited_keys:
                        active_artifact = alt_bcf
        except Exception as e:
            print(f"Notice: Could not parse '{active_artifact}' ({e}). Falling back to full .bib validation.")
            cited_keys = set()

    if cited_keys:
        print(f"Processing {len(cited_keys)} cited keys from '{active_artifact}'...")
    else:
        print("No build artifacts found (e.g., Overleaf environment). Processing all entries in references.bib...")

    # 3. Load the source bibliography
    db, entries = parse_bib_file(INPUT_BIB)

    original_count = len(entries)
    used_entries = []
    warnings = []
    
    cache = load_cache()

    # 4. Process entries
    for entry in entries:
        if cited_keys and entry.get('ID') not in cited_keys:
            continue

        for field in STRIP_FIELDS:
            entry.pop(field, None)

        placeholder_val = entry.get("placeholder")
        if placeholder_val is not None:
            if isinstance(placeholder_val, str):
                if placeholder_val.strip().lower() not in {"false", "no", "0"}:
                    continue
            elif bool(placeholder_val):
                continue

        entry_hash = get_entry_hash(entry)
        
        if entry_hash in cache:
            validation_result = cache[entry_hash]
            if args.verbose:
                print(f"CACHE HIT: Using stored metadata for {entry['ID']}")
        else:
            if args.verbose:
                print(f"CACHE MISS: Re-checking metadata for {entry['ID']}...")
            validation_result = None
            
            if 'doi' in entry:
                validation_result = validate_doi_metadata(entry.get('doi'), args.email, args.verbose)
            
            if not validation_result and 'isbn' in entry:
                validation_result = validate_isbn_metadata(entry.get('isbn'), args.email, args.verbose)

            if not validation_result and (entry.get('ENTRYTYPE') == 'patent' or entry['ID'].startswith('US')):
                validation_result = validate_patent_url(entry['ID'])
            
            cache[entry_hash] = validation_result

        if validation_result:
            if 'url' not in entry and 'url' in validation_result:
                entry['url'] = validation_result['url']
                if entry_hash not in cache:
                    print(f"Added missing URL to {entry['ID']} via {validation_result['source']}")

        # Check for Retractions or Updates
        if validation_result:
            if validation_result.get("retracted"):
                warnings.append(
                    f"RETRACTED PAPER: {entry['ID']} ({entry.get('doi')}) has been retracted! "
                    f"Update DOIs: {', '.join(validation_result.get('updates', []))}"
                )
            elif validation_result.get("updates"):
                if args.verbose:
                    print(f"  [*] Notice: {entry['ID']} has subsequent errata/corrections: "
                          f"{', '.join(validation_result['updates'])}")

        has_id = any(k in entry for k in ['doi', 'url', 'isbn'])
        if not has_id:
            warnings.append(f"LOW METADATA: {entry['ID']} lacks DOI, URL, or ISBN.")

        used_entries.append(entry)

        e_year = entry.get('year')
        v_year = validation_result.get('year') if validation_result else None

        if e_year and v_year:
            try:
                if int(e_year) != int(v_year):
                    warnings.append(f"Mismatch in years: {entry['ID']} ({e_year}) != Crossref ({v_year})")
            except (ValueError, TypeError):
                if str(e_year) != str(v_year):
                    warnings.append(f"Potential mismatch in years: {entry['ID']} {e_year} vs {v_year}")

        bib_title = entry.get('title')
        if validation_result:
            api_title = validation_result.get('title', None)
            if api_title:
                ratio = SequenceMatcher(None, bib_title.lower(), api_title.lower()).ratio()
                if ratio < 0.6:
                    warnings.append(f"Title mismatch for {entry['ID']}: '{bib_title}' vs API '{api_title}'")


    # 5. Final Output and Summary
    write_bib_file(OUTPUT_BIB, db, used_entries)
    save_cache(cache)

    count_used = len(used_entries)
    print("\n" + "=" * 30)
    print(f"Reduced {original_count} -> {count_used} entries.")
    
    if count_used > 0:
        completion_rate = (1 - len(warnings) / count_used) * 100
        print(f"Completion rate: {max(0, completion_rate):.1f}%")
        for w in warnings:
            print(f"  [!] {w}")
    else:
        print("No entries were processed. Check your .aux/.bcf file.")


if __name__ == "__main__":
    main()
