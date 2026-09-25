"""
Quick reference for testing OpenAI integration.

SETUP:
------
1. Install OpenAI library:
   pip install openai

2. Set API key:
   
   Option A - Using .env file (RECOMMENDED):
   - Copy .env.example to .env
   - Edit .env and add your real API key:
     OPENAI_API_KEY=sk-your-actual-key-here
   
   Option B - Using environment variable (PowerShell):
   $env:OPENAI_API_KEY='sk-your-actual-key-here'
   
   Option C - Using environment variable (Bash/Linux):
   export OPENAI_API_KEY='sk-your-actual-key-here'

3. Test it:
   python test_openai_provider.py

EXAMPLE USAGE:
--------------
from services.llm_service import get_hyperparameter_suggestions

result = get_hyperparameter_suggestions(
    model_name="RandomForestRegressor",
    metrics={"rmse": 3886.56, "r2": 0.5826, "mae": 3226.15},
    llm_provider="openai"
)

print(result)
# Output: {"hyperparameters": {"n_estimators": 150, "max_depth": 8, ...}}

SECURITY NOTES:
---------------
- API key is read from environment only (not hardcoded)
- 30-second timeout prevents hanging
- JSON parsing is validated before returning
- No arbitrary code execution
- Safe fallback to empty dict on any error

ERROR HANDLING:
---------------
The function returns {} (empty dict) if:
- OPENAI_API_KEY not set
- openai library not installed
- API call fails/times out
- Response is not valid JSON
- Response missing "hyperparameters" key
"""
