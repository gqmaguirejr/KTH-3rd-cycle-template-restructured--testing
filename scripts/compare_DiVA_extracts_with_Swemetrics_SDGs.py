#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-
"""
compare_DiVA_extracts_with_Swemetrics_SDGs.py

Runs the fast parallel dual-language SDG classifier on DiVA JSON extracts
(default: /tmp/combined_JSON_output.json) for publications present in the
Swemetrics SDG spreadsheet (default: /tmp/kth_sdg_duck.csv) and computes
statistical agreement metrics between the dual-language classifier and
Swemetrics SDG assignments (overall and stratified by Swemetrics 'source',
such as 'native' vs. model-derived assignments).
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
    O(N) multi-pattern trie scanner for Track 1 (acronyms), Track 2 (phrases),
    AND literal STRINGS terms, with lazy short-circuit evaluation for wildcard
    STRINGS clauses.
    """

    def __init__(self) -> None:
        clf.args = argparse.Namespace(verbose=False)

        # Build Track 1 Trie (case-sensitive acronyms -> list of (sdg_num, acr))
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

        # Track 3: Deduplicate STRINGS OR-clauses & index pure literals into a Trie
        unique_clause_patterns: dict[str, int] = {}
        self.clause_is_pure_literal: list[bool] = []
        self.clause_fallback_regex: list[re.Pattern[str] | None] = []
        self.strings_literal_trie: dict = {}
        self.strings_rule_ids: dict[int, list[tuple[str, tuple[int, ...]]]] = {}

        for sdg_num, rules in clf.COMPILED_STRINGS_RULES.items():
            rule_entries = []
            for r in rules:
                c_ids = []
                for cpat in r["clauses"]:
                    pat_key = cpat.pattern
                    cid = unique_clause_patterns.get(pat_key)
                    if cid is None:
                        cid = len(self.clause_is_pure_literal)
                        unique_clause_patterns[pat_key] = cid

                        # Try decomposing simple word-bounded literal alternations: \b(?:foo|bar)\b or \bfoo\b
                        m_group = re.fullmatch(r"\\b\(\?:([^()]+)\)\\b", pat_key)
                        raw_alts = m_group.group(1).split("|") if m_group else [pat_key]
                        literals = []
                        all_lit = True
                        for alt in raw_alts:
                            m_lit = re.fullmatch(r"(?:\\b)?([a-z0-9 \-]+)(?:\\b)?", alt.lower())
                            if m_lit:
                                literals.append(m_lit.group(1))
                            else:
                                all_lit = False
                                break

                        if all_lit and literals:
                            self.clause_is_pure_literal.append(True)
                            self.clause_fallback_regex.append(None)
                            for lit in literals:
                                node = self.strings_literal_trie
                                for ch in lit:
                                    node = node.setdefault(ch, {})
                                node.setdefault("$", []).append(cid)
                        else:
                            self.clause_is_pure_literal.append(False)
                            self.clause_fallback_regex.append(cpat)

                    c_ids.append(cid)
                # Order clauses so pure-literal (O(1) lookup) clauses are checked first!
                c_ids.sort(key=lambda idx: 0 if self.clause_is_pure_literal[idx] else 1)
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

    @staticmethod
    def _scan_strings_lit_trie(trie: dict, lower_text: str, matched_cids: set[int]) -> None:
        n = len(lower_text)
        for i in range(n):
            if i > 0 and _is_word_char(lower_text[i - 1]):
                continue
            node = trie
            j = i
            while j < n and lower_text[j] in node:
                node = node[lower_text[j]]
                j += 1
                if "$" in node and (j == n or not _is_word_char(lower_text[j])):
                    matched_cids.update(node["$"])

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

        # Track 3: Single-pass literal STRINGS Trie + lazy short-circuit wildcard regexes
        matched_cids: set[int] = set()
        if self.strings_literal_trie:
            self._scan_strings_lit_trie(self.strings_literal_trie, lower_text, matched_cids)

        regex_cache: dict[int, bool] = {}

        def _clause_ok(cid: int) -> bool:
            if self.clause_is_pure_literal[cid]:
                return cid in matched_cids
            res = regex_cache.get(cid)
            if res is None:
                cpat = self.clause_fallback_regex[cid]
                res = bool(cpat.search(lower_text)) if cpat is not None else False
                regex_cache[cid] = res
            return res

        for sdg_num, rule_entries in self.strings_rule_ids.items():
            hits = 0
            rule_matches: list[tuple[str, int]] = []
            for raw_rule, c_ids in rule_entries:
                if all(_clause_ok(cid) for cid in c_ids):
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


