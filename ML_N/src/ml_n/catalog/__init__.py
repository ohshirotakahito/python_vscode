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
from .pair_manifest import (
    TdmsPairRecord,
    discover_tdms_pairs,
    resolve_manifest_path,
)

__all__ = [
    "CatalogBuildError",
    "CatalogBuildResult",
    "FileCatalogRecord",
    "TdmsPairRecord",
    "build_catalog",
    "build_catalog_record",
    "discover_tdms_pairs",
    "discover_tdms_files",
    "normalize_float",
    "normalize_int",
    "normalize_text",
    "resolve_manifest_path",
]
