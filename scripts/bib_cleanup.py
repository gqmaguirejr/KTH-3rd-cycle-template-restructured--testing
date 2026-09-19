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
import time
import urllib.parse
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

import requests
import bibtexparser
from isbnlib import canonical, is_isbn10, is_isbn13, meta, to_isbn10


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
    """Tiered metadata check: Crossref -> Google (via isbnlib) -> Open Library -> Wikidata."""
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
        time.sleep(0.5)
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

    # 3. Try Open Library (try ISBN-13 first, then fallback to ISBN-10)
    for lookup_code in filter(None, [isbn, to_isbn10(isbn) if is_isbn13(isbn) else None]):
        try:
            data = meta(lookup_code, service='openl')
            if data:
                return {
                    "title": data.get("Title"),
                    "year": str(data.get("Year", "")),
                    "source": "Open Library"
                }
        except Exception as e:
            if verbose:
                print(f"  [!] Error in validate_isbn_metadata (Open Library for {lookup_code}): {e}")

    # 4. Try Wikipedia/Wikidata citation index via isbnlib
    for lookup_code in filter(None, [isbn, to_isbn10(isbn) if is_isbn13(isbn) else None]):
        try:
            data = meta(lookup_code, service='wiki')
            if data:
                return {
                    "title": data.get("Title"),
                    "year": str(data.get("Year", "")),
                    "source": "Wikidata (ISBN)"
                }
        except Exception as e:
            if verbose:
                print(f"  [!] Error in validate_isbn_metadata (wiki for {lookup_code}): {e}")

    # 5. Open Library Search API fallback
    for lookup_code in filter(None, [isbn, to_isbn10(isbn) if is_isbn13(isbn) else None]):
        try:
            ol_url = f"https://openlibrary.org/search.json?isbn={lookup_code}&limit=1"
            headers = {'User-Agent': f'BibCleanupScript/1.0 (mailto:{email})'}
            r_ol = requests.get(ol_url, headers=headers, timeout=5)
            if r_ol.status_code == 200:
                docs = r_ol.json().get('docs', [])
                if docs:
                    doc = docs[0]
                    return {
                        "title": doc.get("title"),
                        "year": str(doc.get("first_publish_year", "")),
                        "source": "Open Library Search (ISBN)"
                    }
        except Exception as e:
            if verbose:
                print(f"  [!] Error in Open Library Search API for {lookup_code}: {e}")

    return None


def validate_doi_metadata(doi, email="unknown@example.com", verbose=False):
    """
    Checks DOI validity via Crossref first (with retraction/update checks),
    falling back to DataCite REST API.
    """
    try:
        # Strip scheme prefixes cleanly without touching trailing segments
        clean_doi = re.sub(r'^(https?://(?:dx\.)?doi\.org/|doi:)', '', doi.strip(), flags=re.IGNORECASE)

        # Filter out Crossref test accounts
        for doi_prefix in test_DOI_prefixes_Crossref:
            if clean_doi.startswith(doi_prefix + '/'):
                return None

        # Quote the DOI so slashes and special characters don't break the path
        encoded_doi = urllib.parse.quote(clean_doi, safe='/:')

        # -------------------------------------------------------------
        # 1. Primary Check: Crossref REST API
        # -------------------------------------------------------------
        headers = {'User-Agent': f'BibCleanupScript/1.0 (mailto:{email})'}
        url = f"https://api.crossref.org/works/{encoded_doi}"
        r = requests.get(url, timeout=5, headers=headers)

        if r.status_code == 200:
            res_json = r.json()
            msg_type = res_json.get('message-type')
            
            # Handle direct work vs search work-list
            if msg_type == 'work':
                item = res_json.get('message', {})
            elif msg_type == 'work-list':
                items = res_json.get('message', {}).get('items', [])
                if not items:
                    return None
                exact_items = [it for it in items if it.get('DOI', '').lower() == clean_doi.lower()]
                item = exact_items[0] if exact_items else items[0]
            else:
                item = res_json.get('message', {})

            # --- Year Resolution ---
            def get_cr_year(date_field):
                if date_field:
                    parts = date_field.get('date-parts', [])
                    if parts and len(parts[0]) >= 1:
                        return parts[0][0]
                return None

            final_year = (
                get_cr_year(item.get('published-print')) or 
                get_cr_year(item.get('issued')) or 
                get_cr_year(item.get('published-online')) or 
                get_cr_year(item.get('created'))
            )

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

            # Collect all candidate titles from Crossref
            candidate_titles = []
            for key in ('original-title', 'title', 'subtitle'):
                vals = item.get(key, [])
                if isinstance(vals, list):
                    candidate_titles.extend([v for v in vals if v])
                elif isinstance(vals, str):
                    candidate_titles.append(vals)

            container_titles = item.get("container-title", [])
            if isinstance(container_titles, str):
                container_titles = [container_titles]

            return {
                "title": candidate_titles[0] if candidate_titles else None,
                "all_titles": candidate_titles,
                "container_titles": container_titles,
                "year": str(final_year) if final_year else '',
                "source": "Crossref (DOI)",
                "updates": updates,
                "retracted": is_retracted
            }

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
            return {"url": url, "source": "Google Patents", "title": None}
    except Exception:
        pass
    return None


