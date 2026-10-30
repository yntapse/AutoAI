"""The browser must be able to use http://localhost:3000 with the API at
http://localhost:8000 and keep the temporary database-session cookie.

main.py cannot be imported in a unit test (it needs PostgreSQL at start-up), so
this reads the CORS settings out of main.py's source and applies them, unchanged,
to a small FastAPI app. If someone loosens those settings, or the local
launchers drift back to 127.0.0.1, these tests fail.
"""
import ast
import os
import re
import unittest

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def _cors_settings():
    tree = ast.parse(_read("backend", "main.py"))
    default_origins = None
    kwargs = None
    for node in ast.walk(tree):
        # allow_origins = [ ... ]  inside the "else" branch (the defaults)
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "allow_origins"
            and isinstance(node.value, ast.List)
        ):
            default_origins = ast.literal_eval(node.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_middleware"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "CORSMiddleware"
        ):
            kwargs = {
                k.arg: (ast.literal_eval(k.value) if not isinstance(k.value, ast.Name) else k.value.id)
                for k in node.keywords
            }
    return default_origins, kwargs


class LocalhostCorsConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.default_origins, cls.kwargs = _cors_settings()
        assert cls.kwargs is not None, "CORSMiddleware not found in main.py"
        kwargs = dict(cls.kwargs)
        kwargs["allow_origins"] = cls.default_origins  # the env var is unset by default
        app = FastAPI()
        app.add_middleware(CORSMiddleware, **kwargs)

        @app.get("/catalog/ping")
        def ping():
            return {"ok": True}

        cls.client = TestClient(app)

    def _preflight(self, origin):
        return self.client.options(
            "/catalog/ping",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-requested-by",
            },
        )

    def test_localhost_and_loopback_ip_are_both_allowed_with_credentials(self):
        for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
            r = self._preflight(origin)
            self.assertEqual(r.status_code, 200, origin)
            self.assertEqual(r.headers.get("access-control-allow-origin"), origin)
            self.assertEqual(r.headers.get("access-control-allow-credentials"), "true")

    def test_csrf_header_may_be_sent_cross_origin_from_the_app(self):
        r = self._preflight("http://localhost:3000")
        self.assertIn("x-requested-by", r.headers.get("access-control-allow-headers", "").lower())

    def test_other_sites_are_not_allowed(self):
        for origin in (
            "http://evil.example",
            "https://localhost.evil.example",
            "http://localhost.evil.example:3000",
            "http://127.0.0.1.evil.example",
            "http://192.168.1.20:3000",
        ):
            r = self._preflight(origin)
            self.assertIsNone(r.headers.get("access-control-allow-origin"), origin)
            r = self.client.get("/catalog/ping", headers={"Origin": origin})
            self.assertIsNone(r.headers.get("access-control-allow-origin"), origin)

    def test_origins_are_never_a_wildcard_while_credentials_are_on(self):
        self.assertIs(self.kwargs["allow_credentials"], True)
        self.assertNotIn("*", self.default_origins)
        self.assertNotIn(".*", self.kwargs["allow_origin_regex"].replace("(:\\d+)?", ""))
        self.assertTrue(self.kwargs["allow_origin_regex"].startswith("https?://(localhost|127"))


class LocalDevelopmentUsesOneHostName(unittest.TestCase):
    """The page and the API must share a host name or the SameSite=Lax session
    cookie is not kept."""

    def test_env_local_points_at_localhost(self):
        text = _read(".env.local")
        value = re.search(r"^NEXT_PUBLIC_API_BASE_URL=(\S+)", text, re.M).group(1)
        self.assertEqual(value, "http://localhost:8000")

    def test_backend_launchers_listen_on_localhost(self):
        for path in (
            (".vscode", "launch.json"),
            (".vscode", "tasks.json"),
            ("backend", "restart_backend.bat"),
        ):
            text = _read(*path)
            self.assertNotRegex(text, r"--host\s+127\.0\.0\.1|\"--host\",\s*\"127\.0\.0\.1\"", "/".join(path))
            self.assertRegex(text, r"--host\s+localhost|\"--host\",\s*\"localhost\"", "/".join(path))

    def test_every_frontend_api_base_default_is_localhost(self):
        offenders = []
        for folder, _dirs, files in os.walk(os.path.join(ROOT, "src")):
            for name in files:
                if not name.endswith((".ts", ".tsx")) or ".test." in name or name == "page_backup.tsx":
                    continue
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as handle:
                    if "127.0.0.1:8000" in handle.read():
                        offenders.append(os.path.relpath(path, ROOT))
        self.assertEqual(offenders, [], "these files still use 127.0.0.1:8000")


if __name__ == "__main__":
    unittest.main()
