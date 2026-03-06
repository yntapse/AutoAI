"""
Test script for the /projects/create endpoint.
Run this after starting the FastAPI server with: uvicorn main:app --reload
"""
import requests

# Configuration
BASE_URL = "http://localhost:8000"
ENDPOINT = f"{BASE_URL}/projects/create"

def test_create_project():
    """Test the project creation endpoint with a sample CSV file."""
    
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
    
    # Make request
    print("🚀 Testing POST /projects/create endpoint...")
    print(f"� File: test_sample.csv")
    print(f"📁 Project Name: Test Customer Analysis")
    print()
    
    try:
        # Open file properly in context manager
        with open("test_sample.csv", "rb") as f:
            files = {
                "file": ("test_sample.csv", f, "text/csv")
            }
            data = {
                "project_name": "Test Customer Analysis",
                "target_column": "income"
            }
            
            response = requests.post(ENDPOINT, files=files, data=data, timeout=10)
        
        print(f"📊 Status Code: {response.status_code}")
        print(f"📦 Response:")
        print(response.json())
        
        if response.status_code == 201:
            print("\n✅ Success! Project created successfully.")
        else:
            print("\n❌ Failed! Check the response above.")
            
    except requests.exceptions.ConnectionError:
        print("❌ Connection Error: Make sure the FastAPI server is running!")
        print("   Start it with: uvicorn main:app --reload")
    except Exception as e:
        print(f"❌ Error: {str(e)}")
    finally:
        # Cleanup
        import os
        import time
        try:
            time.sleep(0.1)  # Brief delay to ensure file is released
            if os.path.exists("test_sample.csv"):
                os.remove("test_sample.csv")
                print("\n🧹 Cleaned up test file.")
        except PermissionError:
            print("\n⚠️  Note: Could not delete test file (still in use). Please delete manually if needed.")
        except Exception as cleanup_error:
            print(f"\n⚠️  Cleanup warning: {str(cleanup_error)}")


if __name__ == "__main__":
    test_create_project()
