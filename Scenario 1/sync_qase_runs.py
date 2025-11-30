
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Synchronizes Test Run results from Project B (PB) to the Test Run of Project A (PA)
Using the same Custom Field 'linked_case_id_in_A' present in both projects.
Mapping order (cascade):
1) CUSTOM FIELD:
   - B -> A (preferred): field in B stores the case ID in A.
   - Fallback A -> B: field in A stores the case ID in B.
2) FALLBACK BY CASE:
   - If listing does not return values, fetch each case via GET /case/{code}/{id}.
3) FALLBACK BY TITLE:
   - If there are still no pairs, match 'title' (normalized) between A and B.
Docs:
- GET /case/{code} (lists cases; 'include' only accepts 'external_issues')
- GET /case/{code}/{id} (fetches a specific case)
- GET /custom_field?entity=case (custom fields metadata)
- POST /result/{code}/{id} (create result by Run Id)
- GET /result/{code}?run=<id> (list results by run)
"""
import os
import time
import re
import unicodedata
import requests
from requests.adapters import HTTPAdapter, Retry
from dotenv import load_dotenv

# ---- Config ----
load_dotenv()
API_TOKEN = os.getenv("QASE_API_TOKEN")
HOST = os.getenv("QASE_HOST", "qase.io")
SSL = os.getenv("QASE_SSL", "true").lower() == "true"
PROJECT_A = os.getenv("PROJECT_A_CODE")  # e.g., "PA"
PROJECT_B = os.getenv("PROJECT_B_CODE")  # e.g., "PB"
RUN_A_ID = int(os.getenv("RUN_A_ID", "0"))  # e.g., 11
RUN_B_ID = int(os.getenv("RUN_B_ID", "0"))  # e.g., 2
CUSTOM_FIELD_B_IN_A = os.getenv("CUSTOM_FIELD_B_IN_A", "linked_case_id_in_A")
CF_SOURCE = os.getenv("CF_SOURCE", "").strip().lower()  # "project_b", "project_a" or empty
BASE_URL = f"https://api.{HOST}/v1"

# ---- HTTP client with retries ----
session = requests.Session()
retries = Retry(total=5, backoff_factor=0.3, status_forcelist=[429, 500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))
session.headers.update({"Token": API_TOKEN, "Content-Type": "application/json", "Accept": "application/json"})

def qase_get(path: str, params: dict = None) -> dict:
    url = f"{BASE_URL}{path}"
    resp = session.get(url, params=params, timeout=30, verify=SSL)
    resp.raise_for_status()
    return resp.json()

def qase_post(path: str, payload: dict) -> dict:
    url = f"{BASE_URL}{path}"
    resp = session.post(url, json=payload, timeout=30, verify=SSL)
    resp.raise_for_status()
    return resp.json()

def paginate_get(path: str, params: dict = None, key: str = "result") -> list:
    """Defensive pagination."""
    items = []
    page = 1
    per_page = 100
    while True:
        merged = dict(params or {})
        merged.update({"limit": per_page, "offset": (page - 1) * per_page})
        data = qase_get(path, params=merged)
        res = data.get(key) or data
        batch = res.get("entities") or res.get("cases") or res.get("results") or []
        items.extend(batch)
        total = res.get("total", len(items))
        if len(items) >= total or not batch:
            break
        page += 1
    return items

# ---- Helpers ----
def safe_extract_int(value) -> int | None:
    """Extract integer from strings like '6', 'CASE-6', 'PA-6'."""
    if value is None:
        return None
    s = str(value).strip()
    if s.isdigit():
        return int(s)
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None

def normalize_title(s: str) -> str:
    """Normalize title for stable matching (lowercase, no accent, compressed spaces)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s

def get_custom_fields_meta() -> dict:
    """
    Returns { project_code: { key_to_id: {...}, id_to_key: {...} } } for A and B.
    Uses GET /custom_field?entity=case.
    """
    meta = {}
    try:
        data = qase_get("/custom_field", params={"entity": "case", "limit": 100, "offset": 0})
        res = data.get("result") or data
        entities = res.get("entities") or []
        for proj in (PROJECT_A, PROJECT_B):
            key_to_id, id_to_key = {}, {}
            for f in entities:
                fid = f.get("id") or f.get("field_id")
                key = f.get("key") or f.get("slug") or f.get("name")
                projects = f.get("projects") or f.get("projects_codes") or []
                if (not projects) or (proj in projects):
                    if fid and key:
                        key_to_id[key] = fid
                        id_to_key[fid] = key
            meta[proj] = {"key_to_id": key_to_id, "id_to_key": id_to_key}
    except requests.HTTPError as e:
        print(f"[WARN] Failed to read custom fields metadata: {e}")
    return meta

