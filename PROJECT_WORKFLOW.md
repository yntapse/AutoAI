# AutoAI Builder – Project Overview and Workflow

This document explains how the AutoAI Builder platform works in simple language.
You can read this even if you are **not technical**. It describes:

- What the product does
- Which technologies we use
- How the different "agents" and services work together
- What happens step‑by‑step from CSV upload to trained model, fine‑tune, and downloads

---

## 1. What AutoAI Builder Does

AutoAI Builder is a web app where you can:

- Upload a **CSV file** with your data (for example, churn data, sales data, prices, etc.)
- Choose which column you want to **predict** (the target)
- Let the system automatically:
  - Clean and prepare the data
  - Train several machine‑learning models
  - Compare them and pick the best one
  - Optionally **fine‑tune** a specific model using an AI assistant (LLM)
- Then you can **download**:
  - The trained model as a `.pkl` file (for developers to integrate)
  - The full **training code** as a `.py` script

You do not need to write code yourself. The system generates and runs the code for you.

---

## 2. Technologies We Use

At a high level we have three main parts:

- **Frontend (UI)** – what you see in the browser
  - Framework: **Next.js** (React + TypeScript)
  - Location in repo: `src/app` and `src/components`
  - Shows dashboards, charts, model tables, fine‑tune controls, and download buttons.

- **Backend API** – the brain of the system
  - Framework: **FastAPI** (Python)
  - Location: `backend/main.py` and other backend files
  - Responsibilities:
    - Receive file uploads
    - Store project and run information in the database
    - Coordinate training, fine‑tuning, and downloads
    - Talk to the LLM provider (OpenAI, Claude, Gemini, etc.) to suggest hyperparameters

- **Sandbox Worker** – safe place where training code runs
  - Script: `backend/sandbox_worker.py`
  - Purpose: run dynamically generated Python training code in an isolated process
  - This prevents a single training run from blocking or crashing the main API.

Other key tools and libraries:

- **Database**: PostgreSQL (accessed through SQLAlchemy models in `backend/models/`)
- **ML Libraries**:
  - `scikit-learn` (e.g. `LinearRegression`, `RandomForestRegressor`)
  - `xgboost` (`XGBRegressor`)
- **Data processing**: `pandas`
- **Model serialization**: `pickle` (saving trained models into `.pkl` files)

---

## 3. Main Concepts (Non‑Technical Definitions)

To understand the workflow, there are a few key concepts:

- **Project**
  - One uploaded dataset + a name you choose.
  - Example: "Customer Churn Predictor" using `churn_data.csv`.

- **Training Run**
  - One round of model training on that dataset.
  - Each run has a **version number** (1, 2, 3, …) for that project.

- **Model Version**
  - Inside one training run, we train multiple models (Linear Regression, Random Forest, XGBoost, etc.).
  - Each of these is stored as a `ModelVersion` with:
    - Name (e.g. `RandomForestRegressor`)
    - Metrics (RMSE, MAE, R²)
    - Hyperparameters (the tuning knobs for the model)

- **Agent Run**
  - A session where an "agent" explores models and improvements across multiple iterations.
  - Under the hood, this is tracked as an `AgentRun` record.

- **Sandbox Job**
  - A single script execution in the sandbox worker (for example, one fine‑tune job).
  - Tracked as a `SandboxJob` in the database.

- **Fine‑Tune**
  - A focused improvement of one selected model using hints from an LLM.
  - It creates a **new Training Run version** with new hyperparameters and metrics.

---

## 4. High-Level Architecture Diagram (Text Version)

Below is a simple text diagram of how the parts talk to each other.

```text
[Browser UI (Next.js)]
        |
        v
[FastAPI Backend]
        |
        +--> [Database (Projects, TrainingRuns, ModelVersions, SandboxJobs, AgentRuns)]
        |
        +--> [Sandbox Worker]
        |         ^
        |         |
        |     (runs training & fine‑tune code)
        |
        +--> [LLM Providers (OpenAI / Claude / Gemini / etc.)]
                    ^
                    |
              (suggest hyperparameters based on your prompt)
```

---

## 5. End‑to‑End Workflow: From CSV to Best Model