def detect_delimiter(csv_path: Path) -> str:
    """Detect whether the CSV/TSV file is tab- or comma-delimited."""
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
    first_line = sample.splitlines()[0] if sample else ""
    if "\t" in first_line and first_line.count("\t") >= first_line.count(","):
        return "\t"
    return ","


def extract_record_text(data: dict) -> str:
    """
    Extracts title, subtitles, keywords, and abstracts (in both 'eng' and 'swe')
    from a DiVA JSON record, matching sdg_classifier_dual_language.py.
    """
    corpus_parts: list[str] = []

    # 1. Primary Title & Subtitle
    title_obj = data.get("Title", {})
    if isinstance(title_obj, dict):
        corpus_parts.append(str(title_obj.get("Main title", "") or ""))
        corpus_parts.append(str(title_obj.get("Subtitle", "") or ""))
    elif isinstance(title_obj, str):
        corpus_parts.append(title_obj)

    # 2. Alternative Title & Subtitle
    alt_title_obj = data.get("Alternative title", {})
    if isinstance(alt_title_obj, dict):
        corpus_parts.append(str(alt_title_obj.get("Main title", "") or ""))
        corpus_parts.append(str(alt_title_obj.get("Subtitle", "") or ""))
    elif isinstance(alt_title_obj, str):
        corpus_parts.append(alt_title_obj)

    # 3. Keywords (both English and Swedish)
    keywords_obj = data.get("keywords", {})
    if isinstance(keywords_obj, dict):
        corpus_parts.append(str(keywords_obj.get("eng", "") or ""))
        corpus_parts.append(str(keywords_obj.get("swe", "") or ""))
    elif isinstance(keywords_obj, str):
        corpus_parts.append(keywords_obj)

    # 4. Abstracts (both English and Swedish, with top-level fallbacks)
    abstracts_obj = data.get("abstracts", {})
    if isinstance(abstracts_obj, dict):
        corpus_parts.append(str(abstracts_obj.get("eng", "") or ""))
        corpus_parts.append(str(abstracts_obj.get("swe", "") or ""))
    else:
        corpus_parts.append(str(data.get("eng", "") or ""))
        corpus_parts.append(str(data.get("swe", "") or ""))

    return ". ".join(p.strip() for p in corpus_parts if p and p.strip())


def parse_diva_numeric_id(diva_id: object) -> int | None:
    if not diva_id:
        return None
    s = str(diva_id).strip()
    if ":" in s:
        _, num_part = s.split(":", 1)
        if num_part.isdigit():
            return int(num_part)
    elif s.isdigit():
        return int(s)
    return None


def parse_sdg_set(raw_sdgs: object) -> set[int]:
    if not raw_sdgs:
        return set()
    found = re.findall(r"\b(1[0-7]|[1-9])\b", str(raw_sdgs))
    return {int(x) for x in found}


def load_swemetrics_records(csv_path: Path, verbose: bool = False) -> tuple[dict[int, dict], int, int]:
    """
    Loads Swemetrics SDG assignments keyed by integer record_id.
    Returns (records_by_id, valid_csv_rows, malformed_rows).
    """
    delimiter = detect_delimiter(csv_path)
    malformed_rows = 0
    valid_csv_rows = 0
    extracted: dict[int, dict] = {}

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        for row_idx, row in enumerate(reader, 1):
            if verbose:
                print(f"{row_idx=}, {row=}")
            try:
                record_id = int(str(row["record_id"]).strip())
                target_sdg = int(str(row["sdg_id"]).strip())
                raw_score = str(row.get("score", "") or "").strip()
                score = float(raw_score) if raw_score else 0.0
                source = str(row.get("source", "") or "").strip() or "unknown"
                classified_at = str(row.get("classified", "") or "").strip()
            except (KeyError, ValueError, TypeError):
                if verbose:
                    print(f"malformed row: {row=}")
                malformed_rows += 1
                continue

            if not (1 <= target_sdg <= 17):
                malformed_rows += 1
                continue

            entry = extracted.get(record_id)
            if entry is None:
                entry = {
                    "sdgs": set(),
                    "sources": set(),
                    "scores_by_sdg": {},
                    "classified_at": classified_at,
                }
                extracted[record_id] = entry

            entry["sdgs"].add(target_sdg)
            entry["sources"].add(source)
            entry["scores_by_sdg"][target_sdg] = score
            valid_csv_rows += 1

    # Derive canonical source label per record
    for entry in extracted.values():
        srcs = entry["sources"]
        if len(srcs) == 1:
            entry["source"] = next(iter(srcs))
        else:
            entry["source"] = "+".join(sorted(srcs))

    return extracted, valid_csv_rows, malformed_rows


