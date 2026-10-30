"""Complete OpenAI authentication diagnostic for the ETL Copilot.

Touches no ETL, join, schema, validation or SQL logic.

    cd backend
    python -m etl_copilot.test_openai_connection

Produces every piece of evidence needed to decide whether the fault is in
this code or in the key's status at OpenAI:

  [1] the shell value           - is anything set outside .env
  [2] backend/.env              - what the file holds, byte level
  [3] hidden characters         - non-ascii, zero-width, quotes, CR/LF
  [4] structural validation     - prefix, T3BlbkFJ marker, charset
  [5] precedence                - which source actually wins
  [6] other OPENAI_* variables  - base_url / org / project interference
  [7] SDK client                - what the client was built with
  [8] transmitted bearer token  - proof the SDK sends the loaded key
  [9] live call                 - the COMPLETE OpenAI error response

The full key is never printed. Only length, prefix, first/last characters
and a SHA-256 prefix, all of which are safe to paste into a bug report.
"""

import hashlib
import json
import os
import sys
import unicodedata

_SHELL_RAW = os.environ.get("OPENAI_API_KEY")

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BACKEND_DIR, ".env")


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def section(number, title):
    print("\n[%d] %s" % (number, title))


def describe(label, value):
    if value is None:
        print("  %-22s NOT SET" % label)
        return None

    stripped = value.strip()
    print("  %-22s len=%d  sha256=%s" % (label, len(value), sha(value)))
    print("  %-22s head=%r" % ("", stripped[:15]))
    print("  %-22s tail=%r" % ("", stripped[-15:]))

    if stripped != value:
        print("  %-22s *** SURROUNDING WHITESPACE - stripped len=%d ***"
              % ("", len(stripped)))

    return stripped


