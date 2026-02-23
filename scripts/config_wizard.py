#!/usr/bin/python3
# -*- coding: utf-8 -*-
# -*- mode: python; python-indent-offset: 4 -*-
# run the script locally with streamlit run ./scripts/config_wizard.py
# this will run the script with your local browser: http://localhost:8501
# fill in the form, then upload the config_snippet.tex file to your repository
# This will trigger the .github/workflows/merge_wizard.yml to run.
# This workflow will merge your snippet values into custom_configuration.tex at the repository.


import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
import json
import os

# --- 1. CONFIGURATION & BILINGUAL DATA ---
STATE_FILE = "wizard_session.json"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

TRANSLATIONS = {
    'English': {
        'title': "🎓 KTH Thesis Template Configuration Wizard",
        'lang_label': "Select Language",
        'auth_header': "1. Author & Thesis Information",
        'sup_header': "2. Supervisors",
        'out_header': "3. LaTeX Generation",
        'user_label': "KTH Username",
        'fetch_btn': "Fetch Data",
        'edit_expander': "Edit Details",
        'fname': "First Name", 'lname': "Last Name", 'email': "Email",
        'kthid': "KTH ID", 'school': "School Acronym", 'edu_code': "Education Subject",
        'thesis_title': "Thesis Title", 'thesis_subtitle': "Subtitle",
        'save_btn': "Save Information",
        'add_kth_sup': "Add KTH Supervisor", 'add_ext_sup': "Add External Supervisor",
        'ext_org': "Organization (University, Dept)", 'add_btn': "Add",
        'curr_list': "Current Supervisors", 'rem_btn': "Remove",
        'reset_btn': "Clear All Data (Reset)", 'dl_btn': "Download .tex snippet",
        'info_saved': "Session data saved to:",
        'degree_label': "Degree Name", 'modifier_label': "Modifier (Philosophy)",
    },
    'Swedish': {
        'title': "🎓 Konfigurationsguide för KTH:s examensarbetesmall",
        'lang_label': "Välj språk",
        'auth_header': "1. Författar- & avhandlingsinformation",
        'sup_header': "2. Handledare",
        'out_header': "3. LaTeX-generering",
        'user_label': "KTH-användarnamn",
        'fetch_btn': "Hämta data",
        'edit_expander': "Redigera detaljer",
        'fname': "Förnamn", 'lname': "Efternamn", 'email': "E-post",
        'kthid': "KTH-ID", 'school': "Skolakronym", 'edu_code': "Utbildningsämne",
        'thesis_title': "Uppsatsens titel", 'thesis_subtitle': "Undertitel",
        'save_btn': "Spara information",
        'add_kth_sup': "Lägg till KTH-handledare", 'add_ext_sup': "Lägg till extern handledare",
        'ext_org': "Organisation (Universitet, avd)", 'add_btn': "Lägg till",
        'curr_list': "Nuvarande handledare", 'rem_btn': "Ta bort",
        'reset_btn': "Rensa all data (Återställ)", 'dl_btn': "Ladda ner .tex-fil",
        'info_saved': "Sessionsdata sparad i:",
        'degree_label': "Examensnamn", 'modifier_label': "Tillägg (Philosophy)",
    }
}

SCHOOL_MAP_letters = {'a': "ABE", 'm': "ITM", 's': "SCI", 'c': "CBH", 'j': "EECS"}