def extract_cf_value_from_case(case_obj: dict, target_key: str, target_field_id: int | None) -> int | None:
    """
    Attempts to obtain the custom field value from the case:
    - By matching field_id (when known)
    - By matching 'key'/'name' when present
    - Accepts varied value formats (string/number)
    """
    custom_fields = case_obj.get("custom_fields") or case_obj.get("fields") or []

    # Shape 1: list of dicts with 'field_id' and 'value'
    if isinstance(custom_fields, list):
        val = None
        if target_field_id:
            for cf in custom_fields:
                fid = cf.get("field_id") or cf.get("id")
                if fid and int(fid) == int(target_field_id):
                    val = cf.get("value") or cf.get("data") or cf.get("text")
                    break
        if val is None:
            for cf in custom_fields:
                if (cf.get("key") == target_key) or (cf.get("name") == target_key):
                    val = cf.get("value") or cf.get("data") or cf.get("text")
                    break
        return safe_extract_int(val)

    # Shape 2: simple dict {key: value}
    if isinstance(custom_fields, dict):
        val = custom_fields.get(target_key)
        if val is None and target_field_id:
            val = custom_fields.get(str(target_field_id))
        return safe_extract_int(val)

    # Generic scan (fallback)
    def _walk(obj):
        if isinstance(obj, dict):
            if obj.get("key") == target_key or obj.get("name") == target_key:
                v = obj.get("value") or obj.get("data") or obj.get("text")
                return safe_extract_int(v)
            for k, v in obj.items():
                r = _walk(v)
                if r is not None:
                    return r
        elif isinstance(obj, list):
            for it in obj:
                r = _walk(it)
                if r is not None:
                    return r
        return None

    return _walk(case_obj)

def list_cases(code: str) -> list:
    """
    Lists project cases (without include=custom_fields, which is not supported).
    """
    return paginate_get(f"/case/{code}", params=None, key="result")

def get_case(code: str, cid: int) -> dict:
    """Fetch a specific case (fallback) via GET /case/{code}/{id}."""
    data = qase_get(f"/case/{code}/{cid}")
    return (data.get("result") or data).get("case") or (data.get("result") or data)

def build_mapping_by_custom_field() -> dict:
    mapping = {}
    cf_meta = get_custom_fields_meta()
    field_id_b = (cf_meta.get(PROJECT_B) or {}).get("key_to_id", {}).get(CUSTOM_FIELD_B_IN_A)
    field_id_a = (cf_meta.get(PROJECT_A) or {}).get("key_to_id", {}).get(CUSTOM_FIELD_B_IN_A)

    force_b = CF_SOURCE == "project_b"
    force_a = CF_SOURCE == "project_a"

    # --- B -> A ---
    if not force_a:
        try:
            print(">> Attempt 1: reading cases from Project B to build B→A via custom field...")
            cases_b = list_cases(PROJECT_B)
            for c in cases_b:
                b_id = c.get("id")
                a_id = extract_cf_value_from_case(c, CUSTOM_FIELD_B_IN_A, field_id_b)
                if b_id and a_id:
                    mapping[int(b_id)] = int(a_id)

            if not mapping and cases_b:
                print(">> Listing did not return values; trying fallback GET /case/{code}/{id} per case in B...")
                for c in cases_b:
                    b_id = c.get("id")
                    if not b_id:
                        continue
                    full = get_case(PROJECT_B, int(b_id))
                    a_id = extract_cf_value_from_case(full, CUSTOM_FIELD_B_IN_A, field_id_b)
                    if a_id:
                        mapping[int(b_id)] = int(a_id)

            if mapping:
                print(f">> Mapping built via Project B: {len(mapping)} pairs B→A")
            return mapping
        except requests.HTTPError as e:
            print(f"[WARN] Error building mapping via Project B: {e}")

    # --- A -> B ---
    if not force_b:
        try:
            print(">> Attempt 2: reading cases from Project A (fallback) to build B→A...")
            cases_a = list_cases(PROJECT_A)
            for c in cases_a:
                a_id = c.get("id")
                b_id = extract_cf_value_from_case(c, CUSTOM_FIELD_B_IN_A, field_id_a)
                if a_id and b_id:
                    mapping[int(b_id)] = int(a_id)

            if not mapping and cases_a:
                print(">> Listing did not return values; trying fallback GET /case/{code}/{id} per case in A...")
                for c in cases_a:
                    a_id = c.get("id")
                    if not a_id:
                        continue
                    full = get_case(PROJECT_A, int(a_id))
                    b_id = extract_cf_value_from_case(full, CUSTOM_FIELD_B_IN_A, field_id_a)
                    if b_id:
                        mapping[int(b_id)] = int(a_id)

            if mapping:
                print(f">> Mapping built via Project A (fallback): {len(mapping)} pairs B→A")
            return mapping
        except requests.HTTPError as e:
            print(f"[WARN] Error building mapping via Project A: {e}")

    return {}

