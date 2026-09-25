"""
LLM Service for hyperparameter suggestions.

Routes requests to different LLM providers and returns structured hyperparameter recommendations.
"""
import os
import json
import logging
from typing import Dict, Any, Callable


logger = logging.getLogger(__name__)


SAFE_HYPERPARAMETERS = {
    "RandomForestRegressor": {
        "n_estimators": {"type": int, "min": 10, "max": 500},
        "max_depth": {"type": int, "min": 1, "max": 50},
        "min_samples_split": {"type": int, "min": 2, "max": 20},
        "min_samples_leaf": {"type": int, "min": 1, "max": 20}
    },
    "XGBRegressor": {
        "n_estimators": {"type": int, "min": 50, "max": 500},
        "max_depth": {"type": int, "min": 1, "max": 15},
        "learning_rate": {"type": float, "min": 0.001, "max": 0.5},
        "subsample": {"type": float, "min": 0.5, "max": 1.0}
    },
    "Ridge": {
        "alpha": {"type": float, "min": 0.01, "max": 100.0}
    },
    "Lasso": {
        "alpha": {"type": float, "min": 0.000001, "max": 100.0},
        "max_iter": {"type": int, "min": 100, "max": 100000}
    },
    "ElasticNet": {
        "alpha": {"type": float, "min": 0.000001, "max": 100.0},
        "l1_ratio": {"type": float, "min": 0.0, "max": 1.0},
        "max_iter": {"type": int, "min": 100, "max": 100000}
    },
    "LinearRegression": {}
}


def validate_hyperparameters(model_name: str, suggested_params: Any) -> Dict[str, Any]:
    """
    Validate LLM-suggested hyperparameters against a strict whitelist.

    Rules:
    - Unknown keys are removed
    - Values are cast to the configured type
    - Values outside min/max bounds are discarded
    - Any invalid parameter is discarded

    Args:
        model_name: Name of the model for parameter whitelist lookup
        suggested_params: Raw suggested hyperparameters from LLM output

    Returns:
        Safe validated hyperparameter dictionary (possibly empty)
    """
    if not isinstance(suggested_params, dict):
        return {}

    allowed_params = SAFE_HYPERPARAMETERS.get(model_name)
    if allowed_params is None:
        return {}

    validated: Dict[str, Any] = {}

    for param_name, raw_value in suggested_params.items():
        rules = allowed_params.get(param_name)
        if rules is None:
            continue

        expected_type = rules["type"]

        # Reject booleans for numeric fields (bool is a subclass of int)
        if isinstance(raw_value, bool):
            continue

        try:
            cast_value = expected_type(raw_value)
        except (TypeError, ValueError):
            continue

        min_value = rules["min"]
        max_value = rules["max"]

        if cast_value < min_value or cast_value > max_value:
            continue

        validated[param_name] = cast_value

    return validated


def get_hyperparameter_suggestions(
    model_name: str, 
    metrics: Dict[str, float], 
    llm_provider: str
) -> Dict[str, Any]:
    """
    Get hyperparameter suggestions from the specified LLM provider.
    
    Args:
        model_name: Name of the ML model (e.g., "RandomForestRegressor")
        metrics: Current model metrics (e.g., {"rmse": 100.5, "r2": 0.85})
        llm_provider: LLM provider name ("openai", "claude", "gemini", "mistral", "groq")
        
    Returns:
        Dictionary containing hyperparameter suggestions with structure:
        {
            "hyperparameters": {
                "param_name": value,
                ...
            }
        }
        Returns empty dict if provider fails or is invalid.
    """
    # Provider routing map
    providers: Dict[str, Callable] = {
        "openai": _openai_provider,
        "claude": _claude_provider,
        "gemini": _gemini_provider,
        "mistral": _mistral_provider,
        "groq": _groq_provider
    }
    
    # Get provider function
    provider_func = providers.get(llm_provider)
    
    if provider_func is None:
        return {}
    
    try:
        # Call provider-specific function
        response = provider_func(model_name, metrics)
        
        # Validate response structure
        if not isinstance(response, dict) or "hyperparameters" not in response:
            return {}

        response_model_name = response.get("model_name")
        selected_model_name = (
            response_model_name
            if isinstance(response_model_name, str) and response_model_name in SAFE_HYPERPARAMETERS
            else model_name
        )

        original_hyperparameters = response.get("hyperparameters")
        logger.info(
            "Original LLM output for %s (%s): %s",
            model_name,
            llm_provider,
            response,
        )

        validated_hyperparameters = validate_hyperparameters(selected_model_name, original_hyperparameters)
        logger.info(
            "Validated hyperparameters for %s (%s) using selected model %s: %s",
            model_name,
            llm_provider,
            selected_model_name,
            validated_hyperparameters,
        )

        return {
            "model_name": selected_model_name,
            "hyperparameters": validated_hyperparameters
        }
        
    except Exception as e:
        # Log error in production (using logging module)
        print(f"Error getting suggestions from {llm_provider}: {str(e)}")
        return {}