EDUCATION_CODES = {
    'ARKITEKT': {'swe': "Arkitektur", 'eng': "Architecture"},
    'BIOLFYS': {'swe': "Biologisk fysik", 'eng': "Biological Physics"},
    'BIOTEKN': {'swe': "Bioteknologi", 'eng': "Biotechnology"},
    'BYV': {'swe': "Byggvetenskap", 'eng': "Civil and Architectural Engineering"},
    'DATALGEM': {'swe': "Datalogi", 'eng': "Computer Science"},
    'DATALOGI': {'swe': "Datalogi", 'eng': "Computer Science"},
    'ELSYSETS': {'swe': "Elektro- och systemteknik", 'eng': "Electrical Engineering"},
    'ELSYSGEM': {'swe': "Elektro- och systemteknik", 'eng': "Electrical Engineering"},
    'ELSYTEKN': {'swe': "Elektro- och systemteknik", 'eng': "Electrical Engineering"},
    'ENERGIT': {'swe': "Energiteknik", 'eng': "Energy Technology"},
    'FARKTE': {'swe': "Farkostteknik", 'eng': "Vehicle and Maritime Engineering"},
    'FASTBYGG': {'swe': "Fastigheter och byggande", 'eng': "Real Estate and Construction Management"},
    'FIBERPOL': {'swe': "Fiber- och polymervetenskap", 'eng': "Fibre and Polymer Science"},
    'FILOSOFI': {'swe': "Filosofi", 'eng': "Philosophy"},
    'FLYGRYMD': {'swe': "Flyg- och rymdteknik", 'eng': "Aerospace Engineering"},
    'FYSIK': {'swe': "Fysik", 'eng': "Physics"},
    'BUSINADM': {'swe': "Företagsekonomi", 'eng': "Business Studies"}, 
    'GEODEINF': {'swe': "Geodesi och geoinformatik", 'eng': "Geodesy and Geoinformatics"},
    'ISTTVM': {'swe': "Historiska studier av teknik, vetenskap och miljö", 'eng': "History of Science, Technology and Environment"}, 
    'HALLBST': {'swe': "Hållbarhetsstudier", 'eng': "Sustainability studies"},
    'HÅLLF': {'swe': "Hållfasthetslära", 'eng': "Solid Mechanics"}, 
    'INDEKOL': {'swe': "Industriell ekologi", 'eng': "Industrial Ecology"},
    'INDEKO': {'swe': "Industriell ekonomi och organisation", 'eng': "Industrial Engineering and Management"},
    'INDPROD': {'swe': "Industriell produktion", 'eng': "Production Engineering"},
    'INFKTEKN': {'swe': "Informations- och kommunikationsteknik", 'eng': "Information and Communication Technology"}, 
    'INFKTGEM': {'swe': "Informations- och kommunikationsteknik", 'eng': "Information and Communication Technology"},
    'KEMI': {'swe': "Kemi", 'eng': "Chemistry"},
    'KEMITEKN': {'swe': "Kemiteknik", 'eng': "Chemical Engineering"}, 
    'MASKINKO': {'swe': "Maskinkonstruktion", 'eng': "Machine Design"},
    'MATTE': {'swe': "Matematik", 'eng': "Mathematics"},
    'MEDICINT': {'swe': "Medicinsk teknologi", 'eng': "Medical Technology"}, 
    'MEDIAT': {'swe': "Medieteknik", 'eng': "Media Technology"},
    'MILJOTEK': {'swe': "Miljöteknik", 'eng': "Environmental Engineering"},
    'MÄNDATOR': {'swe': "Människa-datorinteraktion", 'eng': "Human-computer Interaction"}, 
    'NATIEKON': {'swe': "Nationalekonomi", 'eng': "Economics"}, 
    'SAMHPLAN': {'swe': "Samhällsplanering", 'eng': "Urban and Regional Planning"},
    'TALMUKOM': {'swe': "Tal- och musikkommunikation", 'eng': "Speech and Music Communication"}, 
    'TEKHÄLSA': {'swe': "Teknik och hälsa", 'eng': "Technology and Health"},
    'TECLEARN': {'swe': "Teknik och lärande", 'eng': "Technology and Learning"},
    'TEVLKOMM': {'swe': "Teknikvetenskapens lärande och kommunikation", 'eng': "Education and Communication in the Technological Sciences"},
    'TEMATRVE': {'swe': "Teknisk materialvetenskap", 'eng': "Materials Science and Engineering"}, 
    'TEMEKAN': {'swe': "Teknisk mekanik", 'eng': "Engineering Mechanics"},
    'TKEMIBIO': {'swe': "Teoretisk kemi och biologi", 'eng': "Theoretical Chemistry and Biology"},
    'TILLFYS': {'swe': "Tillämpad fysik", 'eng': "Applied Physics"},
    'TIMABEMA': {'swe': "Tillämpad matematik och beräkningsmatematik", 'eng': "Applied and Computational Mathematics"},
    'TRANGEM': {'swe': "Transportvetenskap", 'eng': "Transport Science"},
    'TRANSPVP': {'swe': "Transportvetenskap", 'eng': "Transport Science"},
    'VATTVTEK': {'swe': "Vattenvårdsteknik", 'eng': "Water Resources Engineering"},
    'KTHXXX': {'swe': "*****Okänt ämneområde*****", 'eng': "*****Unknown subject area*****"}
}

