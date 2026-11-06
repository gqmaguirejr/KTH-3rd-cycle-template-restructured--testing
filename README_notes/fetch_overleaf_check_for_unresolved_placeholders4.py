#!/usr/bin/env python3
"""
fetch_overleaf_check_for_unresolved_placeholders4.py

Automates Overleaf log retrieval and audits for unresolved BibLaTeX placeholder
references. Programmatically discovers all MongoDB 24-hex ObjectIds directly
from Overleaf's /updates and /entities endpoints without requiring manual
JSON mapping files or browser developer window scripts.
"""

import os
import re
import sys
import uuid
import requests

try:
    import browser_cookie3
except ImportError:
    browser_cookie3 = None

TARGET_BANNER = "UNRESOLVED PLACEHOLDER REFERENCES DETECTED IN THIS BUILD:"
PLACEHOLDER_MARKER = "Unresolved placeholder reference"

FallBack_Project_ID="000000000000000000000000" # replace with your project ID
FallBack_CLSIserver="clsi-cache-zone-b-prod-3" # replace with your CLSI server

def get_authenticated_session(project_id: str) -> tuple[requests.Session, str]:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://www.overleaf.com/project/{project_id}",
        "Origin": "https://www.overleaf.com",
        "Accept": "application/json, text/plain, */*",
    })

    cookie_val = os.environ.get("OVERLEAF_SESSION_COOKIE")

    if not cookie_val and browser_cookie3:
        try:
            cj = browser_cookie3.chrome(domain_name=".overleaf.com")
            session.cookies.update(cj)
            for c in cj:
                if c.name == "overleaf_session2":
                    cookie_val = c.value
                    break
        except Exception as e:
            sys.stderr.write(f"Note: browser_cookie3: {e}\n")

    if cookie_val:
        if "overleaf_session2=" in cookie_val:
            cookie_val = cookie_val.split("overleaf_session2=")[1].split(";")[0].strip()
        session.cookies.set("overleaf_session2", cookie_val, domain=".overleaf.com", path="/")

    res = session.get(f"https://www.overleaf.com/project/{project_id}")
    if res.status_code == 403 or "login" in res.url:
        sys.stderr.write("Authentication failed. Ensure you are logged into Overleaf in Chrome.\n")
        sys.exit(1)

    csrf_token = None
    for pat in [
        r'window\.csrfToken\s*=\s*["\']([^"\']+)["\']',
        r'<meta\s+name=["\']ol-csrfToken["\']\s+content=["\']([^"\']+)["\']',
        r'"_csrf"\s*:\s*["\']([^"\']+)["\']',
    ]:
        m = re.search(pat, res.text)
        if m:
            csrf_token = m.group(1)
            break

    if not csrf_token:
        sys.stderr.write("Failed to retrieve CSRF token from project page.\n")
        sys.exit(1)

    return session, csrf_token


