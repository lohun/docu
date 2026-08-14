from app.security import hash_password, pwd_context, verify_password


def test_argon2id_scheme() -> None:
    assert pwd_context.schemes() == ("argon2",)


def test_hash_and_verify_roundtrip() -> None:
    password_hash = hash_password("S3cret-pass!")
    assert verify_password("S3cret-pass!", password_hash) is True


def test_verify_rejects_wrong_password() -> None:
    password_hash = hash_password("correct-password")
    assert verify_password("wrong-password", password_hash) is False


def test_hash_is_salted_argon2id() -> None:
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b
    assert pwd_context.identify(a) == "argon2"
