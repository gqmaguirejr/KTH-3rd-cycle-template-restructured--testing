#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
sdg_classifier_dual_language.py

Audits and classifies research text against the UN SDGs using Elsevier V4,
UN DESA keyword sets, and dual-language auxiliary engineering terms (English and Swedish).
Also employs terms extracted by generate_swedish_sdg_dictionary.py

Supports:
  - Dual-language JSON ingestion (extracting primary & alternative titles,
    subtitles, keywords, and abstracts for 'eng' and 'swe').
  - Standalone PDF or text file ingestion.
  - In-text explicit SDG mention parsing.
  - In-place updating of JSON files with verified SDGs.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

import Elsevier_Data_Repository_V4_SDG_terms as sdg_dict

try:
    from uoa_additional_terms import UOA_ADDITIONAL_SDG_TERMS
except ImportError:
    UOA_ADDITIONAL_SDG_TERMS = {}

# Generic meta-terms that falsely inflate SDG scores across unrelated abstracts
GENERIC_SDG_META_TERMS = {
    "sdg", "sdgs", "sustainable development", "sustainable development goals",
    "sustainable development goals (sdgs)", "development goals", "agenda 2030",
    "the concept of sustainable development", "concept of sustainable development",
    "a sustainable development",
    # Swedish Terms
    "hållbar utveckling", "globala målen", "agenda2030"
}

