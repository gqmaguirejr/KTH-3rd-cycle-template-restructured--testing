#!/usr/bin/python3.11
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-

import os
import re
import requests
import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter
from isbnlib import is_isbn10, is_isbn13, canonical, meta
import argparse
from pathlib import Path
import hashlib
import json
import subprocess

CACHE_FILE = ".bib_validator_cache.json"

def get_git_email():
    """Retrieves the global or local git user email as a fallback."""
    try:
        # Changed from ['git', 'config', 'get', 'user.email'] 
        # to the classic ['git', 'config', 'user.email']
        return subprocess.check_output(['git', 'config', 'user.email']).decode().strip()
    except Exception:
        return None

def get_entry_hash(entry):
    """Computes a stable hash of the entry's key-value pairs."""
    # We sort the keys to ensure the hash is consistent regardless of dict order
    relevant_data = {k: v for k, v in entry.items() if k not in STRIP_FIELDS}
    entry_str = json.dumps(relevant_data, sort_keys=True)
    return hashlib.sha256(entry_str.encode('utf-8')).hexdigest()

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=4)

# Configuration
INPUT_BIB = 'references.bib'
OUTPUT_BIB = 'referencesUsed.bib'
STRIP_FIELDS = ['abstract', 'file', 'mendeley-groups', 'keywords']

def get_cited_keys(artifact_path):
    """Supports both .aux (BibTeX) and .bcf (BibLaTeX/Biber)."""
    path = Path(artifact_path)
    if not path.exists():
        raise FileNotFoundError(f"Could not find build artifact: {artifact_path}")
    
    content = path.read_text(encoding='utf-8')
    cited = set()
    
    if path.suffix == '.aux':
        # Standard BibTeX format: \citation{key1,key2}
        keys = re.findall(r'\\citation\{([^}]+)\}', content)
        for k_group in keys:
            for k in k_group.split(','):
                cited.add(k.strip())
                
    elif path.suffix == '.bcf':
        # BibLaTeX/Biber XML format: <bcf:citekey>key</bcf:citekey>
        keys = re.findall(r'<bcf:citekey[^>]*>([^<]+)</bcf:citekey>', content)
        cited.update(keys)
        
    return cited

def validate_isbn_metadata(isbn, email="unknown@example.com", verbose=False):
    """Tiered metadata check: Crossref -> Google (via isbnlib) -> Open Library."""
    global args
    isbn = canonical(isbn)
    if not (is_isbn10(isbn) or is_isbn13(isbn)):
        return None

    # 1. Try Crossref (Excellent for academic books/proceedings)
    try:
        # The Crossref 'Polite' User-Agent format
        headers = {
            'User-Agent': f'BibCleanupScript/1.0 (mailto:{email})'
        }

        # Crossref uses a specific API for ISBN-A or DOI lookups
        r = requests.get(f"https://api.crossref.org/works?filter=isbn:{isbn}", timeout=5, headers=headers)
        if r.status_code == 200 and r.json()['message']['total-results'] > 0:
            if verbose:
                print(f"{r.json}")
            item = r.json()['message']['items'][0]
            return {"title": item.get("title", [None])[0], "source": "Crossref"}
    except Exception: pass

    # 2. Try Google Books (via isbnlib default service)
    try:
        data = meta(isbn, service='goob') # 'goob' is Google Books
        if data:
            return {"title": data.get("Title"), "source": "Google"}
    except Exception: pass

    # 3. Try Open Library
    try:
        data = meta(isbn, service='openl')
        if data:
            return {"title": data.get("Title"), "source": "Open Library"}
    except Exception: pass

    return None

def validate_doi_metadata(doi, email="unknown@example.com", verbose=False):
    """Checks DOI validity via Crossref."""
    global args
    try:
        # Clean the DOI just in case
        doi = doi.strip().replace("doi:", "") 
        headers = {'User-Agent': f'BibCleanupScript/1.0 (mailto:{email})'}
        url = f"https://api.crossref.org/works/{doi}"
        
        r = requests.get(url, timeout=5, headers=headers)
        
        if r.status_code != 200:
            if verbose:
                print(f"  [!] Crossref lookup failed for {doi} (Status: {r.status_code})")
            return None

        item = r.json()['message']
        
        # Helper to extract year
        def get_year(date_field):
            if date_field:
                parts = date_field.get('date-parts', [])
                if parts and len(parts[0]) >= 1:
                    return parts[0][0]
            return None

        final_year = get_year(item.get('published-print')) or get_year(item.get('issued'))

        if verbose:
            print(f"  [+] Crossref Found: {final_year=}") # This should now appear

        return {
            "title": item.get("title", [None])[0], 
            "year": str(final_year) if final_year else '',
            "source": "Crossref (DOI)"
        }
    except Exception as e:
        if verbose:
            print(f"  [!] Error in validate_doi_metadata: {e}")
        pass
    return None


def validate_patent_url(patent_id):
    """Checks if a Google Patents page exists and returns the URL."""
    clean_id = patent_id.replace(" ", "").upper()
    url = f"https://patents.google.com/patent/{clean_id}/en"
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        # Use a head request to be faster/polite
        r = requests.head(url, headers=headers, timeout=5, allow_redirects=True)
        if r.status_code == 200:
            return {"title": f"Patent {clean_id}", "url": url, "source": "Google Patents"}
    except Exception:
        pass
    return None

