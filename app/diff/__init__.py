from app.diff.openapi_diff import OpenAPIDiffEngine
from app.diff.text_diff import TextDiffEngine


def get_diff_engine(source_type: str):
    if source_type == "openapi":
        return OpenAPIDiffEngine()
    return TextDiffEngine()
