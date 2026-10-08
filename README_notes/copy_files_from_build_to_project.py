#!/usr/bin/env python3
"""
copy_files_from_build_to_project.py

Compiles an Overleaf sub-document or project root, downloads generated build
artifacts (e.g. output.pdf, fordiva.json, referencesUsed.bib, citedtags.bib)
from the CLSI build container, and persists them into the project tree.
"""

import io
import os
import re
import sys
import time
import uuid
import requests

try:
    import browser_cookie3
except ImportError:
    browser_cookie3 = None


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
        r'<meta\s+name=["\']ol-csrfToken["\']\s+content=["\']([^"\']+)["\']',
        r'window\.csrfToken\s*=\s*["\']([^"\']+)["\']',
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


def fetch_project_paths(session: requests.Session, project_id: str, csrf_token: str) -> list[str]:
    headers = {
        "X-Csrf-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json",
    }
    try:
        r = session.get(
            f"https://www.overleaf.com/project/{project_id}/entities?projectId={project_id}",
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            return [e.get("path", "").lstrip("/") for e in data.get("entities", []) if "path" in e]
    except Exception:
        pass
    return []


def get_or_create_destination_folder(
    session: requests.Session,
    project_id: str,
    csrf_token: str,
    base_folder_name: str = "artifacts",
) -> tuple[str, str]:
    """
    Creates a destination folder on the project root and returns (folder_name, folder_id).
    If base_folder_name already exists, falls back to a timestamped folder name
    to guarantee a freshly allocated, valid MongoDB ObjectId.
    """
    headers = {
        "X-Csrf-Token": csrf_token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    candidates = [
        base_folder_name,
        f"{base_folder_name}_{int(time.time())}",
    ]

    for fname in candidates:
        post_res = session.post(
            f"https://www.overleaf.com/project/{project_id}/folder",
            headers=headers,
            json={"name": fname},
            timeout=10,
        )
        if post_res.status_code in (200, 201):
            data = post_res.json()
            fid = data.get("_id")
            if fid:
                return fname, fid

    sys.stderr.write(f"Error: Unable to create destination folder in Overleaf.\n")
    sys.exit(1)


def compile_and_get_artifacts(
    session: requests.Session,
    project_id: str,
    csrf_token: str,
    root_resource_path: str | None = None,
) -> tuple[str, list[dict], dict]:
    compile_url = f"https://www.overleaf.com/project/{project_id}/compile?enable_pdf_caching=true"
    headers = {
        "Content-Type": "application/json",
        "X-Csrf-Token": csrf_token,
    }

    editor_id = str(uuid.uuid4())
    payload = {
        "draft": False,
        "png2pdf": True,
        "incrementalCompilesEnabled": True,
        "stopOnFirstError": False,
        "editorId": editor_id,
    }

    if root_resource_path:
        payload["rootResourcePath"] = root_resource_path
    else:
        payload["check"] = "silent"

    compile_res = session.post(compile_url, headers=headers, json=payload)
    if compile_res.status_code != 200:
        sys.stderr.write(f"POST compile failed: HTTP {compile_res.status_code}\n")
        sys.stderr.write(compile_res.text[:300] + "\n")
        sys.exit(1)

    data = compile_res.json()
    status = data.get("status", "unknown")
    routing = {
        "clsiServerId": data.get("clsiServerId", FallBack_CLSIserver),
        "compileGroup": data.get("compileGroup", "priority"),
        "editorId": editor_id,
        "pdfDownloadDomain": data.get("pdfDownloadDomain", "https://compiles.overleafusercontent.com").rstrip("/"),
    }
    return status, data.get("outputFiles", []), routing


def download_build_artifact(
    session: requests.Session,
    artifact_url: str,
    routing: dict,
) -> bytes | None:
    params = {
        "clsiserverid": routing["clsiServerId"],
        "compileGroup": routing["compileGroup"],
        "editorId": routing["editorId"],
    }

    cdn_url = f"{routing['pdfDownloadDomain']}{artifact_url}"
    res = session.get(cdn_url, params=params)
    if res.status_code == 200:
        return res.content

    main_url = f"https://www.overleaf.com{artifact_url}"
    res = session.get(main_url, params=params)
    if res.status_code == 200:
        return res.content

    return None


def upload_to_folder(
    session: requests.Session,
    project_id: str,
    csrf_token: str,
    file_bytes: bytes,
    dest_name: str,
    folder_id: str,
) -> bool:
    upload_url = f"https://www.overleaf.com/project/{project_id}/upload"
    
    # 1. folder_id MUST be in query params, not body
    params = {
        "folder_id": folder_id,
    }
    
    headers = {
        "X-Csrf-Token": csrf_token,
        "Accept": "application/json",
    }
    
    # 2. Body must contain ONLY 'name' (FineUploader uses this, validator allows this)
    data = {
        "name": dest_name,
    }
    
    # 3. File tuple: (filename, stream, mime_type)
    files = {
        "qqfile": (dest_name, io.BytesIO(file_bytes), "application/octet-stream"),
    }

    res = session.post(upload_url, headers=headers, params=params, data=data, files=files)
    if res.status_code in (200, 201):
        try:
            return res.json().get("success", True)
        except Exception:
            return True

    sys.stderr.write(f"Failed to upload '{dest_name}': HTTP {res.status_code} - {res.text[:300]}\n")
    return False


def main():
    if len(sys.argv) < 2:
        sys.stderr.write(
            "Usage:\n"
            "  ./copy_files_from_build_to_project.py [subdoc.tex] <artifact1> [artifact2 ...]\n"
            "  ./copy_files_from_build_to_project.py README_programmer_notes_Biber_and_BibLaTeX.tex output.pdf fordiva.json referencesUsed.bib citedtags.bib\n\n"
        )
        sys.exit(1)

    project_id = os.environ.get("OVERLEAF_PROJECT_ID") or FallBack_Project_ID

    args = sys.argv[1:]
    target_subdoc = None

    if args[0].endswith(".tex"):
        target_subdoc = args[0]
        targets = args[1:]
    else:
        targets = args

    if not targets:
        targets = ["output.pdf", "fordiva.json", "referencesUsed.bib", "citedtags.bib"]

    session, csrf_token = get_authenticated_session(project_id)
    known_paths = fetch_project_paths(session, project_id, csrf_token)

    # Dynamically resolve destination folder and ID
    folder_name, folder_id = get_or_create_destination_folder(session, project_id, csrf_token, "artifacts")

    root_resource_path = None
    if target_subdoc:
        clean_target = target_subdoc.strip().lstrip("./")
        root_resource_path = next(
            (p for p in known_paths if p == clean_target or os.path.basename(p) == os.path.basename(clean_target)),
            clean_target,
        )

    sys.stdout.write(f"Initiating compilation on Overleaf (target: {root_resource_path or 'default root'})...\n")
    status, artifacts, routing = compile_and_get_artifacts(
        session=session,
        project_id=project_id,
        csrf_token=csrf_token,
        root_resource_path=root_resource_path,
    )

    sys.stdout.write(f"Compilation Status: {status}\n")
    artifact_map = {item.get("path"): item.get("url") for item in artifacts if "path" in item and "url" in item}
    sys.stdout.write(f"Total build artifacts returned: {len(artifact_map)}\n")
    sys.stdout.write(f"Destination folder '{folder_name}' ID: {folder_id}\n\n")

    persisted_count = 0
    for target in targets:
        src_name = os.path.basename(target)
        dest_name = src_name

        matched_artifact_path = None
        if src_name in artifact_map:
            matched_artifact_path = src_name
        else:
            for art_path in artifact_map:
                if os.path.basename(art_path) == src_name:
                    matched_artifact_path = art_path
                    break

        if not matched_artifact_path and src_name.endswith(".pdf"):
            for art_path in artifact_map:
                if art_path.endswith(".pdf"):
                    matched_artifact_path = art_path
                    break

        if not matched_artifact_path:
            sys.stdout.write(f"⚠️  Artifact '{src_name}' was not produced by this build run (skipping).\n")
            continue

        url = artifact_map[matched_artifact_path]
        content = download_build_artifact(session, url, routing)

        if content is None:
            sys.stderr.write(f"❌ Failed to download artifact data for '{matched_artifact_path}'.\n")
            continue

        sys.stdout.write(f"📦 Fetched '{matched_artifact_path}' ({len(content)} bytes) from build container.\n")
        success = upload_to_folder(
            session=session,
            project_id=project_id,
            csrf_token=csrf_token,
            file_bytes=content,
            dest_name=dest_name,
            folder_id=folder_id,
        )

        if success:
            sys.stdout.write(f"✅ Successfully persisted '{folder_name}/{dest_name}' into the project tree.\n")
            persisted_count += 1

    sys.stdout.write(f"\nDone. {persisted_count}/{len(targets)} files successfully persisted to Overleaf project.\n")


if __name__ == "__main__":
    main()
