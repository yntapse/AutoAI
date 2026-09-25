"""
Test script for the /training/start endpoint.
Run this after starting the FastAPI server with: uvicorn main:app --reload
"""
import requests
import json

# Configuration
BASE_URL = "http://localhost:8000"
CREATE_ENDPOINT = f"{BASE_URL}/projects/create"
TRAINING_ENDPOINT = f"{BASE_URL}/training/start"

def test_training_endpoint():
    """Test the training endpoint with a sample CSV file."""
    
    # Create a sample CSV file with more data for better training
    csv_content = """name,age,city,income,experience
John Doe,30,New York,50000,5
Jane Smith,25,Los Angeles,60000,3
Bob Johnson,35,Chicago,55000,8
Alice Brown,28,Houston,58000,4
Charlie Wilson,32,Phoenix,52000,6
David Lee,40,Seattle,75000,15
Emma Davis,27,Boston,62000,4
Frank Miller,33,Denver,58000,7
Grace Taylor,29,Miami,59000,5
Henry Clark,38,Austin,72000,12
Isabel Martinez,26,Portland,56000,3
Jack Robinson,34,Atlanta,64000,9
Karen White,31,Dallas,61000,6
Leo Harris,36,San Diego,68000,11
Maria Garcia,24,San Jose,54000,2
Nathan King,37,Columbus,66000,10
Olivia Scott,28,Indianapolis,57000,5
Paul Adams,33,Charlotte,63000,8
Quinn Baker,30,Detroit,59000,6
Rachel Green,29,Nashville,60000,5"""
    
    # Save test CSV
    with open("test_training.csv", "w") as f:
        f.write(csv_content)
    
    print("="*70)
    print("Testing /training/start Endpoint")
    print("="*70)
    
    # Step 1: Upload the file first
    print("\n[Step 1: Upload CSV File]")
    print("Creating project and uploading CSV...")
    
    try:
        with open("test_training.csv", "rb") as f:
            files = {"file": ("test_training.csv", f, "text/csv")}
            data = {
                "project_name": "Training Test Project",
                "target_column": "income"
            }
            upload_response = requests.post(CREATE_ENDPOINT, files=files, data=data, timeout=10)
        
        if upload_response.status_code != 201:
            print(f"❌ Upload failed with status {upload_response.status_code}")
            print(upload_response.json())
            return
        
        upload_data = upload_response.json()
        file_id = upload_data["file_id"]
        print(f"✅ File uploaded successfully!")
        print(f"File ID: {file_id}")
        print(f"Total rows: {upload_data['total_rows']}")
        print(f"Total columns: {upload_data['total_columns']}")
        print(f"Columns: {', '.join(upload_data['column_names'])}")
        
    except requests.exceptions.ConnectionError:
        print("❌ Connection Error: Make sure the FastAPI server is running!")
        print("   Start it with: uvicorn main:app --reload")
        return
    except Exception as e:
        print(f"❌ Upload Error: {str(e)}")
        return
    
    # Step 2: Start training
    print("\n" + "="*70)
    print("\n[Step 2: Start Training]")
    print(f"Training models with target column: income")
    print("Models: LinearRegression, Ridge, RandomForestRegressor, XGBRegressor")
    print()
    
    try:
        training_request = {
            "file_id": file_id,
            "target_column": "income"
        }
        
        training_response = requests.post(
            TRAINING_ENDPOINT,
            json=training_request,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        
        print(f"Status Code: {training_response.status_code}")
        
        if training_response.status_code == 200:
            results = training_response.json()
            
            print("\n📊 Training Results:")
            print("-" * 70)
            
            for i, model in enumerate(results["models"], 1):
                print(f"\n{i}. {model['name']}")
                print(f"   RMSE: {model['rmse']:.2f}")
                print(f"   MAE:  {model['mae']:.2f}")
                print(f"   R²:   {model['r2']:.4f}")
            
            print("\n" + "="*70)
            print("\n🏆 Best Model:")
            best = results["best_model"]
            print(f"   Name: {best['name']}")
            print(f"   RMSE: {best['rmse']:.2f}")
            print(f"   MAE:  {best['mae']:.2f}")
            print(f"   R²:   {best['r2']:.4f}")
            
            print("\n✅ Training completed successfully!")
            
        else:
            print(f"\n❌ Training failed!")
            print(f"Response: {training_response.json()}")
            
    except Exception as e:
        print(f"❌ Training Error: {str(e)}")
    
    # Step 3: Test with invalid file_id
    print("\n" + "="*70)
    print("\n[Step 3: Test Error Handling - Invalid File ID]")
    
    try:
        invalid_request = {
            "file_id": "nonexistent-file.csv",
            "target_column": "income"
        }
        
        error_response = requests.post(
            TRAINING_ENDPOINT,
            json=invalid_request,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        print(f"Status Code: {error_response.status_code}")
        print(f"Response: {error_response.json()}")
        
        if error_response.status_code == 404:
            print("\n✅ Correctly returned 404 for non-existent file")
        else:
            print("\n❌ Expected 404 status code")
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
    
    # Step 4: Test with invalid target column
    print("\n" + "="*70)
    print("\n[Step 4: Test Error Handling - Invalid Target Column]")
    
    try:
        invalid_target_request = {
            "file_id": file_id,
            "target_column": "nonexistent_column"
        }
        
        error_response = requests.post(
            TRAINING_ENDPOINT,
            json=invalid_target_request,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        print(f"Status Code: {error_response.status_code}")
        print(f"Response: {error_response.json()}")
        
        if error_response.status_code == 400:
            print("\n✅ Correctly returned 400 for invalid target column")
        else:
            print("\n❌ Expected 400 status code")
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
    
    # Step 5: Test with non-numeric target
    print("\n" + "="*70)
    print("\n[Step 5: Test Error Handling - Non-Numeric Target]")
    
    try:
        non_numeric_request = {
            "file_id": file_id,
            "target_column": "name"
        }
        
        error_response = requests.post(
            TRAINING_ENDPOINT,
            json=non_numeric_request,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        print(f"Status Code: {error_response.status_code}")
        print(f"Response: {error_response.json()}")
        
        if error_response.status_code == 400:
            print("\n✅ Correctly returned 400 for non-numeric target")
        else:
            print("\n❌ Expected 400 status code")
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
    
    # Cleanup
    print("\n" + "="*70)
    import os
    import time
    try:
        time.sleep(0.1)
        if os.path.exists("test_training.csv"):
            os.remove("test_training.csv")
            print("\n🧹 Cleanup complete - test file removed")
    except Exception as cleanup_error:
        print(f"\n⚠️  Cleanup warning: {str(cleanup_error)}")
    
    print("\n" + "="*70)
    print("All tests completed!")
    print("="*70 + "\n")


if __name__ == "__main__":
    test_training_endpoint()
