"""rxiv2pdf: bioRxiv/medRxiv preprint -> journal-style PDF, built from JATS XML."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("rxiv2pdf")  # single source: pyproject.toml
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
