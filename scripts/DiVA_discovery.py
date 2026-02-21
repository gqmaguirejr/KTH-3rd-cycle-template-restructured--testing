#!/usr/bin/python3
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-
#
# This script is designed to be safe: it reads existing data from publications_map.json and only adds or updates fields, ensuring user-defined labels and statuses are never lost.
# 
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
                # 1. Ignore commented lines and look for \kthid{...}
                match = re.search(r'^\s*\\kthid\{([^}]+)\}', line)
                if match:
                    found_id = match.group(1).strip()
                    
                    # 2. Reject the placeholder u1XXXXXX
                    if found_id == placeholder_id:
                        print(f"Info: Found placeholder {placeholder_id}. Using fallback.")
                        return fallback_id
                    
                    # 3. Optional: Validate format (starts with u)
                    if not found_id.startswith('u'):
                        print(f"Warning: Extracted ID '{found_id}' looks invalid. Using fallback.")
                        return fallback_id
                        
                    return found_id
    except FileNotFoundError:
        print(f"Warning: {CONFIG_FILE} not found. Using fallback ID.")
    
    return fallback_id

def normalize_text(text):
    """Simple normalization for fuzzy matching."""
    if not text: return ""
    return re.sub(r'[^\w\s]', '', text).lower().strip()

def fetch_diva_mods(kthid):
    """Fetches MODS records from DiVA API."""
    url = f'https://kth.diva-portal.org/smash/export.jsf?format=mods&addFilename=true&aq=[[{{\"personId\":\"{kthid}\"}}]]&aqe=[]&aq2=[[]]&onlyFullText=false&noOfRows=5000&sortOrder=title_sort_asc&sortOrder2=title_sort_asc'
    print(f"Fetching DiVA records for {kthid}...")
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
        # Get DiVA ID from recordInfo
        diva_id = None
        for elem in record:
            if elem.tag.count("}recordInfo") == 1:
                for sub in elem:
                    if sub.tag.count("}recordIdentifier") == 1:
                        diva_id = sub.text
        
        if not diva_id: continue

        # Extract basic info for the map
        title_dict = {} # Based on your notebook logic
        year = "Unknown"
        pub_type = "unknown"

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

        main_title = title_dict.get('eng') or title_dict.get('swe') or "Untitled"

        # Update or Create entry in map
        if diva_id not in pub_map:
            print(f"New publication discovered: {diva_id}")
            pub_map[diva_id] = {
                "title": main_title,
                "year": year,
                "pubtype": pub_type,
                "status": "unprocessed",
                "label": None,
                "bib_key": None,
                "in_bib": False,
                "pdf_downloaded": False
            }
        else:
            # Update metadata but preserve user status/label
            pub_map[diva_id]["title"] = main_title
            pub_map[diva_id]["year"] = year
            pub_map[diva_id]["pubtype"] = pub_type

# --- Robust Cross-referencing with .bib ---
        found_in_bib = False

        norm_diva_title = normalize_text(pub_map[diva_id].get('title', ''))
        diva_doi = normalize_text(pub_map[diva_id].get('doi', '')) # Ensure DOI is captured in discovery

        for entry in bib_entries:
            # 1. Primary Match: DOI
            bib_doi = normalize_text(entry.get('doi', ''))
            if diva_doi and bib_doi and (diva_doi == bib_doi):
                found_in_bib = True
            
            # 2. Secondary Match: Title (if DOI fails)
            if not found_in_bib:
                # Strip LaTeX braces from BibTeX title: {New} -> New
                bib_title_raw = entry.get('title', '').replace('{', '').replace('}', '')
                norm_bib_title = normalize_text(bib_title_raw)
                
                # Check for direct inclusion (handles subtitles) or fuzzy similarity
                if (norm_diva_title in norm_bib_title) or (fuzz.ratio(norm_diva_title, norm_bib_title) > FUZZY_THRESHOLD):
                    found_in_bib = True

            if found_in_bib:
                pub_map[diva_id]["in_bib"] = True
                pub_map[diva_id]["bib_key"] = entry['ID']
                break
        
        if not found_in_bib:
            pub_map[diva_id]["in_bib"] = False
            pub_map[diva_id]["bib_key"] = None

    # Save the updated map
    with open(MAP_FILE, 'w', encoding='utf-8') as f:
        json.dump(pub_map, f, indent=2, ensure_ascii=False)
    print(f"Sync complete. {MAP_FILE} updated.")

if __name__ == "__main__":
    sync_discovery()
