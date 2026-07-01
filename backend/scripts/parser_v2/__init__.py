"""
parser_v2 — DOM-aware HTML replacement package.

Public API:
    apply_dom_replacements(html_text, rules, image_map) -> (html, count)
"""

from .main import apply_dom_replacements

__all__ = ['apply_dom_replacements']
