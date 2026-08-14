import pytest
from app.security import SSRFError, validate_target_url


def test_ssrf_allows_public_urls() -> None:
    # Public domains or IPs should pass validation
    url = "https://example.com/api/openapi.json"
    assert validate_target_url(url) == url


def test_ssrf_blocks_private_ip_ranges() -> None:
    bad_urls = [
        "http://127.0.0.1/secret",
        "http://10.0.0.5:8080/data",
        "http://172.16.0.1/admin",
        "http://192.168.1.1/router",
        "http://0.0.0.0/internal",
    ]
    for url in bad_urls:
        with pytest.raises(SSRFError):
            validate_target_url(url)


def test_ssrf_blocks_metadata_endpoint() -> None:
    url = "http://169.254.169.254/latest/meta-data/"
    with pytest.raises(SSRFError):
        validate_target_url(url)


def test_ssrf_blocks_non_http_schemes() -> None:
    bad_schemes = [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://example.com/",
    ]
    for url in bad_schemes:
        with pytest.raises(SSRFError):
            validate_target_url(url)


def test_ssrf_blocks_localhost_domain() -> None:
    with pytest.raises(SSRFError):
        validate_target_url("http://localhost/admin")
