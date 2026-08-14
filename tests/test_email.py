from app.email import build_verification_email, send_email


def test_build_verification_email_contains_link() -> None:
    subject, text = build_verification_email("user@example.com", "https://app.example/verify?token=abc")
    assert subject == "Verify your email"
    assert "https://app.example/verify?token=abc" in text


def test_send_email_skips_when_smtp_not_configured() -> None:
    send_email("user@example.com", "subject", "body")
