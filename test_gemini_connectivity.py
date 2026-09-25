import os
import traceback
from pathlib import Path


def _load_key_from_backend_env() -> str | None:
    env_path = Path(__file__).parent / "backend" / ".env"
    if not env_path.exists():
        return None

    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("GEMINI_API_KEY="):
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        return None

    return None


def main() -> None:
    try:
        use_new_sdk = False
        try:
            from google import genai
            use_new_sdk = True
        except Exception:
            import google.generativeai as genai

        api_key = os.getenv("GEMINI_API_KEY") or _load_key_from_backend_env()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")

        prompt = "Return JSON: {\"test\": \"success\"}"

        if use_new_sdk:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model="gemini-flash-latest",
                contents=prompt,
            )
        else:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(prompt)

        print("FULL RAW RESPONSE OBJECT:")
        print(response)
        print("\nRESPONSE.TEXT:")
        print(getattr(response, "text", None))

    except Exception as exc:
        print("ERROR:", str(exc))
        traceback.print_exc()


if __name__ == "__main__":
    main()
