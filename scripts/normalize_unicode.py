#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
normalize_unicode.py

Normalize Unicode to NFC format as recommended by the W3C Character Model for the World Wide Web explicitly recommends NFC for all web content and metadata interchange, as almost all keyboard inputs, browsers, and XML/HTML parsers produce and expect precomposed characters. 
"""
import argparse
import json
from pathlib import Path
import sys
import re
import unicodedata
from typing import Any

global args

# ligature. LaTeX commonly does it for ff, fi, fl, ffi, ffl, ...
ligature_table= {'\ufb00': 'ff', # 'ﬀ'
                  '\ufb03': 'f‌f‌i', # 'ﬃ'
                  '\ufb04': 'ffl', # 'ﬄ'
                  '\ufb01': 'fi', # 'ﬁ'
                  '\ufb02': 'fl', # 'ﬂ'
                  '\ua732': 'AA', # 'Ꜳ'
                  '\ua733': 'aa', # 'ꜳ'
                  '\ua733': 'aa', # 'ꜳ'
                  '\u00c6': 'AE', # 'Æ'
                  '\u00e6': 'ae', # 'æ'
                  '\uab31': 'aə', # 'ꬱ'
                  '\ua734': 'AO', # 'Ꜵ'
                  '\ua735': 'ao', # 'ꜵ'
                  '\ua736': 'AU', # 'Ꜷ'
                  '\ua737': 'au', # 'ꜷ'
                  '\ua738': 'AV', # 'Ꜹ'
                  '\ua739': 'av', # 'ꜹ'
                  '\ua73a': 'AV', # 'Ꜻ'  - note the bar
                  '\ua73b': 'av', # 'ꜻ'  - note the bar
                  '\ua73c': 'AY', # 'Ꜽ'
                  '\ua76a': 'ET', # 'Ꝫ'
                  '\ua76b': 'et', # 'ꝫ'
                  '\uab41': 'əø', # 'ꭁ'
                  '\u01F6': 'Hv', # 'Ƕ'
                  '\u0195': 'hu', # 'ƕ'
                  '\u2114': 'lb', # '℔'
                  '\u1efa': 'IL', # 'Ỻ'
                  '\u0152': 'OE', # 'Œ'
                  '\u0153': 'oe', # 'œ'
                  '\ua74e': 'OO', # 'Ꝏ'
                  '\ua74f': 'oo', # 'ꝏ'
                  '\uab62': 'ɔe', # 'ꭢ'
                  '\u1e9e': 'fs', # 'ẞ'
                  '\u00df': 'fz', # 'ß'
                  '\ufb06': 'st', # 'ﬆ'
                  '\ufb05': 'ſt', # 'ﬅ'  -- long ST
                  '\ua728': 'Tz', # 'Ꜩ'
                  '\ua729': 'tz', # 'ꜩ'
                  '\u1d6b': 'ue', # 'ᵫ'
                  '\uab63': 'uo', # 'ꭣ'
                  #'\u0057': 'UU', # 'W'
                  #'\u0077': 'uu', # 'w'
                  '\ua760': 'VY', # 'Ꝡ'
                  '\ua761': 'vy', # 'ꝡ'
                  # 
                  '\u0238': 'db', # 'ȸ'
                  '\u02a3': 'dz', # 'ʣ'
                  '\u1b66': 'dʐ', # 'ꭦ'
                  '\u02a5': 'dʑ', # 'ʥ'
                  '\u02a4': 'dʒ', # 'ʤ'
                  '\u02a9': 'fŋ', # 'ʩ'
                  '\u02aa': 'ls', # 'ʪ'
                  '\u02ab': 'lz', # 'ʫ'
                  '\u026e': 'lʒ', # 'ɮ'
                  '\u0239': 'qp', # 'ȹ'
                  '\u02a8': 'tɕ', # 'ʨ'
                  '\u02a6': 'ts', # 'ʦ'
                  '\uab67': 'tʂ', # 'ꭧ'
                  '\u02a7': 'tʃ', # 'ʧ'
                  '\uab50': 'ui', # 'ꭐ'
                  '\uab51': 'ui', # 'ꭑ' -- turned ui
                  '\u026f': 'uu', # 'ɯ'
                  # digraphs with single code points
                  '\u01f1': 'DZ', # 'Ǳ'
                  '\u01f2': 'Dz', # 'ǲ'
                  '\u01f3': 'dz', # 'ǳ'
                  '\u01c4': 'DŽ', # 'Ǆ'
                  '\u01c5': 'Dž', # 'ǅ'
                  '\u01c6': 'dž', # 'ǆ'
                  '\u0132': 'IJ', # 'Ĳ'
                  '\u0133': 'ij', # 'ĳ'
                  '\u01c7': 'LJ', # 'Ǉ'
                  '\u01c8': 'Lj', # 'ǈ'
                  '\u01c9': 'lj', # 'ǉ'
                  '\u01ca': 'NJ', # 'Ǌ'
                  '\u01cb': 'Nj', # 'ǋ'
                  '\u01cc': 'nj', # 'ǌ'
                  '\u1d7a': 'th', # 'ᵺ'
                  }

def replace_ligature(s: str) -> str:
    global args
    # check for ligatures and replace them with separate characters
    if not s:
        return s
    
    for l in ligature_table:
        if s.find(l) >= 0:
            if args.verbose:
                print("found ligature {0} replacing with {1}".format(l, ligature_table[l]))  
            s=s.replace(l, ligature_table[l])
    #
    return s


def clean_diva_text(text: str) -> str:
    # 1. Canonical composition
    text = unicodedata.normalize("NFC", text)

    # 2. Replace non-breaking spaces (and thin/figure spaces if desired) with regular space
    text = text.replace("\u00a0", " ")

    # 3. Remove invisible layout artifacts (soft hyphen, zero-width space, BOM)
    text = re.sub(r"[\u00ad\u200b\u200c\u200d\ufeff]", "", text)

    # 4. Collapse multiple spaces/tabs while preserving intentional newlines
    text = re.sub(r"[ \t]+", " ", text)

    # 5. Replace ligatures
    text = replace_ligature(text)

    return text.strip()

def audit_unicode_normalization(text: str) -> dict[str, Any]:
  """Inspects a string for NFD/non-NFC sequences and reports combining marks."""
  nfc_text = unicodedata.normalize("NFC", text)
  is_nfc = text == nfc_text

  combining_sequences = []
  for idx, ch in enumerate(text):
    if unicodedata.combining(ch) != 0:
      prev_ch = text[idx - 1] if idx > 0 else ""
      cluster = prev_ch + ch
      combining_sequences.append({
          "index": idx,
          "cluster": cluster,
          "codepoints": [f"U+{ord(c):04X}" for c in cluster],
          "names": [unicodedata.name(c, "UNKNOWN") for c in cluster],
          "nfc_equivalent": unicodedata.normalize("NFC", cluster),
      })

  return {
      "is_nfc": is_nfc,
      "original_len": len(text),
      "nfc_len": len(nfc_text),
      "combining_marks_found": len(combining_sequences),
      "details": combining_sequences,
      "normalized_text": nfc_text,
  }


def normalize_diva_record(data: Any) -> Any:
  """Recursively normalizes all string keys and values in a nested JSON structure."""
  if isinstance(data, str):
    return clean_diva_text(data)
  if isinstance(data, dict):
    return {
        (
            clean_diva_text(k) if isinstance(k, str) else k
        ): normalize_diva_record(v)
        for k, v in data.items()
    }
  if isinstance(data, list):
    return [normalize_diva_record(item) for item in data]
  return data


def main():
    global args

    parser = argparse.ArgumentParser(
        description="Normailize Unicode in JSON dict"
    )
    parser.add_argument(
        "input_file",
        type=Path,
        help="Path to JSON file",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print lots of information",
    )

    args = parser.parse_args()

    if not args.input_file.is_file():
        sys.exit(f"Error: File '{args.input_file}' not found.")

    is_json = False

    # Check if the content is valid JSON regardless of file extension
    try:
        with open(args.input_file, "r", encoding="utf-8") as f:
            raw_content = f.read()
            doc_data = json.loads(raw_content)
            is_json = True
    except (json.JSONDecodeError, UnicodeDecodeError):
        is_json = False

    if is_json:
        report = normalize_diva_record(doc_data)

        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"{is_json=} {args.input_file} is not in JSON format")



if __name__ == "__main__":
    main()

