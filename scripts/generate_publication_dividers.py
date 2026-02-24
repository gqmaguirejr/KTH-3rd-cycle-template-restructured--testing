import json
import os
import re

def clean_latex_string(text):
    """Cleans a string for LaTeX while preserving math mode."""
    if not text: return ""
    text = re.sub(r'\s+', ' ', text).strip()
    parts = re.split(r'(\$.*?\$)', text)
    mapping = {
        '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#',
        '_': r'\_', '{': r'\{', '}': r'\}',
        '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'
    }
    cleaned_parts = []
    for part in parts:
        if part.startswith('$') and part.endswith('$'):
            cleaned_parts.append(part)
        else:
            local_mapping = mapping.copy()
            del local_mapping['$'] 
            temp_part = part
            for char, escape in local_mapping.items():
                temp_part = temp_part.replace(char, escape)
            cleaned_parts.append(temp_part)
    return "".join(cleaned_parts)

def generate_latex_dividers(json_path, output_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        pubs = json.load(f)

    # 1. Filter for included papers
    # 2. Sort them numerically by their 'tab_index'
    included_papers = [p for p in pubs.values() if p.get('status') == 'included']
    included_papers.sort(key=lambda x: x.get('tab_index', 99)) # Default to 99 if missing

    lines = [
        "%% Auto-generated divider pages",
        "\\ifincludepublications",
        "    \\cleardoublepage",
        "    \\fancyhead{} % Clear headers for divider section",
        "    \\part{Included publications}",
        "    \\cleardoublepage",
        "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%",
        "% set up macros for BibTeX - if necessary",
        "",
        "\\ifbiblatex",
        "\\relax",
        "\\else",
        "\\makeatletter",
        "% Disable the back reference",
        "%\\renewcommand*{\\backref}[1]{}",
        "%\\renewcommand*{\\backrefalt}[4]{}",
        "\\newcommand*{\\backref}[1]{}",
        "\\newcommand*{\\backrefalt}[4]{}",
        "",
        "% No special openbib formatting",
        "\\let\\@openbib@code\\@empty % see https://www.latex-project.org/help/documentation/classes.pdf",
        "",
        "% Turn off item's biblabel",
        "\\renewcommand\\@biblabel[1]{}",
        "",
        "% Redefine the thebibliography to format as we want",
        "\\renewenvironment{thebibliography}[1]",
        "{\\list{}%",
        "   {\\leftmargin0pt \\usecounter{enumiv}}%",
        "\\sloppy",
        "\\clubpenalty4000",
        "\\@clubpenalty \\clubpenalty",
        "\\widowpenalty4000%",
        "\\sfcode`\\.\\@m}",
        "{\\def\\@noitemerr",
        "{\\@latex@warning{Empty `thebibliography' environment}}%",
        "\\endlist}",
        "\\makeatother",
        "\\fi % end of ifbiblatex",
        "%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%",
        "% Make a file to put the cited information into",
        "\\FileOpen{citedtagsfile}{citedtags.bib}",
        "",
        "\\makeatletter",
        "\\newcommand\\removebibheader{\\let\\bib@heading\\relax}",
        "\\makeatother",
    ]

    for data in included_papers:
        idx = data.get('tab_index')
        label = data.get('label')
        bib_key = data.get('bib_key')
        pdf_file = data.get('file_path', '')
        pdf_downloaded = data.get('pdf_downloaded', False)
        # Remove .pdf extension for the \myfancytab command
        path_base = pdf_file.rsplit('.', 1)[0] if '.' in pdf_file else pdf_file
        
        scale = data.get('scale', 0.9)
        pages = data.get('pdf_pages', '1-')
        perm = data.get('permission_text', '')

        lines.append(f"% Divider for {label}")
        lines.append("\\thispagestyle{empty}")
        lines.append("\\begin{dividerContent}{0cm}{5cm}")
        # Note: using the explicit tab_index (idx) instead of the loop counter
        lines.append(f"\\myfancytab[RIGHT]{{\\ref*{{{label}}}}}{{{idx}}}{{{bib_key}}}{{{path_base}}}")
        lines.append("")
        lines.append(perm)
        lines.append("\\end{dividerContent}")
        lines.append("\\cleardoublepage")
        if pdf_downloaded:
            lines.append(f"\\includepdf[pages={{{pages}}},scale={scale}]{{{pdf_file}}}")
        else:
            print(f"no PDF file downloaded for {label}")
            # Add a comment in the TeX file
            lines.append(f"% PDF file missing for {label} - please check your Included_publications/ folder")
            # Optional: Add a visual placeholder in the PDF
            # Clean the path string so underscores like 'Included_publications' don't break LaTeX
            clean_path = clean_latex_string(pdf_file)
            lines.append(f"\\begin{{center}}\\huge\\color{{red}}MISSING PDF: {clean_path}\\end{{center}}")
            lines.append(f"%\\includepdf[pages={{{pages}}},scale={scale}]{{{pdf_file}}}")
        lines.append("\\cleardoublepage")

    lines.append("\\FileClose{citedtagsfile}")
    lines.append("\\fi")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    generate_latex_dividers('publications_map.json', 'lib/publications_dividers_generated.tex')