def load_filtered_diva_records(
    json_path: Path, target_record_ids: set[int]
) -> tuple[dict[int, dict], int, int]:
    """
    Streams concatenated JSON objects or a JSON list from json_path, keeping only
    unique records whose numeric divaID is in target_record_ids.
    Returns (matched_records_by_id, total_unique_json_ids, duplicate_json_rows_skipped).
    """
    raw = json_path.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    idx, n = 0, len(raw)

    seen_ids: set[int] = set()
    duplicates = 0
    matched: dict[int, dict] = {}

    def _consume_obj(obj: object) -> None:
        nonlocal duplicates
        if not isinstance(obj, dict):
            return
        num_id = parse_diva_numeric_id(obj.get("divaID"))
        if num_id is None:
            return
        if num_id in seen_ids:
            duplicates += 1
            return
        seen_ids.add(num_id)
        if num_id in target_record_ids:
            matched[num_id] = obj

    while idx < n:
        while idx < n and raw[idx].isspace():
            idx += 1
        if idx >= n:
            break
        obj, idx = decoder.raw_decode(raw, idx)
        if isinstance(obj, list):
            for item in obj:
                _consume_obj(item)
        elif isinstance(obj, dict):
            _consume_obj(obj)

    return matched, len(seen_ids), duplicates


def _new_bucket() -> dict:
    return {
        "n_pubs": 0,
        "exact_set_match": 0,
        "any_overlap": 0,
        "all_swemetrics_recovered": 0,
        "top1_in_swemetrics": 0,
        "classifier_abstain": 0,
        "jaccard_sum": 0.0,
        "micro_tp": 0,
        "micro_fp": 0,
        "micro_fn": 0,
        "swemetrics_sdg_support": Counter(),
        "classifier_sdg_support": Counter(),
        "diva_sdg_support": Counter(),
        "sdg_tp": Counter(),
        "sdg_fp": Counter(),
        "sdg_fn": Counter(),
        "sdg_top1_tp": Counter(),
        "sdg_any_hit_on_swemetrics": Counter(),
        "sdg_zero_hit_on_swemetrics": Counter(),
    }


def _merge_bucket(dst: dict, src: dict) -> None:
    for k in (
        "n_pubs",
        "exact_set_match",
        "any_overlap",
        "all_swemetrics_recovered",
        "top1_in_swemetrics",
        "classifier_abstain",
        "micro_tp",
        "micro_fp",
        "micro_fn",
    ):
        dst[k] += src[k]
    dst["jaccard_sum"] += src["jaccard_sum"]
    for ck in (
        "swemetrics_sdg_support",
        "classifier_sdg_support",
        "diva_sdg_support",
        "sdg_tp",
        "sdg_fp",
        "sdg_fn",
        "sdg_top1_tp",
        "sdg_any_hit_on_swemetrics",
        "sdg_zero_hit_on_swemetrics",
    ):
        dst[ck].update(src[ck])


