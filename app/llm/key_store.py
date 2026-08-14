from cryptography.fernet import Fernet, InvalidToken


class KeyStoreError(Exception):
    pass


class FernetKeyStore:
    def __init__(self, master_key: str | None = None) -> None:
        if master_key:
            self._fernet = Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
        else:
            self._fernet = Fernet(Fernet.generate_key())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken as e:
            raise KeyStoreError("unable to decrypt value with configured master key") from e
