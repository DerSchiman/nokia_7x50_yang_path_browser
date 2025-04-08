from fastapi import FastAPI, UploadFile, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request  # Needed for rendering templates
from pathlib import Path
import subprocess
import shutil
from lxml import etree
from lxml.etree import _Element 
import argparse
import uvicorn
from typing import List 
import logging
import re


# FastAPI instance
app = FastAPI(title="YANG Path Browser")
# Jinja
templates = Jinja2Templates(directory="templates")

# Globals
flat_paths: dict = {}
loaded_model: dict= {}
flat_dir = Path("flat")
flat_dir.mkdir(exist_ok=True)
app.mount("/flat", StaticFiles(directory="flat"), name="flat")
flattening_summary: dict= {}
combined_files = {
    "state": "nokia-combined/nokia-state.yang",
    "conf": "nokia-combined/nokia-conf.yang"
}

# Function to flatten YANG models
def flatten_yang_models(yang_path: str):
    global flat_paths
    counts = {}

    for key, rel_path in combined_files.items():
        full_path = Path(yang_path) / rel_path
        output_file = flat_dir / f"nokia-{key}-flat-paths.txt"

        # Check if output is up-to-date
        if output_file.exists() and output_file.stat().st_mtime > full_path.stat().st_mtime:
            logging.info(f"⏩ Skipping flatten for {key}, already up to date.")
        else:
            logging.info(f"🔁 Regenerating flatten for {key}")
            cmd = [
                "pyang", "-f", "flatten",
                "-p", yang_path,
                "-p", str(Path(yang_path) / "ietf"),
                "-p", str(Path(yang_path) / "nokia-submodule"),
                "-p", str(Path(yang_path) / "nokia-combined"),
                str(full_path)
            ]

            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"pyang error while processing {full_path}:\n{result.stderr}")
            with open(output_file, "w") as f:
                f.write(result.stdout)

        # Always read the output into flat_paths (even if reused)
        with open(output_file, "r") as f:
            paths = [line.split(":", 1)[-1].strip() for line in f if "/" in line]
            flat_paths[key] = paths
            counts[key] = len(paths)

    return counts


# Function to load the YANG model
def load_yang_model(yang_path: Path):
    global loaded_model
    loaded_model = {}
    counts = {}

    for key, rel_path in combined_files.items():
        full_path = Path(yang_path) / rel_path
        output_file = flat_dir / f"nokia-{key}-pyang.yin"

        if output_file.exists() and output_file.stat().st_mtime > full_path.stat().st_mtime:
            logging.info(f"⏩ Skipping YIN regen for {key}, already up to date.")
        else:
            logging.info(f"🔁 Regenerating YIN for {key}")
            cmd = [
                "pyang", "-f", "yin",
                "-p", str(yang_path),
                "-p", str(yang_path / "ietf"),
                "-p", str(yang_path / "nokia-submodule"),
                "-p", str(yang_path / "nokia-combined"),
                str(full_path)
            ]

            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"pyang error while processing {full_path}:\n{result.stderr}")
            with open(output_file, "w") as f:
                f.write(result.stdout)

        try:
            tree = etree.parse(output_file)
            loaded_model[key] = tree
        except Exception as e:
            print(f"❌ Failed to parse {output_file}: {e}")
            continue

    return counts

    
# Function to search YANG path
def search_yang_path(yang_models: dict, yang_path: str):
    if not yang_path or "/" not in yang_path:
        return None

    path_parts = yang_path.strip("/").split("/")
    top_container_name = path_parts[0]

    model_key_map = {
        "configure": "conf",
        "state": "state"
    }
    model_key = model_key_map.get(top_container_name)

    if model_key is None or model_key not in yang_models:
        print(f"Model key '{model_key}' not loaded or invalid alias.")
        return None

    root = yang_models[model_key].getroot()
    current = None
    ns = {"yin": "urn:ietf:params:xml:ns:yang:yin:1"}

    for child in root:
        if child.tag.endswith("container") and child.get("name") == top_container_name:
            current = child
            break

    if current is None:
        print(f"Top-level container '{top_container_name}' not found.")
        return None

    deepest_valid = current
    last_valid_part = top_container_name

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
            if current.tag.endswith("list"):
                key_node = current.find("yin:key", namespaces=ns)
                if key_node is not None:
                    key_name = key_node.get("value")
                    for child in current:
                        tag = child.tag.split("}")[-1]
                        name = child.get("name")
                        if not name or tag in ("choice", "case"):
                            continue
                        if name == part:
                            found = child
                            break
                    if found:
                        current = found
                        deepest_valid = current
                        last_valid_part = part
                        continue
            break

        current = found
        deepest_valid = current
        last_valid_part = part

    # Return both the deepest valid node and its actual path
    valid_path = flatten_path_to_element(deepest_valid)
    # Strip known model wrappers from beginning of path for gNMI output
    if valid_path and valid_path[0] in ("nokia-state", "nokia-conf"):
        valid_path = valid_path[1:]

    return deepest_valid, "/" + "/".join(valid_path)

