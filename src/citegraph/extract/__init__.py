from citegraph.extract.base import Extractor, module_name_for, package_for
from citegraph.extract.configfiles import ConfigFileExtractor
from citegraph.extract.python import PythonExtractor

EXTRACTORS: dict[str, Extractor] = {"python": PythonExtractor(), "config": ConfigFileExtractor()}

__all__ = [
    "EXTRACTORS",
    "ConfigFileExtractor",
    "Extractor",
    "PythonExtractor",
    "module_name_for",
    "package_for",
]
