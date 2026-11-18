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


def clean_diva_text(text: str) -> str:
    # 1. Canonical composition
    text = unicodedata.normalize("NFC", text)

    # 2. Replace non-breaking spaces (and thin/figure spaces if desired) with regular space
    text = text.replace("\u00a0", " ")

    # 3. Remove invisible layout artifacts (soft hyphen, zero-width space, BOM)
    text = re.sub(r"[\u00ad\u200b\u200c\u200d\ufeff]", "", text)

    # 4. Collapse multiple spaces/tabs while preserving intentional newlines
    text = re.sub(r"[ \t]+", " ", text)

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