### 5.1. Uploading Data and Creating a Project

1. You open the web app and click **"New Project"**.
2. You **upload a CSV file** and provide a project name.
3. The frontend sends this to the backend endpoint
   `POST /projects/create`.
4. The backend:
   - Saves the file into `backend/uploads/` with a generated ID.
   - Analyzes the dataset (rows, columns, column names).
   - Creates a `Project` record in the database.
5. The API returns a response with:
   - The project name
   - Dataset summary
   - A `file_id` that represents this CSV.
6. The UI stores this info and shows it on the **Training** and **Results** pages.

### 5.2. First Training Run (Baseline Models)

1. On the **Training** page you pick the **target column** (what you want to predict).
2. The backend starts a **Training Run** for this project.
3. Inside that run, it trains several built‑in models:
   - Linear Regression
   - Ridge / Lasso / ElasticNet
   - Random Forest Regressor
   - XGBoost Regressor
4. For each model it:
   - Prepares the data (one‑hot encoding for categories, handling missing values).
   - Fits the model on training data.
   - Evaluates it on test data and records metrics.
5. The results are stored in the database as `ModelVersion` rows and summarized in
   an in‑memory **TRAINING_HISTORY** structure.
6. The **Results** page calls the backend (e.g. `GET /training/history/{file_id}`)
   to show a table and charts of the models and metrics.

---

## 6. Fine‑Tuning With the LLM

### 6.1. What Fine‑Tuning Means Here

Fine‑tuning in this app means:

- You choose a specific model (for example, `RandomForestRegressor`).
- You describe what you want in plain language, for example:
  - "Improve recall on minority class"
  - "Reduce overfitting"
  - "Make the model faster"
- The system asks an **LLM** (like OpenAI) for better hyperparameters.
- Then it runs a new training script with those hyperparameters, creating a
  **new Training Run version**.

### 6.2. Fine‑Tune Request Flow

1. On the **Results** page, you type a prompt in the fine‑tune box and pick a model.
2. The frontend calls the backend endpoint:

   - `POST /training/fine-tune`

   The request includes:

   - `file_id` – which dataset to use
   - `target_column` – what we are predicting
   - `llm_provider` – e.g. `openai`
   - `prompt` – your natural‑language instructions
   - `model_name` – which model you want to improve

3. The backend reconstructs the **latest training history** if needed,
   finds the model you selected (even if it was in a previous version),
   and extracts its **baseline metrics**.

4. It calls a helper that talks to the LLM provider to get **improved hyperparameters**.

5. Then there are two possible execution paths:

   - **Sandbox path (preferred)**
     - Backend builds a small training script with the tuned hyperparameters.
     - It creates a `SandboxJob` record and a new `TrainingRun` with a new version number.
     - The `sandbox_worker.py` process picks up the job and runs the script.
     - The backend polls the sandbox job (`_poll_sandbox_job_for_fine_tune`) until it finishes.
     - The result metrics (RMSE, MAE, R²) and hyperparameters are stored back into `TrainingRun`
       and `ModelVersion` rows.

   - **Native path (fallback)**
     - The backend runs `fine_tune_best_model()` directly inside the API process using the
       tuned hyperparameters, then stores the results.

6. The endpoint returns a structured response to the frontend with:

   - `previous_version` and `new_version`
   - Before/after metrics
   - `training_run_id` for the new fine‑tuned run
   - Calculated improvements (how much RMSE and R² changed)

7. The UI shows these improvements and lets you **accept** or **revert** the changes.

---

## 7. How the Orchestrator and Agents Work Together

We can think of the backend as an **orchestrator** that coordinates several agents and services:

- **UI Orchestrator (Frontend)**
  - Orchestrates user interactions: upload, training, fine‑tune, and downloads.

- **Training Orchestrator (Backend)**
  - Decides when to:
    - Start a baseline training run
    - Store metrics and versions
    - Trigger a fine‑tune run
    - Call the LLM for hyperparameters
    - Queue a sandbox job
    - Poll for status and handle failures

- **LLM Agent**
  - Receives the current model name and metrics + your prompt.
  - Suggests a new set of hyperparameters.
  - We then feed those hyperparameters into the training scripts.