def _process_batch(
    batch: list[tuple[int, str, str, set[int], str, set[int], dict[int, float]]],
    top_k: int,
    min_hits: int,
    use_explicit_mentions: bool,
    collect_csv_rows: bool,
) -> tuple[dict, dict[str, dict], list[list]]:
    assert _WORKER_SCANNER is not None

    overall = _new_bucket()
    by_source: dict[str, dict] = defaultdict(_new_bucket)
    csv_rows: list[list] = []

    for record_id, diva_id, text, swe_sdgs, source, diva_sdgs, scores_by_sdg in batch:
        counts, matched_phrases, explicit_mentions = _WORKER_SCANNER.scan(text)
        ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)

        inferred_topk = [g for g, c in ranked if c >= min_hits][:top_k]
        if use_explicit_mentions and explicit_mentions:
            for g in sorted(explicit_mentions):
                if g not in inferred_topk:
                    inferred_topk.append(g)

        pred_set = set(inferred_topk)
        top1_sdg = ranked[0][0] if ranked[0][1] >= min_hits else None

        tp = len(swe_sdgs & pred_set)
        fp = len(pred_set - swe_sdgs)
        fn = len(swe_sdgs - pred_set)
        union_sz = len(swe_sdgs | pred_set)
        jacc = (tp / union_sz) if union_sz else 1.0

        for b in (overall, by_source[source]):
            b["n_pubs"] += 1
            b["micro_tp"] += tp
            b["micro_fp"] += fp
            b["micro_fn"] += fn
            b["jaccard_sum"] += jacc

            if pred_set == swe_sdgs:
                b["exact_set_match"] += 1
            if tp > 0:
                b["any_overlap"] += 1
            if swe_sdgs.issubset(pred_set):
                b["all_swemetrics_recovered"] += 1
            if top1_sdg is not None and top1_sdg in swe_sdgs:
                b["top1_in_swemetrics"] += 1
                b["sdg_top1_tp"][top1_sdg] += 1
            if not pred_set:
                b["classifier_abstain"] += 1

            for g in range(1, 18):
                in_swe = g in swe_sdgs
                in_pred = g in pred_set
                if in_swe:
                    b["swemetrics_sdg_support"][g] += 1
                    if counts[g] > 0:
                        b["sdg_any_hit_on_swemetrics"][g] += 1
                    else:
                        b["sdg_zero_hit_on_swemetrics"][g] += 1
                if in_pred:
                    b["classifier_sdg_support"][g] += 1
                if g in diva_sdgs:
                    b["diva_sdg_support"][g] += 1

                if in_swe and in_pred:
                    b["sdg_tp"][g] += 1
                elif in_pred and not in_swe:
                    b["sdg_fp"][g] += 1
                elif in_swe and not in_pred:
                    b["sdg_fn"][g] += 1

        if collect_csv_rows:
            match_vec = [counts[g] for g in range(1, 18)]
            focus_goals = sorted(swe_sdgs | pred_set | diva_sdgs)
            term_summaries = []
            for g in focus_goals:
                terms_str = ", ".join(f"{p}:{c}" for p, c in matched_phrases[g][:3])
                if terms_str:
                    term_summaries.append(f"SDG {g}[{terms_str}]")

            scores_str = "; ".join(
                f"{g}:{scores_by_sdg[g]:.3f}" for g in sorted(scores_by_sdg) if scores_by_sdg[g] > 0
            )
            csv_rows.append([
                record_id,
                diva_id,
                source,
                ",".join(str(g) for g in sorted(swe_sdgs)),
                scores_str,
                ",".join(str(g) for g in sorted(diva_sdgs)),
                top1_sdg or "",
                ",".join(str(g) for g in inferred_topk),
                ",".join(str(g) for g in sorted(swe_sdgs & pred_set)),
                ",".join(str(g) for g in sorted(pred_set - swe_sdgs)),
                ",".join(str(g) for g in sorted(swe_sdgs - pred_set)),
                f"{jacc:.4f}",
                json.dumps(match_vec),
                "; ".join(term_summaries),
            ])

    return overall, dict(by_source), csv_rows