# Dual-Language Auxiliary Terms (English & Swedish)
AUXILIARY_SDG_TERMS = {
    1: [
        # Energy & Household Poverty
        "energy poverty", "household poverty", "energy coaching",

        # Swedish Terms
        "energifattigdom", "hushållsfattigdom", "energicoachning",
        "fattigdom", "globalt mål 1 - ingen fattigdom", # "hållbar utveckling",
        "avskaffa fattigdom", "extrem fattigdom", "multidimensionell fattigdom",
        "relativ fattigdom", "sociala trygghetssystem", "grundläggande service",
        "ekonomiska resurser", "mikrofinansiering", "social trygghet", "fattigdomsminskning",
        "utsatta grupper", "ekonomisk utsatthet"
    ],
    2: [
        # Food systems & bio-based coatings / agriculture
        "food systems governance", "local food systems", "food supply systems",
        "food supply chain", "food security", "chitosan", "antimicrobial coatings",
        # Swedish Terms
        "livsmedelssystem", "matsystem", "livsmedelsförsörjning", "livsmedelsstyrning",
        "matsystemsstyrning", "livsmedelskedja",
        "avskaffa hunger", "tryggad livsmedelsförsörjning", "god näringsstatus",
        "hållbart jordbruk", "jordbrukets produktivitet", "småskaliga livsmedelsproducenter",
        "motståndskraftiga jordbruksmetoder", "genetisk mångfald", "livsmedelsmarknader",
        "livsmedelsstrategin", "matsvinn", "underernäring", "lokalproducerad mat"
    ],
    3: [
        # Clinical & Cardiovascular
        "cardiac arrest", "cardiac", "electrocardiogram", "ecg", "ecgi",
        "arrhythmia", "healthcare", "health care", "clinical adoption",
        "clinical", "clinicians", "medical imaging", "biomedical",
        "cardiovascular", "heart disease", "patient monitoring",
        "hospitalized patients", "vital signs", "e-health", "mhealth",
        # Biomechanics, Assistive Technology & Rehabilitation
        "prosthesis", "prostheses", "prosthetic", "prosthetic foot",
        "prosthetics", "prosthetic users", "transtibial", "transfemoral",
        "amputee", "amputees", "amputation", "orthosis", "orthoses",
        "orthotics", "biomechanics", "biomechanical", "gait",
        "gait analysis", "motion capture", "rehabilitation",
        "assistive technology", "assistive devices", "force plate",
        "force plates", "heel strike",
        # Targeted Drug Delivery, Neuromuscular & Vision
        "myopia", "microneedle", "microneedles", "drug delivery",
        "electromyography", "motor unit", "motor units", "radiation therapy",
        "radiotherapy", "spinal cord injury", "corticomuscular coherence",
        # Swedish Terms
        "hälso- och sjukvård", "medicinsk teknik", "läkemedelstillförsel",
        "mikronålar", "hjärtstopp", "ryggmärgsskada", "gånganalys",
        "rehabilitering", "elektromyografi", "strålbehandling",
        "globalt mål 3 - hälsa och välbefinnande", "hälsovård",
        # "hållbar utveckling",
        "sjukvård",
        "hälsa och välbefinnande", "folkhälsa", "mödradödlighet",
        "barnadödlighet", "smittsamma sjukdomar", "icke-smittsamma sjukdomar",
        "psykisk hälsa", "missbruk", "trafikolyckor", "sexuell och reproduktiv hälsa",
        "allmän hälso- och sjukvård", "kemikalieexponering", "luftföroreningar och hälsa",
        "medicinteknik", "hjälpmedelsteknik"
    ],
    4: [
        # Pedagogy & Educational Research
        "challenge-based learning", "teacher education", "teacher students",
        "digital competence", "pedagogical practice", "sloyd", "global competence",
        "education fiction", "digital response systems",
        # Swedish Terms
        "lärarutbildning", "lärarstudenter", "digital kompetens", "pedagogisk praktik",
        "slöjd", "utbildningsfiktion", "utmaningsbaserat lärande", "bedömning i skolan",
        "globalt mål 4 - god utbildning för alla", # "hållbar utveckling",
        "utbildning",
        "god utbildning", "likvärdig utbildning", "inkluderande utbildning",
        "förskoleverksamhet", "grundskola", "gymnasieskola", "högre utbildning",
        "yrkesutbildning", "livslångt lärande", "läs- och skrivkunnighet",
        "didaktik"
    ],
    5: [
        # Diversity & Governance
        "corporate governance", "homosocial", "homosociality", "gender diversity",
        "gender equality", "female executives", "daughter effect",
        # Swedish Terms
        "bolagsstyrning", "könsmässig mångfald",
        "jämställdhetsfond", "kvinnlig representation",
        "jämställdhet", "diskriminering av kvinnor", "våld mot kvinnor",
        "obetalt hem- och omsorgsarbete", "fullt deltagande i ledarskap",
        "beslutsfattande", "reproduktiv hälsa", "lika rätt till ekonomiska resurser",
        "homosocialitet",
        "jämställdhetsintegrering", "styrelsesammansättning"
    ],
    6: [
        # Water & Wastewater
        "groundwater intelligence", "greywater reuse", "rainwater harvesting",
        "water distribution networks", "wastewater treatment",
        # Swedish Terms
        "grundvatten", "gråvatten", "dagvattenhantering", "avloppsrening",
        "vattenförsörjning", "diväteoxid", "globalt mål 6 - rent vatten och sanitet",
        "rent vatten och sanitet", "sanitet", "vatten",
        "dricksvatten", "sanitet och hygien", "vattenkvalitet",
        "vattenförorening", "avloppsvatten", "vatteneffektivitet","integrerad vattenresursförvaltning",
        "vattenskydd", "avsaltning"
    ],
    7: [
        # Clean Energy, Grid & Heat Recovery
        "district heating", "excess heat", "heat recovery", "pvt",
        "concentrating solar power", "smart meter", "smart meters",
        "demand flexibility", "energy transition", "lead-cooled reactor",
        "nuclear fuel", "electric power engineering", "supercritical co2",
        # Swedish Terms
        "fjärrvärme", "spillvärme", "värmeåtervinning", "smarta mätare",
        "efterfrågeflexibilitet", "energiomställning", "blykyld reaktor",
        "kärnbränsle", "elkraftteknik",
        "hållbar energi", "förnybar energi", "energieffektivisering",
        "ren energiteknik", "fossilfria bränslen", "smarta elnät",
        "solceller", "vindkraft", "värmepumpar",
    ],
    8: [
        # Workplace Safety & Manufacturing
        "safety culture", "safety practices", "construction sector",
        "collaborative product development", "assembly features",
        # Swedish Terms
        "säkerhetskultur", "säkerhetspraktiker", "byggsektorn",
        "arbetsplatskultur", "monteringsegenskaper", "tillverkningsteknik",
        "anständiga arbetsvillkor", "ekonomisk tillväxt", "resurseffektivitet i konsumtion och produktion",
        "full sysselsättning", "ungdomsarbetslöshet", "tvångsarbete",
        "trygg arbetsmiljö", "arbetstagares rättigheter",
        "arbetsplatsolyckor", "produktionssystem", "monteringsprocesser", "arbetsmiljö"
    ],
    9: [
        # AI / Software / Digital Infrastructure
        "artificial intelligence", "machine learning", "deep learning",
        "federated learning", "neural network", "neural networks",
        "distributed computing", "edge computing", "edge devices",
        "internet of things", "sensor network", "telecommunications",
        "cloud computing", "data infrastructure", "anomaly detection",
        # Electrical & Hardware Engineering
        "power converter", "power converters", "traction inverter",
        "inverter", "inverters", "power electronics", "gate driver",
        "gate drivers", "silicon carbide", "mosfet", "mosfets",
        "semiconductor", "semiconductors", "soft switching",
        "printed circuit board", "pcb", "hardware prototype",
        "circuit design", "finite element method",
        # Infrastructure, Transport, Robotics & Materials
        "rail freight", "rail infrastructure", "track access charges",
        "cell-free massive mimo", "reconfigurable intelligent surface",
        "articulated robot", "unbound granular materials", "asphalt binder",
        "additive manufacturing", "metasurface", "metasurfaces",
        "rail infrastructure",
        # Swedish Terms
        "artificiell intelligens", "maskininlärning", "djupinlärning",
        "sakernas internet", "telekommunikation", "molninfrastruktur",
        "kraftelektronik", "halvledare", "godståg", "järnvägsinfrastruktur",
        "banavgifter", "cellfri massiv mimo", "metaytor", "metayta",
        "additiv tillverkning", "asfaltbindemedel",
        "hållbar industri", "innovationer och infrastruktur", "motståndskraftig infrastruktur",
        "inkluderande industrialisering", "småföretagens tillgång till finansiering",
        "uppgradera industriell infrastruktur", "ren teknik och miljöanpassade processer",
        "vetenskaplig forskning och teknisk kapacitet", "informations- och kommunikationsteknik",
        "tillgång till internet", "cyberfysiska system", "järnvägsinfrastruktur", "tågtrafik", "persontåg", "snabbtåg"
    ],
    10: [
        # Inclusion & Equity
        "assistive technology", "prosthetic users", "physical rehabilitation",
        "mobility inequality", "gender disparities in leadership",
        # Swedish Terms
        "hjälpmedelsteknik", "fysisk rehabilitering", "tillgänglighet",
        "minskad ojämlikhet", "inkomsttillväxt för de fattigaste", "social och ekonomisk inkludering",
        "lika möjligheter", "diskriminerande lagstiftning", "finansmarknadsreglering",
        "migration och rörlighet", "säker migration", "tillgänglighet för personer med funktionsnedsättning",
        "funktionsvariation"
    ],
    11: [
        # Urban Infrastructure, Mobility, Acoustics & Housing
        "urban mobility", "public transport", "on-demand public transport",
        "cycling", "green space", "urban green", "shared housing",
        "traffic noise", "environmental acoustics", "climate-neutral cities",
        "wheelchair accessibility", "smart cities", "ground-borne vibration",
        # Swedish Terms
        "urban mobilitet", "kollektivtrafik", "delningsbostäder", "delat boende",
        "grönområden", "stadsplanering", "trafikbuller", "miljöakustik",
        "klimatneutrala städer", "smarta städer", "markvibrationer",
        "hållbara städer och samhällen", "trygga bostäder", "hållbara transportsystem",
        "inkluderande stadsplanering", "kultur- och naturarv", "katastrofriskreducering",
        "miljöpåverkan från städer", "luftkvalitet i städer", "kommunal avfallshantering",
        "gröna områden och allmänna platser", "stads- och regionplanering",
        "bebyggelse", "bosättning", "globalt mål 11 - hållbara städer och samhällen", "stad"

    ],
    12: [
        # Circular Economy & Materials
        "circular economy", "circular supply chain", "circular manufacturing",
        "biowaste valorization", "hard carbon", "sodium-ion battery",
        "catalytic graphitization", "bio-based carbon", "remanufacturing",
        # Swedish Terms
        "cirkulär ekonomi", "cirkulära leveranskedjor", "cirkulär tillverkning",
        "biokol", "natriumjonbatterier", "återvinning", "återanvändning",
        "biografit", "lignin",
        "hållbar konsumtion och produktion", "hållbar förvaltning av naturresurser", "minska matsvinnet",
        "kemikaliehantering", "avfallsminskning", "materialåtervinning",
        "hållbarhetstänkande i företag",
        "hållbarhetsredovisning", "hållbar offentlig upphandling",
        "biomassa"
    ],
    13: [
        # Climate Mitigation & Clean Processing
        "carbon dioxide removal", "carbon removal", "beccs", "sustainable aviation fuel",
        "climate criteria", "fossil-free steelmaking", "h-dri", "climate policy",
        # Swedish Terms
        "koldioxidinfångning", "fossilfritt stål", "klimatåtgärder",
        "klimatdeklaration", "växthusgasutsläpp",
        "globalt mål 13 - bekämpa klimatförändringen", "klimatförändring",
        "bekämpa klimatförändringarna", "klimatanpassning",
        "motståndskraft mot klimatrelaterade faror", "klimatpolitik",
        "klimatstrategier", "kunskap och kapacitet om klimatförändringar",
        "koldioxidutsläpp", "negativa utsläpp"
    ],
    14: [
        # Swedish Terms
        "biodiversitet", "biologisk mångfald", "biologiska mångfalden", "ekosystem", "globalt mål 14 - hav och marina resurser",
        "hav och marina resurser", "marina föroreningar", "marint skräp",
        "eutrofiering", "marina och kustnära ekosystem", "havsförsurning",
        "hållbart fiske", "överfiske", "kust- och havsområden",
        "havsrättskonventionen", "blå ekonomi", "undervattensakustik",
        ],
    15: [
        # Swedish Terms
        "biodiversitet", "biologisk mångfald", "biologiska mångfalden", "ekosystem", "globalt mål 15 - ekosystem och biologisk mångfald",
        "ekosystem och biologisk mångfald", "landbaserade ekosystem", "sötvattensekosystem",
        "hållbart skogsbruk", "avskogning", "återplantering av skog", "markförstöring",
        "skyddade arter", "tjuvjakt", "invasiva främmande arter", "skogsbruksmetoder", "våtmarker"
        ],

    16: [
        # Governance, Transparency & Media Monitoring
        "open science", "transparent policymaking", "media monitoring",
        "intersectional invisibility", "open data governance",
        # Swedish Terms
        "öppen vetenskap", "transparens i styrning", "mediebevakning",
        "fred", "globalt mål 16 - fredliga och inkluderande samhällen", "institution", "rättvisa",
        "fredliga och inkluderande samhällen", "minska våld", "övergrepp och våld mot barn",
        "rättssäkerhet", "lika tillgång till rättvisa", "olagliga finansiella flöden",
        "korruption och mutor", "effektiva och transparenta institutioner", "ansvarsskyldighet",
        "representativt beslutsfattande", "allmän tillgång till information"
    ],
    17: [
        # Global Partnerships & Science Diplomacy
        "academic internationalisation", "science diplomacy",
        "transnational education", "development-focused scholarships",
        # Swedish Terms
        "akademisk internationalisering", "forskningsdiplomati",
        "globalt mål 17 - genomförande och globalt partnerskap", "koordination", "samarbete",
        "genomförande och globalt partnerskap", "inhemsk resursmobilisering", "utvecklingsbistånd",
        "skuldlättnad", "tekniköverföring", "kapacitetsuppbyggnad", "multilaterala handelssystemet",
        "makroekonomisk stabilitet", "samstämmighet i politiken", "globala partnerskap",
        "tillförlitliga data och statistik",
    ]
}

