#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-
"""
compare_dual_language_classifier_to_OSDG_CD.py

Evaluates sdg_classifier_dual_language.py against the OSDG Community Dataset
(e.g., /tmp/osdg-community-data-v2024-04-01.csv).

Columns expected in the OSDG-CD CSV:
  - doi: Digital Object Identifier of the original document
  - text_id: unique text identifier
  - text: text excerpt from the document
  - sdg: the SDG (1..17) the text is validated against
  - labels_negative: number of volunteers who rejected the suggested SDG label
  - labels_positive: number of volunteers who accepted the suggested SDG label
  - agreement: |labels_positive - labels_negative| / (labels_positive + labels_negative)
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import re
import sys
import time
import unicodedata

# Import matching tables, rules, and cleaning helpers from sdg_classifier_dual_language.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sdg_classifier_dual_language as clf


class FastDualLanguageScanner:
    """
    Pre-compiles all regex patterns from sdg_classifier_dual_language.py once
    so tens of thousands of OSDG-CD excerpts can be classified in seconds with
    identical matching semantics.
    """

    def __init__(self) -> None:
        # Ensure clf.replace_ligature() does not fail when clf.main() was not invoked
        clf.args = argparse.Namespace(verbose=False)

        # Track 1: Case-sensitive acronyms
        self.acronym_patterns: dict[int, list[tuple[str, re.Pattern[str]]]] = {
            sdg: [
                (acr, re.compile(rf"\b{re.escape(acr)}\b"))
                for acr in acrs
            ]
            for sdg, acrs in clf.ACRONYMS_SDG_TERMS.items()
        }

        # Track 2: Case-insensitive dual-language phrases
        sdg_terms = clf.build_dual_language_term_index()
        self.phrase_patterns: dict[int, list[tuple[str, re.Pattern[str]]]] = {}
        for sdg_num, terms in sdg_terms.items():
            compiled_list: list[tuple[str, re.Pattern[str]]] = []
            for phrase in terms:
                if len(phrase) < 3:
                    continue
                if (sdg_num, phrase) in clf.CUSTOM_TERM_REGEX:
                    pat_str = clf.CUSTOM_TERM_REGEX[(sdg_num, phrase)]
                else:
                    escaped = re.escape(phrase.lower())
                    if " " not in phrase and len(phrase) >= 6:
                        pat_str = rf"(?<!\w){escaped}"
                    else:
                        pat_str = rf"(?<!\w){escaped}(?!\w)"
                compiled_list.append((phrase, re.compile(pat_str)))
            self.phrase_patterns[sdg_num] = compiled_list

        # Track 3: Pre-compiled STRINGS co-occurrence rules from module
        self.strings_rules = clf.COMPILED_STRINGS_RULES

    def scan(
        self, raw_text: str
    ) -> tuple[dict[int, int], dict[int, list[tuple[str, int]]], set[int]]:
        text = clf.clean_text_artifacts(raw_text)
        text = unicodedata.normalize("NFC", text)
        text = clf.replace_ligature(text)

        counts: dict[int, int] = {i: 0 for i in range(1, 18)}
        matched_phrases: dict[int, list[tuple[str, int]]] = {i: [] for i in range(1, 18)}

        # Track 1: Case-Sensitive Acronym Matching
        for sdg_num, pats in self.acronym_patterns.items():
            acr_counter: Counter[str] = Counter()
            for acr, pat in pats:
                m = len(pat.findall(text))
                if m > 0:
                    acr_counter[acr] += m
            counts[sdg_num] += sum(acr_counter.values())
            matched_phrases[sdg_num].extend(acr_counter.most_common(10))

        # Track 2: Case-Insensitive General Phrase Matching
        lower_text = f" {text.lower()} "
        for sdg_num, pats in self.phrase_patterns.items():
            phrase_counter: Counter[str] = Counter()
            for phrase, pat in pats:
                m = len(pat.findall(lower_text))
                if m > 0:
                    phrase_counter[phrase] += m
            counts[sdg_num] += sum(phrase_counter.values())
            matched_phrases[sdg_num].extend(phrase_counter.most_common(10))

        # Track 3: STRINGS co-occurrence rules
        for sdg_num, rules in self.strings_rules.items():
            hits = 0
            rule_matches: list[tuple[str, int]] = []
            for r in rules:
                if all(clause_pat.search(lower_text) for clause_pat in r["clauses"]):
                    hits += 1
                    rule_matches.append((r["raw"], 1))
            if hits > 0:
                counts[sdg_num] += hits
                matched_phrases[sdg_num].extend(rule_matches)

        # Track 4: Explicit author declarations
        explicit_mentions = clf.extract_explicit_sdg_mentions(text)

        return counts, matched_phrases, explicit_mentions


def detect_delimiter(csv_path: Path) -> str:
    """Detect whether the OSDG-CD file is tab- or comma-delimited."""
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
    first_line = sample.splitlines()[0] if sample else ""
    if "\t" in first_line and first_line.count("\t") >= first_line.count(","):
        return "\t"
    return ","


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare sdg_classifier_dual_language.py against OSDG Community Dataset (OSDG-CD)."
    )
    parser.add_argument(
        "csv_file",
        nargs="?",
        type=Path,
        default=Path("/tmp/osdg-community-data-v2024-04-01.csv"),
        help="Path to OSDG-CD CSV file (default: /tmp/osdg-community-data-v2024-04-01.csv)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Maximum candidate SDGs inferred per excerpt (default: 3)",
    )
    parser.add_argument(
        "--min-hits",
        type=int,
        default=2,
        help="Minimum keyword occurrences required to infer an SDG (default: 2)",
    )
    parser.add_argument(
        "--min-agreement",
        type=float,
        default=0.0,
        help="Minimum volunteer agreement threshold in [0.0, 1.0] (default: 0.0)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of rows to process (0 = all rows)",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=None,
        help="Optional path to write per-excerpt comparison results as CSV",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output summary metrics as JSON",
    )
    args = parser.parse_args()

    if not args.csv_file.is_file():
        sys.exit(f"Error: OSDG-CD file '{args.csv_file}' not found.")

    t0 = time.perf_counter()
    scanner = FastDualLanguageScanner()
    delimiter = detect_delimiter(args.csv_file)

    # Counters for positive-consensus rows (labels_positive > labels_negative)
    pos_total = 0
    pos_top1_hit = 0
    pos_topk_hit = 0
    pos_any_hit = 0
    pos_abstain = 0  # no goal reached --min-hits

    # Counters for negative-consensus rows (labels_negative > labels_positive)
    neg_total = 0
    neg_triggered_rejected_sdg = 0  # classifier inferred the volunteer-rejected SDG (>= min_hits)
    neg_cleanly_rejected = 0        # classifier did NOT infer the rejected SDG

    # Counters for tied rows (labels_positive == labels_negative)
    tie_total = 0

    # Per-SDG metrics on positive-consensus rows
    sdg_support: Counter[int] = Counter()
    sdg_tp_topk: Counter[int] = Counter()
    sdg_tp_top1: Counter[int] = Counter()
    sdg_fp_topk: Counter[int] = Counter()  # inferred on positive rows whose validated label != g
    sdg_neg_support: Counter[int] = Counter()
    sdg_neg_fp: Counter[int] = Counter()

    skipped_agreement = 0
    malformed_rows = 0

    csv_writer = None
    out_fh = None
    if args.output_csv:
        out_fh = open(args.output_csv, "w", encoding="utf-8", newline="")
        csv_writer = csv.writer(out_fh)
        csv_writer.writerow([
            "text_id",
            "doi",
            "osdg_sdg",
            "labels_positive",
            "labels_negative",
            "agreement",
            "consensus_polarity",
            "classifier_top1",
            "classifier_topk",
            "hits_for_osdg_sdg",
            "top1_match",
            "topk_match",
            "top_matched_terms",
        ])

    with open(args.csv_file, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        for row_idx, row in enumerate(reader, 1):
            if args.limit and row_idx > args.limit:
                break

            try:
                target_sdg = int(str(row["sdg"]).strip())
                lbl_pos = int(float(row["labels_positive"]))
                lbl_neg = int(float(row["labels_negative"]))
                agreement = float(row["agreement"])
                text = row.get("text", "") or ""
            except (KeyError, ValueError, TypeError):
                malformed_rows += 1
                continue

            if not (1 <= target_sdg <= 17):
                malformed_rows += 1
                continue

            if agreement < args.min_agreement:
                skipped_agreement += 1
                continue

            counts, matched_phrases, explicit_mentions = scanner.scan(text)
            ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)

            inferred_topk = [g for g, c in ranked if c >= args.min_hits][: args.top_k]
            if explicit_mentions:
                # Mirror precedence in sdg_classifier_dual_language.py when explicit SDG mentions exist
                for g in sorted(explicit_mentions):
                    if g not in inferred_topk:
                        inferred_topk.append(g)

            top1_sdg = ranked[0][0] if ranked[0][1] >= args.min_hits else None
            target_hits = counts[target_sdg]

            if lbl_pos > lbl_neg:
                polarity = "positive"
                pos_total += 1
                sdg_support[target_sdg] += 1

                if not inferred_topk:
                    pos_abstain += 1
                if target_hits > 0:
                    pos_any_hit += 1
                if top1_sdg == target_sdg:
                    pos_top1_hit += 1
                    sdg_tp_top1[target_sdg] += 1
                if target_sdg in inferred_topk:
                    pos_topk_hit += 1
                    sdg_tp_topk[target_sdg] += 1

                for g in inferred_topk:
                    if g != target_sdg:
                        sdg_fp_topk[g] += 1

            elif lbl_neg > lbl_pos:
                polarity = "negative"
                neg_total += 1
                sdg_neg_support[target_sdg] += 1
                if target_sdg in inferred_topk:
                    neg_triggered_rejected_sdg += 1
                    sdg_neg_fp[target_sdg] += 1
                else:
                    neg_cleanly_rejected += 1
            else:
                polarity = "tie"
                tie_total += 1

            if csv_writer:
                top_terms_str = "; ".join(
                    f"{p}:{c}" for p, c in matched_phrases[target_sdg][:5]
                )
                csv_writer.writerow([
                    row.get("text_id", ""),
                    row.get("doi", ""),
                    target_sdg,
                    lbl_pos,
                    lbl_neg,
                    f"{agreement:.4f}",
                    polarity,
                    top1_sdg or "",
                    ",".join(str(g) for g in inferred_topk),
                    target_hits,
                    int(top1_sdg == target_sdg),
                    int(target_sdg in inferred_topk),
                    top_terms_str,
                ])

    if out_fh:
        out_fh.close()

    elapsed = time.perf_counter() - t0

    # Build per-SDG breakdown
    per_sdg_rows = []
    for g in range(1, 18):
        sup = sdg_support[g]
        tp_k = sdg_tp_topk[g]
        tp_1 = sdg_tp_top1[g]
        fp_k = sdg_fp_topk[g]
        rec_k = (tp_k / sup) if sup else 0.0
        rec_1 = (tp_1 / sup) if sup else 0.0
        prec_k = (tp_k / (tp_k + fp_k)) if (tp_k + fp_k) else 0.0
        f1_k = (2 * prec_k * rec_k / (prec_k + rec_k)) if (prec_k + rec_k) else 0.0
        neg_sup = sdg_neg_support[g]
        neg_fp = sdg_neg_fp[g]
        neg_fp_rate = (neg_fp / neg_sup) if neg_sup else 0.0
        per_sdg_rows.append({
            "sdg": g,
            "pos_support": sup,
            "top1_recall": round(rec_1, 4),
            "topk_recall": round(rec_k, 4),
            "topk_precision_proxy": round(prec_k, 4),
            "topk_f1_proxy": round(f1_k, 4),
            "neg_support": neg_sup,
            "neg_false_positive_rate": round(neg_fp_rate, 4),
        })

    summary = {
        "csv_file": str(args.csv_file),
        "elapsed_seconds": round(elapsed, 2),
        "parameters": {
            "top_k": args.top_k,
            "min_hits": args.min_hits,
            "min_agreement": args.min_agreement,
        },
        "counts": {
            "positive_consensus_rows": pos_total,
            "negative_consensus_rows": neg_total,
            "tied_rows": tie_total,
            "skipped_below_min_agreement": skipped_agreement,
            "malformed_rows": malformed_rows,
        },
        "positive_consensus_metrics": {
            "top1_accuracy": round(pos_top1_hit / pos_total, 4) if pos_total else 0.0,
            "topk_recall": round(pos_topk_hit / pos_total, 4) if pos_total else 0.0,
            "any_hit_ge1_rate": round(pos_any_hit / pos_total, 4) if pos_total else 0.0,
            "abstention_rate": round(pos_abstain / pos_total, 4) if pos_total else 0.0,
        },
        "negative_consensus_metrics": {
            "specificity_cleanly_rejected": round(neg_cleanly_rejected / neg_total, 4) if neg_total else 0.0,
            "false_positive_rate_on_rejected": round(neg_triggered_rejected_sdg / neg_total, 4) if neg_total else 0.0,
        },
        "per_sdg": per_sdg_rows,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return

    print("=" * 78)
    print("==> Dual-Language Classifier vs. OSDG Community Dataset (OSDG-CD)")
    print(f"Dataset          : {args.csv_file}")
    print(f"Parameters       : top_k={args.top_k}, min_hits={args.min_hits}, min_agreement>={args.min_agreement:.2f}")
    print(f"Elapsed Time     : {elapsed:.2f}s")
    print("-" * 78)
    print(f"Positive-Consensus Excerpts (labels_positive > labels_negative) : {pos_total}")
    print(f"Negative-Consensus Excerpts (labels_negative > labels_positive) : {neg_total}")
    print(f"Tied Excerpts               (labels_positive == labels_negative): {tie_total}")
    if skipped_agreement:
        print(f"Skipped (< min_agreement)                                       : {skipped_agreement}")
    if malformed_rows:
        print(f"Skipped (malformed/non-1..17 SDG)                               : {malformed_rows}")
    print("-" * 78)

    if pos_total > 0:
        print("1. Positive-Consensus Agreement (Validated OSDG Label Present):")
        print(f"   * Top-1 Exact Match          : {pos_top1_hit:6d} / {pos_total} ({100.0 * pos_top1_hit / pos_total:5.1f}%)")
        print(f"   * Top-{args.top_k} Match (hits>={args.min_hits})     : {pos_topk_hit:6d} / {pos_total} ({100.0 * pos_topk_hit / pos_total:5.1f}%)")
        print(f"   * Any Lexical Hit (hits>=1)  : {pos_any_hit:6d} / {pos_total} ({100.0 * pos_any_hit / pos_total:5.1f}%)")
        print(f"   * Abstention (no goal >={args.min_hits})   : {pos_abstain:6d} / {pos_total} ({100.0 * pos_abstain / pos_total:5.1f}%)")
        print()

    if neg_total > 0:
        print("2. Negative-Consensus Specificity (Volunteer-Rejected OSDG Label):")
        print(f"   * Cleanly Rejected (not in Top-{args.top_k}) : {neg_cleanly_rejected:6d} / {neg_total} ({100.0 * neg_cleanly_rejected / neg_total:5.1f}%)")
        print(f"   * False Positive (in Top-{args.top_k})       : {neg_triggered_rejected_sdg:6d} / {neg_total} ({100.0 * neg_triggered_rejected_sdg / neg_total:5.1f}%)")
        print()

    print("3. Per-SDG Breakdown:")
    print(f"{'SDG':>4} | {'Pos N':>6} | {'Top-1':>7} | {'Top-' + str(args.top_k):>7} | {'Prec*':>7} | {'F1*':>7} | {'Neg N':>6} | {'Neg FP':>7}")
    print("-" * 78)
    for r in per_sdg_rows:
        print(
            f"{r['sdg']:>4d} | "
            f"{r['pos_support']:>6d} | "
            f"{100.0 * r['top1_recall']:>6.1f}% | "
            f"{100.0 * r['topk_recall']:>6.1f}% | "
            f"{100.0 * r['topk_precision_proxy']:>6.1f}% | "
            f"{100.0 * r['topk_f1_proxy']:>6.1f}% | "
            f"{r['neg_support']:>6d} | "
            f"{100.0 * r['neg_false_positive_rate']:>6.1f}%"
        )
    print("-" * 78)
    print("*Note: Because each OSDG-CD row validates a single candidate SDG rather than")
    print(" an exhaustive multi-label set, 'Prec*' treats valid co-occurring SDGs in")
    print(" Top-k as non-target hits and is therefore a conservative lower bound.")
    if args.output_csv:
        print(f"Detailed per-excerpt CSV written to: {args.output_csv}")
    print("=" * 78)


if __name__ == "__main__":
    main()
