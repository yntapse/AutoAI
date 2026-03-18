"""
Prompt Architect Agent Service — Two-Stage LLM Pipeline

Agent 1 (Prompt Architect): Creates a strict, structured "generation blueprint"
for a single model family.  Agent 2 (Script Generator, existing LLM path) then
receives only that blueprint as its prompt instead of a freeform instruction.

This module owns:
  1. Blueprint schema definition + validation
  2. Error-context packet builder
  3. Architect prompt construction + LLM call
  4. Blueprint → Generator-prompt translator
  5. Single-model AST enforcement gate
"""

from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------
ENABLE_PROMPT_ARCHITECT = os.getenv("ENABLE_PROMPT_ARCHITECT", "true").lower() in (
    "1",
    "true",
    "yes",
)

# ---------------------------------------------------------------------------
# Blueprint JSON schema (what Agent 1 must produce)
# ---------------------------------------------------------------------------

ALLOWED_MODEL_FAMILIES = {
    "LinearRegression",
    "Ridge",
    "Lasso",
    "ElasticNet",
    "RandomForestRegressor",
    "XGBRegressor",
}

FORBIDDEN_PATTERNS = [
    "GridSearchCV",
    "RandomizedSearchCV",
    "cross_val_score",
    "TargetEncoder",
    "MLPRegressor",
    "tensorflow",
    "keras",
    "torch",
]

REQUIRED_VARIABLES = [
    "X_train",
    "X_test",
    "y_train",
    "y_test",
    "model",
    "predictions",
]

# Maximum number of preprocessing / training steps the architect may specify
_MAX_STEPS = 12


@dataclass
class Blueprint:
    """Validated generation blueprint produced by the architect agent."""

    model_family: str
    objective: str
    preprocessing_steps: List[str]
    training_steps: List[str]
    hyperparameter_hints: Dict[str, Any]
    allowed_imports: List[str]
    forbidden_patterns: List[str]
    required_variables: List[str]
    diff_from_previous: str
    output_rules: List[str]

    # Architect telemetry
    architect_raw: Optional[str] = None
    architect_fallback_used: bool = False


@dataclass
class ErrorContext:
    """Structured error packet passed to the architect for failure-aware guidance."""

    error_type: str  # docker_timeout | oom | syntax_error | import_error | runtime_error | none
    exit_code: Optional[int] = None
    stage: str = "unknown"  # sandbox_execution | python_compile | import_resolution
    message: str = ""
    retryable: bool = True


@dataclass
class ArchitectTelemetry:
    """Per-model, per-iteration observability record."""

    model_family: str
    iteration: int
    architect_success: bool = False
    generator_success: bool = False
    validator_pass: bool = False
    fallback_used: bool = False
    reason_code: Optional[str] = None  # multi_model | forbidden_api | missing_vars | …


# ---------------------------------------------------------------------------
# Error-context builder (maps sandbox result → ErrorContext)
# ---------------------------------------------------------------------------


def build_error_context(result: Optional[Dict[str, Any]]) -> ErrorContext:
    """Convert a sandbox job result dict into a typed ErrorContext."""

    if result is None:
        return ErrorContext(error_type="none")

    # Successful run
    if "result_json" in result and isinstance(result["result_json"], dict):
        rj = result["result_json"]
        if rj.get("rmse") is not None:
            return ErrorContext(error_type="none")

    error_log: str = result.get("error_log") or result.get("error") or ""
    exit_code: Optional[int] = result.get("exit_code")
    status: str = result.get("status", "")

    # Parse exit code embedded in error string (e.g. "Docker run failed with exit code 137")
    if exit_code is None and error_log:
        _ec_match = re.search(r"exit code (\d+)", error_log, re.IGNORECASE)
        if _ec_match:
            try:
                exit_code = int(_ec_match.group(1))
            except ValueError:
                pass

    # Docker / OS OOM (exit code 137 = SIGKILL, or "Killed" in output)
    if exit_code == 137 or "MemoryError" in error_log or "Killed" in error_log:
        return ErrorContext(
            error_type="oom",
            exit_code=exit_code,
            stage="sandbox_execution",
            message=error_log[:300],
            retryable=True,
        )

    # Timeout
    if status == "timeout" or exit_code in (-1, 124) or "timeout" in error_log.lower():
        return ErrorContext(
            error_type="docker_timeout",
            exit_code=exit_code,
            stage="sandbox_execution",
            message=error_log[:300],
            retryable=True,
        )

    # Syntax error
    if "SyntaxError" in error_log or "IndentationError" in error_log:
        return ErrorContext(
            error_type="syntax_error",
            exit_code=exit_code,
            stage="python_compile",
            message=error_log[:300],
            retryable=True,
        )

    # Import error
    if "ModuleNotFoundError" in error_log or "ImportError" in error_log:
        return ErrorContext(
            error_type="import_error",
            exit_code=exit_code,
            stage="import_resolution",
            message=error_log[:300],
            retryable=True,
        )

    # Generic runtime error
    if error_log or (exit_code is not None and exit_code != 0):
        return ErrorContext(
            error_type="runtime_error",
            exit_code=exit_code,
            stage="sandbox_execution",
            message=error_log[:300],
            retryable=True,
        )

    return ErrorContext(error_type="none")


