# Testing Guide: LLM Model Code Generation Changes

## ✅ Changes Implemented

### 1. **Removed Hardcoded Model Instantiation** 
   - Location: `backend/main.py` line 1528-1533
   - **Before**: Returned fixed hyperparameters like `Ridge(alpha=1.0)`, `XGBRegressor(n_estimators=150)`
   - **After**: Returns placeholder comment, LLM generates full model definition

### 2. **Updated LLM Prompt with Full Creative Freedom**
   - Location: `backend/main.py` line 3093-3125
   - **Before**: "Do NOT redefine model class", restrictive constraints
   - **After**: "FULL CONTROL to define model instantiation with ANY hyperparameters"
   - Provides: Previous iteration code, performance metrics, dataset context

### 3. **Updated Frontend to Display R² Score**
   - Location: `src/app/(dashboard)/training/[id]/page.tsx` line 803, 854
   - **Before**: Fake "accuracy" calculated from RMSE (always 0%)
   - **After**: Real R² score (coefficient of determination)

### 4. **Removed Model Injection**
   - Location: `backend/main.py` line 3157-3190
   - **Before**: System injected hardcoded model, `is_model_injected=True`
   - **After**: LLM generates model in TRAINING section, `is_model_injected=False`

---

## 🧪 How to Test

### **Step 1: Start Fresh Training**

1. **Start Backend** (if not running):
   ```powershell
   cd "g:\ai agent saas\autoai-builder\backend"
   python main.py
   ```

2. **Start Frontend** (in new terminal):
   ```powershell
   cd "g:\ai agent saas\autoai-builder"
   npm run dev
   ```

3. **Navigate to**: `http://localhost:3000`

4. **Upload a dataset** and start training with:
   - LLM Provider: OpenAI
   - Model: gpt-4o-mini
   - At least 3-4 iterations

---

## 🔍 What to Verify

### **Test 1: Each Iteration Improves Models**

**How to Check:**
- Monitor the training page during execution
- Look at "RMSE" values decreasing across iterations
- Check R² Score increasing (higher is better)

**Expected Result:**
- Iteration 1: RMSE ~15000, R² ~40-60%
- Iteration 2: RMSE ~12000, R² ~60-75%
- Iteration 3: RMSE ~10000, R² ~75-85%
- Iteration 4: RMSE ~9000, R² ~85-90%

**Where to Find:**
- Live: Training page → "Models Being Trained" cards
- After: Results page → "Model Leaderboard" table
- Backend logs: Look for "ITERATION X RESULT → Model: ..., RMSE: ..."

---

### **Test 2: Each Iteration Generates Different Code**

**How to Check:**
1. **During Training**: Download code for the same model (e.g., Ridge) at different iterations
2. **Compare**: Training sections should have different hyperparameters, preprocessing strategies

**Expected Differences:**

**Iteration 1 - Ridge:**
```python
# LLM might start conservative
model = Ridge(alpha=1.0)
model.fit(X_train, y_train)
predictions = model.predict(X_test)
```

**Iteration 2 - Ridge (after seeing RMSE):**
```python
# LLM tries hyperparameter tuning
from sklearn.model_selection import GridSearchCV
param_grid = {'alpha': [0.1, 1.0, 10.0, 100.0]}
model = GridSearchCV(Ridge(), param_grid, cv=5, scoring='neg_mean_squared_error')
model.fit(X_train, y_train)
predictions = model.predict(X_test)
```

**Iteration 3 - Ridge (optimization):**
```python
# LLM adds feature selection based on previous results
from sklearn.feature_selection import SelectKBest, f_regression
selector = SelectKBest(f_regression, k=15)
X_train_selected = selector.fit_transform(X_train, y_train)
X_test_selected = selector.transform(X_test)
model = Ridge(alpha=0.5, solver='saga', max_iter=5000)
model.fit(X_train_selected, y_train)
predictions = model.predict(X_test_selected)
```

---

### **Test 3: Each ML Model Has Unique Code**

**How to Check:**
1. **After Training Completes**: Go to Results page
2. **Download Code** for different models (LinearRegression, Ridge, RandomForest, XGBoost)
3. **Compare**: Each should have model-specific optimizations

**Expected Differences:**

**LinearRegression (Simple):**
```python
# No hyperparameters needed
model = LinearRegression()
model.fit(X_train, y_train)
predictions = model.predict(X_test)
```

**Ridge (Regularization Focus):**
```python
# Alpha tuning for regularization
model = Ridge(alpha=5.0, solver='cholesky')
model.fit(X_train, y_train)
predictions = model.predict(X_test)
```

**RandomForestRegressor (Tree-Based):**
```python
# Tree hyperparameters + feature importance
model = RandomForestRegressor(
    n_estimators=200,
    max_depth=15,
    min_samples_split=5,
    max_features='sqrt',
    random_state=42
)
model.fit(X_train, y_train)

# Use feature importances for selection
importances = model.feature_importances_
top_features = np.argsort(importances)[-20:]
X_train = X_train[:, top_features]
X_test = X_test[:, top_features]

model.fit(X_train, y_train)
predictions = model.predict(X_test)
```