def main():
    global args

    git_fallback = get_git_email() or "your-backup-contact@example.com"
    default_email = os.environ.get('USER_EMAIL', git_fallback)

    # 1. Setup the specific argument parser
    arg_parser = argparse.ArgumentParser(description="Clean and validate BibTeX based on used citations.")
    arg_parser.add_argument("--artifact", default="output.aux", help="Path to .aux or .bcf file")
    arg_parser.add_argument("--verbose", action='store_true', help="Print lots of output to stdout")
# Logic: Check --email flag first, then USER_EMAIL env var, then hardcoded fallback
    arg_parser.add_argument(
        "--email", 
        type=str, 
        default=default_email,
        help="Contact email for Crossref 'Polite' API pool"
    )

    args = arg_parser.parse_args()
    print(f"{args.artifact=}")

    if args.verbose:
        print(f"using {args.email} as the polite e-mail address for Crossref")

    # 2. Extract cited keys with "Stub-Aware" failover
    artifact_path = Path(args.artifact)
    
    # Try to get keys from the initial file
    try:
        cited_keys = get_cited_keys(artifact_path)
    except FileNotFoundError:
        cited_keys = set()

    # If the .aux was a Biber stub (empty keys) or missing, try the .bcf
    if not cited_keys and artifact_path.suffix == '.aux':
        alt_path = artifact_path.with_suffix('.bcf')
        if alt_path.exists():
            print(f"Notice: '{artifact_path}' appears to be a Biber stub. Switching to '{alt_path}'.")
            cited_keys = get_cited_keys(alt_path)
            artifact_path = alt_path
    
    if not cited_keys:
        print(f"Error: No citations found in '{artifact_path}' or its .bcf equivalent.")
        return

    print(f"Processing {len(cited_keys)} citations from {artifact_path}...")


    print(f"{cited_keys=}")

    # Load the source bibliography
    parser = BibTexParser(common_strings=True, ignore_nonstandard_types=False)
    with open(INPUT_BIB, 'r', encoding='utf-8') as f:
        db = bibtexparser.load(f, parser=parser)

    original_count = len(db.entries)
    used_entries = []
    warnings = []
    cache = load_cache()
    new_cache = {}

    # 4. Process entries

    for entry in db.entries:
        # Filter for used entries only
        if cited_keys and entry['ID'] not in cited_keys:
            continue

        # Strip sensitive fields
        for field in STRIP_FIELDS:
            entry.pop(field, None)

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
            
            # Store new result in the session cache
            new_cache[entry_hash] = validation_result

        # 4b. INJECT DATA (From new lookup OR from existing cache)
        if validation_result:
            if 'url' not in entry and 'url' in validation_result:
                entry['url'] = validation_result['url']
                # Only print if it's a fresh lookup (not in old cache) to keep logs clean
                if entry_hash not in cache:
                    print(f"Added missing URL to {entry['ID']} via {validation_result['source']}")
            
            # Always ensure the new session cache is updated
            new_cache[entry_hash] = validation_result

        # 4c. Check for presence of IDs (AFTER injection)
        has_id = any(k in entry for k in ['doi', 'url', 'isbn'])
        if not has_id:
            warnings.append(f"LOW METADATA: {entry['ID']} lacks DOI, URL, or ISBN.")

        # 4d. CRITICAL: Add the processed (and potentially updated) entry to our list
        used_entries.append(entry)

        # 4e. check year of entry and year of validation_result (if it exists)
        # Use .get() to safely check for keys without crashing

        # if entry.get('year') and validation_result and validation_result.get('year'):
        #     if args.verbose:
        #         print(f"DEBUG: Comparing {entry['ID']} - Bib: {entry['year']} vs API: {validation_result['year']}")

        e_year = entry.get('year')
        v_year = validation_result.get('year') if validation_result else None

        if e_year and v_year:
            try:
                if int(e_year) != int(v_year):
                    warnings.append(f"Mismatch in years: {entry['ID']} ({e_year}) != Crossref ({v_year})")
            except (ValueError, TypeError):
                # Handle cases where the year isn't a simple integer (e.g., '1975a')
                if str(e_year) != str(v_year):
                    warnings.append(f"Potential mismatch in years: {entry['ID']} {e_year} vs {v_year}")

    # 5. Final Output and Summary
    db.entries = used_entries
    writer = BibTexWriter()
    with open(OUTPUT_BIB, 'w', encoding='utf-8') as f:
        f.write(writer.write(db))

    # Save the updated cache
    save_cache(new_cache)

    # Calculate statistics safely
    count_used = len(used_entries)
    print("\n" + "="*30)
    print(f"Reduced {original_count} -> {count_used} entries.")
    
    if count_used > 0:
        completion_rate = (1 - len(warnings)/count_used) * 100
        print(f"Completion rate: {max(0, completion_rate):.1f}%")
        for w in warnings:
            print(f"  [!] {w}")
    else:
        print("No entries were processed. Check your .aux/.bcf file.")

if __name__ == "__main__":
    main()
