#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-
"""
compare_dual_language_classifier_to_OSDG_CD_parallel_edition_v2.py (Fast Parallel Edition)

Evaluates sdg_classifier_dual_language.py against the OSDG Community Dataset
(e.g., /tmp/osdg-community-data-v2024-04-01.csv) using combined per-SDG master
regexes and multiprocessing.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections import defaultdict
import csv
import json
import multiprocessing as mp
import os
from pathlib import Path
import re
import sys
import time
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sdg_classifier_dual_language as clf

# Worker-local compiled state
_WORKER_SCANNER: "CombinedDualLanguageScanner | None" = None

def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


class CombinedDualLanguageScanner:
    """
    O(N) multi-pattern trie scanner for Track 1 (acronyms) and Track 2 (phrases),
    combined with deduplicated clause evaluation for Track 3 (STRINGS rules).
    Processes ~5,000 OSDG-CD rows in ~2-4 seconds instead of 200+ seconds.
    """

    def __init__(self) -> None:
        clf.args = argparse.Namespace(verbose=False)

        # Build Track 1 Trie (case-sensitive acronyms -> list of sdg_nums)
        self.acr_trie: dict = {}
        for sdg_num, acrs in clf.ACRONYMS_SDG_TERMS.items():
            for acr in set(acrs):
                node = self.acr_trie
                for ch in acr:
                    node = node.setdefault(ch, {})
                node.setdefault("$", []).append((sdg_num, acr))

        # Build Track 2 Trie (case-insensitive standard phrases) + custom regexes
        sdg_terms = clf.build_dual_language_term_index()
        self.phrase_trie: dict = {}
        self.custom_patterns: dict[int, list[tuple[str, re.Pattern[str]]]] = {
            i: [] for i in range(1, 18)
        }

        for sdg_num, terms in sdg_terms.items():
            for phrase in terms:
                if len(phrase) < 3:
                    continue
                if (sdg_num, phrase) in clf.CUSTOM_TERM_REGEX:
                    self.custom_patterns[sdg_num].append(
                        (phrase, re.compile(clf.CUSTOM_TERM_REGEX[(sdg_num, phrase)]))
                    )
                else:
                    p_low = phrase.lower()
                    is_prefix = (" " not in p_low and len(p_low) >= 6)
                    node = self.phrase_trie
                    for ch in p_low:
                        node = node.setdefault(ch, {})
                    node.setdefault("$", []).append((sdg_num, phrase, is_prefix))

        # Track 3: Deduplicate identical STRINGS OR-clauses across all 17 SDGs
        unique_clause_patterns: dict[str, int] = {}
        self.compiled_unique_clauses: list[re.Pattern[str]] = []
        self.strings_rule_ids: dict[int, list[tuple[str, tuple[int, ...]]]] = {}

        for sdg_num, rules in clf.COMPILED_STRINGS_RULES.items():
            rule_entries = []
            for r in rules:
                c_ids = []
                for cpat in r["clauses"]:
                    pat_key = cpat.pattern
                    cid = unique_clause_patterns.get(pat_key)
                    if cid is None:
                        cid = len(self.compiled_unique_clauses)
                        unique_clause_patterns[pat_key] = cid
                        self.compiled_unique_clauses.append(cpat)
                    c_ids.append(cid)
                rule_entries.append((r["raw"], tuple(c_ids)))
            self.strings_rule_ids[sdg_num] = rule_entries

    @staticmethod
    def _scan_acr_trie(trie: dict, text: str) -> dict[int, Counter[str]]:
        out: dict[int, Counter[str]] = defaultdict(Counter)
        n = len(text)
        for i in range(n):
            if i > 0 and _is_word_char(text[i - 1]):
                continue
            node = trie
            j = i
            while j < n and text[j] in node:
                node = node[text[j]]
                j += 1
                if "$" in node and (j == n or not _is_word_char(text[j])):
                    for sdg_num, acr in node["$"]:
                        out[sdg_num][acr] += 1
        return out

    @staticmethod
    def _scan_phrase_trie(trie: dict, lower_text: str) -> dict[int, Counter[str]]:
        out: dict[int, Counter[str]] = defaultdict(Counter)
        n = len(lower_text)
        for i in range(n):
            if i > 0 and _is_word_char(lower_text[i - 1]):
                continue
            node = trie
            j = i
            while j < n and lower_text[j] in node:
                node = node[lower_text[j]]
                j += 1
                if "$" in node:
                    end_ok = (j == n or not _is_word_char(lower_text[j]))
                    for sdg_num, phrase, is_prefix in node["$"]:
                        if is_prefix or end_ok:
                            out[sdg_num][phrase] += 1
        return out

    def scan(
        self, raw_text: str
    ) -> tuple[dict[int, int], dict[int, list[tuple[str, int]]], set[int]]:
        text = clf.clean_text_artifacts(raw_text)
        text = unicodedata.normalize("NFC", text)
        text = clf.replace_ligature(text)

        counts: dict[int, int] = {i: 0 for i in range(1, 18)}
        matched_phrases: dict[int, list[tuple[str, int]]] = {i: [] for i in range(1, 18)}

        # Track 1: O(N) Acronym Trie
        acr_hits = self._scan_acr_trie(self.acr_trie, text)
        for sdg_num, c in acr_hits.items():
            counts[sdg_num] += sum(c.values())
            matched_phrases[sdg_num].extend(c.most_common(10))

        # Track 2: O(N) Phrase Trie + Custom Regexes
        lower_text = f" {text.lower()} "
        phrase_hits = self._scan_phrase_trie(self.phrase_trie, lower_text)
        for sdg_num in range(1, 18):
            c = phrase_hits[sdg_num]
            for phrase, cpat in self.custom_patterns[sdg_num]:
                m = len(cpat.findall(lower_text))
                if m > 0:
                    c[phrase] += m
            if c:
                counts[sdg_num] += sum(c.values())
                matched_phrases[sdg_num].extend(c.most_common(10))

        # Track 3: Deduplicated STRINGS Clause Evaluation
        clause_results = [
            bool(cpat.search(lower_text)) for cpat in self.compiled_unique_clauses
        ]
        for sdg_num, rule_entries in self.strings_rule_ids.items():
            hits = 0
            rule_matches: list[tuple[str, int]] = []
            for raw_rule, c_ids in rule_entries:
                if all(clause_results[cid] for cid in c_ids):
                    hits += 1
                    rule_matches.append((raw_rule, 1))
            if hits > 0:
                counts[sdg_num] += hits
                matched_phrases[sdg_num].extend(rule_matches)

        # Track 4: Explicit author declarations
        explicit_mentions = clf.extract_explicit_sdg_mentions(text)

        return counts, matched_phrases, explicit_mentions

def _init_worker() -> None:
    global _WORKER_SCANNER
    _WORKER_SCANNER = CombinedDualLanguageScanner()


def _process_batch(
    batch: list[tuple[str, str, int, int, int, float, str]],
    top_k: int,
    min_hits: int,
    collect_csv_rows: bool,
) -> tuple[dict, list[list]]:
    assert _WORKER_SCANNER is not None

    pos_total = pos_top1_hit = pos_topk_hit = pos_any_hit = pos_abstain = 0
    neg_total = neg_triggered_rejected = neg_cleanly_rejected = tie_total = 0

    sdg_support: Counter[int] = Counter()
    sdg_tp_topk: Counter[int] = Counter()
    sdg_tp_top1: Counter[int] = Counter()
    sdg_fp_topk: Counter[int] = Counter()
    sdg_neg_support: Counter[int] = Counter()
    sdg_neg_fp: Counter[int] = Counter()
    csv_rows: list[list] = []

    for text_id, doi, target_sdg, lbl_pos, lbl_neg, agreement, text in batch:
        counts, matched_phrases, explicit_mentions = _WORKER_SCANNER.scan(text)
        ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)

        inferred_topk = [g for g, c in ranked if c >= min_hits][:top_k]
        if explicit_mentions:
            for g in sorted(explicit_mentions):
                if g not in inferred_topk:
                    inferred_topk.append(g)

        top1_sdg = ranked[0][0] if ranked[0][1] >= min_hits else None
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
                neg_triggered_rejected += 1
                sdg_neg_fp[target_sdg] += 1
            else:
                neg_cleanly_rejected += 1
        else:
            polarity = "tie"
            tie_total += 1

        if collect_csv_rows:
            top_terms_str = "; ".join(
                f"{p}:{c}" for p, c in matched_phrases[target_sdg][:5]
            )
            csv_rows.append([
                text_id,
                doi,
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

    stats = {
        "pos_total": pos_total,
        "pos_top1_hit": pos_top1_hit,
        "pos_topk_hit": pos_topk_hit,
        "pos_any_hit": pos_any_hit,
        "pos_abstain": pos_abstain,
        "neg_total": neg_total,
        "neg_triggered_rejected": neg_triggered_rejected,
        "neg_cleanly_rejected": neg_cleanly_rejected,
        "tie_total": tie_total,
        "sdg_support": sdg_support,
        "sdg_tp_topk": sdg_tp_topk,
        "sdg_tp_top1": sdg_tp_top1,
        "sdg_fp_topk": sdg_fp_topk,
        "sdg_neg_support": sdg_neg_support,
        "sdg_neg_fp": sdg_neg_fp,
    }
    return stats, csv_rows


def detect_delimiter(csv_path: Path) -> str:
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
    first_line = sample.splitlines()[0] if sample else ""
    if "\t" in first_line and first_line.count("\t") >= first_line.count(","):
        return "\t"
    return ","


def iter_batches(
    csv_path: Path,
    delimiter: str,
    min_agreement: float,
    limit: int,
    batch_size: int = 500,
):
    batch = []
    skipped_agreement = 0
    malformed_rows = 0

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        for row_idx, row in enumerate(reader, 1):
            if limit and row_idx > limit:
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

            if agreement < min_agreement:
                skipped_agreement += 1
                continue

            batch.append((
                row.get("text_id", ""),
                row.get("doi", ""),
                target_sdg,
                lbl_pos,
                lbl_neg,
                agreement,
                text,
            ))
            if len(batch) >= batch_size:
                yield batch, skipped_agreement, malformed_rows
                batch = []

    if batch or skipped_agreement or malformed_rows:
        yield batch, skipped_agreement, malformed_rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fast parallel comparison of sdg_classifier_dual_language.py against OSDG-CD."
    )
    parser.add_argument(
        "csv_file",
        nargs="?",
        type=Path,
        default=Path("/tmp/osdg-community-data-v2024-04-01.csv"),
        help="Path to OSDG-CD CSV file (default: /tmp/osdg-community-data-v2024-04-01.csv)",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-hits", type=int, default=2)
    parser.add_argument("--min-agreement", type=float, default=0.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    parser.add_argument("--output-csv", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not args.csv_file.is_file():
        sys.exit(f"Error: OSDG-CD file '{args.csv_file}' not found.")

    t0 = time.perf_counter()
    delimiter = detect_delimiter(args.csv_file)

    csv_writer = None
    out_fh = None
    if args.output_csv:
        out_fh = open(args.output_csv, "w", encoding="utf-8", newline="")
        csv_writer = csv.writer(out_fh)
        csv_writer.writerow([
            "text_id", "doi", "osdg_sdg", "labels_positive", "labels_negative",
            "agreement", "consensus_polarity", "classifier_top1", "classifier_topk",
            "hits_for_osdg_sdg", "top1_match", "topk_match", "top_matched_terms",
        ])

    totals = Counter()
    sdg_support: Counter[int] = Counter()
    sdg_tp_topk: Counter[int] = Counter()
    sdg_tp_top1: Counter[int] = Counter()
    sdg_fp_topk: Counter[int] = Counter()
    sdg_neg_support: Counter[int] = Counter()
    sdg_neg_fp: Counter[int] = Counter()
    skipped_agreement = 0
    malformed_rows = 0

    def task_gen():
        nonlocal skipped_agreement, malformed_rows
        for batch, sk, mal in iter_batches(
            args.csv_file, delimiter, args.min_agreement, args.limit, batch_size=500
        ):
            skipped_agreement = sk
            malformed_rows = mal
            if batch:
                yield (batch, args.top_k, args.min_hits, csv_writer is not None)

    processed = 0
    next_report = 5000

    with mp.Pool(processes=args.workers, initializer=_init_worker) as pool:
        for stats, csv_rows in pool.starmap(_process_batch, task_gen(), chunksize=1):
            for k in (
                "pos_total", "pos_top1_hit", "pos_topk_hit", "pos_any_hit",
                "pos_abstain", "neg_total", "neg_triggered_rejected",
                "neg_cleanly_rejected", "tie_total",
            ):
                totals[k] += stats[k]
            sdg_support.update(stats["sdg_support"])
            sdg_tp_topk.update(stats["sdg_tp_topk"])
            sdg_tp_top1.update(stats["sdg_tp_top1"])
            sdg_fp_topk.update(stats["sdg_fp_topk"])
            sdg_neg_support.update(stats["sdg_neg_support"])
            sdg_neg_fp.update(stats["sdg_neg_fp"])

            if csv_writer and csv_rows:
                csv_writer.writerows(csv_rows)

            processed = totals["pos_total"] + totals["neg_total"] + totals["tie_total"]
            if not args.json and processed >= next_report:
                print(
                    f"[progress] {processed} rows processed in {time.perf_counter() - t0:.1f}s...",
                    file=sys.stderr,
                    flush=True,
                )
                next_report += 5000

    if out_fh:
        out_fh.close()

    elapsed = time.perf_counter() - t0
    pos_total = totals["pos_total"]
    neg_total = totals["neg_total"]
    tie_total = totals["tie_total"]

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
        per_sdg_rows.append({
            "sdg": g,
            "pos_support": sup,
            "top1_recall": round(rec_1, 4),
            "topk_recall": round(rec_k, 4),
            "topk_precision_proxy": round(prec_k, 4),
            "topk_f1_proxy": round(f1_k, 4),
            "neg_support": neg_sup,
            "neg_false_positive_rate": round((neg_fp / neg_sup) if neg_sup else 0.0, 4),
        })

    summary = {
        "csv_file": str(args.csv_file),
        "elapsed_seconds": round(elapsed, 2),
        "workers": args.workers,
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
            "top1_accuracy": round(totals["pos_top1_hit"] / pos_total, 4) if pos_total else 0.0,
            "topk_recall": round(totals["pos_topk_hit"] / pos_total, 4) if pos_total else 0.0,
            "any_hit_ge1_rate": round(totals["pos_any_hit"] / pos_total, 4) if pos_total else 0.0,
            "abstention_rate": round(totals["pos_abstain"] / pos_total, 4) if pos_total else 0.0,
        },
        "negative_consensus_metrics": {
            "specificity_cleanly_rejected": round(totals["neg_cleanly_rejected"] / neg_total, 4) if neg_total else 0.0,
            "false_positive_rate_on_rejected": round(totals["neg_triggered_rejected"] / neg_total, 4) if neg_total else 0.0,
        },
        "per_sdg": per_sdg_rows,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return

    print("=" * 78)
    print("==> Dual-Language Classifier vs. OSDG Community Dataset (OSDG-CD)")
    print(f"Dataset          : {args.csv_file}")
    print(f"Parameters       : top_k={args.top_k}, min_hits={args.min_hits}, min_agreement>={args.min_agreement:.2f}, workers={args.workers}")
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
        print(f"   * Top-1 Exact Match          : {totals['pos_top1_hit']:6d} / {pos_total} ({100.0 * totals['pos_top1_hit'] / pos_total:5.1f}%)")
        print(f"   * Top-{args.top_k} Match (hits>={args.min_hits})     : {totals['pos_topk_hit']:6d} / {pos_total} ({100.0 * totals['pos_topk_hit'] / pos_total:5.1f}%)")
        print(f"   * Any Lexical Hit (hits>=1)  : {totals['pos_any_hit']:6d} / {pos_total} ({100.0 * totals['pos_any_hit'] / pos_total:5.1f}%)")
        print(f"   * Abstention (no goal >={args.min_hits})   : {totals['pos_abstain']:6d} / {pos_total} ({100.0 * totals['pos_abstain'] / pos_total:5.1f}%)")
        print()

    if neg_total > 0:
        print("2. Negative-Consensus Specificity (Volunteer-Rejected OSDG Label):")
        print(f"   * Cleanly Rejected (not in Top-{args.top_k}) : {totals['neg_cleanly_rejected']:6d} / {neg_total} ({100.0 * totals['neg_cleanly_rejected'] / neg_total:5.1f}%)")
        print(f"   * False Positive (in Top-{args.top_k})       : {totals['neg_triggered_rejected']:6d} / {neg_total} ({100.0 * totals['neg_triggered_rejected'] / neg_total:5.1f}%)")
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