# ---------------------------------------------------------------------------
# Load and Pre-compile STRINGS Co-occurrence Rules
# ---------------------------------------------------------------------------
# The STRINGS project (Science, Technology, Research and Innovations for the Global Goals) is a global research initiative led by the Science Policy Research Unit (SPRU) at the University of Sussex, UCL STEaPP, and the UNDP to map and align science and innovation with the UN Sustainable Development Goals (SDGs).
# Confraria, Ciarli, Colonna, and Noyons, “The STRINGS queries to identify documents related to the SDGs (+ country-SDG data)”. Zenodo, Jan. 08, 2024. doi: 10.5281/zenodo.10470841.
# Creative Commons Attribution 4.0 International
# https://zenodo.org/records/10470841/files/STRINGS_SDG_queries_FINAL.xlsx?download=1
# The spreadsheet is compiled with compile_strings_queries.py
STRINGS_RULES_FILE = Path("STRINGS_rules.json")
COMPILED_STRINGS_RULES: dict[int, list[dict]] = {}

if STRINGS_RULES_FILE.is_file():
    with open(STRINGS_RULES_FILE, "r", encoding="utf-8") as f:
        _raw_rules = json.load(f)
        for _g_str, _rule_list in _raw_rules.items():
            _g = int(_g_str)
            COMPILED_STRINGS_RULES[_g] = []
            for _r in _rule_list:
                # Combine OR terms in each clause into a single regex: (?:pat1|pat2|...)
                compiled_clauses = [
                    re.compile("|".join(clause), flags=re.IGNORECASE)
                    for clause in _r["clauses"]
                    if clause
                ]
                if compiled_clauses:
                    COMPILED_STRINGS_RULES[_g].append({
                        "raw": _r["raw"],
                        "clauses": compiled_clauses
                    })

