# PyrunAI Backend

FastAPI backend for AutoAI Builder - PyrunAI SaaS platform.

## 🚀 Setup

### Install Dependencies
```bash
pip install fastapi uvicorn[standard] pandas scikit-learn xgboost python-multipart
```

### Run Development Server
```bash
uvicorn main:app --reload
```

The server will start at `http://localhost:8000`

## 📝 API Documentation

Once the server is running, visit:
- **Interactive API docs**: http://localhost:8000/docs
- **Alternative docs**: http://localhost:8000/redoc

## 🔌 Endpoints

### `GET /`
Health check endpoint.

**Response:**
```json
{
  "message": "PyrunAI Backend Running"
}
```

### `POST /projects/create`
Create a new ML project by uploading a CSV file.

**Request:**
- `project_name` (form field, required): Name of the project
- `file` (file upload, required): CSV file to upload

**Response (201 Created):**
```json
{
  "project_name": "Customer Churn Predictor",
  "total_rows": 1000,
  "total_columns": 12,
  "column_names": ["id", "age", "gender", "..."],
  "file_id": "uuid-generated-filename.csv",
  "original_filename": "churn_data.csv"
}
```

**Error Responses:**
- `400 Bad Request`: Invalid file format or empty CSV
- `500 Internal Server Error`: Server-side processing error

## 🧪 Testing

Run the test script:
```bash
python test_upload.py
```

Or test manually with curl:
```bash
curl -X POST "http://localhost:8000/projects/create" \
  -F "project_name=Test Project" \
  -F "file=@your_data.csv"
```

## 📁 Project Structure

```
backend/
├── main.py              # FastAPI application
├── uploads/             # Uploaded CSV files (auto-created)
├── test_upload.py       # Test script
└── README.md            # This file
```

## 🔐 Features

- ✅ CSV file upload with validation
- ✅ Unique filename generation (UUID)
- ✅ Automatic uploads folder creation
- ✅ Pandas CSV analysis
- ✅ Error handling with proper HTTP status codes
- ✅ Automatic cleanup on processing errors
- ✅ Production-ready structure

## 🎯 Next Steps

- [ ] Add database integration
- [ ] Implement ML model training pipeline
- [ ] Add authentication/authorization
- [ ] Add project listing endpoint
- [ ] Add model evaluation endpoints