def flatten_path_to_element(element: _Element) -> list[str]:
    """
    Builds the full YANG path from an element by walking up the parent chain.
    """
    path_parts = []
    current = element
    while current is not None and current.get("name"):
        path_parts.insert(0, current.get("name"))
        current = current.getparent()
    return path_parts

def normalize_path(p: str) -> str:
    """
    Normalizes a full YANG path by:
    - Stripping leading/trailing slashes
    - Removing known prefixes (nokia-conf:, nokia-state:)
    - Removing list key selectors (e.g., [key=value])
    """
    p = p.strip("/")
    p = re.sub(r"^(nokia-(conf|state)[:/])", "", p)
    p = re.sub(r"\[[^\]]+\]", "", p)
    return p

loaded_model_path = ""
last_loaded_file = flat_dir / ".last_loaded"
if last_loaded_file.exists():
    with open(last_loaded_file) as f:
        last_path = Path(f.read().strip())
        if (last_path / "nokia-combined/nokia-state.yang").exists():
            logging.info(f"🔄 Auto-loading model from {last_path}")
            flatten_yang_models(str(last_path))
            load_yang_model(last_path)
            loaded_model_path = str(last_path)

@app.get("/", response_class=HTMLResponse)
def home(request: Request, model: str = "state", q: str = ""):
    result_html = ""
    if model in flat_paths and q:
        matches = sorted(set(flat_paths[model]))
        matches = [m for m in matches if q in m]
        if matches:
            result_html = f"<h2>Results for <b>{q}</b> in <code>{model}</code></h2><ul>"
            for path in matches:
                highlighted = path.replace(q, f"<span style='color:red;font-weight:bold'>{q}</span>")
                result_html += f"<li><a href='/yang_details?path={path}'>{highlighted}</a></li>"
            result_html += "</ul>"
        else:
            result_html = f"<p>No results found for <b>{q}</b> in <code>{model}</code></p>"

    return templates.TemplateResponse("home.html", {
        "request": request,
        "model": model,
        "q": q,
        "result_html": result_html,
        "flattening_summary": flattening_summary,
        "loaded_model_path": loaded_model_path,
        "is_loaded": bool(loaded_model_path),
    })



@app.get("/yang_details", response_class=HTMLResponse)
def get_yang_details(path: str, request: Request):
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

    # Generate gNMI example with list keys
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

    # ✅ Strip prefixes like nokia-state: for gnmi output
    cleaned_parts = [part.split(":")[-1] for part in patched_parts]
    gnmi_example = "gnmic get --path /" + "/".join(cleaned_parts)

    is_partial_match = normalize_path(resolved_path) != normalize_path(path)

    return templates.TemplateResponse("details.html", {
        "request": request,
        "path": path,
        "resolved_path": normalize_path(resolved_path),  # display clean version
        "is_partial_match": is_partial_match,
        "description": description,
        "type_text": type_text,
        "element_kind": element_kind,
        "key_text": key_text,
        "gnmi_example": gnmi_example,
    })