def clean_text_artifacts(text: str) -> str:
    """Removes HTML and TeX artifacts and normalizes whitespace."""
    if not text:
        return ""
    # Strip HTML tags
    t = re.sub(r"<[^>]+>", " ", text)
    # Strip TeX comments
    t = re.sub(r"(?<!\\)%.*?\n", "\n", t)
    t = t.replace(r"\%", "%")
    # Strip TeX macros
    t = re.sub(r"\\[a-zA-Z]+(\[[^\]]*\])?(\{([^}]*)\})?", r" \3 ", t)
    t = re.sub(r"[{}]", " ", t)
    t = t.replace("---", " ").replace("--", " ")
    return " ".join(t.split()).lower()


def extract_explicit_sdg_mentions(text: str) -> set[int]:
    """Captures explicitly cited SDGs (e.g., 'SDG 3, 9, 11', 'SDGs 8 and 12', 'Mål 9')."""
    explicit_goals = set()
    tokens = text.split()
    i = 0
    while i < len(tokens):
        raw_tok = tokens[i].strip("()[]{}:;.\"").lower()
        if raw_tok in ("sdg", "sdgs", "goal", "goals", "mål", "delmål"):
            j = i + 1
            while j < len(tokens):
                t = tokens[j].strip("()[]{};.\"").lower()
                if t in ("and", "&", "or", ",", "och"):
                    j += 1
                    continue
                found_digits = re.findall(r"\b\d+\b", t)
                if found_digits:
                    for d in found_digits:
                        val = int(d)
                        if 1 <= val <= 17:
                            explicit_goals.add(val)
                    j += 1
                else:
                    break
            i = j
        else:
            i += 1
    return explicit_goals