**XGBRegressor (Gradient Boosting):**
```python
# Boosting-specific parameters
model = XGBRegressor(
    n_estimators=500,
    max_depth=8,
    learning_rate=0.01,
    subsample=0.8,
    colsample_bytree=0.7,
    gamma=0.1,
    reg_alpha=0.5,
    reg_lambda=1.0,
    random_state=42,
    verbosity=0
)
model.fit(X_train, y_train)
predictions = model.predict(X_test)
```

---

### **Test 4: Download Button Provides Latest Code**

**How to Check:**
1. **Go to Results page** after training completes
2. **Click "Download Code"** for any model
3. **Check file contents**

**Expected Result:**
- File should contain code from the **BEST performing iteration**
- Preprocessing section should show iteration-specific improvements
- Training section should include LLM-generated model instantiation
- Hyperparameters should be optimized (not default values)

**File Naming:**
- Format: `training_code_{training_run_id}_{model_name}.py`
- Example: `training_code_abc123_Ridge.py`

**What Latest Means:**
- Code from the iteration with **lowest RMSE** for that model
- Includes all optimizations LLM learned during training
- NOT the first iteration's code

---

## 📊 Expected vs Old Behavior

### **OLD System (Downloaded File You Have)**
```python
# SYSTEM MODEL SECTION is EMPTY
# 

# AUTONOMOUS_TRAINING_SECTION
try:
    hyperparams = {}
    model.fit(X_train, y_train)  # ERROR: model undefined!
    predictions = model.predict(X_test)
except Exception:
    model = Ridge(alpha=1.0)  # Falls back to hardcoded
```

**Problem**: Model undefined → falls back to system default → all models use generic code

---

### **NEW System (After Your Changes)**
```python
# SYSTEM MODEL SECTION is EMPTY (no injection)


# AUTONOMOUS_TRAINING_SECTION
try:
    # LLM generates FULL model instantiation
    from sklearn.model_selection import GridSearchCV
    
    param_grid = {
        'alpha': [0.1, 0.5, 1.0, 5.0, 10.0],
        'solver': ['auto', 'svd', 'cholesky']
    }
    
    model = GridSearchCV(
        Ridge(),
        param_grid,
        cv=5,
        scoring='neg_mean_squared_error',
        n_jobs=-1
    )
    
    model.fit(X_train, y_train)
    predictions = model.predict(X_test)
except Exception:
    # Falls back only on error
```

**Result**: Each model gets unique LLM-generated code with specific hyperparameters

---

## 🐛 Troubleshooting

### **If Models Still Look Similar:**

1. **Check LLM API Key**:
   ```powershell
   # Backend logs should show successful LLM calls
   # Look for: "Calling OpenAI for model generation..."
   ```

2. **Verify Temperature Setting**:
   - LLM should use `temperature=0.7` for creativity
   - Check `_generate_autonomous_section_via_llm()` function

3. **Check Iteration Count**:
   - Need at least 3-4 iterations for LLM to learn
   - First iteration might be conservative

4. **Verify Context is Being Passed**:
   - Backend logs should show: "Previous RMSE: X"
   - LLM receives previous code in prompt

---

## ✅ Success Criteria

After testing, you should see:

- ✅ **Different code per model** (LinearRegression ≠ Ridge ≠ RandomForest ≠ XGBoost)
- ✅ **Different code per iteration** (Iteration 1 ≠ Iteration 2 ≠ Iteration 3)
- ✅ **Improving metrics** (RMSE decreasing, R² increasing across iterations)
- ✅ **LLM-generated hyperparameters** (not hardcoded defaults like `alpha=1.0`)
- ✅ **Model-specific strategies** (Ridge uses alpha tuning, RandomForest uses tree parameters)
- ✅ **Download provides best code** (lowest RMSE iteration, not first iteration)
- ✅ **R² Score displays correctly** (not 0%, actual percentage like 85.3%)

---

## 📝 Quick Comparison Checklist

After training, compare downloaded code files:

```powershell
# Download code for all models
# Then compare:

# 1. Model instantiation lines
Select-String "model = " training_code_*_Ridge.py
Select-String "model = " training_code_*_RandomForest.py
Select-String "model = " training_code_*_XGBoost.py

# 2. Hyperparameters
Select-String "alpha|n_estimators|max_depth|learning_rate" training_code_*.py

# 3. Preprocessing differences
Select-String "PowerTransformer|SelectKBest|GridSearchCV" training_code_*.py
```

**If all files show identical preprocessing and NO model instantiation** → Old code still running

**If files show different hyperparameters and unique preprocessing** → ✅ New code working!

---

## 🚀 Next Steps

1. **Run a fresh training** with at least 4 iterations
2. **Download code** for 3-4 different models
3. **Compare files** using the checklist above
4. **Check metrics** - RMSE should decrease across iterations
5. **Report findings**: Are codes different? Are metrics improving?
