"""
Test script for the /training/fine-tune endpoint.
Run this after starting the FastAPI server with: uvicorn main:app --reload
"""
import requests
import json

# Configuration
BASE_URL = "http://localhost:8000"
CREATE_ENDPOINT = f"{BASE_URL}/projects/create"
TRAINING_ENDPOINT = f"{BASE_URL}/training/start"
FINETUNE_ENDPOINT = f"{BASE_URL}/training/fine-tune"
HISTORY_ENDPOINT = f"{BASE_URL}/training/history"

def test_fine_tune():
    """Test the fine-tune endpoint."""
    
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
    with open("test_finetune.csv", "w") as f:
        f.write(csv_content)
    
    print("="*70)
    print("Testing /training/fine-tune Endpoint")
    print("="*70)
    
    # Step 1: Upload the file
    print("\n[Step 1: Upload CSV File]")
    
    try:
        with open("test_finetune.csv", "rb") as f:
            files = {"file": ("test_finetune.csv", f, "text/csv")}
            data = {
                "project_name": "Fine-tune Test Project",
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
    
    # Step 2: Test fine-tune without initial training (should fail)
    print("\n" + "="*70)
    print("\n[Step 2: Test Fine-tune Without Training History - Should Return 404]")
    
    try:
        finetune_request = {
            "file_id": file_id,
            "target_column": "income",
            "llm_provider": "openai"
        }
        
        error_response = requests.post(
            FINETUNE_ENDPOINT,
            json=finetune_request,
            timeout=10
        )
        
        print(f"Status Code: {error_response.status_code}")
        print(f"Response: {error_response.json()}")
        
        if error_response.status_code == 404:
            print("\n✅ Correctly returned 404 for missing training history")
        else:
            print("\n❌ Expected 404 status code")
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
    
    # Step 3: Run initial training
    print("\n" + "="*70)
    print("\n[Step 3: Run Initial Training]")
    
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
            print(f"✅ Initial training completed!")
            print(f"Version: {results['version']}")
            print(f"Best Model: {results['best_model']['name']}")
            print(f"Best RMSE: {results['best_model']['rmse']:.2f}")
            print(f"Best R²: {results['best_model']['r2']:.4f}")
        else:
            print(f"❌ Training failed: {training_response.json()}")
            return
            
    except Exception as e:
        print(f"❌ Training Error: {str(e)}")
        return
    
    # Step 4: Fine-tune the best model
    print("\n" + "="*70)
    print("\n[Step 4: Fine-tune the Best Model]")
    print("Improving hyperparameters and re-training...")
    
    try:
        finetune_response = requests.post(
            FINETUNE_ENDPOINT,
            json=finetune_request,
            timeout=30
        )
        
        if finetune_response.status_code == 200:
            results = finetune_response.json()
            
            print(f"\n✅ Fine-tuning completed!")
            print(f"\nPrevious Version: {results['previous_version']}")
            print(f"New Version: {results['new_version']}")
            
            print(f"\n{'='*70}")
            print("BEFORE (Original Model):")
            print(f"{'='*70}")
            before = results['before']
            print(f"Model: {before['name']}")
            print(f"RMSE:  {before['rmse']:.2f}")
            print(f"MAE:   {before['mae']:.2f}")
            print(f"R²:    {before['r2']:.4f}")
            
            print(f"\n{'='*70}")
            print("AFTER (Fine-tuned Model):")
            print(f"{'='*70}")
            after = results['after']
            print(f"Model: {after['name']}")
            print(f"RMSE:  {after['rmse']:.2f}")
            print(f"MAE:   {after['mae']:.2f}")
            print(f"R²:    {after['r2']:.4f}")
            
            print(f"\n{'='*70}")
            print("IMPROVEMENT:")
            print(f"{'='*70}")
            improvement = results['improvement']
            rmse_change = improvement['rmse_change']
            r2_change = improvement['r2_change']
            
            print(f"RMSE Change: {rmse_change:+.2f} {'(Better!)' if rmse_change < 0 else '(Worse)'}")
            print(f"R² Change:   {r2_change:+.4f} {'(Better!)' if r2_change > 0 else '(Worse)'}")
            
            if results['new_version'] == results['previous_version'] + 1:
                print("\n✅ Version correctly incremented!")
            else:
                print("\n❌ Version increment incorrect")
        else:
            print(f"❌ Fine-tuning failed: {finetune_response.json()}")
            return
            
    except Exception as e:
        print(f"❌ Fine-tuning Error: {str(e)}")
        return
    
    # Step 5: Check training history
    print("\n" + "="*70)
    print("\n[Step 5: Verify Training History]")
    
    try:
        history_response = requests.get(
            f"{HISTORY_ENDPOINT}/{file_id}",
            timeout=10
        )
        
        if history_response.status_code == 200:
            history = history_response.json()
            
            print(f"✅ History retrieved!")
            print(f"Total Versions: {len(history['versions'])}")
            
            if len(history['versions']) == 2:
                print("\n✅ Both versions stored in history!")
                
                print("\n" + "-"*70)
                for version_data in history["versions"]:
                    print(f"\nVersion {version_data['version']}:")
                    print(f"  Best Model: {version_data['best_model']['name']}")
                    print(f"  RMSE: {version_data['best_model']['rmse']:.2f}")
                    print(f"  R²:   {version_data['best_model']['r2']:.4f}")
                    print(f"  Models in version: {len(version_data['models'])}")
            else:
                print(f"\n❌ Expected 2 versions, found {len(history['versions'])}")
        else:
            print(f"❌ History retrieval failed")
            
    except Exception as e:
        print(f"❌ History Error: {str(e)}")
    
    # Step 6: Fine-tune again (Version 3)
    print("\n" + "="*70)
    print("\n[Step 6: Fine-tune Again to Create Version 3]")
    
    try:
        finetune_response = requests.post(
            FINETUNE_ENDPOINT,
            json=finetune_request,
            timeout=30
        )
        
        if finetune_response.status_code == 200:
            results = finetune_response.json()
            
            print(f"✅ Second fine-tuning completed!")
            print(f"Previous Version: {results['previous_version']}")
            print(f"New Version: {results['new_version']}")
            
            if results['new_version'] == 3:
                print("\n✅ Version 3 correctly created!")
            else:
                print(f"\n❌ Expected version 3, got {results['new_version']}")
        else:
            print(f"❌ Fine-tuning failed")
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
    
    # Cleanup
    print("\n" + "="*70)
    import os
    import time
    try:
        time.sleep(0.1)
        if os.path.exists("test_finetune.csv"):
            os.remove("test_finetune.csv")
            print("\n🧹 Cleanup complete")
    except Exception as cleanup_error:
        print(f"\n⚠️  Cleanup warning: {str(cleanup_error)}")
    
    print("\n" + "="*70)
    print("All fine-tune tests completed!")
    print("="*70 + "\n")


if __name__ == "__main__":
    test_fine_tune()
