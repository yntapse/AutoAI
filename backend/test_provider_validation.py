"""
Quick test to verify LLM provider validation in /training/fine-tune endpoint.
"""
import requests

BASE_URL = "http://localhost:8000"
FINETUNE_ENDPOINT = f"{BASE_URL}/training/fine-tune"

def test_provider_validation():
    """Test that invalid providers return 400 error."""
    
    print("="*70)
    print("Testing LLM Provider Validation")
    print("="*70)
    
    # Test with invalid provider
    print("\n[Test 1: Invalid Provider]")
    invalid_request = {
        "file_id": "test.csv",
        "target_column": "target",
        "llm_provider": "invalid_provider"
    }
    
    try:
        response = requests.post(FINETUNE_ENDPOINT, json=invalid_request, timeout=10)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        
        if response.status_code == 400:
            print("[PASS] Correctly rejected invalid provider with 400")
        else:
            print(f"[FAIL] Expected 400, got {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("[ERROR] Connection Error: Make sure the FastAPI server is running!")
        return
    except Exception as e:
        print(f"[ERROR] Error: {str(e)}")
    
    # Test with each valid provider
    print("\n[Test 2: Valid Providers]")
    valid_providers = ["openai", "claude", "gemini", "mistral", "groq"]
    
    for provider in valid_providers:
        valid_request = {
            "file_id": "test.csv",
            "target_column": "target",
            "llm_provider": provider
        }
        
        try:
            response = requests.post(FINETUNE_ENDPOINT, json=valid_request, timeout=10)
            # We expect 404 (no training history) not 400 (invalid provider)
            if response.status_code == 404:
                print(f"[PASS] '{provider}' accepted (got 404 for missing history)")
            elif response.status_code == 400 and "Invalid llm_provider" in str(response.json()):
                print(f"[FAIL] '{provider}' rejected as invalid")
            else:
                print(f"[INFO] '{provider}' got status {response.status_code}")
        except Exception as e:
            print(f"[ERROR] Error testing '{provider}': {str(e)}")
    
    print("\n" + "="*70)
    print("Provider validation tests completed!")
    print("="*70 + "\n")

if __name__ == "__main__":
    test_provider_validation()