def build_dual_language_term_index() -> dict[int, list[str]]:
    """Builds the comprehensive SDG keyword index across English, Swedish, and extensions."""
    mapping = {}
    for i in range(1, 17):
        var_name = f"sdg{i}_terms"
        raw_terms = list(getattr(sdg_dict, var_name, []))

        # 1. Merge dual-language auxiliary terms
        if i in AUXILIARY_SDG_TERMS:
            raw_terms.extend(AUXILIARY_SDG_TERMS[i])

        # 2. Merge Auckland extensions if available
        if i in UOA_ADDITIONAL_SDG_TERMS:
            raw_terms.extend(UOA_ADDITIONAL_SDG_TERMS[i])

        cleaned_terms = set()
        for t in raw_terms:
            t_clean = t.strip().lower()
            t_clean = re.sub(r"^(and|the|och|att)\s+", "", t_clean).strip()
            if not t_clean or t_clean in GENERIC_SDG_META_TERMS:
                continue
            cleaned_terms.add(t_clean)

        mapping[i] = sorted(list(cleaned_terms), key=len, reverse=True)
    return mapping


def extract_from_dual_language_json(json_path: Path) -> tuple[str, dict]:
    """
    Extracts all textual content across primary and alternative fields
    (Title, Subtitle, Alternative Title, Keywords, and Abstracts in 'eng' and 'swe').
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    corpus_parts = []

    # 1. Primary Title
    title_obj = data.get("Title", {})
    if isinstance(title_obj, dict):
        corpus_parts.append(title_obj.get("Main title", ""))
        corpus_parts.append(title_obj.get("Subtitle", ""))
    elif isinstance(title_obj, str):
        corpus_parts.append(title_obj)

    # 2. Alternative Title
    alt_title_obj = data.get("Alternative title", {})
    if isinstance(alt_title_obj, dict):
        corpus_parts.append(alt_title_obj.get("Main title", ""))
        corpus_parts.append(alt_title_obj.get("Subtitle", ""))

    # 3. Keywords (both English and Swedish)
    keywords_obj = data.get("keywords", {})
    if isinstance(keywords_obj, dict):
        corpus_parts.append(keywords_obj.get("eng", ""))
        corpus_parts.append(keywords_obj.get("swe", ""))
    elif isinstance(keywords_obj, str):
        corpus_parts.append(keywords_obj)

    # 4. Abstracts (both English and Swedish)
    abstracts_obj = data.get("abstracts", {})
    if isinstance(abstracts_obj, dict):
        corpus_parts.append(abstracts_obj.get("eng", ""))
        corpus_parts.append(abstracts_obj.get("swe", ""))

    # Top-level language string fallbacks
    corpus_parts.append(data.get("eng", ""))
    corpus_parts.append(data.get("swe", ""))

    raw_corpus = ". ".join(p for p in corpus_parts if p)
    cleaned_corpus = clean_text_artifacts(raw_corpus)
    return cleaned_corpus, data


def extract_from_pdf(pdf_path: Path) -> str:
    """Extracts text from PDF while removing references."""
    if fitz is None:
        raise RuntimeError(
            "PyMuPDF ('fitz') is required for PDF parsing. Install with: pip install pymupdf"
        )
    doc = fitz.open(str(pdf_path))
    pages = [doc[i].get_text("text") for i in range(len(doc))]
    full_text = "\n".join(pages)
    full_text = re.sub(r"(\w+)-\n(\w+)", r"\1\2", full_text)

    bib_marker = re.search(
        r"\n(References|Bibliography|Litteraturförteckning|Referenser)\s*\n",
        full_text,
        flags=re.IGNORECASE,
    )
    if bib_marker:
        full_text = full_text[: bib_marker.start()]

    return clean_text_artifacts(full_text)


def evaluate_strings_rules(text: str) -> tuple[dict[int, int], dict[int, list[tuple[str, int]]]]:
    """Evaluates multi-term co-occurrence rules from STRINGS_SDG_queries_FINAL.xlsx.
    
    A rule matches if ALL its clauses (AND blocks) find at least one match (OR blocks).
    """
    rule_counts = {i: 0 for i in range(1, 18)}
    matched_rules = {i: [] for i in range(1, 18)}

    if not COMPILED_STRINGS_RULES:
        return rule_counts, matched_rules

    for sdg_num, rules in COMPILED_STRINGS_RULES.items():
        hits = 0
        rule_matches = []
        for r in rules:
            # Check if all AND clauses are satisfied in the text
            if all(clause_pat.search(text) for clause_pat in r["clauses"]):
                hits += 1
                rule_matches.append((r["raw"], 1))

        if hits > 0:
            rule_counts[sdg_num] = hits
            matched_rules[sdg_num] = rule_matches

    return rule_counts, matched_rules

def scan_text_for_sdgs(
    text: str,
) -> tuple[dict[int, int], dict[int, list[tuple[str, int]]], set[int]]:
    """Calculates keyword match frequencies, STRINGS co-occurrence expressions,
    and explicit author declarations.
    """
    sdg_terms = build_dual_language_term_index()
    counts: dict[int, int] = {i: 0 for i in range(1, 18)}
    matched_phrases: dict[int, list[tuple[str, int]]] = {i: [] for i in range(1, 18)}

    padded_text = f" {text} "
    explicit_mentions = extract_explicit_sdg_mentions(text)

    # 1. Standard vocabulary matching (Elsevier, UoA, and Swedish auxiliaries)
    for sdg_num, terms in sdg_terms.items():
        counter = Counter()
        for phrase in terms:
            if len(phrase) < 3:
                continue

            escaped = re.escape(phrase)
            matches = len(re.findall(rf"(?<!\w){escaped}(?!\w)", padded_text, flags=re.IGNORECASE))
            if matches > 0:
                counter[phrase] += matches

        counts[sdg_num] = sum(counter.values())
        matched_phrases[sdg_num] = counter.most_common(10)

    # 2. Evaluate STRINGS co-occurrence rules (AND/OR queries)
    strings_counts, strings_matched = evaluate_strings_rules(padded_text)
    for g in range(1, 18):
        counts[g] += strings_counts[g]
        matched_phrases[g].extend(strings_matched[g])

    # 3. SDG 17 Pattern Matching
    sdg17_counter = Counter()
    for pattern in getattr(sdg_dict, "SDG17_PATTERNS", []):
        hits = re.findall(pattern, padded_text, flags=re.IGNORECASE)
        if hits:
            clean_name = pattern.replace(r"\b", "").replace(r".*?", " ")
            sdg17_counter[clean_name] += len(hits)

    counts[17] += sum(sdg17_counter.values())
    matched_phrases[17].extend(sdg17_counter.most_common(10))

    return counts, matched_phrases, explicit_mentions


def main():
    parser = argparse.ArgumentParser(
        description="Dual-Language SDG Classifier (English & Swedish)."
    )
    parser.add_argument(
        "input_file",
        type=Path,
        help="Path to JSON file, PDF, or text file",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Maximum candidate SDGs to infer from keywords (default: 3)",
    )
    parser.add_argument(
        "--min-hits",
        type=int,
        default=2,
        help="Minimum keyword occurrences required to consider an SDG (default: 2)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="If input is JSON, write recommended SDGs directly back to the file",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print top matched phrases per SDG",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as raw JSON",
    )

    args = parser.parse_args()

    if not args.input_file.is_file():
        sys.exit(f"Error: File '{args.input_file}' not found.")

    is_json = args.input_file.suffix.lower() == ".json"
    doc_data = None

    if is_json:
        text, doc_data = extract_from_dual_language_json(args.input_file)
    elif args.input_file.suffix.lower() == ".pdf":
        text = extract_from_pdf(args.input_file)
    else:
        text = clean_text_artifacts(args.input_file.read_text(encoding="utf-8"))

    counts, matched_phrases, explicit_mentions = scan_text_for_sdgs(text)
    vector = [counts[i] for i in range(1, 18)]

    # Read pre-existing metadata declarations if present in JSON
    file_assigned_sdgs = set()
    if is_json and doc_data and "SDGs" in doc_data:
        raw_sdgs = str(doc_data["SDGs"])
        found = re.findall(r"\b(1[0-7]|[1-9])\b", raw_sdgs)
        file_assigned_sdgs = {int(x) for x in found}

    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    top_by_keyword = [g for g, c in ranked if c >= args.min_hits][: args.top_k]

    # Precedence hierarchy: In-text explicit > Pre-existing file metadata > Top keywords
    if explicit_mentions:
        combined_goals = sorted(list(explicit_mentions))
        selection_reason = "Scoped to author's explicit in-text declarations."
    elif file_assigned_sdgs:
        combined_goals = sorted(list(file_assigned_sdgs))
        selection_reason = "Scoped to pre-assigned SDGs in metadata."
    else:
        combined_goals = sorted(top_by_keyword)
        selection_reason = f"Top {args.top_k} inferred keywords (min hits >= {args.min_hits})."

    top_candidates = [f"SDG {g}" for g in combined_goals]
    suggestion_str = ", ".join(top_candidates) if top_candidates else "None"

    if args.json:
        out = {
            "file": args.input_file.name,
            "sdg_vector": vector,
            "explicit_mentions": sorted(list(explicit_mentions)),
            "metadata_assigned_sdgs": sorted(list(file_assigned_sdgs)),
            "ranked_matches": {f"SDG {g}": c for g, c in ranked if c > 0},
            "recommended": top_candidates,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    # CLI Report
    print("=" * 65)
    print(f"==> Dual-Language SDG Classification Report: {args.input_file.name}")
    print(f"Match Vector (SDG 1 - 17):\n{vector}\n")

    if is_json and doc_data:
        curr = doc_data.get("SDGs", "")
        print(f"Assigned value in JSON: \"{curr}\"")
        print()

    if explicit_mentions:
        print("Explicit In-Text Goal Declarations:")
        for g in sorted(explicit_mentions):
            print(f"  [*] SDG {g:2d} (keyword occurrences: {counts[g]})")
        print()

    print("Top Keyword Matching Goals:")
    has_hits = False
    for rank, (goal, hits) in enumerate(ranked, 1):
        if hits == 0:
            continue
        has_hits = True
        flag = " [Explicitly cited]" if goal in explicit_mentions else ""
        print(f"  [{rank:2d}] SDG {goal:2d} : {hits:4d} hit(s){flag}")
        if args.verbose:
            for phrase, count in matched_phrases[goal][:5]:
                print(f"       * \"{phrase}\": {count}")

    if not has_hits:
        print("  (No significant keyword matches found)")

    print("-" * 65)
    print(f"Note: {selection_reason}")
    print(f"Suggested 'SDGs' value: \"{suggestion_str}\"")

    if is_json and args.update:
        if top_candidates:
            # Store numeric string matching fordiva/DiVA format
            doc_data["SDGs"] = ", ".join(str(g) for g in combined_goals)
            with open(args.input_file, "w", encoding="utf-8") as f:
                json.dump(doc_data, f, indent=2, ensure_ascii=False)
            print(f"\n✔ Successfully updated '{args.input_file.name}' with: \"{doc_data['SDGs']}\"")
        else:
            print("\n[!] No significant SDG terms found; file not updated.")
    print("=" * 65)


if __name__ == "__main__":
    main()
