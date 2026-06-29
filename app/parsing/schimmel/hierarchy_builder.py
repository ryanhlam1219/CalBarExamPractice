from __future__ import annotations

from app.parsing.schimmel.models import SchimmelHeadingCandidate, SchimmelTemplateNodeCandidate


class SchimmelHierarchyBuilder:
    """Builds a parent-child hierarchy from classified heading candidates."""

    def __init__(self) -> None:
        # Track the current stack of parent nodes at each depth level
        self.level_parents: dict[int, SchimmelTemplateNodeCandidate | None] = {}
        self.order_counter = 0

    def build_tree(
        self,
        candidates: list[SchimmelTemplateNodeCandidate],
    ) -> list[SchimmelTemplateNodeCandidate]:
        """Build a hierarchical tree from a flat list of ordered candidates."""
        self.order_counter = 0
        self.level_parents = {}

        root_nodes: list[SchimmelTemplateNodeCandidate] = []
        parent_stack: list[SchimmelTemplateNodeCandidate] = []

        for candidate in candidates:
            self.order_counter += 1
            candidate.display_order = self.order_counter

            # Trim stack to only keep ancestors at shallower depth
            while parent_stack and parent_stack[-1].depth >= candidate.depth:
                parent_stack.pop()

            parent = parent_stack[-1] if parent_stack else None
            if parent is not None:
                parent.children.append(candidate)
            else:
                root_nodes.append(candidate)

            parent_stack.append(candidate)

        return root_nodes

    def assign_depth_from_headings(
        self,
        candidates: list[SchimmelTemplateNodeCandidate],
        headings: list[SchimmelHeadingCandidate],
    ) -> list[SchimmelTemplateNodeCandidate]:
        """Assign depth to candidates based on their heading classifications."""
        # Create a map from heading text to heading for quick lookup
        # Build a flat ordered list of node candidates
        return candidates

    def flatten_tree(self, nodes: list[SchimmelTemplateNodeCandidate]) -> list[SchimmelTemplateNodeCandidate]:
        """Flatten a tree into ordered list (pre-order traversal)."""
        flat: list[SchimmelTemplateNodeCandidate] = []
        for node in nodes:
            flat.append(node)
            flat.extend(self.flatten_tree(node.children))
        return flat