def _format_bucket_metrics(b: dict) -> dict:
    n = b["n_pubs"]
    tp = b["micro_tp"]
    fp = b["micro_fp"]
    fn = b["micro_fn"]
    prec = (tp / (tp + fp)) if (tp + fp) else 0.0
    rec = (tp / (tp + fn)) if (tp + fn) else 0.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0

    macro_f1s = []
    for g in range(1, 18):
        sup = b["swemetrics_sdg_support"][g]
        if sup > 0:
            gtp = b["sdg_tp"][g]
            gfp = b["sdg_fp"][g]
            gfn = b["sdg_fn"][g]
            gp = (gtp / (gtp + gfp)) if (gtp + gfp) else 0.0
            gr = (gtp / (gtp + gfn)) if (gtp + gfn) else 0.0
            gf1 = (2 * gp * gr / (gp + gr)) if (gp + gr) else 0.0
            macro_f1s.append(gf1)

    return {
        "n_publications": n,
        "top1_in_swemetrics_rate": round(b["top1_in_swemetrics"] / n, 4) if n else 0.0,
        "any_overlap_rate": round(b["any_overlap"] / n, 4) if n else 0.0,
        "full_recall_rate": round(b["all_swemetrics_recovered"] / n, 4) if n else 0.0,
        "exact_set_match_rate": round(b["exact_set_match"] / n, 4) if n else 0.0,
        "classifier_abstention_rate": round(b["classifier_abstain"] / n, 4) if n else 0.0,
        "mean_jaccard_similarity": round(b["jaccard_sum"] / n, 4) if n else 0.0,
        "micro_precision": round(prec, 4),
        "micro_recall": round(rec, 4),
        "micro_f1": round(f1, 4),
        "macro_f1_active_sdgs": round(sum(macro_f1s) / len(macro_f1s), 4) if macro_f1s else 0.0,
    }