def clean_title(title: str) -> str:
    """Strips TeX commands, math mode, braces, and non-alphanumeric noise for robust comparison."""
    if not title:
        return ""
    t = re.sub(r'\$.*?\$', '', title)
    t = re.sub(r'\\[a-zA-Z]+', '', t)
    t = re.sub(r'[{}"\'`~^]', '', t)
    t = re.sub(r'[^a-zA-Z0-9\s]', ' ', t)
    return ' '.join(t.lower().split())


def titles_match(bib_title: str, api_title: str, threshold: float = 0.6) -> bool:
    c_bib = clean_title(bib_title)
    c_api = clean_title(api_title)
    
    if not c_bib or not c_api:
        return True
    
    ratio = SequenceMatcher(None, c_bib, c_api).ratio()
    if ratio >= threshold:
        return True
    
    if len(c_bib) > 15 and (c_bib in c_api or c_api in c_bib):
        return True
    
    words_bib = set(c_bib.split())
    words_api = set(c_api.split())
    if words_bib and words_api:
        overlap = len(words_bib & words_api) / min(len(words_bib), len(words_api))
        if overlap >= 0.7:
            return True

    return False


def check_wayback_machine(url, timeout=5, verbose=False):
    """Queries the Wayback Machine Availability API for an archived snapshot."""
    try:
        api_url = f"https://archive.org/wayback/available?url={urllib.parse.quote(url, safe='')}"
        headers = {'User-Agent': 'BibCleanupScript/1.0'}
        r = requests.get(api_url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            closest = data.get("archived_snapshots", {}).get("closest", {})
            if closest.get("available"):
                snapshot_url = closest.get("url")
                timestamp = closest.get("timestamp")
                if verbose:
                    print(f"  [+] Found Wayback Machine snapshot for {url}: {snapshot_url}")
                return {
                    "available": True,
                    "snapshot_url": snapshot_url,
                    "timestamp": timestamp
                }
    except Exception as e:
        if verbose:
            print(f"  [!] Wayback Machine query failed for {url}: {e}")
    return {"available": False}


def validate_url_liveness(url, timeout=10, verbose=False):
    """
    Checks if a URL is reachable. Tries HEAD first, falling back to GET
    (streamed to prevent downloading large bodies) if HEAD is forbidden or unsupported.
    Falls back to the Internet Archive Wayback Machine if the live URL is dead.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }
    
    status_code = None
    error_msg = None
    is_live = False

    try:
        r = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        if r.status_code in {403, 405, 501}:
            r = requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=True)
        
        status_code = r.status_code
        if status_code < 400 or status_code in {401, 403}:
            is_live = True
            if verbose:
                msg = "reachable" if status_code < 400 else "server alive, crawler restricted"
                print(f"  [+] URL {msg} ({status_code}): {url}")
            return {
                "url": url,
                "status_code": status_code,
                "source": "URL Liveness Check",
                "valid": True
            }
        else:
            error_msg = f"HTTP {status_code}"
            if verbose:
                print(f"  [!] URL returned error {status_code}: {url}")
    except Exception as e:
        error_msg = str(e)
        if verbose:
            print(f"  [!] URL request failed: {e}")

    # Fallback to Wayback Machine if the live URL failed
    if not is_live:
        if verbose:
            print(f"  [*] Checking Wayback Machine fallback for: {url}")
        wb = check_wayback_machine(url, timeout=timeout, verbose=verbose)
        if wb.get("available"):
            return {
                "url": url,
                "status_code": status_code,
                "source": "Wayback Machine",
                "valid": True,
                "archived_url": wb.get("snapshot_url"),
                "timestamp": wb.get("timestamp"),
                "error": error_msg
            }
        else:
            return {
                "url": url,
                "status_code": status_code,
                "source": "URL Liveness Check",
                "valid": False,
                "error": error_msg
            }


def suggest_crossref_doi(entry, email="unknown@example.com", verbose=False):
    """
    Searches Crossref by bibliographic query to find a potential matching DOI.
    Enforces strict title ratio (>=0.85), primary author alignment, and publication
    year proximity to eliminate false suggestions on successor works.
    """
    title = entry.get('title')
    if not title:
        return None

    c_bib_title = clean_title(title)
    if len(c_bib_title) < 10:
        return None

    params = {
        'query.bibliographic': c_bib_title,
        'rows': 3
    }
    
    entry_author = entry.get('author', '')
    first_author_family = ""
    if entry_author:
        first_token = entry_author.split(' and ')[0].strip()
        if ',' in first_token:
            first_author_family = clean_title(first_token.split(',')[0])
        else:
            first_author_family = clean_title(first_token.split()[-1])
        if first_author_family:
            params['query.author'] = first_author_family

    headers = {'User-Agent': f'BibCleanupScript/1.0 (mailto:{email})'}
    
    try:
        r = requests.get("https://api.crossref.org/works", params=params, headers=headers, timeout=5)
        if r.status_code == 200:
            items = r.json().get('message', {}).get('items', [])
            for item in items:
                candidate_doi = item.get('DOI')
                api_titles = item.get('title', [])
                if not (candidate_doi and api_titles):
                    continue

                # 1. Strict title matching (ratio only, no broad substring fallback)
                c_api_title = clean_title(api_titles[0])
                sim = SequenceMatcher(None, c_bib_title, c_api_title).ratio()
                if sim < 0.85:
                    continue

                # 2. Check author alignment if available
                if first_author_family:
                    cr_authors = item.get('author', [])
                    # Check first author or any author in Crossref
                    author_matched = any(
                        first_author_family in clean_title(a.get('family', ''))
                        for a in cr_authors
                    )
                    if not author_matched:
                        continue

                # 3. Year proximity check
                date_parts = (item.get('published-print', {}) or item.get('issued', {})).get('date-parts', [[]])
                cand_year = date_parts[0][0] if date_parts and date_parts[0] else None
                
                entry_year = entry.get('year') or (entry.get('date', '')[:4] if entry.get('date') else None)
                if entry_year and cand_year:
                    try:
                        # Allow at most 2 years drift between preprint / draft year and formal publication
                        if abs(int(entry_year) - int(cand_year)) > 2:
                            continue
                    except (ValueError, TypeError):
                        pass

                return {
                    "doi": candidate_doi,
                    "title": api_titles[0],
                    "year": str(cand_year) if cand_year else '',
                    "score": item.get('score', 0)
                }
    except Exception as e:
        if verbose:
            print(f"  [!] Error in suggest_crossref_doi for {entry.get('ID')}: {e}")

    return None

def validate_arxiv_metadata(eprint_id, verbose=False):
    """
    Queries the arXiv Atom API for a given preprint identifier.
    Extracts title, publication year, official journal DOI (if published),
    formal journal reference, authors, primary category, and comments.
    """
    raw_id = re.sub(r'^arxiv:\s*', '', eprint_id.strip(), flags=re.IGNORECASE)
    clean_id = re.sub(r'v\d+$', '', raw_id)
    canonical_doi = f"10.48550/arXiv.{clean_id}"

    url = f"http://export.arxiv.org/api/query?id_list={clean_id}"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            root = ET.fromstring(r.text)
            ns = {
                'atom': 'http://www.w3.org/2005/Atom',
                'arxiv': 'http://arxiv.org/schemas/atom'
            }
            entry_node = root.find('atom:entry', ns)
            if entry_node is not None:
                title_node = entry_node.find('atom:title', ns)
                title = title_node.text.strip().replace('\n', ' ') if title_node is not None else None
                if title and title.lower() == "error":
                    return None
                
                published_node = entry_node.find('atom:published', ns)
                pub_year = published_node.text[:4] if published_node is not None else ""
                
                journal_doi_node = entry_node.find('arxiv:doi', ns)
                journal_doi = journal_doi_node.text.strip() if journal_doi_node is not None else None
                
                journal_ref_node = entry_node.find('arxiv:journal_ref', ns)
                journal_ref = journal_ref_node.text.strip() if journal_ref_node is not None else None

                author_nodes = entry_node.findall('atom:author/atom:name', ns)
                authors = [a.text.strip() for a in author_nodes if a.text]

                primary_cat_node = entry_node.find('arxiv:primary_category', ns)
                primary_cat = primary_cat_node.attrib.get('term') if primary_cat_node is not None else None

                comment_node = entry_node.find('arxiv:comment', ns)
                comment = comment_node.text.strip() if comment_node is not None else None

                if verbose:
                    print(f"  [+] arXiv DOI Found: {canonical_doi} ({pub_year})")
                    if journal_doi:
                        print(f"  [*] Notice: {clean_id} has formal journal DOI: {journal_doi}")

                return {
                    "title": title,
                    "year": str(pub_year),
                    "doi": canonical_doi,
                    "authors": authors,
                    "primary_class": primary_cat,
                    "comment": comment,
                    "source": "arXiv API",
                    "journal_doi": journal_doi,
                    "journal_ref": journal_ref,
                    "valid": True
                }

    except Exception as e:
        if verbose:
            print(f"  [!] Error querying arXiv API for {clean_id}: {e}")
            
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
        is_cache_hit = False
        
        if entry_hash in cache:
            validation_result = cache[entry_hash]
            is_cache_hit = True
            if args.verbose:
                print(f"CACHE HIT: Using stored metadata for {entry['ID']}")
        else:
            if args.verbose:
                print(f"CACHE MISS: Re-checking metadata for {entry['ID']}...")
            validation_result = None
            
            # 1. DOI Check
            doi_candidate = entry.get('doi')
            if not doi_candidate and entry['ID'].startswith('10.'):
                doi_candidate = entry['ID']
            if not doi_candidate and 'url' in entry and 'doi.org/10.' in entry['url']:
                m = re.search(r'10\.\d{4,9}/[-._;()/:A-Za-z0-9]+', entry['url'])
                if m:
                    doi_candidate = m.group(0)

            if doi_candidate:
                validation_result = validate_doi_metadata(doi_candidate, args.email, args.verbose)

            # 2. Extract potential arXiv ID from entry fields or key
            arxiv_id = entry.get('eprint')
            if not arxiv_id:
                if entry.get('archiveprefix', '').lower() == 'arxiv' or 'arxiv.org' in entry.get('url', '').lower():
                    m = re.search(r'(\d{4}\.\d{4,5}(v\d+)?|[a-z\-]+(\.[A-Z]{2})?/\d{7})', entry.get('url', ''))
                    if m:
                        arxiv_id = m.group(1)
            if not arxiv_id and doi_candidate and '10.48550/arxiv.' in doi_candidate.lower():
                arxiv_id = re.sub(r'^10\.48550/arxiv\.', '', doi_candidate, flags=re.IGNORECASE)
            if not arxiv_id:
                m_key = re.search(r'\b\d{4}\.\d{4,5}(v\d+)?\b', entry['ID'])
                if m_key:
                    arxiv_id = m_key.group(0)

            # 2b. arXiv Resolution and Formal Publication Upgrade Detection
            if arxiv_id:
                is_arxiv_doi = doi_candidate and '10.48550/arxiv.' in doi_candidate.lower()
                if not validation_result or is_arxiv_doi:
                    arxiv_meta = validate_arxiv_metadata(arxiv_id, args.verbose)
                    if arxiv_meta:
                        if not validation_result:
                            validation_result = arxiv_meta
                        else:
                            validation_result["journal_doi"] = arxiv_meta.get("journal_doi")
                            validation_result["journal_ref"] = arxiv_meta.get("journal_ref")
                            if arxiv_meta.get("authors"):
                                validation_result["authors"] = arxiv_meta["authors"]
                            if arxiv_meta.get("doi"):
                                validation_result["doi"] = arxiv_meta["doi"]

            # 3. ISBN Check
            if not validation_result and 'isbn' in entry:
                validation_result = validate_isbn_metadata(entry.get('isbn'), args.email, args.verbose)

            # 4. Patent Check
            if not validation_result and (entry.get('ENTRYTYPE') == 'patent' or entry['ID'].startswith('US')):
                validation_result = validate_patent_url(entry['ID'])

            # 5. URL Liveness check (for entries lacking PIDs OR where PID lookup failed)
            if not validation_result and 'url' in entry:
                validation_result = validate_url_liveness(entry.get('url'), timeout=10, verbose=args.verbose)
                
            # 6. Suggest missing DOI for entries lacking DOI/ISBN
            has_pid = any(k in entry for k in ['doi', 'isbn'])
            if not has_pid and entry.get('ENTRYTYPE') not in {'patent', 'standard'}:
                sug = suggest_crossref_doi(entry, args.email, args.verbose)
                if sug:
                    if not validation_result:
                        validation_result = {"source": "Crossref Suggestion", "valid": True}
                    validation_result["suggested_doi"] = sug

            # Only cache if validation succeeded, or if it wasn't a broken URL
            if validation_result and validation_result.get("valid") is not False:
                cache[entry_hash] = validation_result
            elif validation_result and validation_result.get("source") != "URL Liveness Check":
                cache[entry_hash] = validation_result

        # 4b. Inject data and notify on first discovery
        if validation_result:
            if 'url' not in entry and 'url' in validation_result:
                entry['url'] = validation_result['url']
                if not is_cache_hit:
                    print(f"Added missing URL to {entry['ID']} via {validation_result['source']}")

        # 4c. Check URL Liveness failure (runs on BOTH fresh lookups and cached hits)
        if validation_result and validation_result.get("source") in {"URL Liveness Check", "Wayback Machine"}:
            if validation_result.get("source") == "Wayback Machine":
                snap_url = validation_result.get("archived_url")
                warnings.append(
                    f"DECAYED URL (Archived in Wayback): {entry['ID']} ({entry.get('url')}) "
                    f"is dead, but archived copy found: {snap_url}"
                )
            elif not validation_result.get("valid"):
                status = validation_result.get("status_code")
                err_info = f"HTTP {status}" if status else validation_result.get("error", "Connection error")
                warnings.append(f"BROKEN URL: {entry['ID']} ({entry.get('url')}) unreachable: {err_info}")

        # 4d. Preprint Publication Notices and DOI Suggestions
        if validation_result:
            # Check if formal journal DOI was discovered
            if validation_result.get("journal_doi"):
                entry_doi = entry.get("doi", "")
                # Only warn if the entry doesn't already have this formal DOI
                if validation_result["journal_doi"].lower() not in entry_doi.lower():
                    j_ref = f" in '{validation_result['journal_ref']}'" if validation_result.get("journal_ref") else ""
                    warnings.append(
                        f"PUBLISHED PREPRINT: {entry['ID']} has a formal publication DOI{j_ref}: "
                        f"{validation_result['journal_doi']}. Consider updating references.bib"
                    )
            elif validation_result.get("journal_ref"):
                # Only warn if references.bib is missing publication venue details
                has_venue = any(k in entry for k in ['journal', 'booktitle', 'isbn'])
                if not has_venue or entry.get('ENTRYTYPE') in {'misc', 'online', 'unpublished'}:
                    warnings.append(
                        f"PUBLISHED PREPRINT: {entry['ID']} has a formal publication reference: "
                        f"'{validation_result['journal_ref']}'. Consider updating references.bib"
                    )

            # Suggest adding canonical arXiv-minted DOI if entry lacks a 'doi' field
            if 'doi' not in entry and validation_result.get("doi"):
                cand_doi = validation_result["doi"]
                warnings.append(
                    f"SUGGESTION: {entry['ID']} lacks a DOI field, but arXiv minted canonical DOI: {cand_doi}. "
                    f"Consider adding 'doi = {{{cand_doi}}}' to references.bib"
                )

            # Suggest adding Crossref-matched DOI
            if validation_result.get("suggested_doi"):
                sug = validation_result["suggested_doi"]
                sug_doi = sug['doi']
                sug_yr = f" ({sug['year']})" if sug.get('year') else ""
                warnings.append(
                    f"SUGGESTION: {entry['ID']} lacks a DOI, but Crossref matched {sug_doi}{sug_yr}. "
                    f"Consider adding 'doi = {{{sug_doi}}}' to references.bib"
                )

        # 4e. Check for Retractions or Updates
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

        e_year = entry.get('year') or (entry.get('date', '')[:4] if entry.get('date') else None)
        v_year = validation_result.get('year') if validation_result else None

        if e_year and v_year:
            try:
                if int(e_year) != int(v_year):
                    warnings.append(f"Mismatch in years: {entry['ID']} ({e_year}) != {validation_result['source']} ({v_year})")
            except (ValueError, TypeError):
                if str(e_year) != str(v_year):
                    warnings.append(f"Potential mismatch in years: {entry['ID']} {e_year} vs {v_year}")

        # Check title and container similarity
        bib_title = entry.get('title')
        bib_booktitle = entry.get('booktitle')
        if validation_result and (bib_title or bib_booktitle):
            is_patent = entry.get('ENTRYTYPE') == 'patent' or entry['ID'].startswith('US')
            
            if not is_patent:
                api_titles_to_test = list(validation_result.get('all_titles', []))
                if not api_titles_to_test and validation_result.get('title'):
                    api_titles_to_test.append(validation_result['title'])
                
                for ct in validation_result.get('container_titles', []):
                    if ct and ct not in api_titles_to_test:
                        api_titles_to_test.append(ct)
                if validation_result.get('container_title'):
                    ct = validation_result['container_title']
                    if ct not in api_titles_to_test:
                        api_titles_to_test.append(ct)

                if api_titles_to_test:
                    matched = False
                    if bib_title:
                        matched = any(titles_match(bib_title, t, threshold=0.6) for t in api_titles_to_test)
                    if not matched and bib_booktitle:
                        matched = any(titles_match(bib_booktitle, t, threshold=0.6) for t in api_titles_to_test)

                    if not matched:
                        display_title = validation_result.get('title', '')
                        warnings.append(f"Title mismatch for {entry['ID']}: '{bib_title}' vs API '{display_title}'")

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
