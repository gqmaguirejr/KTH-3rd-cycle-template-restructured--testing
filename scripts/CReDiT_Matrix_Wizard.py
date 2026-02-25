#!/usr/bin/python3
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-
# run the script locally with streamlit run ./scripts/CReDiT_Matrix_Wizard.py

import streamlit as st
import json
import pandas as pd
import bibtexparser
from bibtexparser.bparser import BibTexParser
from pathlib import Path
import os
import signal

# The 14 official CReDiT roles
CREDIT_ROLES = [
    "Conceptualization", "Data Curation", "Formal Analysis", 
    "Funding Acquisition", "Investigation", "Methodology", 
    "Project Administration", "Resources", "Software", 
    "Supervision", "Validation", "Visualization", 
    "Writing – Original Draft", "Writing – Review & Editing"
]

def load_json(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(file_path, data):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_authors_from_bib(bib_path, bib_key):
    """Parses references.bib using an inclusive parser."""
    if not bib_path.exists(): return []
    with open(bib_path, 'r', encoding='utf-8') as bibfile:
        parser = BibTexParser(ignore_nonstandard_types=False) #
        library = bibtexparser.load(bibfile, parser=parser)
    for entry in library.entries:
        if entry.get('ID') == bib_key:
            author_str = entry.get('author', '')
            return [a.strip() for a in author_str.split(' and ')]
    return []

st.set_page_config(page_title="CReDiT Wizard", layout="wide")

# Sidebar for Administrative Controls
with st.sidebar:
    st.header("Controls")
    if st.button("🔴 Quit Wizard", help="Click to stop the Streamlit server"):
        st.warning("Shutting down the server. You can close this tab now.")
        # Sends a signal to the process to terminate cleanly
        os.kill(os.getpid(), signal.SIGINT)

st.title("🎓 CReDiT Contribution Wizard")

json_path = Path("publications_map.json")
bib_path = Path("references.bib")

if not json_path.exists():
    st.error(f"Missing {json_path}")
else:
    data = load_json(json_path)
    # Filter for included papers only
    included = {k: v for k, v in data.items() if v.get("status") == "included"}
    
    # Sort tabs by tab_index
    sorted_keys = sorted(included.keys(), key=lambda x: included[x].get("tab_index", 999))
    
    if not sorted_keys:
        st.warning("No papers are marked as 'included' in your map.")
    else:
        tabs = st.tabs([included[k].get("label", k) for k in sorted_keys])

        for i, key in enumerate(sorted_keys):
            paper = included[key]
            with tabs[i]:
                st.subheader(f"Paper {paper.get('label')}: {paper.get('title')}")
                authors = get_authors_from_bib(bib_path, paper.get("bib_key"))
                
                if not authors:
                    st.error(f"Authors not found for {paper.get('bib_key')}")
                    continue

                # CReDiT Matrix setup
                existing_credit = paper.get("credit_contributions", {})
                df = pd.DataFrame(False, index=authors, columns=CREDIT_ROLES)
                for auth, roles in existing_credit.items():
                    if auth in df.index:
                        for r in roles: df.at[auth, r] = True

                # Data Editor with updated 2026 'width' parameter
                edited_df = st.data_editor(
                    df, key=f"ed_{key}", width="stretch", num_rows="fixed",
                    column_config={r: st.column_config.CheckboxColumn() for r in CREDIT_ROLES}
                )

                st.markdown("---")
                col1, col2 = st.columns(2)
                
                with col1:
                    # Multi-select for equal contributors
                    eq_contribs = st.multiselect(
                        "Identify Equal Contributors:",
                        options=authors,
                        default=paper.get("equal_contributors", []),
                        key=f"eq_{key}"
                    )
                
                with col2:
                    # Custom domain note (e.g. CS vs Medicine distinction)
                    contrib_note = st.text_area(
                        "Custom Contribution Note:",
                        value=paper.get("contribution_note", ""),
                        key=f"note_{key}"
                    )

                if st.button(f"Update JSON for Paper {paper.get('label')}", type="primary"):
                    new_credit = {auth: edited_df.columns[edited_df.loc[auth]].tolist() 
                                  for auth in edited_df.index if edited_df.loc[auth].any()}
                    
                    data[key]["credit_contributions"] = new_credit
                    data[key]["equal_contributors"] = eq_contribs
                    data[key]["contribution_note"] = contrib_note
                    save_json(json_path, data)
                    st.success("Successfully updated publications_map.json")
