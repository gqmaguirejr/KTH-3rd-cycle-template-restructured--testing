#!/usr/bin/python3
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-
#

import json
from pathlib import Path

def clean_latex_string(text):
    """
    Escapes LaTeX special characters.
    Note: CReDiT roles often include '&' which must be '\&'.
    """
    if not text:
        return ""
    # Remove bibtex-style braces and escape ampersands
    text = text.replace("{", "").replace("}", "")
    text = text.replace("&", r"\&")
    return text

def generate_contributions(json_path, output_path):
    if not Path(json_path).exists():
        return

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Filter and sort by the student's defined tab order
    included = [v for v in data.values() if v.get("status") == "included"]
    included.sort(key=lambda x: x.get("tab_index", 999))

    tex = ["% Auto-generated CReDiT Contributions\n"]
    
    for paper in included:
        label = paper.get("label", "Paper").split(":")[-1]
        title = clean_latex_string(paper.get("title", "Untitled"))
        
        tex.append(f"\\subsection*{{Paper {label}: {title}}}")
        
        # Handle Equal Contribution Notes
        eq = paper.get("equal_contributors", [])
        if eq:
            names = " and ".join([clean_latex_string(n) for n in eq])
            tex.append(f"\\textit{{{names} contributed equally to this work.}}\\\\")
        
        # Handle specific domain/clinical notes
        note = paper.get("contribution_note", "")
        if note:
            tex.append(f"\\textit{{{clean_latex_string(note)}}}\\\\")

        # CReDiT Role List using enumitem description

        # Increase leftmargin and add itemsep for vertical breathing room 
        tex.append("\\begin{description}[style=multiline, leftmargin=4cm, font=\\bfseries, itemsep=1.5ex]")
    
        credit = paper.get("credit_contributions", {})
        for author, roles in credit.items():
            author_name = clean_latex_string(author)
            # Wrap the name in a parbox to allow wrapping and remove the colon 
            # The width (3.8cm) should be slightly less than the leftmargin (4cm)
            label_content = f"\\parbox[t]{{3.8cm}}{{{author_name}}}"
        
            clean_roles = [clean_latex_string(r) for r in roles]
            role_str = ", ".join(clean_roles)
        
            tex.append(f"    \\item[{label_content}] {role_str}")
    
        tex.append("\\end{description}\n")
        tex.append("\\bigskip\n")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(tex))

if __name__ == "__main__":
    generate_contributions("publications_map.json", "lib/thesis_contributions_generated.tex")
