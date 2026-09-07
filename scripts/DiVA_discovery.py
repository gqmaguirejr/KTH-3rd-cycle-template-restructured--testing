#!/usr/bin/python3
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-
#
# This script is designed to be safe: it reads existing data from publications_map.json and only adds or updates fields, ensuring user-defined labels and statuses are never lost.
# 
#!/usr/bin/python3
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-

import os
import re
import json
import urllib.request
import pymods
import bibtexparser
from bibtexparser.bparser import BibTexParser
from thefuzz import fuzz

# --- Configuration ---
CONFIG_FILE = 'custom_configuration.tex'
BIB_FILE = 'references.bib'
MAP_FILE = 'publications_map.json'
DIVA_MODS_TEMP = '/tmp/diva_discovery_mods.xml'
FUZZY_THRESHOLD = 90

def get_kthid_from_config():
    """Extracts and validates KTHID from the LaTeX configuration file."""
    placeholder_id = "u1XXXXXX"
    fallback_id = "u1d13i2c"
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                match = re.search(r'^\s*\\kthid\{([^}]+)\}', line)
                if match:
                    found_id = match.group(1).strip()
                    if found_id == placeholder_id:
                        return fallback_id
                    return found_id
    except FileNotFoundError:
        print(f"Warning: {CONFIG_FILE} not found. Using fallback ID.")
    return fallback_id

def normalize_text(text):
    """Simple normalization for fuzzy matching."""
    if not text: return ""
    return re.sub(r'[^\w\s]', '', text).lower().strip()

def normalize_doi(doi_str):
    """Normalize a DOI by removing URL/URI prefixes and converting to lowercase."""
    if not doi_str:
        return ""
    doi = doi_str.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi.strip()

def fetch_diva_mods(kthid):
    """Fetches MODS records from DiVA API."""
    url = f'https://kth.diva-portal.org/smash/export.jsf?format=mods&addFilename=true&aq=[[{{\"personId\":\"{kthid}\"}}]]&aqe=[]&aq2=[[]]&onlyFullText=false&noOfRows=5000&sortOrder=title_sort_asc&sortOrder2=title_sort_asc'
    try:
        with urllib.request.urlopen(url) as response:
            data = response.read()
            with open(DIVA_MODS_TEMP, 'wb') as f:
                f.write(data)
        return pymods.MODSReader(DIVA_MODS_TEMP)
    except Exception as e:
        print(f"Error fetching from DiVA: {e}")
        return []

