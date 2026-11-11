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

Note that the transformer model SDGdetector, performs poorly on technical documents as it was not trained on this kind of data.

OSDG https://github.com/osdg-ai/osdg-tool available at https://www.osdg.ai/ does much better than SDGdetector, but still not as specific as sdg_classifier_dual_language.py. However, according to its web page: "OSDG currently supports 15 languages: English, Arabic, Danish, Dutch, Finnish, French, German, Italian, Korean, Polish, Portuguese, Russian, Spanish, Swedish, and Turkish." See the paper at https://arxiv.org/abs/2211.11252

See also https://aurora-universities.eu/research/open-science/dashboard/deliverables/
There is a list of related efforts at https://aurora-universities.eu/research/open-science/dashboard/faq/
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

# Define dedicated case-sensitive acronyms
ACRONYMS_SDG_TERMS: dict[int, list[str]] = {
    3: [
        # Clinical & Diagnostics
        "ECG", "ECGI", "EMG", "MRI", "PET", "CT",
        # Therapeutics, Biomaterials & Genetics
        "LNP", "mRNA", "2PP", "RUMS", "DNA", "RNA", "PZT", "PZT-5H", "ASR",
        # Public Health & Protection
        "GBV",
    ],
    4: [
        # Educational Acronyms
        "STEM", "STEAM", "VET",
        # Engineering Education & Pedagogical Models (Target 4.3 & 4.4)
        "CBL", "SDT", "WIA", "WIAs"

    ],
    7: [
        # Solar & Clean Generation
        "PV", "PVT", "CCS", "BECCS", "SNG",
        # Storage & Grid Infrastructure
        "BESS", "LIB", "LIBs", "SIB", "SIBs", "GSHP", "GSHPs", "LCOE",
        "HP", "HPs", "ASHP", 
        # Energy Modeling Frameworks
        "OSeMOSYS", "ESOMs", "CLEWs",
        # Grid Electrification & Vehicle-Grid Integration (Target 7.1 & 7.2)
        "V2G", "EV", "EVs", "BEV", "BEVs"
        # Concentrated Solar, Thermal Storage & Power Cycles (Target 7.2, 9.4, 13.2)
        "CSP", "TES", "sCO2", "P2H2P", "DNI",

        "COP", "EPC",
    ],
    9: [
        # Connectivity, AI & Computation
        "5G", "6G", "AI", "ML", "DL", "FL", "XAI", "XRL", "IoT",
        "MIMO", "RIS", "ssRIS", "SMS", "SCA", "FPPSCA", "OFDM", "RAN", "O-RAN",
        "RU", "RUs", "DU", "DUs", "CU", "CUs", "UE", "UEs",
        "O-RU", "O-RUs", "O-DU", "O-DUs", "O-CU", "O-CUs",
        "O-CU-CP", "O-CU-UP", "RIC", "SMO", "SLA",
        # Hardware, Robotics & Navigation
        "MEMS", "PCB", "MOSFET", "MOSFETs", "SiC", "AGV",
        "INS", "DVL",
        # Systems, Design & Digital Engineering
        "CPS", "CFD", "BIM", "STEP", "AP242",
        "SANS",  # Small-Angle Neutron Scattering
        # Aerial Robotics & Urban Air Mobility (Target 9.1 & 9.5)
        "UAM", "UAV", "UAVs",
        # Industrial Decarbonization & Clean Facility Retrofit (Target 9.4)
        "BECCS", "DACCS", "CCS", "CRCF",

        # Additive Manufacturing & Advanced Characterization (Target 9.5)
        "AM", "XRD", "PBF", "PBF-EB", "PBF-LB",

        "EAF", "DRI", "H-DRI", "NG-DRI",

        # Fluid Dynamics & Acoustic Engineering (Target 9.5)
        "RANS", "URANS", "LES", "SPL", "SWL", "BPF",

        # Electric & Freight Transport (Target 9.1 & 11.2)
        "EV", "EVs", "BEV", "BEVs", "HDV", "HDVs", "CLD", "CLDs",

        # Concentrated Solar, Thermal Storage & Power Cycles (Target 7.2, 9.4, 13.2)
        "CSP", "TES", "sCO2", "P2H2P", "DNI",

        # Field Robotics, Remote Sensing & Perception (Target 9.5)
        "LIDAR", "LiDAR", "IMU",

        "GIS",

    ],
    11: [
        # Public & Active Transit
        "BRT", "LRT", "TOD", "MaaS", "ITS",
        # Connected & Autonomous Transport (Target 11.2)
        "CAV", "CAVs",
        # Spatial Analysis & Built Environment
        "GIS",
        # Smart City Operations
        "ICCC", "ICCCs", "SPV", "SPVs",

        "EPC",

    ],
    12: [
        # Metallurgy & Decarbonized Materials
        "EAF", "DRI", "H-DRI",
        # Circular Economy & Supply Chains
        "CMS", "CSC", "CSCs", "EoL", "XPS", "XPSs",
        # Material Characterization
        "FTIR", "TGA",
    ],
    13: [
        # Climate Metrics & Policy
        "GHG", "LCA", "CDR", "NDC",
        # Heavy Industry Transition & Carbon Removal
        "H-DRI", "BECCS", "CCS", "DACCS", "CRCF",

        # Concentrated Solar, Thermal Storage & Power Cycles (Target 7.2, 9.4, 13.2)
        "CSP", "TES", "sCO2", "P2H2P", "DNI",

        "GWP", "F-gas",
    ],
    14: [
        # Marine & Underwater Technology
        "AUV", "AUVs", "ROV", "ROVs", "DVL", "SONAR"
    ],
    17: [
        # Country classifications & Global North-South Cooperation
        "LMIC", "LMICs", "ODA",
        # Open Modeling & Inter-institutional Frameworks
        "CLEWs",
    ],
}