# ---------------------------------------------------------------------------
# Blueprint validation
# ---------------------------------------------------------------------------


def validate_blueprint(raw: Any, expected_model: str) -> Tuple[bool, Optional[Blueprint], List[str]]:
    """Validate a parsed JSON object against the blueprint schema.

    Returns (is_valid, blueprint_or_None, errors).
    """
    errors: List[str] = []

    if not isinstance(raw, dict):
        return False, None, ["Blueprint is not a JSON object"]

    # model_family
    mf = raw.get("model_family", "")
    if mf not in ALLOWED_MODEL_FAMILIES:
        errors.append(f"Invalid model_family '{mf}'")
    if mf != expected_model:
        errors.append(f"model_family '{mf}' does not match expected '{expected_model}'")

    # preprocessing_steps
    pre = raw.get("preprocessing_steps")
    if not isinstance(pre, list) or len(pre) == 0:
        errors.append("Missing or empty preprocessing_steps")
    elif len(pre) > _MAX_STEPS:
        pre = pre[:_MAX_STEPS]

    # training_steps
    tr = raw.get("training_steps")
    if not isinstance(tr, list) or len(tr) == 0:
        errors.append("Missing or empty training_steps")
    elif len(tr) > _MAX_STEPS:
        tr = tr[:_MAX_STEPS]

    # hyperparameter_hints
    hp = raw.get("hyperparameter_hints")
    if not isinstance(hp, dict):
        hp = {}

    # diff_from_previous
    diff = raw.get("diff_from_previous", "")
    if not isinstance(diff, str):
        diff = ""

    if errors:
        return False, None, errors

    bp = Blueprint(
        model_family=mf,
        objective=str(raw.get("objective", "minimize RMSE"))[:200],
        preprocessing_steps=[str(s)[:200] for s in pre[:_MAX_STEPS]],
        training_steps=[str(s)[:200] for s in tr[:_MAX_STEPS]],
        hyperparameter_hints=hp,
        allowed_imports=_default_allowed_imports(),
        forbidden_patterns=list(FORBIDDEN_PATTERNS),
        required_variables=list(REQUIRED_VARIABLES),
        diff_from_previous=diff[:300],
        output_rules=_default_output_rules(),
    )
    return True, bp, []


def _default_allowed_imports() -> List[str]:
    return [
        "pandas",
        "numpy",
        "sklearn",
        "xgboost",
        "scipy",
    ]


def _default_output_rules() -> List[str]:
    return [
        "Output ONLY executable Python code",
        "No markdown formatting",
        "No natural language explanations",
        "Use exact section markers: AUTONOMOUS_PREPROCESSING_SECTION and AUTONOMOUS_TRAINING_SECTION",
        "Define model as single instance of the specified model_family",
        "Do NOT define multiple model classes",
    ]


# ---------------------------------------------------------------------------
# Deterministic fallback blueprint (when architect LLM fails)
# ---------------------------------------------------------------------------

