from cryptography.fernet import Fernet

from app.llm.key_store import FernetKeyStore, KeyStoreError

import pytest


def test_fernet_encrypt_decrypt_roundtrip() -> None:
    master_key = Fernet.generate_key().decode()
    store = FernetKeyStore(master_key)

    encrypted = store.encrypt("nvapi-platform-key-1234567890")
    assert encrypted != "nvapi-platform-key-1234567890"
    assert store.decrypt(encrypted) == "nvapi-platform-key-1234567890"


def test_fernet_wrong_master_key_cannot_decrypt() -> None:
    store_a = FernetKeyStore(Fernet.generate_key().decode())
    store_b = FernetKeyStore(Fernet.generate_key().decode())

    token = store_a.encrypt("secret")
    with pytest.raises(KeyStoreError):
        store_b.decrypt(token)
