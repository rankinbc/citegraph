from citegraph.extract.base import Extractor, module_name_for, package_for
from citegraph.extract.python import PythonExtractor

EXTRACTORS: dict[str, Extractor] = {"python": PythonExtractor()}

__all__ = ["EXTRACTORS", "Extractor", "PythonExtractor", "module_name_for", "package_for"]
