from citegraph.extract.base import Extractor, module_name_for, package_for
from citegraph.extract.configfiles import ConfigFileExtractor
from citegraph.extract.csharp import CSharpExtractor
from citegraph.extract.python import PythonExtractor
from citegraph.models import ExtractResult

EXTRACTORS: dict[str, Extractor] = {
    "python": PythonExtractor(),
    "csharp": CSharpExtractor(),
    "config": ConfigFileExtractor(),
}


def stored_module(rel_path: str, lang: str, result: ExtractResult) -> str | None:
    """The `files.module` value: the dotted module for Python, the first namespace for C#, None otherwise."""
    if lang == "python":
        return module_name_for(rel_path)
    if lang == "csharp":
        return result.module
    return None


__all__ = [
    "EXTRACTORS",
    "CSharpExtractor",
    "ConfigFileExtractor",
    "Extractor",
    "PythonExtractor",
    "module_name_for",
    "package_for",
    "stored_module",
]
