"""
Test script for training versioning and history endpoints.
Run this after starting the FastAPI server with: uvicorn main:app --reload
"""
import requests
import json
import time

# Configuration
BASE_URL = "http://localhost:8000"
CREATE_ENDPOINT = f"{BASE_URL}/projects/create"
TRAINING_ENDPOINT = f"{BASE_URL}/training/start"
HISTORY_ENDPOINT = f"{BASE_URL}/training/history"

def test_training_versioning():
    """Test training versioning and history retrieval."""
    
    # Create a sample CSV file
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
    with open("test_versioning.csv", "w") as f:
        f.write(csv_content)
    
    print("="*70)
    print("Testing Training Versioning & History")
    print("="*70)
    
    # Step 1: Upload the file first
    print("\n[Step 1: Upload CSV File]")
    
    try:
        with open("test_versioning.csv", "rb") as f:
            files = {"file": ("test_versioning.csv", f, "text/csv")}
            data = {
                "project_name": "Versioning Test Project",
                "target_column": "income"
            }
            upload_response = requests.post(CREATE_ENDPOINT, files=files, data=data, timeout=10)
        
        if upload_response.status_code != 201:
            print(f"❌ Upload failed with status {upload_response.status_code}")
            return
        
        upload_data = upload_response.json()
        file_id = upload_data["file_id"]
        print(f"✅ File uploaded successfully!")
        print(f"File ID: {file_id}")
        
    except requests.exceptions.ConnectionError:
        print("❌ Connection Error: Make sure the FastAPI server is running!")
        print("   Start it with: uvicorn main:app --reload")
        return
    except Exception as e:
        print(f"❌ Upload Error: {str(e)}")
        return
    
    # Step 2: First training run (Version 1)
    print("\n" + "="*70)
    print("\n[Step 2: First Training Run - Version 1]")
    
    try:
        training_request = {
            "file_id": file_id,
            "target_column": "income"
        }
        
        training_response = requests.post(
            TRAINING_ENDPOINT,
            json=training_request,
            timeout=30
        )
        
        if training_response.status_code == 200:
            results = training_response.json()
            version = results.get("version")
            
            print(f"✅ Training completed!")
            print(f"Version: {version}")
            print(f"Best Model: {results['best_model']['name']}")
            print(f"Best RMSE: {results['best_model']['rmse']:.2f}")
            
            if version == 1:
                print("\n✅ Version 1 correctly assigned!")
            else:
                print(f"\n❌ Expected version 1, got {version}")
        else:
            print(f"❌ Training failed: {training_response.json()}")
            return
            
    except Exception as e:
        print(f"❌ Training Error: {str(e)}")
        return
    
    # Step 3: Second training run (Version 2)
    print("\n" + "="*70)
    print("\n[Step 3: Second Training Run - Version 2]")
    print("Running training again on the same file...")
    
    try:
        time.sleep(0.5)  # Small delay
        
        training_response = requests.post(
            TRAINING_ENDPOINT,
            json=training_request,
            timeout=30
        )
        
        if training_response.status_code == 200:
            results = training_response.json()
            version = results.get("version")
            
            print(f"✅ Training completed!")
            print(f"Version: {version}")
            print(f"Best Model: {results['best_model']['name']}")
            print(f"Best RMSE: {results['best_model']['rmse']:.2f}")
            
            if version == 2:
                print("\n✅ Version 2 correctly assigned!")
            else:
                print(f"\n❌ Expected version 2, got {version}")
        else:
            print(f"❌ Training failed: {training_response.json()}")
            return
            
    except Exception as e:
        print(f"❌ Training Error: {str(e)}")
        return
    
    # Step 4: Third training run (Version 3)
    print("\n" + "="*70)
    print("\n[Step 4: Third Training Run - Version 3]")
    print("Running training one more time...")
    
    try:
        time.sleep(0.5)  # Small delay
        
        training_response = requests.post(
            TRAINING_ENDPOINT,
            json=training_request,
            timeout=30
        )
        
        if training_response.status_code == 200:
            results = training_response.json()
            version = results.get("version")
            
            print(f"✅ Training completed!")
            print(f"Version: {version}")
            print(f"Best Model: {results['best_model']['name']}")
            
            if version == 3:
                print("\n✅ Version 3 correctly assigned!")
            else:
                print(f"\n❌ Expected version 3, got {version}")
        else:
            print(f"❌ Training failed: {training_response.json()}")
            return
            
    except Exception as e:
        print(f"❌ Training Error: {str(e)}")
        return
    
    # Step 5: Retrieve training history
    print("\n" + "="*70)
    print("\n[Step 5: Retrieve Training History]")
    print(f"Getting history for file: {file_id}")
    
    try:
        history_response = requests.get(
            f"{HISTORY_ENDPOINT}/{file_id}",
            timeout=10
        )
        
        if history_response.status_code == 200:
            history = history_response.json()
            
            print(f"\n✅ History retrieved successfully!")
            print(f"File ID: {history['file_id']}")
            print(f"Total Versions: {len(history['versions'])}")
            
            print("\n" + "-"*70)
            print("Version History:")
            print("-"*70)
            
            for version_data in history["versions"]:
                print(f"\nVersion {version_data['version']}:")
                print(f"  Best Model: {version_data['best_model']['name']}")
                print(f"  RMSE: {version_data['best_model']['rmse']:.2f}")
                print(f"  MAE:  {version_data['best_model']['mae']:.2f}")
                print(f"  R²:   {version_data['best_model']['r2']:.4f}")
                print(f"  Total Models Trained: {len(version_data['models'])}")
            
            if len(history['versions']) == 3:
                print("\n✅ All 3 versions stored correctly!")
            else:
                print(f"\n❌ Expected 3 versions, found {len(history['versions'])}")
        else:
            print(f"❌ History retrieval failed: {history_response.json()}")
            
    except Exception as e:
        print(f"❌ History Error: {str(e)}")
    
    # Step 6: Test history for non-existent file
    print("\n" + "="*70)
    print("\n[Step 6: Test 404 for Non-Existent File]")
    
    try:
        error_response = requests.get(
            f"{HISTORY_ENDPOINT}/nonexistent-file.csv",
            timeout=10
        )
        
        print(f"Status Code: {error_response.status_code}")
        print(f"Response: {error_response.json()}")
        
        if error_response.status_code == 404:
            print("\n✅ Correctly returned 404 for non-existent file history")
        else:
            print("\n❌ Expected 404 status code")
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
    
    # Step 7: Upload a new file and verify Version 1 starts fresh
    print("\n" + "="*70)
    print("\n[Step 7: Test New File Gets Version 1]")
    
    try:
        with open("test_versioning.csv", "rb") as f:
            files = {"file": ("test_versioning_2.csv", f, "text/csv")}
            data = {
                "project_name": "Second Project",
                "target_column": "income"
            }
            upload_response = requests.post(CREATE_ENDPOINT, files=files, data=data, timeout=10)
        
        if upload_response.status_code == 201:
            new_file_id = upload_response.json()["file_id"]
            print(f"New File ID: {new_file_id}")
            
            # Train on new file
            new_training_request = {
                "file_id": new_file_id,
                "target_column": "income"
            }
            
            training_response = requests.post(
                TRAINING_ENDPOINT,
                json=new_training_request,
                timeout=30
            )
            
            if training_response.status_code == 200:
                results = training_response.json()
                version = results.get("version")
                
                print(f"\n✅ New file training completed!")
                print(f"Version: {version}")
                
                if version == 1:
                    print("\n✅ New file correctly starts at version 1!")
                else:
                    print(f"\n❌ Expected version 1, got {version}")
            else:
                print(f"❌ Training failed")
                
    except Exception as e:
        print(f"❌ Error: {str(e)}")
    
    # Cleanup
    print("\n" + "="*70)
    import os
    try:
        time.sleep(0.1)
        if os.path.exists("test_versioning.csv"):
            os.remove("test_versioning.csv")
            print("\n🧹 Cleanup complete")
    except Exception as cleanup_error:
        print(f"\n⚠️  Cleanup warning: {str(cleanup_error)}")
    
    print("\n" + "="*70)
    print("All versioning tests completed!")
    print("="*70 + "\n")


if __name__ == "__main__":
    test_training_versioning()