def fetch_project_doc_mapping(session: requests.Session, project_id: str, csrf_token: str) -> dict[str, str]:
    """
    Queries Overleaf's /updates endpoint to dynamically extract the mapping between
    file relative paths and their 24-hex MongoDB ObjectIds.
    """
    mapping = {}
    headers = {
        "X-Csrf-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json",
    }

    try:
        r = session.get(f"https://www.overleaf.com/project/{project_id}/updates", headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            updates = data.get("updates", [])
            for u in updates:
                # Overleaf update records store pathnames and document/blob metadata
                pathnames = u.get("pathnames", [])
                
                # Check for explicit docs/files lists inside updates
                docs = u.get("docs", []) or u.get("meta", {}).get("docs", [])
                for d in docs:
                    d_id = d.get("id") or d.get("_id")
                    d_path = d.get("pathname") or d.get("path")
                    if d_id and d_path:
                        clean_p = d_path.lstrip("/")
                        mapping[clean_p] = d_id
                        mapping[os.path.basename(clean_p)] = d_id

                # Inspect patches/ops where doc IDs are paired with paths
                patches = u.get("patches", [])
                for p in patches:
                    d_id = p.get("doc") or p.get("doc_id")
                    d_path = p.get("pathname") or p.get("path")
                    if d_id and d_path:
                        clean_p = d_path.lstrip("/")
                        mapping[clean_p] = d_id
                        mapping[os.path.basename(clean_p)] = d_id

                # If updates associate a single doc_id with pathnames
                u_doc_id = u.get("doc") or u.get("doc_id") or u.get("id")
                if u_doc_id and re.fullmatch(r"[a-f0-9]{24}", str(u_doc_id)):
                    for path in pathnames:
                        clean_p = path.lstrip("/")
                        mapping[clean_p] = u_doc_id
                        mapping[os.path.basename(clean_p)] = u_doc_id

            # Fallback regex scan over the payload for any "pathname":"...", "id":"..." structures
            if not mapping:
                for match in re.finditer(
                    r'(?:pathname|path)["\']?\s*:\s*["\']([^"\']+)["\'].*?(?:id|_id|doc)["\']?\s*:\s*["\']([a-f0-9]{24})["\']',
                    r.text
                ):
                    p, did = match.group(1).lstrip("/"), match.group(2)
                    mapping[p] = did
                    mapping[os.path.basename(p)] = did
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to fetch updates mapping: {e}\n")

    return mapping


def fetch_project_paths(session: requests.Session, project_id: str, csrf_token: str) -> list[str]:
    """Retrieves canonical paths from the /entities endpoint."""
    headers = {
        "X-Csrf-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json",
    }
    try:
        r = session.get(
            f"https://www.overleaf.com/project/{project_id}/entities?projectId={project_id}",
            headers=headers,
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            entities = data.get("entities", [])
            return [e.get("path", "").lstrip("/") for e in entities if "path" in e]
    except Exception:
        pass
    return []


def resolve_file_target(
    arg: str | None,
    mapping: dict[str, str],
    known_paths: list[str],
) -> tuple[str | None, str | None]:
    """Resolves an input argument to (root_doc_id, root_resource_path)."""
    if not arg:
        return None, None

    clean = arg.strip().lstrip("./")

    # Invert mapping to support doc_id -> canonical path
    id_to_path = {}
    for p, did in mapping.items():
        if "/" in p:
            id_to_path[did] = p
    for p, did in mapping.items():
        id_to_path.setdefault(did, p)

    # 1. Direct 24-character hexadecimal MongoDB ObjectId
    if re.fullmatch(r"[a-f0-9]{24}", clean):
        doc_id = clean
        path = id_to_path.get(doc_id)
        if not path:
            path = next((p for p in known_paths if os.path.basename(p) == clean), None)
        return doc_id, path

    # 2. Exact match in mapping
    base = os.path.basename(clean)
    doc_id = mapping.get(clean) or mapping.get(base)

    # 3. Substring / fuzzy match across mapped paths and known entities
    if not doc_id:
        candidates = {k: v for k, v in mapping.items() if base.lower() in k.lower()}
        unique_ids = set(candidates.values())
        if len(unique_ids) == 1:
            doc_id = next(iter(unique_ids))
            clean = max([k for k in candidates if candidates[k] == doc_id], key=len)
        elif len(unique_ids) > 1:
            sys.stderr.write(f"Ambiguous match for '{arg}':\n")
            for k in sorted(candidates.keys()):
                sys.stderr.write(f"  * {k}\n")
            sys.exit(1)

    # 4. Resolve relative canonical directory path
    full_path = id_to_path.get(doc_id)
    if not full_path:
        for p in known_paths:
            if os.path.basename(p) == base or p == clean:
                full_path = p
                break

    if not full_path:
        full_path = clean if "/" in clean else base

    return doc_id, full_path


def fetch_latest_log(
    session: requests.Session,
    project_id: str,
    csrf_token: str,
    root_doc_id: str | None = None,
    root_resource_path: str | None = None,
) -> str:
    compile_url = f"https://www.overleaf.com/project/{project_id}/compile?enable_pdf_caching=true"
    headers = {
        "Content-Type": "application/json",
        "X-Csrf-Token": csrf_token,
    }

    payload = {
        "draft": False,
        "png2pdf": True,
        "incrementalCompilesEnabled": True,
        "stopOnFirstError": False,
        "editorId": str(uuid.uuid4()),
    }

    # Pass the resolved MongoDB ObjectId and canonical resource path
    if root_doc_id:
        payload["rootDoc_id"] = root_doc_id
    if root_resource_path:
        payload["rootResourcePath"] = root_resource_path

    # Only request a silent check when building the default root document
    if not root_doc_id and not root_resource_path:
        payload["check"] = "silent"

    compile_res = session.post(compile_url, headers=headers, json=payload)
    if compile_res.status_code != 200:
        sys.stderr.write(f"POST compile failed: HTTP {compile_res.status_code}\n")
        sys.stderr.write(compile_res.text[:300] + "\n")
        sys.exit(1)

    data = compile_res.json()
    output_files = data.get("outputFiles", [])

    log_entry = next((f for f in output_files if f.get("path") == "output.log"), None)
    if not log_entry:
        sys.stderr.write("No output.log found in compile artifacts.\n")
        sys.stderr.write(f"Compile status: {data.get('status')}\n")
        sys.exit(1)

    params = {
        "clsiserverid": data.get("clsiServerId", FallBack_CLSIserver"),
        "compileGroup": data.get("compileGroup", "priority"),
        "editorId": payload["editorId"],
    }

    cdn_domain = data.get(
        "pdfDownloadDomain", "https://compiles.overleafusercontent.com"
    ).rstrip("/")

    # Primary URL directly to Overleaf usercontent CDN
    cdn_url = f"{cdn_domain}{log_entry['url']}"
    res = session.get(cdn_url, params=params)
    if res.status_code == 200:
        return res.text

    # Fallback to main host
    main_url = f"https://www.overleaf.com{log_entry['url']}"
    res = session.get(main_url, params=params)
    if res.status_code == 200:
        return res.text

    sys.stderr.write(f"Failed to fetch output.log (HTTP {res.status_code}).\n")
    sys.exit(1)


def audit_placeholders(log_content: str):
    has_marker = PLACEHOLDER_MARKER in log_content
    has_banner = TARGET_BANNER in log_content

    if not (has_marker or has_banner):
        sys.stdout.write("✅ Clean build: No unresolved placeholder references.\n")
        sys.exit(0)

    wrapped_re = re.compile(
        r"Package biblatex Warning:\s*Unresolved placeholder reference\s*'(?P<key>[^']+)':\s*(?P<note>[^\n]+(?:\n[^\n]+)*?\.)(?:\s+(?:Underfull|Overfull|Package|\n|\[|$))"
    )
    matches = list(wrapped_re.finditer(log_content))

    detected = {}
    if matches:
        for w in matches:
            key = w.group("key")
            note = " ".join(w.group("note").split()).rstrip(".")
            detected.setdefault(key, note)
    else:
        for line in log_content.splitlines():
            if PLACEHOLDER_MARKER in line:
                m = re.search(r"reference\s*'([^']+)':\s*(.*?)(?:\.|$)", line)
                if m:
                    detected.setdefault(m.group(1), m.group(2).strip().rstrip("."))

    total_occurrences = len(matches) if matches else len(detected)
    sys.stdout.write(
        f"\n❌ UNRESOLVED PLACEHOLDERS DETECTED ({len(detected)} unique keys, {total_occurrences} total citations):\n"
    )
    for key, note in sorted(detected.items()):
        sys.stdout.write(f"  * [{key}] {note}\n")

    sys.stdout.write("\n")
    sys.exit(2)


def main():
    project_id = os.environ.get("OVERLEAF_PROJECT_ID") or FallBack_Project_ID
    target_arg = sys.argv[1].strip() if len(sys.argv) > 1 else None

    session, csrf_token = get_authenticated_session(project_id)
    known_paths = fetch_project_paths(session, project_id, csrf_token)
    doc_mapping = fetch_project_doc_mapping(session, project_id, csrf_token)

    root_id, resource_path = resolve_file_target(target_arg, doc_mapping, known_paths)

    if target_arg:
        sys.stdout.write(f"Target Resolved:\n  * rootDoc_id: {root_id}\n  * rootResourcePath: {resource_path}\n\n")

    log_text = fetch_latest_log(
        session=session,
        project_id=project_id,
        csrf_token=csrf_token,
        root_doc_id=root_id,
        root_resource_path=resource_path,
    )
    audit_placeholders(log_text)


if __name__ == "__main__":
    main()
