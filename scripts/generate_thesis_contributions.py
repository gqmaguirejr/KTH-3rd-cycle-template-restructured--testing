#!/usr/bin/python3
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-
#

import json
import re
from pathlib import Path

def clean_latex(text):
    """Basic cleaner for author names and text."""
    return text.replace("{", "").replace("}", "").replace("&", "\\&")

def generate_contributions(json_path, output_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Filter and sort by tab_index
    included = [v for v in data.values() if v.get("status") == "included"]
    included.sort(key=lambda x: x.get("tab_index", 999))

    tex = ["% Generated Thesis Contributions\n"]
    
    for paper in included:
        label = paper.get("label", "Paper").split(":")[-1]
        title = paper.get("title", "Untitled")
        
        tex.append(f"\\subsection*{{Paper {label}: {title}}}")
        
        # 1. Equal Contribution Note
        eq = paper.get("equal_contributors", [])
        if eq:
            names = " and ".join([clean_latex(n) for n in eq])
            tex.append(f"\\textit{{{names} contributed equally to this work.}}\\\\")
        
        # 2. Custom Domain Note (Giacomo/Samuel case)
        note = paper.get("contribution_note", "")
        if note:
            tex.append(f"\\textit{{{clean_latex(note)}}}\\\\")

        # 3. CReDiT Role List
        tex.append("\\begin{description}[style=multiline, leftmargin=3cm, font=\\bfseries]")
        credit = paper.get("credit_contributions", {})
        for author, roles in credit.items():
            author_name = clean_latex(author)
            role_str = ", ".join(roles)
            tex.append(f"    \\item[{author_name}:] {role_str}")
        tex.append("\\end{description}\n")
        tex.append("\\bigskip\n")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(tex))

if __name__ == "__main__":
    generate_contributions("publications_map.json", "lib/thesis_contributions_generated.tex")