@app.post("/load", response_class=HTMLResponse)
async def load_yang_directory(request: Request, yang_path: str = Form(...), yang_folder: List[UploadFile] = Form(...)):
    global flattening_summary, loaded_model, loaded_model_path
    # Create a temporary directory to save the uploaded YANG files
    upload_path = Path("temp_yang_folder")
    if upload_path.exists():
        shutil.rmtree(upload_path)
    upload_path.mkdir()
    print(f"Upload Path is: {upload_path}")

    # Save the uploaded files into the temp folder, preserving the folder structure
    for uploaded_file in yang_folder:
        relative_file_path = Path(uploaded_file.filename)

        # Skip entries that appear to be directories
        if not uploaded_file.filename or uploaded_file.filename.endswith("/"):
            continue
        
        directory = upload_path / relative_file_path.parent
        directory.mkdir(parents=True, exist_ok=True)

        temp_file_path = upload_path / relative_file_path
        with open(temp_file_path, "wb") as f:
            shutil.copyfileobj(uploaded_file.file, f)

    # Get the top-level directory name (e.g., 'nokia 7x50_YangModels master latest_sros_22.10')
    top_level_dir = next(upload_path.glob("*"))  # This will get the first directory in temp_yang_folder
    combined_dir = top_level_dir / "nokia-combined"
    
    # Ensure the expected YANG files exist (nokia-state.yang, nokia-conf.yang)
    nokia_state_file = combined_dir / "nokia-state.yang"
    nokia_conf_file = combined_dir / "nokia-conf.yang"

    # Log the file paths to see what's inside the directory
    print(f"Checking for files:\n{nokia_state_file}\n{nokia_conf_file}")

    if not nokia_state_file.exists() or not nokia_conf_file.exists():
        raise HTTPException(status_code=404, detail="Required YANG models (nokia-state.yang, nokia-conf.yang) are missing.")

    # Flatten the YANG models
    result_flat = flatten_yang_models(top_level_dir)

    # Load the YANG model
    result_yang = load_yang_model(top_level_dir)

    loaded_model_path = str(top_level_dir)
    with open(flat_dir / ".last_loaded", "w") as f:
        f.write(loaded_model_path)

    # After flattening and loading, generate the summary
    state_path = flat_dir / "nokia-state-flat-paths.txt"
    state_pyang_path = flat_dir / "nokia-state-pyang.yin"
    conf_path = flat_dir / "nokia-conf-flat-paths.txt"
    conf_pyang_path = flat_dir / "nokia-conf-pyang.yin"

    flattening_summary = {}

    for key in combined_files:
        flattening_summary[key] = result_flat[key]
        flattening_summary[f"{key}_file"] = f"nokia-{key}-flat-paths.txt"
        flattening_summary[f"{key}_pyang"] = f"nokia-{key}-pyang.yin"


    return templates.TemplateResponse("home.html", {
        "request": request,
        "model": "state",  # or whichever was loaded
        "q": "",
        "result_html": "",
        "flattening_summary": flattening_summary,
        "loaded_model_path": loaded_model_path,
        "is_loaded": bool(loaded_model_path),
    })

    
@app.post("/cleanup", response_class=HTMLResponse)
def cleanup_temp_folders(request: Request):
    global flat_paths, loaded_model, flattening_summary, loaded_model_path
    directory_to_remove = loaded_model_path
    for path in [flat_dir, Path("temp_yang_folder")]:
        if path.exists():
            shutil.rmtree(path)
            path.mkdir(exist_ok=True)

    flat_paths.clear()
    loaded_model.clear()
    flattening_summary = {}
    loaded_model_path = ""

    if last_loaded_file.exists():
        last_loaded_file.unlink()

    return templates.TemplateResponse("cleanup.html", {
        "request": request,
        "directory_to_remove": directory_to_remove,
    })

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YANG Path Browser CLI")
    parser.add_argument("--serve", action="store_true", help="Run the FastAPI web server")
    parser.add_argument("--yang-dir", type=str, help="Run flattening only on a YANG root directory")
    args = parser.parse_args()

    if args.serve:
        uvicorn.run("path_browser:app", host="127.0.0.1", port=8000, reload=True)
    elif args.yang_dir:
        counts = flatten_yang_models(args.yang_dir)
        print("✅ Flattening completed:")
        for k, v in counts.items():
            print(f" - {k}: {v} paths → ./flat/nokia-{k}-flat-paths.txt")
    else:
        parser.print_help()
