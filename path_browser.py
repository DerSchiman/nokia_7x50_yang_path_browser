# Standard libs
import logging
import subprocess
import re
from pathlib import Path

# Third-party
from fastapi import FastAPI, Request, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from lxml import etree
from lxml.etree import _Element

# Local
import uvicorn


app = FastAPI(title="YANG Path Browser 2.0")
templates = Jinja2Templates(directory="templates")

yang_models_base_path = Path("7x50_YangModels")
flat_dir = Path("flat")
flat_dir.mkdir(exist_ok=True)

release_folders = sorted(
    [folder.name for folder in yang_models_base_path.iterdir() if folder.is_dir() and not folder.name.startswith(".")],
    reverse=True
)

release_load_status = {}
flat_paths = {}
loaded_model = {}

combined_files = {
    "state": "nokia-combined/nokia-state.yang",
    "conf": "nokia-combined/nokia-conf.yang"
}

app.mount("/flat", StaticFiles(directory="flat"), name="flat")

def initialize_release_statuses():
    for release in release_folders:
        flat_subdir = flat_dir / release
        required_files = [
            flat_subdir / "nokia-conf-flat-paths.txt",
            flat_subdir / "nokia-state-flat-paths.txt",
            flat_subdir / "nokia-conf-pyang.yin",
            flat_subdir / "nokia-state-pyang.yin"
        ]
        release_load_status[release] = "ok" if all(f.exists() for f in required_files) else "pending"

initialize_release_statuses()

@app.on_event("startup")
async def startup_tasks():
    def process_all_releases():
        for release in release_folders:
            if release_load_status.get(release) == "ok":
                continue
            try:
                preprocess_release_if_needed(release)
                release_load_status[release] = "ok"
            except Exception as e:
                logging.error(f"Failed to preprocess {release}: {e}")
                release_load_status[release] = f"error: {e}"

    import threading
    threading.Thread(target=process_all_releases, daemon=True).start()


def ensure_flattened_releases():
    print("🔍 Scanning for missing flattened YANG data...")
    missing = []
    for release_path in sorted(yang_models_base_path.iterdir(), reverse=True):
        if not release_path.is_dir():
            continue
        release = release_path.name
        flat_subdir = flat_dir / release
        required_files = [
            flat_subdir / "nokia-conf-flat-paths.txt",
            flat_subdir / "nokia-state-flat-paths.txt",
            flat_subdir / "nokia-conf-pyang.yin",
            flat_subdir / "nokia-state-pyang.yin"
        ]
        if not flat_subdir.exists() or not all(f.exists() for f in required_files):
            print(f"❌ Missing files for {release}, triggering flatten/load.")
            missing.append(release)
            flat_subdir.mkdir(parents=True, exist_ok=True)
            flatten_yang_models(release_path, flat_subdir)
            load_yang_model(release_path, flat_subdir)
        else:
            print(f"✅ {release} already preprocessed.")
    print("📦 Flattening complete.")


def get_combined_file_paths(release_path: Path) -> dict:
    """Determine which file names to use for combined conf/state models."""
    modern_path = release_path / "nokia-combined"
    legacy_conf = release_path / "nokia-conf.yang"
    legacy_state = release_path / "nokia-state.yang"

    if modern_path.exists():
        # Check for standard file names
        modern_conf = modern_path / "nokia-conf.yang"
        modern_state = modern_path / "nokia-state.yang"
        if modern_conf.exists() and modern_state.exists():
            return {
                "conf": "nokia-combined/nokia-conf.yang",
                "state": "nokia-combined/nokia-state.yang",
            }

        # Check for combined-suffixed variants
        combined_conf = modern_path / "nokia-conf-combined.yang"
        combined_state = modern_path / "nokia-state-combined.yang"
        if combined_conf.exists() and combined_state.exists():
            return {
                "conf": "nokia-combined/nokia-conf-combined.yang",
                "state": "nokia-combined/nokia-state-combined.yang",
            }

    # Top-level fallback
    if legacy_conf.exists() and legacy_state.exists():
        return {
            "conf": "nokia-conf.yang",
            "state": "nokia-state.yang",
        }

    raise FileNotFoundError(f"Could not find combined or legacy state/conf YANG in {release_path}")

def get_current_loaded_release() -> str:
    for name in release_folders:
        expected = flat_dir / name / "nokia-state-pyang.yin"
        if expected.exists():
            return name
    return release_folders[0]  # fallback