def main():
    print("=== ETL Copilot OpenAI authentication diagnostic ===")

    # ---------------------------------------------------------------- [1]
    section(1, "OPENAI_API_KEY as the shell provided it")
    shell_key = describe("shell", _SHELL_RAW)
    if shell_key is None:
        print("  -> the shell is empty, so backend/.env is the only source")

    # ---------------------------------------------------------------- [2]
    section(2, "backend/.env at byte level")
    env_key = None

    if not os.path.isfile(ENV_PATH):
        print("  no .env at %s" % ENV_PATH)
    else:
        print("  %s" % ENV_PATH)
        raw = io.open(ENV_PATH, "rb").read()
        matching = [l for l in raw.split(b"\n") if l.startswith(b"OPENAI_API_KEY")]

        if not matching:
            print("  no OPENAI_API_KEY line in the file")
        else:
            if len(matching) > 1:
                print("  *** %d OPENAI_API_KEY LINES - the last one wins ***"
                      % len(matching))
            value_bytes = matching[-1].split(b"=", 1)[1]
            print("  raw value bytes        %d" % len(value_bytes))
            print("  ends with CR           %s" % value_bytes.endswith(b"\r"))
            print("  after stripping CRLF   %d" % len(value_bytes.rstrip(b"\r\n")))

            try:
                from dotenv import dotenv_values
                env_key = dotenv_values(ENV_PATH).get("OPENAI_API_KEY")
                env_key = describe("dotenv parsed", env_key)
                if env_key is not None:
                    print("  CR leaked into value   %s" % env_key.endswith("\r"))
            except ImportError:
                print("  python-dotenv not installed")

    # ---------------------------------------------------------------- [3]
    section(3, "Hidden characters in the parsed key")
    candidate = env_key or shell_key

    if candidate is None:
        print("  no key to inspect")
    else:
        odd = [(i, repr(c), hex(ord(c))) for i, c in enumerate(candidate)
               if ord(c) < 32 or ord(c) > 126]
        print("  non-printable / non-ascii  %s" % (odd or "NONE"))

        risky = [(i, repr(c)) for i, c in enumerate(candidate)
                 if c in " \t\"'\r\n\u200b\u00a0\ufeff"]
        print("  space/quote/zero-width/BOM %s" % (risky or "NONE"))
        print("  unicode categories         %s"
              % sorted({unicodedata.category(c) for c in candidate}))

    # ---------------------------------------------------------------- [4]
    section(4, "Structural validation")
    if candidate is None:
        print("  no key to validate")
    else:
        prefixes = ("sk-svcacct-", "sk-proj-", "sk-admin-", "sk-")
        prefix = next((p for p in prefixes if candidate.startswith(p)), None)
        print("  recognised prefix          %s" % (prefix or "*** NONE ***"))

        marker = "T3BlbkFJ"
        index = candidate.find(marker)
        print("  OpenAI marker 'T3BlbkFJ'   %s"
              % ("present at index %d" % index if index != -1
                 else "*** ABSENT - key is malformed or truncated ***"))

        allowed = set(
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        )
        bad = sorted({c for c in candidate if c not in allowed})
        print("  base64url charset          %s"
              % ("clean" if not bad else "*** BAD CHARS %r ***" % bad))
        print("  length                     %d" % len(candidate))

        structurally_ok = bool(prefix) and index != -1 and not bad
        print("  -> structurally valid      %s" % structurally_ok)

    # ---------------------------------------------------------------- [5]
    section(5, "Precedence after load_dotenv()")
    import database  # noqa: F401  - database.py:6 calls load_dotenv()

    effective = describe("effective", os.environ.get("OPENAI_API_KEY"))

    if shell_key and env_key and shell_key != env_key:
        print("  *** SHELL AND .env DIFFER - load_dotenv does not override,")
        print("      so the SHELL key is the one being sent ***")
    elif effective and env_key and effective == env_key and not shell_key:
        print("  -> the .env key is in use (shell was empty)")

    # ---------------------------------------------------------------- [6]
    section(6, "Other OPENAI_* environment variables")
    others = {k: v for k, v in os.environ.items()
              if k.startswith("OPENAI") and k != "OPENAI_API_KEY"}

    if not others:
        print("  none set - no base_url / organization / project interference")
    for name in sorted(others):
        shown = others[name]
        if "KEY" in name or "SECRET" in name:
            shown = "len=%d head=%r" % (len(shown), shown[:12])
        print("  %-26s %s" % (name, shown))

    # ---------------------------------------------------------------- [7]
    section(7, "OpenAISpecGenerator client")
    try:
        from etl_copilot.openai_spec_generator import OpenAISpecGenerator
        generator = OpenAISpecGenerator()
    except Exception as exc:
        print("  CLIENT CONSTRUCTION FAILED: %s: %s" % (type(exc).__name__, exc))
        return 1

    info = generator.fingerprint()
    print("  key length             %d" % info["length"])
    print("  key sha256             %s" % sha(generator.api_key))
    print("  first 10               %r" % generator.api_key[:10])
    print("  last 10                %r" % generator.api_key[-10:])
    print("  base_url               %s" % generator.client.base_url)
    print("  organization           %s" % generator.client.organization)
    print("  project                %s" % getattr(generator.client, "project", None))
    print("  stripped whitespace    %s" % info["had_surrounding_whitespace"])

    # ---------------------------------------------------------------- [8]
    section(8, "Bearer token actually transmitted (no network used)")
    try:
        import httpx

        captured = {}

        class Capture(httpx.BaseTransport):
            def handle_request(self, request):
                captured["url"] = str(request.url)
                captured["auth"] = request.headers.get("authorization")
                for header in ("openai-organization", "openai-project"):
                    if request.headers.get(header):
                        captured[header] = request.headers[header]
                return httpx.Response(200, json={"object": "list", "data": []})

        probe = generator.client.with_options(
            http_client=httpx.Client(transport=Capture())
        )
        probe.models.list()

        header = captured.get("auth") or ""
        token = header[len("Bearer "):] if header.startswith("Bearer ") else None

        print("  request URL            %s" % captured.get("url"))
        print("  bearer token length    %s" % (len(token) if token else None))
        print("  bearer token sha256    %s" % (sha(token) if token else None))
        print("  matches loaded key     %s" % (token == generator.api_key))
        extra = {k: v for k, v in captured.items() if k.startswith("openai")}
        print("  extra openai-* headers %s" % (extra or "NONE"))

        if token == generator.api_key:
            print("  -> the SDK transmits the loaded key byte-for-byte")
    except Exception as exc:
        print("  header capture unavailable: %s: %s" % (type(exc).__name__, exc))

    # ---------------------------------------------------------------- [9]
    section(9, "Live API call - complete response")
    try:
        models = generator.client.models.list()
        names = sorted(m.id for m in models.data)
        print("  AUTH OK - %d models visible" % len(names))
        print("  gpt-4o-mini available  %s" % ("gpt-4o-mini" in names))

        if "gpt-4o-mini" not in names:
            print("  *** generate_spec requests gpt-4o-mini, which this key")
            print("      cannot see. Visible sample: %s ***" % ", ".join(names[:8]))
            return 1
    except Exception as exc:
        name = type(exc).__name__

        if name == "APIConnectionError":
            print("  SKIP: cannot reach api.openai.com from this machine.")
            print("  (%s)" % exc)
            print("  Sections [1]-[8] above are still valid.")
            return 0

        print("  AUTH FAILED")
        print("  exception type         %s" % name)
        print("  status code            %s" % getattr(exc, "status_code", "n/a"))
        print("  request id             %s" % getattr(exc, "request_id", "n/a"))
        print("\n  --- message ---")
        print("  %s" % exc)

        body = getattr(exc, "body", None)
        if body is not None:
            print("\n  --- complete response body ---")
            try:
                print(json.dumps(body, indent=2, default=str))
            except Exception:
                print("  %r" % body)

        response = getattr(exc, "response", None)
        if response is not None:
            print("\n  --- response headers ---")
            for header in sorted(response.headers):
                if header.lower().startswith(("x-request-id", "openai-",
                                              "cf-ray", "date")):
                    print("  %-28s %s" % (header, response.headers[header]))

        print("\n  --- how to read this ---")
        print("  OpenAI echoes back a redacted form of the key it RECEIVED.")
        print("  Compare that echo with section [7]'s first 10 / last 10:")
        print("    same key, still 401 -> OpenAI does not recognise this key")
        print("                           string. Sections [3][4][8] prove the")
        print("                           code transmitted it intact, so the key")
        print("                           is revoked, or its service account or")
        print("                           project was deleted.")
        print("    different key       -> something between .env and the SDK is")
        print("                           substituting a value; sections [1][5]")
        print("                           [6] show what.")
        return 1

    # ---------------------------------------------------------------- [10]
    section(10, "Chat completion - the call generate_spec makes")
    try:
        response = generator.client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            temperature=0,
            messages=[{"role": "user", "content": 'Reply with exactly {"ok": true}'}],
        )
        print("  CALL OK                %s" % response.choices[0].message.content.strip())
        print("  model                  %s" % response.model)
        print("  tokens                 %s" % response.usage.total_tokens)
    except Exception as exc:
        print("  CALL FAILED            %s: %s" % (type(exc).__name__, exc))
        body = getattr(exc, "body", None)
        if body is not None:
            print(json.dumps(body, indent=2, default=str))
        return 1

    print("\n" + "=" * 62)
    print("  AUTHENTICATION OK - test_full_flow should now run")
    return 0


if __name__ == "__main__":
    import io  # noqa: E402  - used in section [2]
    sys.exit(main())