_FALLBACK_BLUEPRINTS: Dict[str, Dict[str, Any]] = {
    "LinearRegression": {
        "preprocessing_steps": [
            "Load CSV with pd.read_csv",
            "Fill missing values with median",
            "One-hot encode categorical columns with pd.get_dummies",
            "Split into train/test with train_test_split(test_size=0.2)",
        ],
        "training_steps": [
            "Instantiate LinearRegression()",
            "Fit on X_train, y_train",
            "Predict on X_test",
        ],
        "hyperparameter_hints": {},
    },
    "Ridge": {
        "preprocessing_steps": [
            "Load CSV with pd.read_csv",
            "Fill missing values with median",
            "One-hot encode categorical columns with pd.get_dummies",
            "StandardScaler on numeric features",
            "Split into train/test with train_test_split(test_size=0.2)",
        ],
        "training_steps": [
            "Instantiate Ridge(alpha=1.0)",
            "Fit on X_train, y_train",
            "Predict on X_test",
        ],
        "hyperparameter_hints": {"alpha": 1.0},
    },
    "Lasso": {
        "preprocessing_steps": [
            "Load CSV with pd.read_csv",
            "Fill missing values with median",
            "One-hot encode categorical columns with pd.get_dummies",
            "StandardScaler on numeric features",
            "Split into train/test with train_test_split(test_size=0.2)",
        ],
        "training_steps": [
            "Instantiate Lasso(alpha=0.1, max_iter=10000)",
            "Fit on X_train, y_train",
            "Predict on X_test",
        ],
        "hyperparameter_hints": {"alpha": 0.1, "max_iter": 10000},
    },
    "ElasticNet": {
        "preprocessing_steps": [
            "Load CSV with pd.read_csv",
            "Fill missing values with median",
            "One-hot encode categorical columns with pd.get_dummies",
            "StandardScaler on numeric features",
            "Split into train/test with train_test_split(test_size=0.2)",
        ],
        "training_steps": [
            "Instantiate ElasticNet(alpha=0.1, l1_ratio=0.5, max_iter=10000)",
            "Fit on X_train, y_train",
            "Predict on X_test",
        ],
        "hyperparameter_hints": {"alpha": 0.1, "l1_ratio": 0.5, "max_iter": 10000},
    },
    "RandomForestRegressor": {
        "preprocessing_steps": [
            "Load CSV with pd.read_csv",
            "Fill missing values with median",
            "One-hot encode categorical columns with pd.get_dummies",
            "Split into train/test with train_test_split(test_size=0.2)",
        ],
        "training_steps": [
            "Instantiate RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42, n_jobs=1)",
            "Fit on X_train, y_train",
            "Predict on X_test",
        ],
        "hyperparameter_hints": {"n_estimators": 100, "max_depth": 6},
    },
    "XGBRegressor": {
        "preprocessing_steps": [
            "Load CSV with pd.read_csv",
            "Fill missing values with median",
            "One-hot encode categorical columns with pd.get_dummies",
            "Split into train/test with train_test_split(test_size=0.2)",
        ],
        "training_steps": [
            "Instantiate XGBRegressor(n_estimators=100, max_depth=6, learning_rate=0.1, random_state=42, n_jobs=1)",
            "Fit on X_train, y_train",
            "Predict on X_test",
        ],
        "hyperparameter_hints": {"n_estimators": 100, "max_depth": 6, "learning_rate": 0.1},
    },
}


def deterministic_blueprint(model_family: str) -> Blueprint:
    """Return a safe, deterministic blueprint that always passes validation."""
    fb = _FALLBACK_BLUEPRINTS.get(model_family, _FALLBACK_BLUEPRINTS["RandomForestRegressor"])
    return Blueprint(
        model_family=model_family if model_family in ALLOWED_MODEL_FAMILIES else "RandomForestRegressor",
        objective="minimize RMSE",
        preprocessing_steps=fb["preprocessing_steps"],
        training_steps=fb["training_steps"],
        hyperparameter_hints=fb["hyperparameter_hints"],
        allowed_imports=_default_allowed_imports(),
        forbidden_patterns=list(FORBIDDEN_PATTERNS),
        required_variables=list(REQUIRED_VARIABLES),
        diff_from_previous="deterministic fallback — no previous context used",
        output_rules=_default_output_rules(),
        architect_fallback_used=True,
    )


# ---------------------------------------------------------------------------
# Architect prompt construction
# ---------------------------------------------------------------------------


