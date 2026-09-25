"""
Test script for the LLM service.
Tests the routing and response structure of hyperparameter suggestions.
"""
import sys
sys.path.append('.')

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

from services.llm_service import get_hyperparameter_suggestions


def test_llm_service():
    """Test LLM service functionality."""
    
    print("="*70)
    print("Testing LLM Service")
    print("="*70)
    
    # Test data
    model_name = "RandomForestRegressor"
    metrics = {
        "rmse": 3886.56,
        "mae": 3226.15,
        "r2": 0.5826
    }
    
    providers = ["openai", "claude", "gemini", "mistral", "groq"]
    
    print("\n[Test 1: Valid Providers]")
    for provider in providers:
        result = get_hyperparameter_suggestions(model_name, metrics, provider)
        
        if "hyperparameters" in result:
            print(f"[PASS] {provider:10s} returned valid structure")
            print(f"       Hyperparameters: {result['hyperparameters']}")
        else:
            print(f"[FAIL] {provider:10s} did not return valid structure")
    
    print("\n[Test 2: Invalid Provider]")
    result = get_hyperparameter_suggestions(model_name, metrics, "invalid_provider")
    if result == {}:
        print("[PASS] Invalid provider correctly returned empty dict")
    else:
        print(f"[FAIL] Invalid provider returned: {result}")
    
    print("\n[Test 3: Different Model Types]")
    models = ["RandomForestRegressor", "XGBRegressor", "Ridge", "LinearRegression"]
    for model in models:
        result = get_hyperparameter_suggestions(model, metrics, "openai")
        if "hyperparameters" in result:
            print(f"[PASS] {model:25s} -> {result['hyperparameters']}")
        else:
            print(f"[FAIL] {model:25s} failed")
    
    print("\n" + "="*70)
    print("LLM Service tests completed!")
    print("="*70 + "\n")


if __name__ == "__main__":
    test_llm_service()