# --- 2. INITIALIZE SESSION STATE ---
if "author" not in st.session_state: st.session_state["author"] = None
if "supervisors" not in st.session_state: st.session_state["supervisors"] = []
if "initialized" not in st.session_state: st.session_state["initialized"] = False
if "edu_code" not in st.session_state: st.session_state["edu_code"] = "KTHXXX"
if "degree_name" not in st.session_state: st.session_state["degree_name"] = "Doctorate"
if "degree_modifier" not in st.session_state: st.session_state["degree_modifier"] = False
if "thesis_title" not in st.session_state: st.session_state["thesis_title"] = ""
if "thesis_subtitle" not in st.session_state: st.session_state["thesis_subtitle"] = ""

# --- 3. LOGIC FUNCTIONS ---
def save_state():
    state = {
        "author": st.session_state["author"], 
        "supervisors": st.session_state["supervisors"], 
        "edu_code": st.session_state["edu_code"],
        "degree_name": st.session_state["degree_name"],
        "degree_modifier": st.session_state["degree_modifier"],
        "thesis_title": st.session_state["thesis_title"],
        "thesis_subtitle": st.session_state["thesis_subtitle"]
    }
    with open(STATE_FILE, "w") as f: json.dump(state, f)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                saved = json.load(f)
                for key, val in saved.items():
                    st.session_state[key] = val
        except: pass

def get_kth_person_info(username):
    try:
        url = f"https://www.kth.se/profile/{username}/"
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code != 200: return None, "Profile not found."
        html_content = res.text
        soup = BeautifulSoup(html_content, 'html.parser')
        kthid = None
        kthid_elem = soup.find(attrs={"class": "kthId"})
        if kthid_elem: kthid = kthid_elem.get_text(strip=True)
        if not kthid:
            span_match = re.search(r'<span class="kthId">(u1[a-z0-9]+)</span>', html_content)
            if span_match: kthid = span_match.group(1)
        if not kthid:
            match = re.search(r'u1[a-z0-9]{6}', html_content)
            kthid = match.group(0) if match else "u1XXXXXX"
        
        email_elem = soup.find('a', href=re.compile(r'mailto:'))
        email = email_elem.get_text(strip=True) if email_elem else ""
        works_for = soup.find('div', class_='worksforWrapper')
        if works_for:
            dept_name = works_for.find('a').get_text(strip=True)
            dir_link = works_for.find('a')['href']
            school_letter = re.search(r'/directory/([a-z])/', dir_link.lower())
            school_acronym = SCHOOL_MAP_letters.get(school_letter.group(1), "XXX") if school_letter else "XXX"
            dir_res = requests.get(dir_link, headers=HEADERS, timeout=10)
            dir_soup = BeautifulSoup(dir_res.text, 'html.parser')
            for row in dir_soup.find_all('tr'):
                e_td = row.find('td', class_='email')
                if e_td and email in e_td.get_text():
                    return {
                        "kthid": kthid, "email": email, "is_kth": True, "dept": dept_name, "school": school_acronym,
                        "fname": row.find('td', class_='firstname').get_text(strip=True),
                        "lname": row.find('td', class_='lastname').get_text(strip=True)
                    }, None
        return None, "Not found in directory."
    except Exception as e: return None, str(e)

# --- 4. UI SETUP ---
st.set_page_config(page_title="KTH Config Wizard", layout="wide")
if not st.session_state["initialized"]:
    load_state()
    st.session_state["initialized"] = True

lang = st.radio("Language / Språk", ["English", "Swedish"], horizontal=True)
t = TRANSLATIONS[lang]
st.title(t['title'])

# --- AUTHOR SECTION ---
st.header(t['auth_header'])

# Degree Config - Closer columns
deg_col1, deg_col2, _ = st.columns([1.5, 1, 2])
with deg_col1:
    st.session_state["degree_name"] = st.radio(t['degree_label'], ["Doctorate", "Licentiate"], 
        index=0 if st.session_state["degree_name"] == "Doctorate" else 1, horizontal=True)
with deg_col2:
    st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True) # Small padding alignment
    st.session_state["degree_modifier"] = st.checkbox(t['modifier_label'], value=st.session_state["degree_modifier"])

# Author Fetch/Manual
c1, c2 = st.columns([1, 2])
with c1:
    auth_user = st.text_input(t['user_label'], key="auth_input")
    if st.button(t['fetch_btn']):
        data, err = get_kth_person_info(auth_user)
        if data: st.session_state['author'] = data; save_state(); st.rerun()
        else: st.error(err)

