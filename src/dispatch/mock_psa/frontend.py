"""Serve only packaged console assets, independent of the process working directory."""

from pathlib import Path

from starlette.staticfiles import StaticFiles


class ConsoleFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-cache"
        return response


def console_directory():
    package = Path(__file__).resolve()
    for root in [package.parents[2], package.parents[3]]:
        candidate = root / "web"
        if (candidate / "index.html").is_file():
            return candidate
    raise RuntimeError("Console assets are missing; reinstall the Dispatch package")