def sync_discovery():
    kthid = get_kthid_from_config()
    
    # Load existing mapping
    pub_map = {}
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE, 'r', encoding='utf-8') as f:
            pub_map = json.load(f)

    # Load BibTeX for cross-referencing
    bib_entries = []
    if os.path.exists(BIB_FILE):
        with open(BIB_FILE, 'r', encoding='utf-8') as f:
            parser = BibTexParser(ignore_nonstandard_types=False)
            bib_db = bibtexparser.load(f, parser=parser)
            bib_entries = bib_db.entries

    # Fetch from DiVA
    mods_records = fetch_diva_mods(kthid)

    for record in mods_records:
        diva_id = None
        for elem in record:
            if elem.tag.count("}recordInfo") == 1:
                for sub in elem:
                    if sub.tag.count("}recordIdentifier") == 1:
                        diva_id = sub.text
        
        if not diva_id: continue

        # Extract basic metadata from MODS
        title_dict = {}
        year = "Unknown"
        pub_type = "unknown"

        # --- Extracted storage structures for annotations/identifiers ---
        diva_identifiers = {}
        author_annotations = {}  # index -> {'orcid': ..., 'kthid': ...}
        author_index = 0

        for elem in record:
            if elem.tag.count("}titleInfo") == 1:
                lang = elem.attrib.get('lang', 'eng')
                for sub in elem:
                    if sub.tag.count("}title") == 1:
                        title_dict[lang] = sub.text
            elif elem.tag.count("}originInfo") == 1:
                for sub in elem:
                    if sub.tag.count("}dateIssued") == 1:
                        year = sub.text[:4] if sub.text else "Unknown"
            elif elem.tag.count("}genre") == 1:
                if elem.attrib.get('type') == "publicationTypeCode":
                    pub_type = elem.text

            # 1. Harvest Name Identifiers (ORCID, KTHID)
            elif elem.tag.count("}name") == 1:
                is_creator = False
                orcid = None
                kthid = None
                
                for sub in elem:
                    if sub.tag.count("}role") == 1:
                        for role_term in sub:
                            if role_term.text and role_term.text.lower() in ["author", "aut"]:
                                is_creator = True
                    elif sub.tag.count("}description") == 1 and sub.text:
                        if "orcid.org=" in sub.text:
                            orcid = sub.text.split("orcid.org=")[-1].strip()
                    elif sub.tag.count("}nameIdentifier") == 1:
                        val = sub.text.strip() if sub.text else ""
                        if val.startswith("u1"):
                            kthid = val

                if is_creator:
                    author_index += 1
                    author_annotations[author_index] = {}
                    if orcid:
                        author_annotations[author_index]["orcid"] = orcid
                    if kthid:
                        author_annotations[author_index]["kthid"] = kthid

            # 2. Harvest Bibliographic & System Identifiers
            elif elem.tag.count("}identifier") == 1:
                id_type = elem.attrib.get('type')
                id_val = elem.text.strip() if elem.text else None
                if id_type and id_val:
                    diva_identifiers[id_type.lower()] = id_val

        main_title = title_dict.get('eng') or title_dict.get('swe') or "Untitled"

        # --- DEEP MERGE LOGIC ---
        if diva_id not in pub_map:
            print(f"New publication discovered: {diva_id}")
            pub_map[diva_id] = {
                "title": main_title,
                "year": year,
                "pubtype": pub_type,
                "identifiers": diva_identifiers,          # <-- Save extracted PIDs
                "author_identifiers": author_annotations, # <-- Save harvested ORCIDs/KTHIDs
                "status": "unprocessed",
                "label": None,
                "tab_index": None,
                "bib_key": None,
                "in_bib": False,
                "pdf_downloaded": False,
                "file_path": "Included_publications/",
                "pdf_pages": "",
                "scale": 1.0,
                "permission_text": ""
            }
        else:
            # Update metadata from DiVA
            pub_map[diva_id]["title"] = main_title
            pub_map[diva_id]["year"] = year
            pub_map[diva_id]["pubtype"] = pub_type
            pub_map[diva_id]["identifiers"] = diva_identifiers
            pub_map[diva_id]["author_identifiers"] = author_annotations
            
            # Ensure new divider fields exist in older JSON records without overwriting
            pub_map[diva_id].setdefault("tab_index", None)
            pub_map[diva_id].setdefault("file_path", "Included_publications/")
            pub_map[diva_id].setdefault("pdf_pages", "")
            pub_map[diva_id].setdefault("scale", 1.0)
            pub_map[diva_id].setdefault("permission_text", "")

        # --- Cross-referencing with .bib remains unchanged ---
        found_in_bib = False
        norm_diva_title = normalize_text(pub_map[diva_id].get('title', ''))
        diva_doi = normalize_doi(diva_identifiers.get('doi', ''))

        for entry in bib_entries:
            # 1. Exact match on DOI if present in both records
            bib_doi = normalize_doi(entry.get('doi', ''))
            if diva_doi and bib_doi and (diva_doi == bib_doi):
                found_in_bib = True
                pub_map[diva_id]["in_bib"] = True
                pub_map[diva_id]["bib_key"] = entry['ID']
                break

            # 2. Fallback: Fuzzy title matching
            bib_title_raw = entry.get('title', '').replace('{', '').replace('}', '')
            norm_bib_title = normalize_text(bib_title_raw)
            
            if (norm_diva_title in norm_bib_title) or (fuzz.ratio(norm_diva_title, norm_bib_title) > FUZZY_THRESHOLD):
                found_in_bib = True
                pub_map[diva_id]["in_bib"] = True
                pub_map[diva_id]["bib_key"] = entry['ID']
                break
            
        if not found_in_bib:
            pub_map[diva_id]["in_bib"] = False
            pub_map[diva_id]["bib_key"] = None

    # Save the updated map
    with open(MAP_FILE, 'w', encoding='utf-8') as f:
        json.dump(pub_map, f, indent=2, ensure_ascii=False)
    print(f"Sync complete. {MAP_FILE} updated with protective merge.")

if __name__ == "__main__":
    sync_discovery()