def build_architect_prompt(
    model_family: str,
    iteration: int,
    strategy_reasoning: str,
    dataset_metadata: Dict[str, Any],
    dataset_profile_summary: Optional[str],
    previous_rmse: Optional[float],
    best_rmse: Optional[float],
    stagnation_count: int,
    error_context: ErrorContext,
    previous_blueprint: Optional[Blueprint],
) -> Tuple[str, str]:
    """Build (system_prompt, user_prompt) for the Architect LLM call.

    The architect MUST return ONLY a JSON object matching the blueprint schema.
    """

    system_prompt = (
        "You are an ML Blueprint Architect. Your job is to produce a STRUCTURED PLAN "
        "(never code) that another LLM will follow to write a Python training script.\n\n"
        "RULES:\n"
        "1. Return ONLY a single JSON object — no markdown, no prose, no explanation.\n"
        "2. The plan must target EXACTLY ONE model family.\n"
        "3. Never include executable Python code in your output.\n"
        "4. Keep preprocessing_steps and training_steps concise (max 8 items each).\n"
        "5. Each step is a short English instruction (1 sentence).\n"
    )

    # ── Error-aware directives ──
    error_directives = ""
    if error_context.error_type == "docker_timeout":
        error_directives = (
            "\n!! PREVIOUS ATTEMPT TIMED OUT !!\n"
            "Your blueprint MUST:\n"
            "- Reduce model complexity (fewer estimators, shallower depth)\n"
            "- Remove computationally expensive preprocessing\n"
            "- Avoid cross-validation or grid search\n"
            f"- Error detail: {error_context.message[:150]}\n"
        )
    elif error_context.error_type == "oom":
        error_directives = (
            "\n!! PREVIOUS ATTEMPT CAUSED OUT-OF-MEMORY !!\n"
            "Your blueprint MUST:\n"
            "- Drastically reduce n_estimators (max 50)\n"
            "- Reduce max_depth (max 5)\n"
            "- Remove one-hot encoding for high-cardinality columns\n"
            "- Use max_features='sqrt' if applicable\n"
            f"- Error detail: {error_context.message[:150]}\n"
        )
    elif error_context.error_type == "syntax_error":
        error_directives = (
            "\n!! PREVIOUS ATTEMPT HAD SYNTAX ERRORS !!\n"
            "Your blueprint MUST:\n"
            "- Use only standard, minimal preprocessing steps\n"
            "- Keep training steps simple and explicit\n"
            "- Avoid complex list comprehensions or lambda expressions\n"
            f"- Error detail: {error_context.message[:150]}\n"
        )
    elif error_context.error_type == "import_error":
        error_directives = (
            "\n!! PREVIOUS ATTEMPT FAILED DUE TO MISSING IMPORTS !!\n"
            "Your blueprint MUST:\n"
            "- Use ONLY: pandas, numpy, sklearn, xgboost\n"
            "- Do NOT use: lightgbm, catboost, optuna, shap, imblearn\n"
            f"- Error detail: {error_context.message[:150]}\n"
        )
    elif error_context.error_type == "runtime_error":
        error_directives = (
            "\n!! PREVIOUS ATTEMPT HAD A RUNTIME ERROR !!\n"
            "Your blueprint MUST:\n"
            "- Simplify preprocessing (avoid complex transformations)\n"
            "- Add explicit type casting for features\n"
            "- Use safe defaults for all hyperparameters\n"
            f"- Error detail: {error_context.message[:150]}\n"
        )

    # ── Previous blueprint context ──
    prev_context = ""
    if previous_blueprint:
        prev_context = (
            f"\nPrevious blueprint for {model_family}:\n"
            f"  preprocessing: {json.dumps(previous_blueprint.preprocessing_steps)}\n"
            f"  training: {json.dumps(previous_blueprint.training_steps)}\n"
            f"  hyperparams: {json.dumps(previous_blueprint.hyperparameter_hints)}\n"
            f"  result RMSE: {previous_rmse}\n"
            "\nYou MUST try something DIFFERENT from the previous blueprint.\n"
            "Specify what changed in the 'diff_from_previous' field.\n"
        )

    user_prompt = (
        f"Model family: {model_family}\n"
        f"Iteration: {iteration}\n"
        f"Best RMSE so far: {best_rmse}\n"
        f"Previous RMSE for this model: {previous_rmse}\n"
        f"Stagnation count: {stagnation_count}\n"
        f"Strategy guidance: {strategy_reasoning[:300]}\n\n"
        f"Dataset:\n{json.dumps(dataset_metadata, default=str)[:500]}\n"
        f"Profile: {(dataset_profile_summary or 'N/A')[:300]}\n"
        f"{error_directives}"
        f"{prev_context}\n"
        f"FORBIDDEN patterns (never suggest these): {json.dumps(FORBIDDEN_PATTERNS)}\n\n"
        f"Return a JSON object with EXACTLY these keys:\n"
        f'{{"model_family": "{model_family}",\n'
        f' "objective": "minimize RMSE",\n'
        f' "preprocessing_steps": ["step 1", "step 2", ...],\n'
        f' "training_steps": ["step 1", "step 2", ...],\n'
        f' "hyperparameter_hints": {{"param": value, ...}},\n'
        f' "diff_from_previous": "what you changed and why"\n'
        f"}}\n"
    )

    return system_prompt, user_prompt


# ---------------------------------------------------------------------------
# Parse architect LLM response → Blueprint
# ---------------------------------------------------------------------------