def _openai_provider(model_name: str, metrics: Dict[str, float]) -> Dict[str, Any]:
    """
    OpenAI provider implementation.
    
    Calls OpenAI ChatCompletion API to get hyperparameter suggestions.
    Returns fallback structured response if API call fails for any reason.
    Never crashes - always returns valid structure.
    """
    # Fallback response with safe default hyperparameters
    fallback_response = {
        "hyperparameters": {
            "n_estimators": 150,
            "max_depth": 8
        }
    }
    
    try:
        # Import OpenAI library
        from openai import OpenAI
    except ImportError:
        print("Error: openai library not installed. Run: pip install openai")
        return fallback_response
    
    # Get API key from environment
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY environment variable not set")
        return fallback_response
    
    try:
        # Initialize OpenAI client
        client = OpenAI(api_key=api_key, timeout=30.0)
        
        # Extract metrics for prompt
        rmse = metrics.get("rmse", 0)
        improvement = metrics.get("improvement", "N/A")
        best_model_name = model_name
        best_rmse = rmse
        
        # Build structured prompt
        system_prompt = """You are an ML hyperparameter optimization agent. 
Return only valid JSON. No explanation."""
        
        previous_best_hyperparameters = json.dumps(
            metrics.get("previous_best_hyperparameters", {}),
            default=str,
        )
        previous_iteration_hyperparameters = json.dumps(
            metrics.get("previous_iteration_hyperparameters", {}),
            default=str,
        )

        mutation_prompt = f"""
You are an autonomous ML optimization agent.

Current best model:
- Model: {best_model_name}
- RMSE: {best_rmse}
- Improvement from previous iteration: {improvement}

Return ONLY this JSON structure:
{{
  "model_name": "ModelNameHere",
  "hyperparameters": {{ ... }},
  "reasoning": "Short explanation of decision"
}}
"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": mutation_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=500
        )
        
        # Extract response content
        content = response.choices[0].message.content
        
        # Parse JSON safely
        try:
            result = json.loads(content)
            
            # Validate structure
            if not isinstance(result, dict) or "hyperparameters" not in result:
                print(f"Error: Invalid response structure from OpenAI: {result}")
                return fallback_response
            
            # Ensure hyperparameters is a dict
            if not isinstance(result["hyperparameters"], dict):
                print("Error: hyperparameters is not a dictionary")
                return fallback_response
            
            return result
            
        except json.JSONDecodeError as e:
            print(f"Error: Failed to parse OpenAI response as JSON: {e}")
            return fallback_response
            
    except Exception as e:
        # Catch ALL exceptions (including API quota errors, timeouts, etc.)
        print(f"Error calling OpenAI API: {str(e)}")
        return fallback_response


def _claude_provider(model_name: str, metrics: Dict[str, float]) -> Dict[str, Any]:
    """
    Claude (Anthropic) provider implementation.
    
    TODO: Implement actual Claude API call.
    Currently returns simulated response.
    """
    # Simulate Claude API response
    suggestions = _generate_mock_suggestions(model_name, metrics)
    
    return {
        "hyperparameters": suggestions
    }


def _gemini_provider(model_name: str, metrics: Dict[str, float]) -> Dict[str, Any]:
    """
    Gemini (Google) provider implementation.
    """
    fallback_response = {"model_name": None, "hyperparameters": {}}

    use_new_sdk = False
    legacy_genai = None
    try:
        import google.genai as genai
        use_new_sdk = True
    except Exception as import_exc:
        try:
            from google import genai
            use_new_sdk = True
        except Exception:
            try:
                import google.generativeai as legacy_genai
                print("GEMINI IMPORT FALLBACK: using google.generativeai")
            except Exception as legacy_import_exc:
                print("GEMINI IMPORT ERROR:", str(import_exc))
                print("GEMINI LEGACY IMPORT ERROR:", str(legacy_import_exc))
                return fallback_response

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        try:
            env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as env_file:
                    for line in env_file:
                        stripped = line.strip()
                        if not stripped or stripped.startswith("#"):
                            continue
                        if stripped.startswith("GEMINI_API_KEY="):
                            api_key = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                            break
        except Exception:
            api_key = None

    if not api_key:
        print("GEMINI API KEY NOT FOUND")
        return fallback_response

    try:
        if use_new_sdk:
            client = genai.Client(api_key=api_key)
        else:
            legacy_genai.configure(api_key=api_key)
            client = legacy_genai.GenerativeModel("gemini-flash-latest")

        best_model_name = model_name
        best_rmse = metrics.get("rmse", 0)
        improvement = metrics.get("improvement", "N/A")

        df = metrics.get("df")
        target_column = metrics.get("target_column")

        number_of_rows = metrics.get("number_of_rows", "unknown")
        number_of_features = metrics.get("number_of_features", "unknown")
        numeric_feature_count = metrics.get("numeric_feature_count", "unknown")
        categorical_feature_count = metrics.get("categorical_feature_count", "unknown")
        missing_value_count = metrics.get("missing_value_count", "unknown")
        target_variance = metrics.get("target_variance", "unknown")

        if (
            df is not None
            and isinstance(target_column, str)
            and hasattr(df, "columns")
            and target_column in getattr(df, "columns", [])
        ):
            feature_df = df.drop(columns=[target_column])
            number_of_rows = len(df)
            number_of_features = df.shape[1] - 1
            numeric_feature_count = feature_df.select_dtypes(include=["number"]).shape[1]
            categorical_feature_count = feature_df.select_dtypes(exclude=["number"]).shape[1]
            missing_value_count = int(df.isnull().sum().sum())
            target_variance = float(df[target_column].var())

        previous_best_hyperparameters = json.dumps(
            metrics.get("previous_best_hyperparameters", {}),
            default=str,
        )
        previous_iteration_hyperparameters = json.dumps(
            metrics.get("previous_iteration_hyperparameters", {}),
            default=str,
        )

        mutation_prompt = f"""