if st.session_state['author']:
    a = st.session_state['author']
    with c2:
        with st.expander(t['edit_expander'], expanded=True):
            st.session_state["thesis_title"] = st.text_input(t['thesis_title'], value=st.session_state["thesis_title"])
            st.session_state["thesis_subtitle"] = st.text_input(t['thesis_subtitle'], value=st.session_state["thesis_subtitle"])
            st.divider()
            a['fname'] = st.text_input(t['fname'], a['fname'])
            a['lname'] = st.text_input(t['lname'], a['lname'])
            a['email'] = st.text_input(t['email'], a['email'])
            a['kthid'] = st.text_input(t['kthid'], a['kthid'])
            a['school'] = st.text_input(t['school'], a['school'])
            
            curr_code = st.session_state['edu_code']
            idx = list(EDUCATION_CODES.keys()).index(curr_code) if curr_code in EDUCATION_CODES else 0
            st.session_state['edu_code'] = st.selectbox(t['edu_code'], list(EDUCATION_CODES.keys()), index=idx)
            
            if st.button(t['save_btn']): save_state(); st.success("Saved")

# --- SUPERVISORS ---
st.header(t['sup_header'])
cs1, cs2 = st.columns([1, 2])
with cs1:
    s_user = st.text_input(t['user_label'], key="sup_input")
    if st.button(t['fetch_btn'], key="sup_fetch"):
        data, err = get_kth_person_info(s_user)
        if data: st.session_state['supervisors'].append(data); save_state(); st.rerun()
        else: st.error(err)
with cs2:
    with st.expander(t['add_ext_sup']):
        ef, el, ee, eo = st.text_input(t['fname']), st.text_input(t['lname']), st.text_input(t['email']), st.text_input(t['ext_org'])
        if st.button(t['add_btn']):
            st.session_state['supervisors'].append({"fname": ef, "lname": el, "email": ee, "org": eo, "is_kth": False})
            save_state(); st.rerun()

if st.session_state['supervisors']:
    st.subheader(t['curr_list'])
    to_del = None
    for i, s in enumerate(st.session_state['supervisors']):
        cols = st.columns([5, 1])
        cols[0].write(f"**{chr(65+i)}:** {s['fname']} {s['lname']} ({s.get('kthid', 'Ext')})")
        if cols[1].button(t['rem_btn'], key=f"r{i}"): to_del = i
    if to_del is not None: st.session_state['supervisors'].pop(to_del); save_state(); st.rerun()

# --- OUTPUT ---
st.header(t['out_header'])
if st.button(t['reset_btn']):
    if os.path.exists(STATE_FILE): os.remove(STATE_FILE)
    st.session_state.clear(); st.rerun()

if st.session_state['author']:
    a, ed = st.session_state['author'], st.session_state['edu_code']
    out = f"\\title{{{st.session_state['thesis_title']}}}\n"
    out += f"\\subtitle{{{st.session_state['thesis_subtitle']}}}\n\n"
    out += f"\\courseCycle{{3}}\n\\educationSubjectcode{{{ed}}}\n\\subjectArea{{\\educationcodeToString{{{ed}}}}}\n"
    out += f"\\degreeName{{{st.session_state['degree_name']}}}\n"
    if st.session_state['degree_modifier']: out += f"\\degreeModifier{{Philosophy}}\n"
    out += f"\n\\authorsFirstname{{{a['fname']}}}\n\\authorsLastname{{{a['lname']}}}\n\\email{{{a['email']}}}\n\\kthid{{{a['kthid']}}}\n"
    out += f"\\authorsSchool{{\\schoolAcronym{{{a['school']}}}}}\n\n"
    for i, s in enumerate(st.session_state['supervisors']):
        l = chr(65+i)
        out += f"\\supervisor{l}sFirstname{{{s['fname']}}}\n\\supervisor{l}sLastname{{{s['lname']}}}\n\\supervisor{l}sEmail{{{s['email']}}}\n"
        if s.get('is_kth'): out += f"\\supervisor{l}sKTHID{{{s['kthid']}}}\n\\supervisor{l}sSchool{{\\schoolAcronym{{{s['school']}}}}}\n\\supervisor{l}sDepartment{{{s['dept']}}}\n\n"
        else: out += f"\\supervisor{l}sOrganization{{{s['org']}}}\n\n"
    st.code(out, language='latex')
    st.download_button(t['dl_btn'], out, "config_snippet.tex")