def parse_architect_response(
    raw_text: Optional[str],
    expected_model: str,
) -> Tuple[bool, Optional[Blueprint], List[str]]:
    """Parse raw LLM text, extract JSON, validate against schema.

    Returns (success, blueprint_or_None, errors).
    """
    if not raw_text:
        return False, None, ["Empty architect response"]

    # Try to find JSON object in the response
    text = raw_text.strip()

    # Strip markdown fences
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1 if lines[0].strip().startswith("```") else 0
        end = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end]).strip()

    # Find first { ... last }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
        return False, None, ["No JSON object found in architect response"]

    json_str = text[first_brace : last_brace + 1]

    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as exc:
        return False, None, [f"JSON parse error: {exc}"]

    return validate_blueprint(parsed, expected_model)


# ---------------------------------------------------------------------------
# Blueprint → Generator prompt translator
# ---------------------------------------------------------------------------


def blueprint_to_generator_prompt(blueprint: Blueprint, target_column: str) -> str:
    """Convert a validated blueprint into a constrained prompt for Agent 2 (Script Generator)."""

    pre_steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(blueprint.preprocessing_steps))
    train_steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(blueprint.training_steps))
    hp_str = json.dumps(blueprint.hyperparameter_hints) if blueprint.hyperparameter_hints else "{}"
    forbidden = ", ".join(blueprint.forbidden_patterns)
    required = ", ".join(blueprint.required_variables)

    prompt = (
        f"You are a Python ML code generator. Follow the blueprint EXACTLY.\n\n"
        f"MODEL: {blueprint.model_family} (use ONLY this model, no other model classes)\n"
        f"TARGET COLUMN: {target_column}\n"
        f"OBJECTIVE: {blueprint.objective}\n\n"
        f"=== PREPROCESSING STEPS (implement in order) ===\n{pre_steps}\n\n"
        f"=== TRAINING STEPS (implement in order) ===\n{train_steps}\n\n"
        f"=== HYPERPARAMETER HINTS ===\n{hp_str}\n\n"
        f"=== CONSTRAINTS ===\n"
        f"- FORBIDDEN (do NOT use): {forbidden}\n"
        f"- REQUIRED variables you must define: {required}\n"
        f"- Use ONLY these imports: pandas, numpy, sklearn, xgboost, scipy\n"
        f"- predictions variable must come from model.predict(X_test)\n"
        f"- Do NOT define result_json, rmse, rmse_holdout, rmse_cv, mae, or r2\n"
        f"- Do NOT print anything\n\n"
        f"=== OUTPUT FORMAT ===\n"
        f"Generate TWO sections using these exact markers:\n"
        f"AUTONOMOUS_PREPROCESSING_SECTION\n"
        f"(your preprocessing code here)\n"
        f"AUTONOMOUS_TRAINING_SECTION\n"
        f"(your training code here)\n\n"
        f"=== CRITICAL RULES ===\n"
        f"- Output ONLY executable Python code — NO natural language\n"
        f"- NO markdown formatting (no ```, no language tags)\n"
        f"- Every line must be valid Python syntax\n"
        f"- Define model as: model = {blueprint.model_family}(...)\n"
        f"- Do NOT instantiate any other model class\n"
    )
    return prompt


# ---------------------------------------------------------------------------
# Single-model AST enforcement gate
# ---------------------------------------------------------------------------


def enforce_single_model(code: str, expected_model: str) -> Tuple[bool, List[str]]:
    """Verify that generated code instantiates ONLY the expected model family.

    Uses AST parsing for reliable detection. Returns (passed, violations).
    """
    violations: List[str] = []

    # Quick regex pre-check for obvious multi-model contamination
    other_models = ALLOWED_MODEL_FAMILIES - {expected_model}
    for other in other_models:
        # Match instantiation pattern: OtherModel(
        pattern = rf"\b{re.escape(other)}\s*\("
        if re.search(pattern, code):
            violations.append(f"Found instantiation of '{other}' — only '{expected_model}' allowed")

    # AST check for assignments like `model = SomeClass(...)`
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if isinstance(node.value, ast.Call):
                    func = node.value.func
                    class_name = None
                    if isinstance(func, ast.Name):
                        class_name = func.id
                    elif isinstance(func, ast.Attribute):
                        class_name = func.attr

                    if class_name and class_name in other_models:
                        violations.append(
                            f"AST: Assignment uses '{class_name}' instead of '{expected_model}'"
                        )
    except SyntaxError:
        # If code doesn't parse, the existing contract validator will catch it
        pass

    # Check for forbidden API usage
    for forbidden in FORBIDDEN_PATTERNS:
        if forbidden in code:
            violations.append(f"Forbidden pattern '{forbidden}' found in generated code")

    return len(violations) == 0, violations
