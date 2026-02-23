#!/usr/bin/python3
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-
# This script reads the uploaded config_snippet.tex, extracts the keys/values, and performs a surgical replacement in the custom_configuration.tex file.

#!/usr/bin/python3
# -*- coding: utf-8 -*-

import re
import os

def merge_configs(main_file, snippet_file):
    if not os.path.exists(snippet_file):
        print(f"No snippet ({snippet_file}) found. Skipping merge.")
        return

    with open(main_file, 'r', encoding='utf-8') as f:
        main_content = f.read()

    with open(snippet_file, 'r', encoding='utf-8') as f:
        snippet_content = f.read()

    # 1. Extract commands from snippet
    # re.DOTALL handles multiline titles
    # Captures \command{value}
    commands = re.findall(r'\\(\w+)\s*\{(.*?)\}(?=\s*\\|\s*$)', snippet_content, re.DOTALL)

    for cmd, value in commands:
        # 2. Regex to find the existing line in custom_configuration.tex
        # (?m)        -> Multiline mode (^ matches start of line)
        # ^\s*%?\s* -> Optional leading comment char and whitespace
        # \\{cmd}     -> The specific LaTeX command
        # (?:\{.*?\})?-> Optional existing braces/content
        # [^\n]* -> Match the rest of the line (including comments)
        pattern = rf'(?m)^\s*%?\s*\\{cmd}(?:\{{.*?\}}|[^\n])*'
        
        replacement = rf'\{cmd}{{{value}}}'

        # Find all occurrences (e.g., active and commented versions of \degreeName)
        matches = list(re.finditer(pattern, main_content))
        
        if matches:
            # Logic: Replace exactly ONE line.
            # Priority: The first active line. If none active, the first commented one.
            target_match = None
            for m in matches:
                if not m.group(0).strip().startswith('%'):
                    target_match = m
                    break
            
            if not target_match:
                target_match = matches[0]

            start, end = target_match.span()
            main_content = main_content[:start] + replacement + main_content[end:]
            print(f"Surgically replaced: \\{cmd}")
        else:
            # If the command is completely missing, append it to a clean section at the top
            # but after the initial file comments.
            main_content = replacement + "\n" + main_content
            print(f"Preprended (new field): \\{cmd}")

    # 3. Save the merged result
    with open(main_file, 'w', encoding='utf-8') as f:
        f.write(main_content)
    
    # 4. Cleanup
    try:
        os.remove(snippet_file)
        print(f"Cleaned up {snippet_file}")
    except:
        pass
    
    print(f"\nMerge complete. {main_file} is now clean and updated.")

if __name__ == "__main__":
    merge_configs('custom_configuration.tex', 'config_snippet.tex')