def preprocess_release_if_needed(release_name: str):
    release_yang_path = yang_models_base_path / release_name
    flat_release_path = flat_dir / release_name
    flat_release_path.mkdir(parents=True, exist_ok=True)

    combined_paths = get_combined_file_paths(release_yang_path)

    for key, rel_path in combined_paths.items():
        yang_file = release_yang_path / rel_path
        flat_txt = flat_release_path / f"nokia-{key}-flat-paths.txt"
        yin_file = flat_release_path / f"nokia-{key}-pyang.yin"

        if not flat_txt.exists() or flat_txt.stat().st_mtime < yang_file.stat().st_mtime:
            logging.info(f"🔁 Regenerating flatten for {release_name} {key}")
            cmd_flat = [
                "pyang", "-f", "flatten",
                "-p", str(release_yang_path),
                "-p", str(release_yang_path / "ietf"),
                "-p", str(release_yang_path / "nokia-submodule"),
                str(yang_file)
            ]
            result = subprocess.run(cmd_flat, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"pyang flatten error for {yang_file}:\n{result.stderr}")
            with open(flat_txt, "w") as f:
                f.write(result.stdout)

        if not yin_file.exists() or yin_file.stat().st_mtime < yang_file.stat().st_mtime:
            logging.info(f"🔁 Regenerating YIN for {release_name} {key}")
            cmd_yin = [
                "pyang", "-f", "yin",
                "-p", str(release_yang_path),
                "-p", str(release_yang_path / "ietf"),
                "-p", str(release_yang_path / "nokia-submodule"),
                str(yang_file)
            ]
            result = subprocess.run(cmd_yin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"pyang YIN error for {yang_file}:\n{result.stderr}")
            with open(yin_file, "w") as f:
                f.write(result.stdout)

def load_release_to_memory(release_name: str):
    global flat_paths, loaded_model
    flat_paths.clear()
    loaded_model.clear()
    flat_release_path = flat_dir / release_name
    logging.info(f"🔃 Loading release into memory: {release_name}")

    for key in combined_files:
        flat_txt = flat_release_path / f"nokia-{key}-flat-paths.txt"
        yin_file = flat_release_path / f"nokia-{key}-pyang.yin"

        if not flat_txt.exists() or not yin_file.exists():
            raise FileNotFoundError(f"Missing preprocessed files for {release_name} {key}")

        # Load paths
        with open(flat_txt) as f:
            flat_paths[key] = [line.strip() for line in f if "/" in line]

        # Load parsed YIN
        tree = etree.parse(str(yin_file))
        loaded_model[key] = tree