# Dual-Language Auxiliary Terms (English & Swedish)
AUXILIARY_SDG_TERMS = {
    1: [ # SDG 1 (No Poverty)
        # Energy & Household Poverty
        "energy poverty", "household poverty", "energy coaching",
        "mobility coaching",

        # Swedish Terms
        "mobilitetscoachning", "personlig mobilitets-coachning",
        "energifattigdom", "hushållsfattigdom", "energicoachning",
        "fattigdom", "globalt mål 1 - ingen fattigdom", # "hållbar utveckling",
        "avskaffa fattigdom", "extrem fattigdom", "multidimensionell fattigdom",
        "relativ fattigdom", "sociala trygghetssystem", "grundläggande service",
        "ekonomiska resurser", "mikrofinansiering", "social trygghet", "fattigdomsminskning",
        "utsatta grupper", "ekonomisk utsatthet"
    ],
    2: [ # SDG 2 (Zero Hunger)
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
    3: [ # SDG 3 (Good Health & Well-Being)
        # Clinical & Cardiovascular
        "cardiac arrest", "cardiac", "electrocardiogram",
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
        "wellbeing", "outdoor recreation",
        # --- Existing Clinical & Health Terms ---
        "health", "patient", "patients", "medicine",
        "disease", "diseases", "treatment", "therapy",
        # --- Built Environment, Housing & Mental Well-Being (Target 3.4) ---
        "well-being", "wellbeing", "mental health", "quality of life",
        "social cohesion", "loneliness", "involuntary loneliness",
        "healthy public spaces", "restorative environment", "nature connectedness",
        # --- Environmental Health: Noise, Air Quality & Particulates (Target 3.9) ---
        "traffic noise", "road traffic noise", "environmental noise", "noise annoyance",
        "particulate emissions", "particulate matter", "air quality", "air pollution",
        # --- MedTech, Biomaterials & Drug Delivery (Target 3.b) ---
        "drug delivery", "transdermal delivery", "deep lung delivery", "therapeutic delivery",
        "medical imaging", "orthovolt therapy", "active implants", "micro-implant",
        "implantable", "microneedles", "biomedical engineering", "biologics",
        # --- Health Systems & Climate Resilience (Target 3.8 & 3.d) ---
        "health systems", "healthcare services", "healthcare access", "access to healthcare",
        "health adaptation", "access to care", "care provision",
        # Environmental Acoustics & Noise Annoyance (Target 3.9)
        "acoustic annoyance", "noise annoyance", "disturbances",
        "inhabitants exposed to noise", "aircraft noise",
        # Biometrics, Psychophysiology & Road Safety (Target 3.6 & 3.4)
        "biometrics", "biometric data", "psychophysiology",
        "heart activity", "brain activity", "cognitive load",
        "risk perception", "road safety", "road users",
        "smartwatch", "smartwatches",

        # Road Transport Emissions & Environmental Health (Target 3.9)
        "global emissions", "tailpipe emissions", "decarbonising the sector",
        "decarbonising", "transport emissions",

        # Vehicle Tonal Noise & Acoustic Compliance (Target 3.9)
        "tonal noise", "acoustic legislation", "sound pressure levels",
        "sound power level",

        # Swedish Terms
        "globala utsläpp", "avgasutsläpp", "transportutsläpp",
        "utsläppsminskning",

        "tonalt ljud", "tonbrus", "akustiklagstiftning",
        "ljudtrycksnivåer", "ljudintensitetsnivåer",

        "biometrisk data", "biometriska data", "psykofysiologi",
        "hjärtaktivitet", "hjärnaktivitet", "kognitiv belastning",
        "riskuppfattning", "trafiksäkerhet", "trafikanter",
        "smartklockor", "smartklocka",

        "akustisk störning", "bullerstörning", "bullerstörningar",
        "bullerexponering", "störningar",
        "hälso- och sjukvård", "sjukvårdssystem", "sjukvård", "vårdtillgång",
        "läkemedelstillförsel", "läkemedelsfrisättning", "medicinteknik", "medicinsk teknik",
        "medicinsk avbildning", "mikronålar", "läkemedel", "biologiska läkemedel",
        "mikroimplantat",
        "trafikbuller", "vägtrafikbuller", "miljöbuller", "bullerexponering",
        "partikelutsläpp", "luftkvalitet", "luftföroreningar",
        "välbefinnande", "psykiskt välbefinnande", "fysiskt välbefinnande", "välmående",
        "psykisk hälsa", "folkhälsa", "livskvalitet", "ensamhet", "ofrivillig ensamhet",
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
        "medicinteknik", "hjälpmedelsteknik", "välbefinnande", "friluftsliv", "naturkontakt"
    ],
    4: [ # SDG 4 (Quality Education)
        # Pedagogy & Educational Research
        "challenge-based learning", "teacher education", "teacher students",
        "digital competence", "pedagogical practice", "sloyd", "global competence",
        "education fiction", "digital response systems",
        "educational facilities", "classroom", "classrooms",
        # Teacher Professional Development & Assessment (Target 4.c & 4.1)
        "teacher training", "in-service teachers", "teacher competence",
        "knowledge assessment", "formative assessment", "comparative judgment",
        "peer learning", "stem education",

        # Engineering Pedagogy & STEM Education Equity (Target 4.3 & 4.5)
        "engineering education", "technical higher education", "technical university",

        # Challenge-Based Learning, Outreach & Authentic Learning (Target 4.3 & 4.4)
        "challenge-based learning", "stem outreach", "authentic learning",
        "work industry-related activities", "student engagement",
        "engineering education", "pedagogy", "pedagogies",

        # Swedish Terms
        "challenge-based learning", "stem-outreach", "autentiskt lärande",
        "arbetslivsrelaterade aktiviteter", "studentengagemang",
        "ingenjörsutbildning", "forskarutbildning", "föruniversitär",

        "ingenjörsutbildning", "teknisk högre utbildning", "tekniskt universitet",
        "lärare", "lärares", "lärarnas", "elevsvar", "kunskapsbedömning",
        "bedömarkompetens", "kompetensutveckling", "kollegialt lärande",
        "likvärdig bedömning", "likvärdighet", "ämnesdidaktik", "ämnesdidaktiska",
        "teknikämnet", "skolverket", "årskurs", "skolinspektionen",
        "lärarutbildning", "lärarstudenter", "digital kompetens", "pedagogisk praktik",
        "slöjd", "utbildningsfiktion", "utmaningsbaserat lärande", "bedömning i skolan",
        "globalt mål 4 - god utbildning för alla", "utbildning",
        "god utbildning", "likvärdig utbildning", "inkluderande utbildning",
        "förskoleverksamhet", "grundskola", "gymnasieskola", "högre utbildning",
        "yrkesutbildning", "livslångt lärande", "läs- och skrivkunnighet",
        "didaktik", "klassrum", "skola", "skolor", "elever", "grundskoleelever",
    ],
    5: [ # SDG 5 (Gender Equality)
        # Diversity & Governance
        "corporate governance", "homosocial", "homosociality", "gender diversity",
        "gender equality", "female executives", "daughter effect",
        # Workplace Culture, STEM/Tech Gender Balance (Target 5.5)
        "male-dominated", "androcentric", "gender equality practices",
        "gendered power relations", "homosocial cultures",


        # Swedish Terms
        "mansdominerad", "mansdominerade", "androcentriska",
        "jämställdhetspraktiker", "könade maktrelationer", "ojämställdhet",
        "bolagsstyrning", "könsmässig mångfald",
        "jämställdhetsfond", "kvinnlig representation",
        "jämställdhet", "diskriminering av kvinnor", "våld mot kvinnor",
        "obetalt hem- och omsorgsarbete", "fullt deltagande i ledarskap",
        "beslutsfattande", "reproduktiv hälsa", "lika rätt till ekonomiska resurser",
        "homosocialitet",
        "jämställdhetsintegrering", "styrelsesammansättning"
    ],
    6: [ # SDG 6 (Clean Water and Sanitation)
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
    7: [ # SDG 7 (Affordable & Clean Energy)
        # Clean Energy, Grid & Heat Recovery
        "district heating", "excess heat", "heat recovery",
        "concentrating solar power", "smart meter", "smart meters",
        "demand flexibility", "energy transition", "lead-cooled reactor",
        "nuclear fuel", "electric power engineering", "supercritical co2",
        "energy storage", "battery", "batteries",
        "sodium-ion battery", "lithium-ion battery",
        "anode materials", "structural battery", 

        # --- Additions for Energy-Resilient & Efficient Production (Target 7.3) ---
        "reconfigurable manufacturing systems", "reconfigurable manufacturing system",
        "rms creation", "energy-efficient manufacturing",

        # Energy Communities, Justice & Grid Flexibility (Target 7.1 & 7.2)
        "energy community", "energy communities", "energy justice",
        "flexibility capital", "just transition", "electricity grid",
        "smart grid", "smart grids",

        "photovoltaic-thermal", "pvt collector", "pvt collectors",
        "borehole regeneration", "ground source heat pump",

        # Transport Electrification & Grid Infrastructure (Target 7.2)
        "electrification", "freight electrification", "electric trucks",
        "charging infrastructure", "charging providers", "electricity suppliers",
        "electricity supply",

        # Waste Heat Recovery, Commercial Heat Pumps & Refrigeration Efficiency (Target 7.3)
        "heat recovery", "waste heat recovery", "surplus heat",
        "commercial refrigeration", "refrigeration system", "refrigeration systems",
        "air conditioning", "energy transition", "heat pumps",


        # Swedish Terms
        "värmeåtervinning", "överskottsvärme", "kylsystem",
        "kommersiell kyla", "luftkonditionering", "energiomställning",
        "värmepumpar", "värmepump", "energideklarationer",

        "elektrifiering", "laddinfrastruktur", "e-lastbilar",
        "eldrivna tunga lastbilar", "laddoperatörer", "elsystemet",
        "elbolag",

        "solhybridkollektor", "solhybridkollektorer", "bergvärmepump",
        "bergvärmepumpar", "borrhålsregenerering",
        "energigemenskap", "energigemenskaper", "energirättvisa",
        "flexibilitetskapital", "rättvis omställning", "smarta elnät",
        "smarta elmätare",

        # Swedish Energy Efficiency & Low-Energy Compounds (Target 7.3)
        "energisnål", "energisnåla", "energisnålt",
        "energisparande", "energianvändning", "energieffektivisering",
        "lågenergi", "energieffektiva produkter",
        "fjärrvärme", "spillvärme", "värmeåtervinning", "smarta mätare",
        "efterfrågeflexibilitet", "energiomställning", "blykyld reaktor",
        "kärnbränsle", "elkraftteknik",
        "hållbar energi", "förnybar energi", "energieffektivisering",
        "ren energiteknik", "fossilfria bränslen", "smarta elnät",
        "solceller", "vindkraft", "värmepumpar", "energylagring",
        "batteri", "batterier", "natriumjonbatterier", "litiumjonbatterier",
        "strukturella batterier",

        "omkonfigurerbara tillverkningssystem",
    ],
    8: [ # SDG 8 (Decent Work and Economic Growth)
        # Workplace Safety & Manufacturing
        "safety culture", "safety practices", "construction sector",
        "collaborative product development", "assembly features",
        "social entrepreneurship", "social enterprises",

        # Sustainable Finance, Capital Allocation & Financial Sector (Target 8.10)
        "sustainable finance", "financial sector", "investment flows",
        "redirect investments", "redirecting investments", "green projects",
        "financing the climate transition",

        # Driver Ergonomics, Cognitive Load & Operator Safety (Target 8.8)
        "cognitive load", "human-machine interaction", "human-computer interaction",
        "remotely controlled vehicle", "driver information",

        # Resource Efficiency & Industrial Decoupling (Target 8.4)
        "industrial by-products", "industrial by-product", "by-product",
        "by-products", "recover metals", "metal recovery",
        "underutilized", "resource efficiency",

        # Capital Intensity, Project Bankability & Cost Competitiveness (Target 8.2)
        "cost competitiveness", "capital intensity", "bankability",
        "techno-economic", "techno-economic analysis", "techno-economic optimization",

        # Financial Sector, Banking & Private Investment (Target 8.10)
        "sustainable finance regulations", "financial regulation",
        "private sector financing", "green investment",

        # Labor Ergonomics, Manual Labor Automation & Productivity (Target 8.2 & 8.8)
        "manual labor", "manual labour", "boost productivity", "improving efficiency",
        "workplace productivity", "harsh working conditions",


        # Swedish Terms
        "manuellt arbete", "öka produktiviteten", "ökar produktiviteten",
        "öka effektiviteten", "arbetsmiljö i skogsbruket",

        "hållbarhetsregelverk", "hållbarhetsregelverken",
        "finansmarknadsreglering", "privat sektors finansiering",
        "investeringar för klimatomställning", "bank", "fond",

        "kostnadskonkurrenskraft", "kapitalkostnader", "finansierbarhet",
        "tekno-ekonomisk", "tekno-ekonomisk analys", "tekno-ekonomisk optimering",

        "industriella biprodukter", "industriell biprodukt", "biprodukter",
        "restprodukter", "restprodukt", "återvinna metaller",
        "metallåtervinning", "resurseffektivitet",

        "kognitiv belastning", "människa-maskininteraktion",
        "människa-datorinteraktion", "fjärrstyrt fordon", "förarinformation",

        "hållbar finansiering", "finansiella sektorn", "finanssektorn",
        "investeringsflöden", "omfördela investeringar", "gröna projekt",

        "entreprenörskap", "socialt företagande", "idéburna verksamheter",
        "kooperativa företag", "sociala företag",
        "säkerhetskultur", "säkerhetspraktiker", "byggsektorn",
        "arbetsplatskultur", "monteringsegenskaper", "tillverkningsteknik",
        "anständiga arbetsvillkor", "ekonomisk tillväxt", "resurseffektivitet i konsumtion och produktion",
        "full sysselsättning", "ungdomsarbetslöshet", "tvångsarbete",
        "trygg arbetsmiljö", "arbetstagares rättigheter",
        "arbetsplatsolyckor", "produktionssystem", "monteringsprocesser", "arbetsmiljö"
    ],
    9: [ # SDG 9 (Industry, Innovation and Infrastructure)
        # AI / Software / Digital Infrastructure
        "artificial intelligence", "machine learning", "deep learning",
        "federated learning", "neural network", "neural networks",
        "distributed computing", "edge computing", "edge devices",
        "internet of things", "sensor network", "telecommunications",
        "cloud computing", "data infrastructure", "anomaly detection",
        # Electrical & Hardware Engineering
        "power converter", "power converters", "traction inverter",
        "inverter", "inverters", "power electronics", "gate driver",
        "gate drivers", "silicon carbide",
        "semiconductor", "semiconductors", "soft switching",
        "printed circuit board", "hardware prototype",
        "circuit design", "finite element method",
        # Infrastructure, Transport, Robotics & Materials
        "rail freight", "rail infrastructure", "track access charges",
        "cell-free massive mimo", "reconfigurable intelligent surface",
        "articulated robot", "unbound granular materials", "asphalt binder",
        "additive manufacturing", "metasurface", "metasurfaces",
        "quantum sensor", "quantum sensors",
        "magnetometer", "magnetometers", "magnetometer array",
        "inertial navigation", "inertial navigation system",
        "autonomous vehicle", "autonomous vehicles",
        "autonomous underwater vehicle", "autonomous underwater vehicles",
        "radio access network", "manufacturing systems",
        "quantum sensing", "atomic magnetometer",
        "inertial sensor", "sensor fusion",
        "doppler velocity log",
        "road infrastructure", "asphalt concrete",
        "pavement engineering", "bitumen",
        "freight volume", "freight volumes",
        "passenger volume", "passenger volumes",
        "automated guided vehicle",
        # Open-RAN & Cellular Disaggregation (Target 9.c)
        "open ran", "open-ran",
        "radio unit", "radio units",
        "distributed unit", "distributed units",
        "centralized unit", "centralized units",
        "ran intelligent controller", "near-rt ric", "non-rt ric",
        "functional split", "functional splits", "wireless fronthaul",
        "optical fronthaul",
        # Automated Manufacturing & Assembly Systems (Target 9.2 & 9.5)
        "automated assembly", "assembly systems", "product development",
        "concept detailing", "manufacturing process", "manufacturing systems",
        "specialized equipment", "feeders, grippers, and fixtures",
        "product information model", "step ap242",

        # Enterprise Finance & Capital Supply (Target 9.3)
        "access to financial services", "affordable credit", "access to finance",
        "venture debt", "growth capital",

        # Turbomachinery, Aerodynamics & Computational Mechanics
        "aeroelastic", "aeroelasticity", "transonic", "compressor cascade",
        "turbomachinery", "aeroengine", "aeroengines", "aerodynamic damping",
        "axial compressor", "flutter onset",

        # Advanced Materials Engineering & Metallurgy
        "stainless steel", "stainless steels", "duplex stainless steel",
        "phase separation", "phase transformation", "neutron scattering",
        "neutron diffraction", "intermetallic", "intermetallics",

        # Aerial Transport Infrastructure, Logistics & Drones (Target 9.1 & 9.5)
        "drone", "drones", "drone industry", "drone transport",
        "drone services", "urban air mobility", "unmanned aerial vehicle",
        "transport of goods", "simulation platform", "flight routes",

        # Industrial Robotics, Kinematics & Calibration (Target 9.2 & 9.5)
        "articulated robot", "articulated robots",
        "industrial robot", "industrial robots", "industrial robotics",
        "robotic system", "robotic systems", "end-effector", "end-effectors",
        "robot accuracy", "robot calibration", "stiffness model", "joint stiffness",
        "high-precision", "high precision", "contact applications",

        # Carbon Capture Plant Infrastructure & Engineering (Target 9.4)
        "capture facility", "capture facilities", "capture technology",
        "capture technologies", "pressurized capture", "physical design",
        "technoeconomic", "techno-economic", "beccs facilities",

        # Rail Systems Engineering & Infrastructure Asset Management (Target 9.1)
        "systems engineering", "rail infrastructure", "rail main lines",
        "model-based systems engineering", "asset management",
        "railway infrastructure", "tracks and yards",
        # Rail Infrastructure Capacity, Redundancy & Maintenance (Target 9.1)
        "rail freight", "freight trains", "freight transport",
        "track fees", "track charges", "rail network", "railway network",
        "triangular tracks", "maintenance backlog", "intermodal transport",


        # Advanced Additive Manufacturing & Characterization (Target 9.5)
        "powder bed fusion", "electron beam powder bed fusion",
        "laser powder bed fusion", "synchrotron", "synchrotron techniques",
        "synchrotron x-ray", "melt pool", "rapid solidification",
        "alloy design", "hot work tool steel",

        # Clean Metallurgy, Hydrogen Processing & Upscaling (Target 9.4 & 9.5)
        "hydrogen reduction", "solid-state hydrogen reduction",
        "metal production", "alloy production", "alloy powders",
        "powder metallurgy", "upscaling", "tube furnace",
        "horizontal tube furnace", "chemical homogeneity",

        # Powertrain Engineering, Compressors & Turbomachinery (Target 9.4 & 9.5)
        "centrifugal compressor", "centrifugal compressors", "heavy-duty truck",
        "heavy-duty trucks", "truck powertrain", "truck powertrains",
        "turbocharger", "turbochargers", "aero-acoustics", "aeroacoustics",
        "blade design", "impeller", "impellers", "volute", "volutes",

        # Heavy Industry Process Pilot & Furnace Infrastructure (Target 9.4)
        "industrial pilot", "pilot-scale", "pilot trials",
        "electric arc furnace", "shaft furnace",

        # Field Robotics, Heavy Mobile Machinery & Off-Road Automation (Target 9.4 & 9.5)
        "autonomous systems", "forest machinery", "path planning",
        "unstructured terrain", "off-road", "terrain-vehicle interaction",
        "field robotics", "intelligent machinery",

        # Water & Wastewater Infrastructure (Target 9.1 & 9.4)
        "wastewater treatment", "wastewater treatment plant",
        "municipal water", "water infrastructure", "sanitation infrastructure",

        # Open Science, Research Infrastructure & Open Source (Target 9.5)
        "open science", "open source", "open-source software",

        # Swedish Terms
        "öppen vetenskap", "öppen källkod",
        
        "avloppsrening", "avloppsreningsverk", "vatteninfrastruktur",
        "kommunalt vatten",

        "autonoma system", "skogsmaskiner", "skogsmaskin", "vägplanering",
        "ostrukturerad terräng", "intelligenta maskiner", "markberedning",

        "industriell pilot", "pilotskalig", "pilotförsök",
        "ljusbågsugn", "schaktugn",

        "centrifugalkompressor", "centrifugalkompressorer", "tunga lastbilar",
        "lastbilar", "turboladdare", "aeroakustik", "bladdesign",
        "pumphjul", "volut", "turbomaskiner",

        "vätereduktion", "vätgasreduktion", "solid-state vätereduktion",
        "metallproduktion", "legeringsframställning", "legeringsproduktion",
        "legeringspulver", "pulvermetallurgi", "uppskalning",
        "rörugn", "kemisk homogenitet",

        "godsjärnväg", "godstransport", "godstransporter",
        "banavgifter", "underhållsskuld", "triangelspår",
        "järnvägsnätet", "järnvägslogistik", "trafikverket",

        "pulverbäddsfusion", "synkrotron", "synkrotrontekniker",
        "synkrotronljus", "smältpool", "legeringsdesign", "verktygsstål",
        "systemteknik", "järnvägsinfrastruktur", "tillgångsförvaltning",
        "spåranläggning", "bangårdar", "modellbaserad systemteknik",
        "artikulär robot", "artikulära robotar", "industrirobotik",
        "robotsystem", "sluteffektor", "sluteffektorn", "sluteffektorns",
        "robotkalibrering", "ledstyvhet", "ledstyvhetsmodell",
        "kvasistatisk", "kvasistatiska", "högprecision",

        "drönare", "drönarindustri", "drönartransporter", "drönartjänster",
        "luftburen mobilitet", "obemannade luftfartyg", "godstransport",
        "godstransporter", "simuleringsplattform", "flygrutter",

        "rostfritt stål", "rostfria stål", "duplexa rostfria stål",
        "fasseparation", "fasomvandling", "neutronspridning",
        "neutrondiffraktion", "intermetalliska faser",

        "aeroelasticitet", "flygmotorer", "kompressorblad", "skovelerosion",
        "transsonisk", "strömningsberäkning",
        "kapitalförsörjning", "tillgång till finansiering", "öka tillgången till finansiering",
        "företagsfinansiering", "såddkapital", "tillväxtkapital",
        "automatiserad montering", "monteringsprocess", "tillverkningsprocess",
        "tillverkningssystem", "produktionsutveckling", "informationsmodellering",
        "väginfrastruktur", "asfaltbeläggning", "vägbyggnad",
        "kvantsensorer", "tröghetssensor", "sensorfusion",
        "5g-nät", "lokala 5g-nät", "privata 5g-nät",
        "artificiell intelligens", "maskininlärning", "djupinlärning",
        "sakernas internet", "telekommunikation", "molninfrastruktur",
        "kraftelektronik", "halvledare", "godståg", "järnvägsinfrastruktur",
        "banavgifter", "cellfri massiv mimo", "metaytor", "metayta",
        "additiv tillverkning", "asfaltbindemedel",
        "hållbar industri", "innovationer och infrastruktur", "motståndskraftig infrastruktur",
        "inkluderande industrialisering", "småföretagens tillgång till finansiering",
        "uppgradera industriell infrastruktur", "ren teknik och miljöanpassade processer",
        "vetenskaplig forskning och teknisk kapacitet", "informations- och kommunikationsteknik",
        "tillgång till internet", "cyberfysiska system",
        "tågtrafik", "persontåg", "snabbtåg",
        "kvantsensor", "tröghetsnavigering", "autonoma undervattensfarkoster"
        "godsvolym", "godsvolymer",
        "resandevolym", "resandevolymer",
        "passagerarvolym", "passagerarvolymer",
        # Swedish 5G / Telecom variants (with and without hyphens, inflections)
        "5g nät", "5g-nät", "5g nätet", "5g-nätet", "5g näten", "5g-näten",
        "lokala 5g nät", "lokala 5g-nät", "privata 5g nät", "privata 5g-nät",
        "privat 5g nät", "privat 5g-nät", "lokalt 5g nät", "lokalt 5g-nät",
        
        # Industrial Automation & Factory Robotics (Target 9.2 & Target 9.5)
        "industrirobot", "industrirobotar",
        "produktionslina", "produktionslinor", "processtyrning", "industriell användning",
        "industriellt bruk", "systemintegratör", "systemintegratören",

        "koldioxidinfångning", "avskiljningsanläggning", "avskiljningsanläggningar",
        "infångningsteknik", "anläggningsdesign",

    ],
    10: [ # SDG 10 (Reduced Inequalities)
        # Inclusion & Equity
        "assistive technology", "prosthetic users", "physical rehabilitation",
        "mobility inequality", "gender disparities in leadership",
        # Global Mobility & Migration (Target 10.7 & Target 10.b)
        "international student mobility", "student mobility",
        "brain drain", "frustrated return", "transnational mobility",
        "knowledge transfer to developing countries",
        # Energy Equity & Vulnerability (Target 10.2 & 10.3)
        "energy justice", "just transition", "vulnerable consumers",

        # Organizational Diversity & Equity Practices (Target 10.2 & 10.3)
        "diversity work", "reproduce inequality", "power structures",
        "underlying hierarchies",

        # Swedish Terms
        "mångfaldsarbete", "reproducera ojämställdhet", "maktstrukturer",
        "underliggande hierarkier",
        "energirättvisa", "rättvis omställning",
        "studentmobilitet", "internationell studentmobilitet",
        "kompetensflykt", "transnationell rörlighet",
        "hjälpmedelsteknik", "fysisk rehabilitering", "tillgänglighet",
        "minskad ojämlikhet", "inkomsttillväxt för de fattigaste", "social och ekonomisk inkludering",
        "lika möjligheter", "diskriminerande lagstiftning", "finansmarknadsreglering",
        "migration och rörlighet", "säker migration", "tillgänglighet för personer med funktionsnedsättning",
        "funktionsvariation"
    ],
    11: [ # SDG 11 (Sustainable Cities and Communities)
        # Urban Infrastructure, Mobility, Acoustics & Housing
        "urban mobility", "public transport", "on-demand public transport",
        "cycling", "green space", "urban green", "shared housing",
        "traffic noise", "environmental acoustics", "climate-neutral cities",
        "wheelchair accessibility", "smart cities", "ground-borne vibration",
        "sustainable housing", "passenger rail",
        "bicycle", "bicycling", "cyclist", "cyclists", "route choice",
        # English Terms (Urban Public Transit & Micromobility)
        "tram", "trams", "tramway", "tramways", "light rail", "light rail transit",
        "subway", "subways", "metro system", "metro systems", "underground rail",
        "bike sharing", "bike-sharing", "bikeshare", "bicycle sharing", "shared micromobility",
        "commuter train", "commuter trains", "commuter rail", "suburban rail",
        # --- NEW ADDITIONS FOR DISASTER RESILIENCE & EMERGENCY COMMUNICATIONS (Target 11.5 & 11.b) ---
        "connectivity during disasters", "emergency communications", "disaster connectivity",
        "disaster resilience", "resilient infrastructure", "critical societal infrastructure",
        "network resiliency", "resilient network design", "disaster management",
        
        # --- NEW ADDITIONS FOR DENSE INDOOR BUILT ENVIRONMENTS (Target 11.1 & 11.c) ---
        "indoor dense spaces", "indoor dense space", "indoor deployment scenarios",
        "built environment", "dense indoor spaces", "dense urban spaces",

        # Built Environment & Construction Practices (Target 11.c)
        "construction sector", "construction sites", "building safety",

        "circular construction", "deconstruction", "building deconstruction",
        "reusable building components", "reclaimed concrete", "donor building",

        # Urban Safety, Crime Prevention & Housing Management (Target 11.1 & 11.7)
        "place management", "housing management", "perceived safety",
        "situational crime prevention", "residential areas", "crime prevention through environmental design",
        "multi-family housing", "cooperative housing",

        # Intelligent & Connected Mobility Systems (Target 11.2)
        "connected and autonomous vehicles", "autonomous vehicles",
        "autonomous mobility", "intelligent transport systems",
        "cooperative intelligent transport systems",

        # Rail Transit Systems & Infrastructure (Target 11.2)
        "rail infrastructure", "railway infrastructure", "rail main lines",
        "freight transport", "freight rail", "rail freight",
        "combined transport", "modal shift", "rail network",
        "punctuality", "train traffic",

        # Micromobility & Active Road Users (Target 11.2)
        "electric scooters", "electric scooter", "travel patterns",
        "travel diaries", "road users",

        # Urban Building Electrification & Dense Housing (Target 11.1 & 11.c)
        "dense urban areas", "dense urban", "multi-family buildings",
        "multi-family building", "electrification of heating",
        "heating electrification", "building owners",

        # Sustainable Freight Logistics & Fleet Transitions (Target 11.2)
        "road freight transport", "freight transport", "freight system",
        "electric trucks", "sustainable transport", "port hinterland",

        # Clean Grid & Settlement Energy Security (Target 11.a)
        "electricity grid", "decarbonizing the electricity grid",

        # Parking Policies, Car Sharing & Shared Micromobility (Target 11.2 & 11.3)
        "flexible parking standards", "parking standards", "car sharing",
        "carpool", "carpooling", "shared mobility", "bike pool",
        "sustainable urban development", "mobility services",

        # Target 11.2 & 11.a: Regional transport links & urban-rural freight logistics
        "regional transport", "regional logistics", "intermodal freight",
        "freight transport logistics", "transport logistics", "city logistics",

        # Target 11.2 & 11.3: Spatial planning & transit routing
        "spatial navigation", "spatial planning", "transport network planning",
        "route planning", "network-level path planning",

        # Autonomous Navigation, Path Planning & Route Safety (Target 11.2)
        "path planning", "roll-out path planning", "autonomous navigation",
        "navigation technologies", "obstacle-filled",

        # Commercial Building Efficiency, Real Estate Energy Sharing & EPCs (Target 11.c)
        "commercial buildings", "building owners", "neighboring buildings",
        "district heating", "space heating demand", "surplus heat",



        # Swedish Terms
        "kommersiella byggnader", "fastighetsägare", "närliggande byggnader",
        "fjärrvärme", "värmebehov", "överskottsvärme", "energideklarationer",

        "vägplanering", "autonom navigation", "autonoma operationer",
        "navigering i svår terräng",

        "rumslig planering", "transportnätsplanering", "ruttplanering",
        
        "regional transport", "regionala transporter", "godstransportlogistik",
        "transportlogistik", "stadslogistik",
        
        "flexibla parkeringstal", "parkeringstal", "bilpool", "bilpooler",
        "cykelpool", "cykelpooler", "delad mobilitet", "mobilitetstjänster",
        "hållbar stadstrafik", "hållbar stadsutveckling", "flerfamiljshus",
        "bostadsrättsförening", "bostadsrättsföreningar",

        "elsystemet", "avkarbonisera elsystemet",
        "godstransporter på väg", "godstransporter", "godstransportsystem",
        "e-lastbilar", "hållbar transportsektor", "hållbart godstransportsystem",

        "täta urbana områden", "flerbostadshus", "elektrifiering av uppvärmning",
        "fastighetsägare", "energibrunnar",
        "godstransport", "godstransporter", "godståg", "godsjärnväg",
        "tågtrafik", "järnvägsnätet", "kombitransporter", "omlastning",
        "punktlighet", "spårbunden godstrafik",
        "elsparkcykel", "elsparkcyklar", "resmönster",
        "resedagböcker", "trafikanter",
        "järnvägsinfrastruktur", "spårbunden trafik",
        "uppkopplade och autonoma fordon", "autonoma fordon",
        "autonom mobilitet", "intelligenta transportsystem",
        "hållbara transportsystem",
        "platsförvaltning", "platsförvaltare", "bostadsförvaltning", "bostadsförvaltare",
        "fastighetsförvaltare", "fastighetsförvaltning", "flerbostadshus",
        "bostadsområde", "bostadsområden", "upplevd trygghet", "trygghet",
        "brottsförebyggande", "brottsprevention", "bostadsrätter", "allmännyttan",
        "allmännyttiga", "störningshantering",
        "cirkulärt byggande", "återbruk i byggsektorn", "demontering",
        "byggsektorn", "byggarbetsplatser", "byggarbetsplats",
        "pendeltåg", "pendeltågen", "pendeltågstrafik", "pendeltågsstation",
        "spårvagn", "spårvagnar", "spårväg", "spårvägar", "lättbana",
        "tunnelbana", "tunnelbanan", "tunnelbanetrafik", "tunnelbanestation",
        "cykeldelning", "delade cyklar", "lånecykel", "lånecyklar", "lånecykelsystem",
        "mikromobilitet",
        "tillgänglighet i stadsmiljö", "rullstolstillgänglighet",
        "fastighetsförvaltning"
        "cykling", "cyklist", "cyklister", "cykelinfrastruktur", "cykelväg",
        "urban mobilitet", "kollektivtrafik", "delningsbostäder", "delat boende",
        "grönområden", "stadsplanering", "trafikbuller", "miljöakustik",
        "klimatneutrala städer", "smarta städer", "markvibrationer",
        "hållbara städer och samhällen", "trygga bostäder", "hållbara transportsystem",
        "inkluderande stadsplanering", "kultur- och naturarv", "katastrofriskreducering",
        "miljöpåverkan från städer", "luftkvalitet i städer", "kommunal avfallshantering",
        "gröna områden och allmänna platser", "stads- och regionplanering",
        "bebyggelse", "bosättning", "globalt mål 11 - hållbara städer och samhällen", "stad",
        "samhällsbyggnad", "samhällsbyggnadsprocessen",
        "fastighetsteknik", "fastighetsvetenskap",
        "fastighetsrätt", "delningsboende", "kollektivt boende",
        "persontåg", "tågtrafikutbud", "resandeprognoser",
        "bostäder",
        # New Swedish disaster & built-environment terms
        "kritisk samhällsinfrastruktur", "samhällsinfrastruktur",
        "täta inomhusmiljöer", "tät inomhusmiljö", "byggd miljö",
        "uppkoppling under katastrofer", "nätverksrobusthet", "resilienta nät",
        "katastrofberedskap", "kriskommunikation"

    ],
    12: [ # SDG 12 (Responsible Consumption and Production)
        # Circular Economy & Materials
        "circular economy", "circular supply chain", "circular manufacturing",
        "biowaste valorization", "hard carbon", "sodium-ion battery",
        "catalytic graphitization", "bio-based carbon", "remanufacturing",
        "steelmaking", "ironmaking", "sustainable lifestyles", "end-of-life",
        "slag", "refractory", "refractories",
        # Renewable Feedstocks & Bio-based Production (Target 12.2 & 12.5)
        "renewable feedstock", "renewable feedstocks", "biomass pyrolysis",
        "bio-oil", "biochar", "syngas", "syngas production", "pyrolysis volatiles",
        # Lean Production & Industrial Waste Elimination (Target 12.5 & 12.6)
        "lean management", "corporate lean", "operational excellence",
        "lean production", "production waste", "waste elimination",
        "implementation waste", "operations management system",

        "material passports", "reclaimed elements", "component reuse", "digital twins for circularity",
        "lifecycle", "life-cycle", "lifecycle-resilient", "lifecycle goals",
        "resource investments", "resource investment",

        # Material Longevity, Degradation Mitigation & Service Life (Target 12.2)
        "service life", "delay embrittlement", "embrittlement",
        "decomposition", "corrosive environments",

        # Production Efficiency & Material Handling (Target 12.2 & 12.5)
        "material handling", "resource requirements", "resource requirement",
        "positioning error", "error reductions",

        # Circular Metallurgy & Recyclable Materials (Target 12.5)
        "recycling-friendly materials", "recycling-friendly",
        "recyclable materials", "material recycling",

        # Clean Metallurgy, Hydrogen Reduction & Smelting (Target 12.2 & 12.4)
        "hydrogen-based reduction", "direct reduced iron", "hematite pellets",
        "shaft furnace", "refining process", "crude steel",

        # By-Product Valorization, Spent Catalyst Recovery & Circular Materials (Target 12.4 & 12.5)
        "industrial by-products", "industrial by-product", "spent catalyst",
        "spent catalysts", "recover metals", "metal recovery",
        "circular materials economy", "materials economy",

        # Clean Steelmaking, Electric Arc Furnace & Slag Valorization (Target 12.4 & 12.5)
        "electric arc furnace", "direct reduced iron", "iron ore pellets",
        "iron ore pellet", "tumbling strength", "compression strength",
        "slag foaming", "bulk slag", "viscosity",

        # Storage Media Alternatives & Molten Salts (Target 12.2)
        "thermal energy storage", "storage media", "molten-salt",
        "molten salt", "heat transfer fluids",

        # Regulatory Frameworks & Sustainability Reporting (Target 12.6)
        "sustainability regulations", "sustainable finance framework",

        # Sustainable Resource Management & Forestry Equipment (Target 12.2)
        "sustainable forestry", "site preparation", "environmental impacts",

        # Swedish Terms
        "hållbart skogsbruk", "markberedning", "minska miljöpåverkan",
        "ekologisk påverkan",

        "hållbarhetsregelverk", "hållbarhetsregelverken",
        "hållbarhetsmål",

        "termisk energilagring", "smältsalt", "smältsalter",
        "smältsaltsbaserade", "värmebärarmedier",

        "ljusbågsugn", "direktreducerat järn", "järnmalmspellets",
        "tumlingsstyrka", "tryckhållfasthet", "slaggskumning",
        "masugnsprocessen",

        "industriella biprodukter", "industriell biprodukt", "restprodukter",
        "restprodukt", "förbrukade katalysatorer", "återvinna metaller",
        "metallåtervinning", "cirkulär materialekonomi",

        "vätgasbaserad reduktion", "direktreducerat järn", "hematitpellets",
        "schaktugn", "raffineringsprocess", "råstål",

        "lättare att återvinna", "återvinningsbara material",
        "materialåtervinning",
        "materialhantering", "resurskrav", "positioneringsfel", "felminskningar",
        "livslängd", "fördröja försprödning", "försprödning",
        "korrosiva miljöer",
        "livscykel", "livscykelresistent", "livscykelresistenta", "livscykelmål",
        "resursinvestering", "resursinvesteringar",
        "materialpass", "komponentåterbruk",
        "lean management", "operationell excellens", "produktionssystem",
        "slöseri", "resursoptimering",
        "förnybar råvara", "förnybara råvaror", "biomassa", "syntesgas", "pyrolysolja",
        "slagg", "infodring", "eldfast material",
        "ståltillverkning", "hållbara livsstilar", "delningsekonomi",
        "cirkulär ekonomi", "cirkulära leveranskedjor", "cirkulär tillverkning",
        "biokol", "natriumjonbatterier", "återvinning", "återanvändning",
        "biografit", "lignin",
        "hållbar konsumtion och produktion", "hållbar förvaltning av naturresurser", "minska matsvinnet",
        "kemikaliehantering", "avfallsminskning", "materialåtervinning",
        "hållbarhetstänkande i företag",
        "hållbarhetsredovisning", "hållbar offentlig upphandling",
        "biomassa"
    ],
    13: [ #SDG 13 (Climate Action)
        # Climate Mitigation & Clean Processing
        "carbon dioxide removal", "carbon removal", "sustainable aviation fuel",
        "climate criteria", "fossil-free steelmaking", "h-dri", "climate policy",
        "carbon footprint", "low-carbon", "life cycle assessment", 
        "ghg emissions",
        "fossil fuel substitution", "fossil replacement", "reduce fossil",
        "fossil-free materials", "fossil bitumen", "decarbonised materials",
        "climate crisis", "climate action", "climate neutrality",
        "climate-neutral", "climate-neutrality", "climate awareness",
        "climate communication",
        "low-carbon pathway", "low-carbon production", "low-carbon manufacturing",
        "low carbon footprint", "environmental footprint",
        "engine efficiency", "aeroengines efficiency", "propulsion efficiency",
        "fuel burn reduction", "power-to-weight ratio",

        # Carbon Removal Certification & Accounting (Target 13.2)
        "carbon removal certification", "carbon removal certification framework",
        "carbon removal", "carbon dioxide removal", "direct air capture",
        "ghg emissions", "lca emissions", "double accounting",

        # Climate Finance & Policy Frameworks (Target 13.a)
        "climate finance", "financing the climate transition", "climate transition",
        "climate policy council", "sustainable activities",

        # Decarbonized Iron & Steel Production (Target 13.2)
        "fossil-free steelmaking", "hydrogen reduction", "hydrogen-based reduction",
        "anthropogenic carbon dioxide emissions",

        # Decarbonization & Clean Heating Transitions (Target 13.2)
        "electrification of heating", "heating electrification",
        "meeting climate targets", "climate targets",

        # Industrial Decarbonization via Hydrogen Reduction (Target 13.2)
        "hydrogen direct reduced iron", "blast furnace process",
        "blast furnace", "hydrogen-based reduction",

        # Grid Decarbonization & Low-Carbon Energy Systems (Target 13.2)
        "decarbonizing the electricity grid", "decarbonizing",
        "low-carbon electricity systems", "low-carbon electricity",
        "dispatchable solar",

        # Climate Finance & Investment (Target 13.a)
        "financing the climate transition", "investments for climate transition",

        # Reforestation & Automated Afforestation (Target 13.2)
        "plant regeneration", "afforestation", "reforestation",

        # F-Gas Phaseout, Low-GWP Refrigerants & Cooling Decarbonization (Target 13.2)
        "fluorinated carbon refrigerants", "fluorinated refrigerants",
        "global warming potential", "low-gwp", "f-gas", "refrigerant",
        "direct carbon emissions", "decarbonization of the heating",
        "climate targets",


        # Swedish Terms
        "fluorerade köldmedier", "köldmedium", "köldmedier",
        "växthusgaser", "f-gas förordning", "f-gas", "klimatmål",
        "koldioxidutsläpp",

        "återbeskogning", "plantering i skogsbruk", "markberedning",
        
        "finansiering och investeringar för klimatomställning",
        "investeringar för klimatomställning", "klimatomställning",

        "avkarbonisera elsystemet", "avkarbonisera",
        "koldioxidsnåla energisystem", "koldioxidsnål solel",
        "planerbar solel",

        "vätgasreducerade järnmalmspellets", "masugnsprocessen",
        "masugn", "vätgasreduktion",

        "elektrifiering av uppvärmning", "uppnå klimatmålen", "klimatmålen",
        "fossilfri ståltillverkning", "vätgasreduktion", "vätgasbaserad reduktion",
        "klimatfinansiering", "finansiera klimatomställningen", "klimatomställning",
        "klimatpolitiska rådet", "hållbara aktiviteter",
        "ramverk för certifiering av koldioxidupptag", "koldioxidupptag",
        "direkt luftinfångning",
        "energieffektivisering i flygmotorer", "minska bränsleförbrukning",
        "koldioxidsnål", "minska koldioxidavtryck", "livscykelanalys", "livscykelanalyser",
        "klimatkris", "klimatneutral",
        "klimatneutralitet", "klimatneutrala",
        "fossilt bitumen", "minska fossila", "fossilfritt material", "fossiloberoende",
        "livscykelanalys",
        "koldioxidavtryck", "koldioxidsnål",
        "koldioxidinfångning", "fossilfritt stål", "klimatåtgärder",
        "klimatdeklaration", "växthusgasutsläpp",
        "globalt mål 13 - bekämpa klimatförändringen", "klimatförändring",
        "bekämpa klimatförändringarna", "klimatanpassning",
        "motståndskraft mot klimatrelaterade faror", "klimatpolitik",
        "klimatstrategier", "kunskap och kapacitet om klimatförändringar",
        "negativa utsläpp"
    ],
    14: [ # SDG 14 (Life Below Water)
        "underwater acoustics", 
        "autonomous underwater vehicle", "autonomous underwater vehicles",
        "multibeam sonar",
        "marine technology", "ocean observation", "oceanographic",
        "underwater vehicle", "underwater vehicles",
        "subsea", "subsea technology",
        "underwater positioning", "marine robotics",
        "kelp forest", "kelp forests", "mangrove forest", "mangrove forests",
        "mangrove", "mangroves",

        # Swedish Terms
        "kelpskog", "kelpskogar", "kelpskogen", "kelpskogarna",
        "tångskog", "tångskogar", "tångskogen", "tångskogarna",
        "mangroveskog", "mangroveskogar", "mangroveskogen", "mangroveskogarna",
        "mangrove", "mangrover",

        "undervattensrobotik", "undervattenspositionering",
        "undervattensfarkoster",
        "biodiversitet", "biologisk mångfald", "biologiska mångfalden", "ekosystem", "globalt mål 14 - hav och marina resurser",
        "hav och marina resurser", "marina föroreningar", "marint skräp",
        "eutrofiering", "marina och kustnära ekosystem", "havsförsurning",
        "hållbart fiske", "överfiske", "kust- och havsområden",
        "havsrättskonventionen", "blå ekonomi", "undervattensakustik",
        "undervattensteknik", "havsobservation", "marinteknik",
        ],
    15: [ # SDG 15 (Life on Land)
        # Forest Regeneration & Sustainable Forestry (Target 15.2)
        "sustainable forestry", "forest machinery", "site preparation",
        "plant regeneration",

        # Swedish Terms
        "hållbart skogsbruk", "skogsmaskiner", "skogsmaskin",
        "markberedning", "skogsföryngring", "plantering",

        "biodiversitet", "biologisk mångfald", "biologiska mångfalden", "ekosystem", "globalt mål 15 - ekosystem och biologisk mångfald",
        "ekosystem och biologisk mångfald", "landbaserade ekosystem", "sötvattensekosystem",
        "hållbart skogsbruk", "avskogning", "återplantering av skog", "markförstöring",
        "skyddade arter", "tjuvjakt", "invasiva främmande arter", "skogsbruksmetoder", "våtmarker"
        ],

    16: [ # SDG 16 (Peace, Justice, and Strong Institutions)
        # Governance, Transparency & Media Monitoring
        "open science", "transparent policymaking", "media monitoring",
        "intersectional invisibility", "open data governance",

        # Swedish Terms
        "öppen vetenskap", "transparens i styrning", "mediebevakning",
        "fred", "globalt mål 16 - fredliga och inkluderande samhällen", "institution", "rättvisa",
        "fredliga och inkluderande samhällen", "minska våld", "övergrepp och våld mot barn",
        "rättssäkerhet", "lika tillgång till rättvisa", "olagliga finansiella flöden",
        "korruption och mutor", "effektiva och transparenta institutioner", "ansvarsskyldighet",
        "representativt beslutsfattande", "allmän tillgång till information",
    ],
    17: [ # SDG 17 (Partnerships for the Goals)
        # Global Partnerships & Science Diplomacy
        "transnational education", "development-focused scholarships",

        # Capacity Building & Open Tools Transfer (Target 17.6 & 17.9)
        "capacity building", "open-source tools", "open source tools",
        "open data practices", "developing countries", "low- and middle-income countries",
        "low- and midde- income countries",  # handle abstract spelling variant
        "implementation of the paris agreement", "paris agreement",

        # Academic Partnerships, Science Cooperation & Diplomacy (Target 17.6 & 17.16)
        "internationalisation", "internationalization",
        "academic internationalisation", "academic internationalization",
        "science diplomacy", "research security", "bilateral research",
        "bilateral research funding", "international student recruitment",
        "global partnerships", "global partnership", "hybrid governance",


        # Swedish Terms
        "akademisk internationalisering", "internationalisering",
        "vetenskapsdiplomati", "forskningsdiplomati", "forskningssäkerhet",
        "bilateral forskningsfinansiering", "bilateral forskning",
        "internationell studentrekrytering",
        "skuldlättnad", "tekniköverföring", "multilaterala handelssystemet",
        "makroekonomisk stabilitet", "samstämmighet i politiken", "globala partnerskap",
        "tillförlitliga data och statistik",
        "kapacitetsuppbyggnad", "öppen källkod", "öppna data",
        "låg- och medelinkomstländer", "utvecklingsländer",
        "genomförande av parisavtalet", "parisavtalet",
        "akademisk internationalisering", 
        "globalt mål 17 - genomförande och globalt partnerskap", "koordination", "samarbete",
        "genomförande och globalt partnerskap", "inhemsk resursmobilisering", "utvecklingsbistånd",
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
    """Removes HTML and TeX artifacts and normalizes whitespace. Preserves case."""
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

    # Replace slashes with spaces to unglue compound alternatives (e.g. A/B -> A B)
    t = t.replace("/", " ")

    t = re.sub(r'([,.;:?!])([a-zA-ZåäöÅÄÖ])', r'\1 \2', t)
    return " ".join(t.split())  # <-- Removed .lower()

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

# Add tokens to exclude from SDG 14 when they lack marine context
SDG14_EXCLUDED_TERMS = {
    "navigation", "sensor fusion", "perception", "trajectory", "control technologies",
    "forest",  # Prevents kelp/mangrove forest fragments from matching terrestrial forestry
}

def build_dual_language_term_index() -> dict[int, list[str]]:
    """Builds the comprehensive SDG keyword index across English, Swedish, and extensions."""
    mapping = {}
    for i in range(1, 18):
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

            # Suppress standalone 'forest' in SDG 14
            if i == 14 and t_clean in SDG14_EXCLUDED_TERMS:
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
    sdg_terms = build_dual_language_term_index()
    counts: dict[int, int] = {i: 0 for i in range(1, 18)}
    matched_phrases: dict[int, list[tuple[str, int]]] = {i: [] for i in range(1, 18)}

    # Track 1: Case-Sensitive Acronym Matching
    for sdg_num, acronyms in ACRONYMS_SDG_TERMS.items():
        acronym_counter = Counter()
        for acr in acronyms:
            pattern = rf"\b{re.escape(acr)}\b"
            matches = len(re.findall(pattern, text))
            if matches > 0:
                acronym_counter[acr] += matches

        counts[sdg_num] += sum(acronym_counter.values())
        matched_phrases[sdg_num].extend(acronym_counter.most_common(10))

    # Track 2: Case-Insensitive General Phrase Matching
    lower_text = f" {text.lower()} "
    for sdg_num, terms in sdg_terms.items():
        counter = Counter()
        for phrase in terms:
            if len(phrase) < 3:
                continue
            escaped = re.escape(phrase.lower())
            
            # Allow single-word Swedish roots (length >= 6) to match compound headwords
            # e.g., 'livscykel' matches 'livscykelresistenta'
            if " " not in phrase and len(phrase) >= 6:
                pattern = rf"(?<!\w){escaped}"
            else:
                pattern = rf"(?<!\w){escaped}(?!\w)"

            matches = len(re.findall(pattern, lower_text))
            if matches > 0:
                counter[phrase] += matches

        counts[sdg_num] += sum(counter.values())
        matched_phrases[sdg_num].extend(counter.most_common(10))

    # Track 3: STRINGS co-occurrence rules
    strings_counts, strings_matched = evaluate_strings_rules(lower_text)
    for g in range(1, 18):
        counts[g] += strings_counts[g]
        matched_phrases[g].extend(strings_matched[g])

    # Track 4: Explicit author declarations
    explicit_mentions = extract_explicit_sdg_mentions(text)

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

# In main():
    doc_data = None
    is_json = False

    if args.input_file.suffix.lower() == ".pdf":
        text = extract_from_pdf(args.input_file)
    else:
        # Check if the content is valid JSON regardless of file extension
        try:
            with open(args.input_file, "r", encoding="utf-8") as f:
                raw_content = f.read()
                doc_data = json.loads(raw_content)
                is_json = True
        except (json.JSONDecodeError, UnicodeDecodeError):
            is_json = False

        if is_json:
            text, doc_data = extract_from_dual_language_json(args.input_file)
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
