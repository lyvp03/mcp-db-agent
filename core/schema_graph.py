"""Schema Graph — Phase 2: Graph data structure with BFS traversal for selective schema injection."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RelationshipEdge:
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    kind: str = "inferred"  # "FK" or "inferred"

    def __str__(self) -> str:
        arrow = "→" if self.kind == "FK" else "↔"
        return f"{self.source_table}.{self.source_column} {arrow} {self.target_table}.{self.target_column} ({self.kind})"


@dataclass
class SchemaGraph:
    """Lightweight graph over the database schema.

    Nodes are table names (str).
    Edges are RelationshipEdge objects parsed from the ``relationships``
    list stored in schema_cache.json.
    """

    tables: dict[str, str] = field(default_factory=dict)  # table_name -> compact schema text
    edges: list[RelationshipEdge] = field(default_factory=list)
    _adj: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set), repr=False)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "SchemaGraph":
        """Build a SchemaGraph from a schema_cache snapshot dict."""
        tables = snapshot.get("tables", {})
        rels_raw = snapshot.get("relationships", [])
        edges: list[RelationshipEdge] = []
        adj: dict[str, set[str]] = defaultdict(set)

        for rel_str in rels_raw:
            edge = _parse_relationship(rel_str)
            if edge is None:
                continue
            edges.append(edge)
            adj[edge.source_table].add(edge.target_table)
            adj[edge.target_table].add(edge.source_table)

        graph = cls(tables=dict(tables), edges=edges, _adj=adj)
        return graph

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------
    def neighbors(self, root_tables: list[str], hops: int = 1) -> set[str]:
        """BFS from *root_tables* up to *hops* edges away.

        Returns the union of root_tables and all discovered neighbours.
        """
        visited: set[str] = set(root_tables)
        frontier: set[str] = set(root_tables)
        for _ in range(hops):
            next_frontier: set[str] = set()
            for node in frontier:
                for neighbour in self._adj.get(node, set()):
                    if neighbour not in visited:
                        visited.add(neighbour)
                        next_frontier.add(neighbour)
            frontier = next_frontier
            if not frontier:
                break
        return visited

    def subgraph_tables(self, root_tables: list[str], max_hops: int = 2) -> list[str]:
        """Return table names reachable from *root_tables* within *max_hops*."""
        relevant = self.neighbors(root_tables, hops=max_hops)
        # Preserve insertion order from original tables dict
        return [t for t in self.tables if t in relevant]

    def subgraph_edges(self, table_set: set[str]) -> list[RelationshipEdge]:
        """Return edges where BOTH endpoints are in *table_set*."""
        return [
            e for e in self.edges
            if e.source_table in table_set and e.target_table in table_set
        ]

    def shortest_path(self, start: str, end: str, max_depth: int = 5) -> list[str]:
        """BFS shortest path from *start* to *end*.

        Returns the list of table names along the path (inclusive),
        or an empty list if no path exists or if it exceeds max_depth.
        """
        if start == end:
            return [start]
        if start not in self._adj or end not in self._adj:
            return []

        visited: set[str] = {start}
        queue: list[tuple[str, list[str]]] = [(start, [start])]

        while queue:
            current, path = queue.pop(0)
            if len(path) > max_depth:
                continue

            for neighbour in self._adj.get(current, set()):
                if neighbour == end:
                    return path + [end]
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append((neighbour, path + [neighbour]))

        return []  # No path found

    def multi_source_shortest_path(self, starts: set[str], ends: set[str], max_depth: int = 5) -> list[str]:
        """Find the shortest path from ANY node in starts to ANY node in ends."""
        if starts & ends:
            return [list(starts & ends)[0]]

        visited: set[str] = set(starts)
        queue: list[tuple[str, list[str]]] = [(s, [s]) for s in starts]

        while queue:
            current, path = queue.pop(0)
            if len(path) > max_depth:
                continue

            for neighbour in self._adj.get(current, set()):
                if neighbour in ends:
                    return path + [neighbour]
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append((neighbour, path + [neighbour]))
        return []

    def find_join_path(self, target_tables: list[str]) -> tuple[set[str], list[list[str]]]:
        """Find the minimal spanning tree (Steiner approximation) connecting target_tables.

        Starts with one table and iteratively finds the shortest path from the growing
        connected component to the nearest unconnected target table.
        This produces a minimal unified subgraph without redundant pairwise paths.
        """
        if len(target_tables) <= 1:
            return set(target_tables), []

        valid_targets = [t for t in target_tables if t in self._adj]
        if not valid_targets:
            return set(target_tables), []

        connected_set: set[str] = {valid_targets[0]}
        remaining_targets: set[str] = set(valid_targets[1:])

        all_path_tables: set[str] = set(target_tables)
        paths: list[list[str]] = []

        while remaining_targets:
            path = self.multi_source_shortest_path(connected_set, remaining_targets)
            if not path:
                # Graph disconnected for this target, move it to connected set to continue
                isolated = remaining_targets.pop()
                connected_set.add(isolated)
                continue

            all_path_tables.update(path)
            connected_set.update(path)
            
            end_node = path[-1]
            if end_node in remaining_targets:
                remaining_targets.remove(end_node)
            paths.append(path)

        return all_path_tables, paths

    # ------------------------------------------------------------------
    # Prompt Generation
    # ------------------------------------------------------------------
    def to_prompt_text(self, selected_tables: list[str] | None = None) -> str:
        """Compact text representation for LLM prompt injection.

        If *selected_tables* is provided, only include those tables.
        Foreign keys are inlined into the signature: table(col1, fk_col→target_table).
        """
        if selected_tables is None:
            selected_tables = list(self.tables.keys())
        table_set = set(selected_tables)

        relevant_edges = self.subgraph_edges(table_set)
        edge_map: dict[tuple[str, str], str] = {}
        for e in relevant_edges:
            edge_map[(e.source_table, e.source_column)] = e.target_table
            if e.kind == "inferred":
                edge_map[(e.target_table, e.target_column)] = e.source_table

        parts: list[str] = []
        for t in selected_tables:
            schema_text = self.tables.get(t, "")
            if not schema_text:
                continue

            # Try to parse as JSON or ast.literal_eval
            parsed = False
            try:
                import json, ast
                try:
                    data = json.loads(schema_text)
                except Exception:
                    data = ast.literal_eval(schema_text)
                    
                if isinstance(data, dict) and "columns" in data:
                    cols = data.get("columns", [])
                    inlined_cols = []
                    for c in cols:
                        col_name = c.get("column", c.get("name", ""))
                        data_type = c.get("data_type", c.get("type", ""))
                        if not col_name:
                            continue
                            
                        target = edge_map.get((t, col_name))
                        if target:
                            if data_type:
                                inlined_cols.append(f"{col_name}→{target} {data_type}")
                            else:
                                inlined_cols.append(f"{col_name}→{target}")
                        else:
                            if data_type:
                                inlined_cols.append(f"{col_name} {data_type}")
                            else:
                                inlined_cols.append(col_name)
                    parts.append(f"{t}({', '.join(inlined_cols)})")
                    parsed = True
            except Exception:
                pass
                
            if parsed:
                continue

            # Fallback for manual string definitions
            m = re.match(r"^([^\(]+)\((.*)\)$", schema_text.strip())
            if m:
                table_name = m.group(1)
                cols_str = m.group(2)
                cols = [c.strip() for c in cols_str.split(",") if c.strip()]
                inlined_cols = []
                for c in cols:
                    parts_c = c.split(maxsplit=1)
                    col_name = parts_c[0] if parts_c else c
                    
                    target = edge_map.get((t, col_name))
                    if target:
                        if len(parts_c) > 1:
                            inlined_cols.append(f"{col_name}→{target} {parts_c[1]}")
                        else:
                            inlined_cols.append(f"{c}→{target}")
                    else:
                        inlined_cols.append(c)
                parts.append(f"{table_name}({', '.join(inlined_cols)})")
            else:
                parts.append(schema_text)

        if relevant_edges:
            rels = "\n".join(f"- {e}" for e in relevant_edges)
            parts.append(
                f"\nRelationships:\n{rels}\n"
                "Use JOIN when querying across these related tables."
            )

        return "\n".join(parts)


# ------------------------------------------------------------------
# Business term mapping for table selection
# ------------------------------------------------------------------
TERM_MAP: dict[str, list[str]] = {
    # Vietnamese
    "khách hàng": ["customer", "cust"],
    "khach hang": ["customer", "cust"],
    "thuê bao": ["subscriber", "sub"],
    "thue bao": ["subscriber", "sub"],
    "hóa đơn": ["invoice", "bill"],
    "hoa don": ["invoice", "bill"],
    "thanh toán": ["payment", "pay"],
    "thanh toan": ["payment", "pay"],
    "gói cước": ["package", "pack"],
    "goi cuoc": ["package", "pack"],
    "tài khoản": ["account", "acc"],
    "tai khoan": ["account", "acc"],
    "đơn hàng": ["order"],
    "don hang": ["order"],
    "địa chỉ": ["address", "addr"],
    "dia chi": ["address", "addr"],
    "số điện thoại": ["phone", "isdn", "msisdn"],
    "so dien thoai": ["phone", "isdn", "msisdn"],
    "sản phẩm": ["product", "prod"],
    "san pham": ["product", "prod"],
    "doanh thu": ["revenue", "income", "amount"],
    # English
    "customer": ["customer", "cust"],
    "subscriber": ["subscriber", "sub"],
    "invoice": ["invoice", "bill"],
    "payment": ["payment", "pay"],
    "account": ["account", "acc"],
    "order": ["order"],
    "product": ["product", "prod"],
}


def select_tables(question: str, graph: SchemaGraph, max_tables: int = 5) -> list[str]:
    """Select relevant tables from the graph based on the user question.

    Strategy:
    1. Keyword/fuzzy match question against table names and column names.
    2. Match business terms from TERM_MAP.
    3. Expand via graph BFS (1-2 hops).
    4. Cap at *max_tables*.
    """
    q = question.casefold()
    matched: set[str] = set()

    # 1. Direct table name match
    for table_name in graph.tables:
        # Match full name or significant substrings
        name_lower = table_name.casefold()
        # e.g., "sample_data_customer" → check "customer" substring
        name_parts = name_lower.replace("_", " ").split()
        for part in name_parts:
            if len(part) >= 3 and part in q:
                matched.add(table_name)
                break

    # 2. Business term matching
    for term, keywords in TERM_MAP.items():
        if term in q:
            for table_name in graph.tables:
                name_lower = table_name.casefold()
                for kw in keywords:
                    if kw in name_lower:
                        matched.add(table_name)

    # 3. Column name matching — check if question mentions a column
    for table_name, schema_text in graph.tables.items():
        # schema_text is like "public.table:\n  col1 type, col2 type, ..."
        cols_part = schema_text.split(":\n  ")[-1] if ":\n  " in schema_text else ""
        for col_entry in cols_part.split(", "):
            col_name = col_entry.split(" ")[0].strip()
            if len(col_name) >= 4 and col_name.casefold() in q:
                matched.add(table_name)

    # 4. If nothing matched, include ALL tables (fallback)
    if not matched:
        return list(graph.tables.keys())[:max_tables]

    matched_list = list(matched)

    # 5. Path-based BFS: find exact JOIN chain between matched tables
    if len(matched_list) >= 2:
        path_tables, _ = graph.find_join_path(matched_list)
    else:
        # Single table: expand neighbors to find related tables
        path_tables = graph.neighbors(matched_list, hops=1)
        # Adaptive: if too few, expand further
        if len(path_tables) <= 2 and len(graph.tables) > len(path_tables):
            path_tables = graph.neighbors(matched_list, hops=2)

    # 6. Cap at max_tables, preserving original table order
    result = [t for t in graph.tables if t in path_tables]
    return result[:max_tables]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
_REL_PATTERN = re.compile(
    r"^(?:(\w+)\.)?(\w+)\.(\w+)\s*(?:→|↔|<->|->|<>)\s*(?:(\w+)\.)?(\w+)\.(\w+)\s*\((\w+)\)$"
)


def _parse_relationship(rel_str: str) -> RelationshipEdge | None:
    """Parse a relationship string like:
    'public.customer.cust_id ↔ public.subscriber.cust_id (inferred)'
    'public.customer.cust_id <-> public.subscriber.cust_id (inferred)'
    """
    m = _REL_PATTERN.match(rel_str.strip())
    if not m:
        return None
    return RelationshipEdge(
        source_table=m.group(2),
        source_column=m.group(3),
        target_table=m.group(5),
        target_column=m.group(6),
        kind=m.group(7),
    )
