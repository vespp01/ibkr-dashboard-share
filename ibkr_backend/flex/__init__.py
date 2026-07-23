"""Flex Web Service integration — HTTP client and XML parser."""

from .client import FlexClient, FlexError
from .parser import parse_flex_xml

__all__ = ["FlexClient", "FlexError", "parse_flex_xml"]