You are an autonomous ML optimization agent.

Current best model:
- Model: {best_model_name}
- RMSE: {best_rmse}
- Improvement from previous iteration: {improvement}

DATASET SUMMARY:
- Rows: {number_of_rows}
- Features: {number_of_features}
- Numeric Features: {numeric_feature_count}
- Categorical Features: {categorical_feature_count}
- Missing Values: {missing_value_count}
- Target Variance: {target_variance}

PREVIOUS CONFIGURATION:
- Previous Best Hyperparameters: {previous_best_hyperparameters}
- Last Iteration Hyperparameters: {previous_iteration_hyperparameters}
- Improvement: {improvement}

Allowed models:
- LinearRegression
- Ridge
- Lasso
- ElasticNet
- RandomForestRegressor
- XGBRegressor

MODEL SELECTION STRATEGY (MVP):

1. If dataset rows < 500:
    - Prefer linear models first:
      LinearRegression
      Ridge
      Lasso
      ElasticNet

2. If numeric_features >= 5 and rows < 300:
    - Strongly consider Lasso or ElasticNet for regularization and feature selection.

3. If rows > 1000:
    - Tree-based models (RandomForestRegressor, XGBRegressor) are allowed.

4. If linear models stagnate:
    - Try Lasso or ElasticNet before switching to tree-based models.

5. Only switch to tree models if:
    - Linear models fail repeatedly
    - Or dataset size is large.

6. Always avoid repeating identical configuration.

CRITICAL:
- You MUST return valid JSON.
- You MUST include model_name.
- You MUST include hyperparameters.
- You MUST include reasoning.
- Do NOT return empty objects.
- Do NOT repeat same model if stagnation detected.

Return ONLY this JSON structure:

{{
    "model_name": "ModelNameHere",
    "hyperparameters": {{ ... }},
    "reasoning": "Short explanation of decision"
}}
"""

        print("=== FINAL MUTATION PROMPT SENT ===")
        print(mutation_prompt)
        print("=================================")

        print("### GEMINI CALL EXECUTED ###")
        print("PROMPT LENGTH:", len(mutation_prompt))
        if use_new_sdk:
            response = client.models.generate_content(
                model="gemini-flash-latest",
                contents=mutation_prompt,
            )
        else:
            response = client.generate_content(mutation_prompt)
        raw_output = getattr(response, "text", "") or ""
        print("GEMINI RAW OUTPUT:", raw_output)

        if not raw_output:
            return fallback_response

        try:
            cleaned_output = raw_output.strip()
            json_candidate = cleaned_output

            if "```" in cleaned_output:
                fence_start = cleaned_output.find("```")
                fence_end = cleaned_output.find("```", fence_start + 3)
                if fence_start != -1 and fence_end != -1:
                    fenced_content = cleaned_output[fence_start + 3:fence_end].strip()
                    if fenced_content.lower().startswith("json"):
                        fenced_content = fenced_content[4:].strip()
                    json_candidate = fenced_content

            start_index = json_candidate.find("{")
            end_index = json_candidate.rfind("}")
            if start_index == -1 or end_index == -1 or end_index <= start_index:
                raise ValueError("No JSON object boundaries found in Gemini response.")

            json_candidate = json_candidate[start_index:end_index + 1]
            parsed = json.loads(json_candidate)

            if not isinstance(parsed, dict):
                raise ValueError("Parsed Gemini response is not a JSON object.")

            if not isinstance(parsed.get("hyperparameters"), dict):
                parsed["hyperparameters"] = {}

            if "model_name" not in parsed:
                parsed["model_name"] = None

            return parsed
        except Exception as parse_exc:
            print("GEMINI PARSING ERROR:", str(parse_exc))
            return fallback_response
    except Exception as gemini_exc:
        print("GEMINI PROVIDER ERROR:", str(gemini_exc))
        return fallback_response


def _mistral_provider(model_name: str, metrics: Dict[str, float]) -> Dict[str, Any]:
    """
    Mistral AI provider implementation.
    
    TODO: Implement actual Mistral API call.
    Currently returns simulated response.
    """
    # Simulate Mistral API response
    suggestions = _generate_mock_suggestions(model_name, metrics)
    
    return {
        "hyperparameters": suggestions
    }


def _groq_provider(model_name: str, metrics: Dict[str, float]) -> Dict[str, Any]:
    """
    Groq provider implementation.
    
    TODO: Implement actual Groq API call.
    Currently returns simulated response.
    """
    # Simulate Groq API response
    suggestions = _generate_mock_suggestions(model_name, metrics)
    
    return {
        "hyperparameters": suggestions
    }


def _generate_mock_suggestions(model_name: str, metrics: Dict[str, float]) -> Dict[str, Any]:
    """
    Generate mock hyperparameter suggestions based on model type.
    
    This is a placeholder that simulates what the LLM would return.
    In production, this logic will be replaced by actual LLM API calls.
    
    Args:
        model_name: Name of the ML model
        metrics: Current model performance metrics
        
    Returns:
        Dictionary of hyperparameter suggestions
    """
    print(f"MOCK SUGGESTIONS INPUT model_name={model_name} metrics={metrics}")

    # Model-specific hyperparameter suggestions
    if model_name == "RandomForestRegressor":
        result = {
            "n_estimators": 150,
            "max_depth": 8,
            "min_samples_split": 5,
            "min_samples_leaf": 2
        }
        print(f"MOCK SUGGESTIONS OUTPUT for {model_name}: {result}")
        return result
    elif model_name == "XGBRegressor":
        result = {
            "n_estimators": 200,
            "max_depth": 6,
            "learning_rate": 0.05,
            "subsample": 0.8
        }
        print(f"MOCK SUGGESTIONS OUTPUT for {model_name}: {result}")
        return result
    elif model_name == "Ridge":
        result = {
            "alpha": 10.0,
            "solver": "auto"
        }
        print(f"MOCK SUGGESTIONS OUTPUT for {model_name}: {result}")
        return result
    elif model_name == "LinearRegression":
        # LinearRegression has no major hyperparameters to tune
        result = {}
        print(f"MOCK SUGGESTIONS OUTPUT for {model_name}: {result}")
        return result
    else:
        # Default suggestions for unknown models
        result = {
            "n_estimators": 150,
            "max_depth": 8
        }
        print(f"MOCK SUGGESTIONS OUTPUT for {model_name}: {result}")
        return result