def _print_bucket_summary(title: str, b: dict) -> None:
    n = b["n_pubs"]
    if n == 0:
        return
    m = _format_bucket_metrics(b)
    print(f"{title} (N = {n}):")
    print(f"   * Top-1 Prediction in Swemetrics Set   : {b['top1_in_swemetrics']:6d} / {n} ({100.0 * m['top1_in_swemetrics_rate']:5.1f}%)")
    print(f"   * Any Overlap (>=1 SDG Shared)         : {b['any_overlap']:6d} / {n} ({100.0 * m['any_overlap_rate']:5.1f}%)")
    print(f"   * Full Recall (All Swemetrics Found)   : {b['all_swemetrics_recovered']:6d} / {n} ({100.0 * m['full_recall_rate']:5.1f}%)")
    print(f"   * Exact Set Match (Pred == Swemetrics) : {b['exact_set_match']:6d} / {n} ({100.0 * m['exact_set_match_rate']:5.1f}%)")
    print(f"   * Classifier Abstention (0 goals >= k) : {b['classifier_abstain']:6d} / {n} ({100.0 * m['classifier_abstention_rate']:5.1f}%)")
    print(f"   * Mean Jaccard Similarity              : {m['mean_jaccard_similarity']:.4f}")
    print(f"   * Micro Precision / Recall / F1        : {100.0 * m['micro_precision']:5.1f}% / {100.0 * m['micro_recall']:5.1f}% / {100.0 * m['micro_f1']:5.1f}%")
    print(f"   * Macro F1 (across active SDGs)        : {100.0 * m['macro_f1_active_sdgs']:5.1f}%")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run dual-language SDG classifier on DiVA JSON extracts and compare with Swemetrics SDGs."
    )
    parser.add_argument(
        "csv_file",
        nargs="?",
        type=Path,
        default=Path("/tmp/kth_sdg_duck.csv"),
        help="Path to Swemetrics CSV/TSV file (default: /tmp/kth_sdg_duck.csv)",
    )
    parser.add_argument(
        "json_file",
        nargs="?",
        type=Path,
        default=Path("/tmp/combined_JSON_output.json"),
        help="Path to DiVA JSON extracts (default: /tmp/combined_JSON_output.json)",
    )
    parser.add_argument("--top-k", type=int, default=3, help="Maximum candidate SDGs inferred per publication (default: 3)")
    parser.add_argument("--min-hits", type=int, default=2, help="Minimum keyword occurrences required to infer an SDG (default: 2)")
    parser.add_argument(
        "--include-explicit",
        action="store_true",
        default=True,
        help="Include explicit in-text SDG declarations alongside keyword predictions (default: True)",
    )
    parser.add_argument(
        "--keywords-only",
        action="store_true",
        help="Ignore explicit in-text SDG mentions and evaluate pure keyword/rule inference",
    )
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    parser.add_argument("--output-csv", type=Path, default=None, help="Optional per-publication comparison CSV path")
    parser.add_argument("--json", action="store_true", help="Output summary statistics as JSON")
    parser.add_argument("--verbose", action="store_true", help="Print verbose loading diagnostics")
    args = parser.parse_args()

    use_explicit = not args.keywords_only

    if not args.csv_file.is_file():
        sys.exit(f"Error: Swemetrics file '{args.csv_file}' not found.")
    if not args.json_file.is_file():
        sys.exit(f"Error: DiVA JSON file '{args.json_file}' not found.")

    t0 = time.perf_counter()

    # 1. Load Swemetrics SDG data
    swe_records, valid_csv_rows, malformed_rows = load_swemetrics_records(
        args.csv_file, verbose=args.verbose
    )
    target_ids = set(swe_records.keys())

    # 2. Load only matching DiVA JSON records
    diva_matched, total_unique_json, dup_json = load_filtered_diva_records(
        args.json_file, target_ids
    )
    missing_in_json = len(target_ids - set(diva_matched.keys()))

    # Build work items for publications present in both Swemetrics and DiVA JSON
    work_items: list[tuple[int, str, str, set[int], str, set[int], dict[int, float]]] = []
    empty_text_count = 0
    for rec_id in sorted(diva_matched.keys()):
        jrec = diva_matched[rec_id]
        srec = swe_records[rec_id]
        text = extract_record_text(jrec)
        if not text:
            empty_text_count += 1
        diva_id_str = str(jrec.get("divaID", f"diva2:{rec_id}"))
        diva_sdgs = parse_sdg_set(jrec.get("SDGs"))
        work_items.append((
            rec_id,
            diva_id_str,
            text,
            srec["sdgs"],
            srec["source"],
            diva_sdgs,
            srec["scores_by_sdg"],
        ))

    batch_size = 250
    batches = [
        (work_items[i : i + batch_size], args.top_k, args.min_hits, use_explicit, args.output_csv is not None)
        for i in range(0, len(work_items), batch_size)
    ]

    overall_bucket = _new_bucket()
    source_buckets: dict[str, dict] = defaultdict(_new_bucket)

    csv_writer = None
    out_fh = None
    if args.output_csv:
        out_fh = open(args.output_csv, "w", encoding="utf-8", newline="")
        csv_writer = csv.writer(out_fh)
        csv_writer.writerow([
            "record_id",
            "divaID",
            "swemetrics_source",
            "swemetrics_sdgs",
            "swemetrics_scores",
            "diva_metadata_sdgs",
            "classifier_top1",
            "classifier_topk",
            "true_positives",
            "false_positives",
            "false_negatives",
            "jaccard",
            "match_vector",
            "matched_terms_summary",
        ])

    if batches:
        with mp.Pool(processes=min(args.workers, len(batches)), initializer=_init_worker) as pool:
            for b_overall, b_sources, csv_rows in pool.starmap(_process_batch, batches, chunksize=1):
                _merge_bucket(overall_bucket, b_overall)
                for src_name, src_b in b_sources.items():
                    _merge_bucket(source_buckets[src_name], src_b)
                if csv_writer and csv_rows:
                    csv_writer.writerows(csv_rows)

    if out_fh:
        out_fh.close()

    elapsed = time.perf_counter() - t0

    # Per-SDG table across all matched publications
    per_sdg_rows = []
    for g in range(1, 18):
        sup = overall_bucket["swemetrics_sdg_support"][g]
        pred_sup = overall_bucket["classifier_sdg_support"][g]
        diva_sup = overall_bucket["diva_sdg_support"][g]
        tp = overall_bucket["sdg_tp"][g]
        fp = overall_bucket["sdg_fp"][g]
        fn = overall_bucket["sdg_fn"][g]
        rec = (tp / sup) if sup else 0.0
        prec = (tp / (tp + fp)) if (tp + fp) else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        per_sdg_rows.append({
            "sdg": g,
            "swemetrics_n": sup,
            "classifier_pred_n": pred_sup,
            "diva_assigned_n": diva_sup,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "top1_tp": overall_bucket["sdg_top1_tp"][g],
            "recall": round(rec, 4),
            "precision": round(prec, 4),
            "f1": round(f1, 4),
            "any_hit_ge1_on_swemetrics": overall_bucket["sdg_any_hit_on_swemetrics"][g],
            "zero_hit_on_swemetrics": overall_bucket["sdg_zero_hit_on_swemetrics"][g],
        })

    summary = {
        "swemetrics_csv": str(args.csv_file),
        "diva_json": str(args.json_file),
        "elapsed_seconds": round(elapsed, 2),
        "workers": args.workers,
        "parameters": {
            "top_k": args.top_k,
            "min_hits": args.min_hits,
            "include_explicit_mentions": use_explicit,
        },
        "dataset_counts": {
            "swemetrics_valid_rows": valid_csv_rows,
            "swemetrics_malformed_rows": malformed_rows,
            "swemetrics_unique_publications": len(target_ids),
            "diva_total_unique_publications": total_unique_json,
            "diva_duplicate_entries_skipped": dup_json,
            "matched_publications_evaluated": len(work_items),
            "swemetrics_ids_missing_in_json": missing_in_json,
            "matched_with_empty_text": empty_text_count,
        },
        "overall_metrics": _format_bucket_metrics(overall_bucket),
        "by_swemetrics_source": {
            src: _format_bucket_metrics(sb)
            for src, sb in sorted(source_buckets.items(), key=lambda x: -x[1]["n_pubs"])
        },
        "per_sdg": per_sdg_rows,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return

    print("=" * 92)
    print("==> Dual-Language Classifier (DiVA Extracts) vs. Swemetrics SDGs")
    print(f"Swemetrics CSV   : {args.csv_file}")
    print(f"DiVA JSON        : {args.json_file}")
    print(f"Parameters       : top_k={args.top_k}, min_hits={args.min_hits}, include_explicit={use_explicit}, workers={args.workers}")
    print(f"Elapsed Time     : {elapsed:.2f}s")
    print("-" * 92)
    print(f"Swemetrics CSV Rows (valid / malformed)        : {valid_csv_rows} / {malformed_rows}")
    print(f"Swemetrics Unique Publications                 : {len(target_ids)}")
    print(f"DiVA JSON Unique Publications (duplicates)     : {total_unique_json} ({dup_json})")
    print(f"Matched Publications Evaluated                 : {len(work_items)}")
    print(f"Swemetrics Publications Missing from JSON      : {missing_in_json}")
    if empty_text_count:
        print(f"Matched Publications with Empty Text           : {empty_text_count}")
    print("-" * 92)

    _print_bucket_summary("1. Overall Multi-Label Agreement (All Matched Publications)", overall_bucket)

    if len(source_buckets) > 1:
        print("2. Breakdown by Swemetrics 'source' Field:")
        print("-" * 92)
        for src_name, sb in sorted(source_buckets.items(), key=lambda x: -x[1]["n_pubs"]):
            _print_bucket_summary(f"   Source = '{src_name}'", sb)

    print("3. Per-SDG Breakdown (Dual-Language Classifier vs. Swemetrics):")
    print(f"{'SDG':>4} | {'Swe N':>6} | {'Pred N':>6} | {'DiVA N':>6} | {'TP':>5} | {'FP':>5} | {'FN':>5} | {'Prec':>6} | {'Rec':>6} | {'F1':>6} | {'0-Hit':>5}")
    print("-" * 92)
    for r in per_sdg_rows:
        print(
            f"{r['sdg']:>4d} | "
            f"{r['swemetrics_n']:>6d} | "
            f"{r['classifier_pred_n']:>6d} | "
            f"{r['diva_assigned_n']:>6d} | "
            f"{r['tp']:>5d} | "
            f"{r['fp']:>5d} | "
            f"{r['fn']:>5d} | "
            f"{100.0 * r['precision']:>5.1f}% | "
            f"{100.0 * r['recall']:>5.1f}% | "
            f"{100.0 * r['f1']:>5.1f}% | "
            f"{r['zero_hit_on_swemetrics']:>5d}"
        )
    print("-" * 92)
    if args.output_csv:
        print(f"Detailed per-publication CSV written to: {args.output_csv}")
    print("=" * 92)


if __name__ == "__main__":
    main()