def build_mapping_by_title() -> dict:
    print(">> Title fallback: trying to match cases by normalized 'title'...")
    cases_a = list_cases(PROJECT_A)
    cases_b = list_cases(PROJECT_B)

    title_to_a_id = {}
    for c in cases_a:
        t = normalize_title(c.get("title"))
        a_id = c.get("id")
        if t and a_id:
            title_to_a_id[t] = int(a_id)

    mapping = {}
    for c in cases_b:
        t = normalize_title(c.get("title"))
        b_id = c.get("id")
        a_id = title_to_a_id.get(t)
        if t and b_id and a_id:
            mapping[int(b_id)] = int(a_id)

    print(f">> Title fallback produced {len(mapping)} pairs B→A")
    return mapping

def build_case_mapping_b_to_a() -> dict:
    mapping = build_mapping_by_custom_field()
    if mapping:
        return mapping
    mapping = build_mapping_by_title()
    if mapping:
        return mapping
    raise RuntimeError(
        f"No mapping found. Check the Custom Field '{CUSTOM_FIELD_B_IN_A}' in Projects A and B "
        "or ensure case titles are identical between A and B."
    )

def get_run_results_b(run_id: int) -> list:
    """Fetch results from Run B (official 'run' filter)."""
    return paginate_get(f"/result/{PROJECT_B}", params={"run": str(run_id)}, key="result")

STATUS_MAP = {
    "passed": "passed",
    "failed": "failed",
    "skipped": "skipped",
    "blocked": "blocked",
}

def post_result_to_run_a(a_run_id: int, a_case_id: int, b_result: dict) -> dict:
    """Create a result in Run A (POST /result/{code}/{id})."""
    payload = {
        "case_id": a_case_id,
        "status": STATUS_MAP.get(b_result.get("status"), "failed"),
        "time": b_result.get("time", 0),
        "time_ms": b_result.get("time_ms"),
        "comment": f"[Synced from B case {b_result.get('case_id')}] {b_result.get('comment','')}",
        "stacktrace": b_result.get("stacktrace"),
        "attachments": b_result.get("attachments", []),
    }
    return qase_post(f"/result/{PROJECT_A}/{a_run_id}", payload)

def sync_run_results():
    print(">> Building case map (B → A) via custom field / title fallback...")
    mapping = build_case_mapping_b_to_a()

    print(f">> Reading results from Run B (id={RUN_B_ID})...")
    results_b = get_run_results_b(RUN_B_ID)

    synced, skipped = [], []
    for r in results_b:
        b_case_id = r.get("case_id")
        a_case_id = mapping.get(b_case_id)
        if not a_case_id:
            skipped.append({"b_case_id": b_case_id, "reason": "No mapping (custom field/title)"})
            continue
        try:
            resp = post_result_to_run_a(RUN_A_ID, a_case_id, r)
            synced.append({
                "b_case_id": b_case_id,
                "a_case_id": a_case_id,
                "status": r.get("status"),
                "result_id": (resp.get("result") or {}).get("id")
            })
            time.sleep(0.05)
        except requests.HTTPError as e:
            skipped.append({"b_case_id": b_case_id, "reason": str(e)})

    print("\n== Synced ==")
    for row in synced:
        print(f"B:{row['b_case_id']} -> A:{row['a_case_id']} "
              f"status={row['status']} "
              f"result_id={row['result_id']}")

    if skipped:
        print("\n== Not synced ==")
        for s in skipped:
            print(f"B:{s['b_case_id']} "
                  f"reason={s['reason']}")

if __name__ == "__main__":
    assert API_TOKEN, "QASE_API_TOKEN not defined in .env"
    assert PROJECT_A and PROJECT_B, "PROJECT_A_CODE/PROJECT_B_CODE not defined in .env"
    assert RUN_A_ID > 0 and RUN_B_ID > 0, "RUN_A_ID/RUN_B_ID invalid in .env"
    sync_run_results()
