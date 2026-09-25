# Setup Instructions

## Environment Variables

Create a `.env` file in the `backend/` directory with your API keys:

```bash
# Copy from example
cp .env.example .env

# Edit .env and add real values
# .env file will be ignored by git (see .gitignore)
```

### Sample .env file:
```
OPENAI_API_KEY=sk-your-actual-key-here
DATABASE_URL=postgresql://pyrun:pyrun123@127.0.0.1:5432/pyrunai
```

`DATABASE_URL` is required for backend startup. If PostgreSQL is not running or the URL is invalid, the API will fail fast with a startup error.

## PostgreSQL via Docker (Recommended)

From `backend/`, run:

```bash
docker compose up -d
```

This project includes `docker-compose.yml` with the required PostgreSQL env vars:

- `POSTGRES_USER=pyrun`
- `POSTGRES_PASSWORD=pyrun123`
- `POSTGRES_DB=pyrunai`

These match:

`DATABASE_URL=postgresql://pyrun:pyrun123@127.0.0.1:5433/pyrunai`

Get your OpenAI API key: https://platform.openai.com/api-keys

## How the Service Loads Environment Variables

The `llm_service.py` reads from environment variables using `os.getenv()`:

```python
api_key = os.getenv("OPENAI_API_KEY")
```

This means the key can come from:
1. **`.env` file** (recommended during development)
2. **System environment variables** (recommended for production)

## Loading .env in Development

If using python-dotenv (install: `pip install python-dotenv`):

```python
from dotenv import load_dotenv
load_dotenv()  # Loads .env file
```

This is already handled automatically by uvicorn with `uvicorn[standard]`.
