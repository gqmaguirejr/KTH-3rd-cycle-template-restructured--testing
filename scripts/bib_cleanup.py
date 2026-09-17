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
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter
from isbnlib import canonical, is_isbn10, is_isbn13, meta

CACHE_FILE = ".bib_validator_cache.json"

# Crossref uses the following DOIs for testing and internal use
# from https://api.crossref.org/members?query=test%20accounts
# and https://api.crossref.org/members?query=Crossref
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

# ACM uses https://dl.acm.org/doi/10.5555/*
# as a prefix when cross-listing content from another publisher,
# such as conference papers from others, for example for a paper from the
# Proceedings of the 33rd International Conference on Neural Information Processing Systems
# https://dl.acm.org/doi/10.5555/3454287.3454840
# ACM does _not_ assign DOIs to these papers, but uses the string when generating the BibTeX key when exporting bibtex
# See also the blog post: https://nickwalker.us/blog/2024/acm-dl-fake-dois/

# Configuration
INPUT_BIB = 'references.bib'
OUTPUT_BIB = 'referencesUsed.bib'
STRIP_FIELDS = ['abstract', 'file', 'groups', 'mendeley-groups', 'keywords', 'annote', 'annotation']


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
        # 1. Match BibLaTeX cite commands in .aux: \abx@aux@cite{...}{key}
        blx_keys = re.findall(r'\\abx@aux@cite\{\d+\}\{([^}]+)\}', content)
        cited.update(k.strip() for k in blx_keys)
        
        # 2. Match classic BibTeX cite commands: \citation{key1,key2}
        bibtex_keys = re.findall(r'\\citation\{([^}]+)\}', content)
        for group in bibtex_keys:
            for k in group.split(","):
                cited.add(k.strip())
                
    elif path.suffix == ".bcf":
        # Match Biber XML citekey nodes
        bcf_keys = re.findall(r'<bcf:citekey[^>]*>([^<]+)</bcf:citekey>', content)
        cited.update(k.strip() for k in bcf_keys)
        
    return cited


def validate_isbn_metadata(isbn, email="unknown@example.com", verbose=False):
    """Tiered metadata check: Crossref -> Google (via isbnlib) -> Open Library."""
    isbn = canonical(isbn)
    if not (is_isbn10(isbn) or is_isbn13(isbn)):
        return None

    # 1. Try Crossref (Excellent for academic books/proceedings)
    try:
        headers = {
            'User-Agent': f'BibCleanupScript/1.0 (mailto:{email})'
        }
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
    """Checks DOI validity via Crossref."""
    try:
        doi = doi.strip().replace("doi:", "") 

        # Filter out Crossref's test DOI prefixes
        for doi_prefix in test_DOI_prefixes_Crossref:
            if doi.startswith(doi_prefix + '/'):
                return None

        headers = {'User-Agent': f'BibCleanupScript/1.0 (mailto:{email})'}
        url = f"https://api.crossref.org/works/{doi}"
        
        r = requests.get(url, timeout=5, headers=headers)
        
        if r.status_code != 200:
            if verbose:
                print(f"  [!] Crossref lookup failed for {doi} (Status: {r.status_code})")
            return None

        item = r.json()['message']
        
        def get_year(date_field):
            if date_field:
                parts = date_field.get('date-parts', [])
                if parts and len(parts[0]) >= 1:
                    return parts[0][0]
            return None

        final_year = get_year(item.get('published-print')) or get_year(item.get('issued'))

        if verbose:
            print(f"  [+] Crossref Found: {final_year=}")

        return {
            "title": item.get("title", [None])[0], 
            "year": str(final_year) if final_year else '',
            "source": "Crossref (DOI)"
        }
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

    # 2. Extract cited keys from build artifact if available; otherwise process all entries in references.bib
    artifact_arg = getattr(args, "artifact", "output.aux")
    base_artifact = Path(artifact_arg)
    
    cited_keys = set()
    
    # Check for .bcf (Biber) or .aux (BibLaTeX/BibTeX)
    if base_artifact.suffix in {".aux", ".bcf"}:
        candidates = [base_artifact.with_suffix(".bcf"), base_artifact.with_suffix(".aux")]
    else:
        candidates = [base_artifact.with_suffix(".bcf"), base_artifact.with_suffix(".aux"), base_artifact]

    active_artifact = next((p for p in candidates if p.is_file()), None)

    if active_artifact:
        try:
            cited_keys = get_cited_keys(active_artifact)
            # If an .aux file produced zero keys, try the .bcf sibling if available
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
    parser = BibTexParser(common_strings=True, ignore_nonstandard_types=False)
    with open(INPUT_BIB, 'r', encoding='utf-8') as f:
        db = bibtexparser.load(f, parser=parser)

    original_count = len(db.entries)
    used_entries = []
    warnings = []
    
    # Mutate the existing cache directly to prevent truncating uninspected entries
    cache = load_cache()

    # 4. Process entries
    for entry in db.entries:
        # Filter for used entries only when cited keys were extracted
        if cited_keys and entry.get('ID') not in cited_keys:
            continue

        # Strip unneeded auxiliary fields
        for field in STRIP_FIELDS:
            entry.pop(field, None)

        # Skip processing for placeholder references
        placeholder_val = entry.get("placeholder")
        if placeholder_val is not None:
            if isinstance(placeholder_val, str):
                if placeholder_val.strip().lower() not in {"false", "no", "0"}:
                    continue
            elif bool(placeholder_val):
                continue

        # 4a. Metadata Validation with Hashing/Caching
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
            
            # Persist lookup result (including None) in cache
            cache[entry_hash] = validation_result

        # 4b. Inject data
        if validation_result:
            if 'url' not in entry and 'url' in validation_result:
                entry['url'] = validation_result['url']
                if entry_hash not in cache:
                    print(f"Added missing URL to {entry['ID']} via {validation_result['source']}")

        # 4c. Check for presence of IDs (AFTER injection)
        has_id = any(k in entry for k in ['doi', 'url', 'isbn'])
        if not has_id:
            warnings.append(f"LOW METADATA: {entry['ID']} lacks DOI, URL, or ISBN.")

        # 4d. Collect processed entry
        used_entries.append(entry)

        # 4e. Compare published years
        e_year = entry.get('year')
        v_year = validation_result.get('year') if validation_result else None

        if e_year and v_year:
            try:
                if int(e_year) != int(v_year):
                    warnings.append(f"Mismatch in years: {entry['ID']} ({e_year}) != Crossref ({v_year})")
            except (ValueError, TypeError):
                if str(e_year) != str(v_year):
                    warnings.append(f"Potential mismatch in years: {entry['ID']} {e_year} vs {v_year}")

    # 5. Final Output and Summary
    db.entries = used_entries
    writer = BibTexWriter()
    with open(OUTPUT_BIB, 'w', encoding='utf-8') as f:
        f.write(writer.write(db))

    # Persist the cumulative cache
    save_cache(cache)

    # Summary statistics
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