- **Sandbox Worker Agent**
  - Its only job is to run training scripts safely and report back metrics.
  - It does not know about the UI or LLM; it just runs Python code it receives.

- **Versioning & History Agent (Logic in Backend)**
  - Keeps track of which model and version came from:
    - Standard training
    - LLM‑generated configuration
    - Fine‑tuning
  - Allows the UI to show labels like **"Fine‑tuned"** next to model names.

---

## 8. Accepting and Reverting Fine‑Tuned Models

On the **Results** page you see buttons like **"Accept Changes"** and **"Revert"**.
These control how the UI and the backend version history line up.

### 8.1. Accepting Changes

When you click **"Accept"** after a fine‑tune:

1. The UI takes a **snapshot** of the current table and results.
2. It updates the table row for the chosen model with the new metrics and marks it as
   `generated_by = "fine_tuned"` so you see a **"Fine‑tuned" badge**.
3. It updates `latestTrainingRunId` to the **new fine‑tuned `training_run_id`**.
4. Future **model downloads** and **code downloads** use this new training run, so you get
   the fine‑tuned model and its code.

### 8.2. Reverting Changes

When you click **"Revert"**:

1. The UI looks at the latest snapshot from the stack.
2. It restores:
   - The previous table rows and metrics
   - The `generated_by` flags (so the **"Fine‑tuned"** badge disappears)
   - The previous `latestTrainingRunId`
3. Now downloads will again use the **original training run**, so the
   `.pkl` and script reflect the **pre‑fine‑tune** model.

This means both the **numbers on screen** and the **downloaded artifacts** stay in sync
with whatever state you currently see.

---

## 9. How Downloads Work (Models and Code)

### 9.1. Downloading Models (`.pkl` files)

1. When you click **"Download Model"** in the UI:
   - The frontend calls the backend `/models/download/{training_run_id}` endpoint.
   - Optionally it sends a specific `model_name` (or downloads all models for that run).

2. The backend:
   - Looks up the `TrainingRun` and `ModelVersion` rows.
   - Loads the original dataset again.
   - Rebuilds the model object with the stored **hyperparameters**.
   - Retrains it on the full data (so the artifact is self‑contained).
   - Saves it into a `.pkl` payload with:
     - Model object
     - Hyperparameters
     - Feature column names
     - Target column name
     - Training run and version info

3. If you choose **"Download All Models"**, it bundles them into a `.zip`.

4. When you have fine‑tuned a model and accepted the change, the `training_run_id`
   points to the fine‑tuned version, so the downloaded model is the improved one.

5. If you **revert**, the `training_run_id` goes back to the old run, so the
   downloads are again the pre‑fine‑tune models.

### 9.2. Downloading Code (`.py` scripts)

1. The **"Download Code"** button triggers `/models/code/{training_run_id}`.
2. The backend tries two strategies:

   - **Sandbox code**
     - If there is a `SandboxJob` with a stored `script_content`, it returns exactly
       that script as a `.py` file.

   - **Synthesized native code**
     - If there is no sandbox script, it uses `_synthesize_native_training_script()`
       to build a complete training script from `ModelVersion` data.
     - This script includes:
       - Dataset loading
       - Preprocessing
       - Model definitions with their hyperparameters
       - Training loop and metrics printout

3. You can also choose **specific model code** from the dropdown; then the backend
   filters the models so the script focuses on the chosen one.

4. As with model downloads, the code is based on the **current** `training_run_id`,
   so accepting or reverting fine‑tunes will change which code you get.

---

## 10. Summary (Non‑Technical)

- You upload a CSV → the system creates a **Project**.
- The system tries multiple models and shows you which one is best.
- You can ask an AI assistant to **fine‑tune** a model using plain English.
- The system creates a new version with improved settings and metrics.
- You can **accept** the new version (and get updated models/code) or **revert**
  back to the previous state.
- Throughout, the backend orchestrator, agents, sandbox worker, database, and LLM
  all work together so you never have to touch raw code unless you want to
  download it.

This document should give you a complete picture of how AutoAI Builder works
from a high level. If you’d like, we can also add a shorter, purely business‑level
summary or a FAQ section tailored for stakeholders.
