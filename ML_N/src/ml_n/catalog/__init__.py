"""Safe searchable measurement catalog."""

from .normalize import (
    build_catalog_record,
    normalize_float,
    normalize_int,
    normalize_text,
)
from .schema import FileCatalogRecord

from .builder import (
    CatalogBuildError,
    CatalogBuildResult,
    build_catalog,
    discover_tdms_files,
)

__all__ = [
    "FileCatalogRecord",
    "build_catalog_record",
    "normalize_float",
    "normalize_int",
    "normalize_text",
    "CatalogBuildError",
    "CatalogBuildResult",
    "build_catalog",
    "discover_tdms_files",
]