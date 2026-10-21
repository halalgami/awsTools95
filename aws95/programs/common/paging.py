"""Moved to aws95.contracts.paging (2d: providers page too); programs keep importing it from here."""
from aws95.contracts.paging import fetch_all_pages, merge_coverage

__all__ = ["fetch_all_pages", "merge_coverage"]
