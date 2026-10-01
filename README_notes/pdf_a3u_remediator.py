import argparse
import fitz
import re

parser = argparse.ArgumentParser(description="Remediate a publication PDF for PDF/A-3u compliance.")
parser.add_argument("input", help="Path to input PDF")
parser.add_argument("output", help="Path to output remediated PDF")
args = parser.parse_args()

doc = fitz.open(args.input)
cleaned_cids = 0
cleaned_tr = 0
patched_cmaps = 0

# 1. Universal structural cleanup and CMap patching across all objects
for xref in range(1, doc.xref_length()):
  try:
    # A. Safely append a fallback range to CMap streams lacking '<fffd>'
    stream_data = doc.xref_stream(xref)
    if stream_data and b"begincmap" in stream_data:
      cmap_text = stream_data.decode("latin1", errors="ignore")
      if "endcmap" in cmap_text and "<fffd>" not in cmap_text:
        # Injecting a fallback bfrange right before endcmap handles unmapped glyphs safely
        fallback_range = "\n1 beginbfrange\n<00> <ff> <fffd>\nendbfrange\n"
        cmap_text = cmap_text.replace("endcmap", fallback_range + "endcmap")
        doc.update_stream(xref, cmap_text.encode("latin1"))
        patched_cmaps += 1
    # B. Fetch object text for dictionary cleanup
    obj_text = doc.xref_object(xref, compressed=False)
    if not obj_text or obj_text == "null":
      obj_text = doc.xref_object(xref, compressed=True)
    if obj_text:
      updated = False
      # Clean up conflicting /CIDSet references from font descriptors
      if "FontDescriptor" in obj_text or "CIDSet" in obj_text:
        new_obj = re.sub(r'/CIDSet\s+\d+\s+0\s+R', '', obj_text)
        if new_obj != obj_text:
          obj_text = new_obj
          updated = True
          cleaned_cids += 1
      # Purge forbidden /TR or /TR2 keys anywhere they appear in dictionaries
      if "/TR" in obj_text or "/TR2" in obj_text:
        new_obj = re.sub(r'/\s*TR2?\b\s*(?:\[[^\]]*\]|/[A-Za-z0-9#+-]+|\d+\s+\d+\s+R|<[^>]*>|\([^)]*\)|<<[^>]*>>)?', '', obj_text)
        if new_obj == obj_text:
          lines = obj_text.splitlines()
          filtered = [l for l in lines if not re.search(r'/\s*TR2?\b', l)]
          new_obj = "\n".join(filtered)
        if new_obj != obj_text:
          obj_text = new_obj
          updated = True
          cleaned_tr += 1
      if updated:
        doc.update_object(xref, obj_text)
  except Exception:
    continue

doc.save(args.output, garbage=4, deflate=True)
doc.close()
print(f"Universal processing complete: Patched {patched_cmaps} CMaps, cleaned {cleaned_cids} CIDSets, purged {cleaned_tr} TR keys.")