@app.on_event("startup")
async def startup_tasks():
    def process_all_releases():
        for release in release_folders:
            if release_load_status.get(release) == "ok":
                continue  # Already preprocessed
            try:
                preprocess_release_if_needed(release)
                release_load_status[release] = "ok"
            except Exception as e:
                logging.error(f"Failed to preprocess {release}: {e}")
                release_load_status[release] = f"error: {e}"

    import threading
    threading.Thread(target=process_all_releases, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
def home(request: Request, release: str = Query(default=release_folders[0]), model: str = "state", q: str = ""):
    # Load release if user switches dropdown
    current_loaded_release = get_current_loaded_release()
    if release != current_loaded_release:
        if release_load_status.get(release) != "ok":
            return templates.TemplateResponse("home.html", {
                "request": request,
                "release_folders": release_folders,
                "selected_release": release,
                "model": model,
                "q": q,
                "result_html": f"<p style='color:red;'>Release <b>{release}</b> is still loading. Please wait...</p>",
                "is_loaded": False,
                "release_load_status": release_load_status,
            })
        else:
            load_release_to_memory(release)

    result_html = ""
    if model in flat_paths and q:
        matches = sorted(set(flat_paths[model]))
        matches = [m for m in matches if q in m]
        if matches:
            result_html = f"<h2>Results for <b>{q}</b> in <code>{model}</code> for <b>{release}</b></h2><ul>"
            for path in matches:
                highlighted = path.replace(q, f"<span style='color:red;font-weight:bold'>{q}</span>")
                result_html += f"<li><a href='/yang_details?path={path}&release={release}'>{highlighted}</a></li>"
            result_html += "</ul>"
        else:
            result_html = f"<p>No results found for <b>{q}</b> in <code>{model}</code></p>"

    return templates.TemplateResponse("home.html", {
        "request": request,
        "release_folders": release_folders,
        "selected_release": release,
        "model": model,
        "q": q,
        "result_html": result_html,
        "is_loaded": True,
        "release_load_status": release_load_status,
    })

@app.get("/yang_details", response_class=HTMLResponse)
def get_yang_details(request: Request, path: str, release: str = Query(...)):
    if release not in release_folders:
        return HTMLResponse(f"<p>Invalid release: {release}</p>", status_code=400)
    
    load_release_to_memory(release)
    result = search_yang_path(loaded_model, path)

    if not result or not isinstance(result, tuple) or result[0] is None:
        return HTMLResponse(f"<p>No information found for path: <code>{path}</code></p>", status_code=404)

    element, resolved_path = result
    ns = {"yin": "urn:ietf:params:xml:ns:yang:yin:1"}

    description = element.findtext("yin:description/yin:text", default="No description available", namespaces=ns).strip()
    type_node = element.find("yin:type", namespaces=ns)
    type_text = type_node.get("name") if type_node is not None else "No type available"
    element_kind = element.tag.split("}")[-1].capitalize()

    key_node = element.find("yin:key", namespaces=ns)
    key_text = key_node.get("value") if key_node is not None else None

    path_parts = resolved_path.strip("/").split("/")
    patched_parts = path_parts.copy()
    current = element
    while current is not None:
        if current.tag.endswith("list"):
            list_name = current.get("name")
            key_node = current.find("yin:key", namespaces=ns)
            if key_node is not None:
                key_value = key_node.get("value")
                try:
                    index = patched_parts.index(list_name)
                    patched_parts[index] = f"{list_name}[{key_value}=example]"
                except ValueError:
                    pass
        current = current.getparent()

    cleaned_parts = [part.split(":")[-1] for part in patched_parts]
    gnmi_example = "gnmic get --path /" + "/".join(cleaned_parts)
    is_partial_match = normalize_path(resolved_path) != normalize_path(path)

    return templates.TemplateResponse("details.html", {
        "request": request,
        "path": path,
        "resolved_path": normalize_path(resolved_path),
        "is_partial_match": is_partial_match,
        "description": description,
        "type_text": type_text,
        "element_kind": element_kind,
        "key_text": key_text,
        "gnmi_example": gnmi_example,
    })

def search_yang_path(yang_models: dict, yang_path: str):
    if not yang_path or "/" not in yang_path:
        return None

    path_parts = yang_path.strip("/").split("/")

    # Handle prefixed first element (e.g. nokia-state:state)
    top_container_name = path_parts[0].split(":")[-1]

    model_key_map = {"configure": "conf", "state": "state"}
    model_key = model_key_map.get(top_container_name)

    if model_key is None or model_key not in yang_models:
        return None

    root = yang_models[model_key].getroot()
    current = None
    ns = {"yin": "urn:ietf:params:xml:ns:yang:yin:1"}

    for child in root:
        if child.tag.endswith("container") and child.get("name") == top_container_name:
            current = child
            break
    if current is None:
        return None

    deepest_valid = current
    for part in path_parts[1:]:
        found = None
        for child in current:
            tag = child.tag.split("}")[-1]
            name = child.get("name")
            if not name or tag in ("choice", "case"):
                continue
            if name == part:
                found = child
                break
        if found is None:
            break
        current = found
        deepest_valid = current

    valid_path = flatten_path_to_element(deepest_valid)
    if valid_path and valid_path[0] in ("nokia-state", "nokia-conf"):
        valid_path = valid_path[1:]

    return deepest_valid, "/" + "/".join(valid_path)

def flatten_path_to_element(element: _Element) -> list[str]:
    path_parts = []
    current = element
    while current is not None and current.get("name"):
        path_parts.insert(0, current.get("name"))
        current = current.getparent()
    return path_parts

def normalize_path(p: str) -> str:
    p = p.strip("/")
    p = re.sub(r"^(nokia-(conf|state)[:/])", "", p)
    p = re.sub(r"\[[^\]]+\]", "", p)
    return p

@app.get("/status")
def get_status():
    return JSONResponse(content={
        "status": "ok",
        "release_status": release_load_status
    })
