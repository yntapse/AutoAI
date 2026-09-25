"""
Comprehensive test script for the /projects/create endpoint with regression validation.
Run this after starting the FastAPI server with: uvicorn main:app --reload
"""
import requests

# Configuration
BASE_URL = "http://localhost:8000"
ENDPOINT = f"{BASE_URL}/projects/create"

def test_create_project():
    """Test the project creation endpoint with regression validation."""
    
    # Create a simple test CSV file
    csv_content = """name,age,city,income
John Doe,30,New York,50000
Jane Smith,25,Los Angeles,60000
Bob Johnson,35,Chicago,55000
Alice Brown,28,Houston,58000
Charlie Wilson,32,Phoenix,52000"""
    
    # Save test CSV
    with open("test_sample.csv", "w") as f:
        f.write(csv_content)
    
    print("="*70)
    print("Testing POST /projects/create with Regression Validation")
    print("="*70)
    
    # Test 1: Valid request with numeric target
    print("\n[Test 1: Valid Request - Numeric Target Column]")
    print(f"File: test_sample.csv")
    print(f"Project Name: Test Customer Analysis")
    print(f"Target Column: income (numeric)")
    print()
    
    try:
        with open("test_sample.csv", "rb") as f:
            files = {"file": ("test_sample.csv", f, "text/csv")}
            data = {
                "project_name": "Test Customer Analysis",
                "target_column": "income"
            }
            response = requests.post(ENDPOINT, files=files, data=data, timeout=10)
        
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        
        if response.status_code == 201:
            resp_data = response.json()
            assert "target_column" in resp_data, "Missing target_column in response"
            assert resp_data["target_column"] == "income", "Wrong target_column value"
            print("\n✅ Test 1 PASSED - Project created with numeric target")
        else:
            print("\n❌ Test 1 FAILED - Expected 201 status")
            
    except requests.exceptions.ConnectionError:
        print("❌ Connection Error: Make sure the FastAPI server is running!")
        print("   Start it with: uvicorn main:app --reload")
        return
    except Exception as e:
        print(f"❌ Test 1 ERROR: {str(e)}")
    
    # Test 2: Invalid target column (non-existent)
    print("\n" + "="*70)
    print("\n[Test 2: Invalid Request - Non-Existent Target Column]")
    print(f"Target Column: salary (doesn't exist in CSV)")
    print()
    
    try:
        with open("test_sample.csv", "rb") as f:
            files = {"file": ("test_sample.csv", f, "text/csv")}
            data = {
                "project_name": "Test Invalid Column",
                "target_column": "salary"
            }
            response = requests.post(ENDPOINT, files=files, data=data, timeout=10)
        
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        
        if response.status_code == 400:
            detail = response.json().get("detail", "")
            if "not found" in detail.lower() or "salary" in detail:
                print("\n✅ Test 2 PASSED - Correctly rejected non-existent column")
            else:
                print("\n⚠️  Test 2 PARTIAL - Got 400 but wrong error message")
        else:
            print("\n❌ Test 2 FAILED - Expected 400 status")
    except Exception as e:
        print(f"❌ Test 2 ERROR: {str(e)}")
    
    # Test 3: Non-numeric target column
    print("\n" + "="*70)
    print("\n[Test 3: Invalid Request - Non-Numeric Target Column]")
    print(f"Target Column: name (string, not numeric)")
    print()
    
    try:
        with open("test_sample.csv", "rb") as f:
            files = {"file": ("test_sample.csv", f, "text/csv")}
            data = {
                "project_name": "Test Non-Numeric Target",
                "target_column": "name"
            }
            response = requests.post(ENDPOINT, files=files, data=data, timeout=10)
        
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        
        if response.status_code == 400:
            detail = response.json().get("detail", "")
            if "regression" in detail.lower() and "numeric" in detail.lower():
                print("\n✅ Test 3 PASSED - Correctly rejected non-numeric target")
            else:
                print("\n⚠️  Test 3 PARTIAL - Got 400 but wrong error message")
                print(f"   Expected message about regression/numeric target")
        else:
            print("\n❌ Test 3 FAILED - Expected 400 status")
    except Exception as e:
        print(f"❌ Test 3 ERROR: {str(e)}")
    
    # Test 4: Another numeric column (age)
    print("\n" + "="*70)
    print("\n[Test 4: Valid Request - Different Numeric Target]")
    print(f"Target Column: age (numeric)")
    print()
    
    try:
        with open("test_sample.csv", "rb") as f:
            files = {"file": ("test_sample.csv", f, "text/csv")}
            data = {
                "project_name": "Age Prediction Project",
                "target_column": "age"
            }
            response = requests.post(ENDPOINT, files=files, data=data, timeout=10)
        
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        
        if response.status_code == 201:
            resp_data = response.json()
            if resp_data.get("target_column") == "age":
                print("\n✅ Test 4 PASSED - Project created with 'age' as target")
            else:
                print("\n❌ Test 4 FAILED - Wrong target_column in response")
        else:
            print("\n❌ Test 4 FAILED - Expected 201 status")
    except Exception as e:
        print(f"❌ Test 4 ERROR: {str(e)}")
    
    # Cleanup
    print("\n" + "="*70)
    import os
    import time
    try:
        time.sleep(0.1)
        if os.path.exists("test_sample.csv"):
            os.remove("test_sample.csv")
            print("\nCleanup complete - test file removed")
    except PermissionError:
        print("\nCould not delete test file - please remove manually")
    except Exception as cleanup_error:
        print(f"\nCleanup warning: {str(cleanup_error)}")
    
    print("\n" + "="*70)
    print("All tests completed!")
    print("="*70 + "\n")


if __name__ == "__main__":
    test_create_project()
