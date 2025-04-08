
# 🧭 YANG Path Browser

**YANG Path Browser** is a developer tool to explore and test **YANG paths** from Nokia SR OS models. It provides metadata for any path (leaf, list, container), and generates example **gNMI** commands ready for use in network testing tools like **Robot Framework**. For SR-Linux the official yang path browser can be found here: [https://yang.srlinux.dev/](https://yang.srlinux.dev/)

> 📌 Supports both `state` and `conf` models. Built on FastAPI + Jinja2.

---

## ✨ Features

- 🔍 **Interactive path search** and metadata inspection  
- 📂 **Drag-and-drop folder upload** of Nokia's full YANG models  
- ⚠️ **Partial match detection** with closest node fallback  
- 🧩 **Type classification**: container, list, leaf  
- 🔧 **Auto gNMI command preview**  
- 🧠 **Persistent auto-reload** of last loaded model (fast startup)

---

<img src="screenshots/search.png" alt="YANG Path Browser Screenshot" width="50%" height="50%">

<img src="screenshots/info.png" alt="YANG Path Details Screenshot" width="50%" height="50%">

## ⚙️ Setup (with `venv`)

### 1. Clone and set up Python environment

```bash
git clone https://github.com/DerSchiman/nokia_7x50_yang_path_browser.git
cd nokia_7x50_yang_path_browser

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install requirements
pip install -r requirements.txt
```

### 2. Run the app

```bash
uvicorn path_browser:app --reload
```

🧠 This is the recommended way to run FastAPI in development.

📡 Open in browser: [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## 📥 Where to Get Nokia YANG Models

All public Nokia SR OS YANG models are here:

🔗 https://github.com/nokia/7x50_YangModels

To download a specific folder (e.g. `latest_sros_22.10`) without cloning the full repo:

1. Go to:  
   https://download-directory.github.io/

2. Paste this URL:  
   ```
   https://github.com/nokia/7x50_YangModels/tree/master/latest_sros_22.10
   ```

3. Download and unzip the folder.

---

---

## 🚀 How to Use the Tool

### 🧳 Upload a Model Folder

1. Start the tool
2. Open the web UI at http://127.0.0.1:8000
3. Upload the folder you downloaded (`latest_sros_22.10`)
4. The tool will process and flatten the YANG models

🗂 It expects:
```
nokia-combined/nokia-state.yang
nokia-combined/nokia-conf.yang
```

---

### 🔍 Search & Inspect Paths

- Use the **search bar** to find nodes (e.g. `sap-egress`)
- Results link to full metadata:
  - **Type**: container / list / leaf
  - **Description**
  - **Base YANG type**
  - **List keys**
  - **gNMI example command**

---

### 💡 gNMI Command Preview

For each YANG node, the tool auto-generates a CLI example:

```bash
gnmic get --path /state/qos/sap-egress[sap-egress-policy-name=example]
```

🧠 You can copy this directly into gNMI automation or adapt it for config testing.

---

---

## 🤖 Using YANG Info for Robot Framework

If you write tests in Robot Framework using gNMI `Get` or `Set`, it's essential to understand the **YANG node type**:

| Type       | Description                                                                 | gNMI Return | Robot Use Hint                                                  |
|------------|-----------------------------------------------------------------------------|-------------|------------------------------------------------------------------|
| `container`| A grouping node, contains sub-nodes but not data itself                     | ⛔ No value | Traverse into it, don’t expect a return value                    |
| `list`     | A repeatable set of fields, requires a key                                  | ✅ Multiple  | Use key(s) like `[id=...]` to index; loop or assert lists        |
| `leaf`     | Holds a scalar value (string, int, bool, etc.)                              | ✅ Single   | Use in `Get` or `Set`, this is where you test actual values      |

---

## 🧪 CLI Mode for Devs

To only generate flattened path files:

```bash
python path_browser.py --yang-dir /your/model/folder
```

---

## 🧼 Cleanup

To delete all temp files and restart from scratch:

```bash
curl -X POST http://127.0.0.1:8000/cleanup
```

Or use the **"Cleanup"** button in the UI.

---

## **Creating Robot Framework Test Cases**

You can use the YANG Path Browser tool to help construct Robot Framework test cases that utilize gNMI for testing network routers.

For instance, when dealing with a list in a YANG model (e.g., `/state/system/cpu[sample-period=60]`), you may need to index the list to get a specific entry, as shown in the Robot test case in the keyword section where `Get From List` is used to extract a particular entry from the list.

### **Why Data Type Matters**

The data type (container, list, or leaf) returned from gNMI commands determines how you parse and handle it in Robot Framework. For lists, you might need to index into the list, while for containers, you may need to navigate through nested dictionaries.

