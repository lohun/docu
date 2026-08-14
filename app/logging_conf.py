import logging
import re
import sys

# Masks platform secrets (NVIDIA keys, bearer tokens, passwords) so accidental
# logging of an API key never reaches disk or the console.
_SECRET_RE = re.compile(
    r"(nvapi-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]+|[Pp]assword[\"']?\s*[:=]\s*[\"']?[^\s\"',}]+)"
)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _SECRET_RE.sub("[REDACTED]", str(record.msg))
        if record.args:
            record.args = tuple(
                _SECRET_RE.sub("[REDACTED]", str(arg)) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").addFilter(RedactFilter())
    logging.getLogger("openai").setLevel(logging.WARNING)
