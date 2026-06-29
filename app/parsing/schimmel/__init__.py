"""Schimmel essay-template PDF parser.

Parses the Schimmel Templates_Bullet Version PDF into a structured
essay-analysis hierarchy with subjects, topics, issues, rules, elements,
exceptions, jurisdiction variants, cross-references, and abbreviations.
"""

from app.parsing.schimmel.parser import SchimmelTemplateParser

__all__ = ["SchimmelTemplateParser"]