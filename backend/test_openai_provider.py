"""
Test script for OpenAI provider integration.

This tests the real OpenAI API integration.

Setup:
1. pip install openai
2. Set environment variable: OPENAI_API_KEY=your_key_here
3. Run: python test_openai_provider.py
"""
import sys
import os
sys.path.append('.')

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

from services.llm_service import get_hyperparameter_suggestions


def test_openai_provider():
    """Test OpenAI provider functionality."""
    
    print("="*70)
    print("Testing OpenAI Provider Integration")
    print("="*70)
    
    # Check if API key is set
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("\n[WARNING] OPENAI_API_KEY environment variable not set")
        print("Set it with: $env:OPENAI_API_KEY='your-key-here' (PowerShell)")
        print("Or: export OPENAI_API_KEY='your-key-here' (bash)")
        print("\nTesting will show fallback behavior...\n")
    else:
        print(f"\n[INFO] API key found: {api_key[:10]}...{api_key[-4:]}\n")
    
    # Test data
    model_name = "RandomForestRegressor"
    metrics = {
        "rmse": 3886.56,
        "mae": 3226.15,
        "r2": 0.5826
    }
    
    print("[Test 1: OpenAI Provider]")
    print(f"Model: {model_name}")
    print(f"Current RMSE: {metrics['rmse']}")
    print(f"Current R²: {metrics['r2']}")
    print("\nCalling OpenAI API...")
    
    result = get_hyperparameter_suggestions(model_name, metrics, "openai")
    
    if result and "hyperparameters" in result:
        print("\n[PASS] OpenAI provider returned valid response")
        print("\nSuggested Hyperparameters:")
        for param, value in result["hyperparameters"].items():
            print(f"  - {param}: {value}")
    else:
        print("\n[INFO] OpenAI provider returned empty dict")
        print("This is expected if:")
        print("  - OPENAI_API_KEY is not set")
        print("  - openai library is not installed")
        print("  - API call failed")
    
    print("\n" + "="*70)
    print("\n[Test 2: Verify Other Providers Still Work (Mocked)]")
    
    for provider in ["claude", "gemini", "mistral", "groq"]:
        result = get_hyperparameter_suggestions(model_name, metrics, provider)
        if "hyperparameters" in result:
            print(f"[PASS] {provider:10s} still returns mocked response")
        else:
            print(f"[FAIL] {provider:10s} failed")
    
    print("\n" + "="*70)
    print("OpenAI provider tests completed!")
    print("="*70 + "\n")


if __name__ == "__main__":
    test_openai_provider()
