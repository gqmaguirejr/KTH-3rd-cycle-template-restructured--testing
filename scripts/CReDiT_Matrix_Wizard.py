#!/usr/bin/python3
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-
# run the script locally with: streamlit run ./scripts/CReDiT_Matrix_Wizard.py

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

# Supported contributor degrees per NISO Z39.104-2022
DEGREES = ["none", "lead", "equal", "supporting"]

def load_json(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(file_path, data):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_authors_from_bib(bib_path, bib_key):
    """Parses references.bib using an inclusive parser."""
    if not bib_path.exists(): 
        return []
    with open(bib_path, 'r', encoding='utf-8') as bibfile:
        parser = BibTexParser(ignore_nonstandard_types=False)
        library = bibtexparser.load(bibfile, parser=parser)
    for entry in library.entries:
        if entry.get('ID') == bib_key:
            author_str = entry.get('author', '')
            return [a.replace("{", "").replace("}", "").strip() for a in author_str.split(' and ')]
    return []

st.set_page_config(page_title="CReDiT Wizard", layout="wide")

# Sidebar for Administrative Controls
with st.sidebar:
    st.header("Controls")
    if st.button("🔴 Quit Wizard", help="Click to stop the Streamlit server"):
        st.warning("Shutting down the server. You can close this tab now.")
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
                authors = []
                bib_authors = get_authors_from_bib(bib_path, paper.get("bib_key"))
                
                if not bib_authors:
                    st.error(f"Authors not found for {paper.get('bib_key')}")
                    continue

                authors.extend(bib_authors)

                # Get 'specific_contributors' from publications_map.json for 'paper'
                specifics = paper.get('specific_contributors', [])
                for person in specifics:
                    formatted_name = f"{person.get('lname', '')}, {person.get('fname', '')}".strip(" ,")
                    if formatted_name and formatted_name not in authors:
                        authors.append(formatted_name)

                # CReDiT Matrix setup with degree support
                existing_credit = paper.get("credit_contributions", {})
                
                # Initialize grid defaulting to "none"
                df = pd.DataFrame("none", index=authors, columns=CREDIT_ROLES)
                
                # Populate existing data supporting both legacy list and dict formats
                for auth, roles_data in existing_credit.items():
                    if auth in df.index:
                        if isinstance(roles_data, dict):
                            for r, degree in roles_data.items():
                                if r in CREDIT_ROLES and degree in DEGREES:
                                    df.at[auth, r] = degree
                        elif isinstance(roles_data, list):
                            # Legacy format: migrate bare presence to 'lead'
                            for r in roles_data:
                                if r in CREDIT_ROLES:
                                    df.at[auth, r] = "lead"

                # Configure each column as a Selectbox dropdown
                column_configs = {
                    r: st.column_config.SelectboxColumn(
                        label=r,
                        help=f"Degree of contribution for {r}",
                        options=DEGREES,
                        default="none",
                        required=True
                    )
                    for r in CREDIT_ROLES
                }

                # Interactive Data Editor
                edited_df = st.data_editor(
                    df,
                    key=f"ed_{key}",
                    width="stretch",
                    num_rows="fixed",
                    column_config=column_configs
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
                    # Extract active contributions as a structured dictionary {role: degree}
                    new_credit = {}
                    for auth in edited_df.index:
                        author_roles = {}
                        for r in CREDIT_ROLES:
                            val = edited_df.loc[auth, r]
                            if val in ["lead", "equal", "supporting"]:
                                author_roles[r] = val
                        if author_roles:
                            new_credit[auth] = author_roles
                    
                    data[key]["credit_contributions"] = new_credit
                    data[key]["equal_contributors"] = eq_contribs
                    data[key]["contribution_note"] = contrib_note
                    save_json(json_path, data)
                    st.success(f"Successfully updated publications_map.json for Paper {paper.get('label')}")
