"""
Test script to demonstrate the parallel execution of model training scripts.
This simulates the parallel execution without needing the full FastAPI backend.
"""
import time
import concurrent.futures
from typing import List, Dict, Optional
import random


def simulate_model_training(model_name: str, execution_time: float) -> Dict[str, Optional[float]]:
    """Simulate training a single model."""
    print(f"[{time.strftime('%H:%M:%S')}] Started training {model_name}...")
    time.sleep(execution_time)  # Simulate training time
    
    # Simulate metrics
    rmse = random.uniform(0.1, 1.0)
    mae = random.uniform(0.1, 0.8)
    r2 = random.uniform(0.7, 0.95)
    
    print(f"[{time.strftime('%H:%M:%S')}] Completed {model_name} | RMSE={rmse:.4f}")
    
    return {
        "model_name": model_name,
        "rmse": rmse,
        "mae": mae,
        "r2": r2,
        "accuracy": r2,
    }


def test_sequential_execution(models: List[str], execution_times: List[float]):
    """Test sequential execution (OLD approach)."""
    print("\n" + "="*70)
    print("SEQUENTIAL EXECUTION TEST (Old Approach)")
    print("="*70)
    
    start_time = time.time()
    results = []
    
    for model_name, exec_time in zip(models, execution_times):
        result = simulate_model_training(model_name, exec_time)
        results.append(result)
    
    total_time = time.time() - start_time
    
    print(f"\n✓ Sequential execution completed in {total_time:.2f} seconds")
    print(f"  Best model: {min(results, key=lambda x: x['rmse'])['model_name']}")
    
    return results, total_time


def test_parallel_execution(models: List[str], execution_times: List[float]):
    """Test parallel execution (NEW approach)."""
    print("\n" + "="*70)
    print("PARALLEL EXECUTION TEST (New Approach)")
    print("="*70)
    
    start_time = time.time()
    results = []
    
    # Use ThreadPoolExecutor to run models in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(models)) as executor:
        # Submit all jobs at once
        future_to_model = {
            executor.submit(simulate_model_training, model_name, exec_time): model_name
            for model_name, exec_time in zip(models, execution_times)
        }
        
        # Collect results as they complete
        for future in concurrent.futures.as_completed(future_to_model):
            model_name = future_to_model[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as exc:
                print(f"  ✗ {model_name} failed: {exc}")
                results.append({
                    "model_name": model_name,
                    "rmse": None,
                    "mae": None,
                    "r2": None,
                    "accuracy": None,
                })
    
    total_time = time.time() - start_time
    
    print(f"\n✓ Parallel execution completed in {total_time:.2f} seconds")
    valid_results = [r for r in results if r['rmse'] is not None]
    if valid_results:
        print(f"  Best model: {min(valid_results, key=lambda x: x['rmse'])['model_name']}")
    
    return results, total_time


def main():
    """Run the parallel execution test."""
    # Define 6 model families with varying execution times
    models = [
        "LinearRegression",
        "Ridge",
        "Lasso",
        "ElasticNet",
        "RandomForestRegressor",
        "XGBRegressor"
    ]
    
    # Simulate realistic execution times (in seconds)
    execution_times = [2, 2, 2, 3, 4, 5]  # Reduced for demo
    
    print("\n" + "="*70)
    print("PARALLEL MODEL EXECUTION TEST")
    print("="*70)
    print(f"\nTesting with {len(models)} models:")
    for model, exec_time in zip(models, execution_times):
        print(f"  • {model:30s} - ~{exec_time}s")
    
    # Test sequential execution
    seq_results, seq_time = test_sequential_execution(models, execution_times)
    
    # Test parallel execution
    par_results, par_time = test_parallel_execution(models, execution_times)
    
    # Calculate speedup
    speedup = seq_time / par_time if par_time > 0 else 0
    time_saved = seq_time - par_time
    
    print("\n" + "="*70)
    print("PERFORMANCE COMPARISON")
    print("="*70)
    print(f"  Sequential time: {seq_time:.2f}s")
    print(f"  Parallel time:   {par_time:.2f}s")
    print(f"  Speedup:         {speedup:.2f}x")
    print(f"  Time saved:      {time_saved:.2f}s ({time_saved/seq_time*100:.1f}%)")
    print("="*70 + "\n")
    
    print("✓ Test completed successfully!")
    print("\nKey Benefits of Parallel Execution:")
    print("  1. All models train simultaneously instead of sequentially")
    print("  2. Total time ≈ longest model time (not sum of all models)")
    print("  3. Better resource utilization on multi-core systems")
    print("  4. Faster iteration cycles for the agent loop")


if __name__ == "__main__":
    main()
