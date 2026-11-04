"""Parse Mellmann's CSV into a reference tree, preserving its text and sources.

Editorial headings and dotted-code ancestry are navigation, not measured
abstraction. Historical entries have separate IDs; absent prefixes are gaps.
"""

import csv
import io
import re

HEADING_COLUMNS = ["chapter", "division1", "division2", "division3", 'section ("tens")']
HEADING = re.compile(r"([A-Z](?:\d+(?:\.\d+)*)?(?:[–-][A-Z]?\d+)?)\.\s+(.+)")
CODE = re.compile(r"[A-Z]\d+(?:\.\d+)*(?:[a-z]|\[[a-z]\]|\[\.\d+\])?")
DESCRIPTION_PREFIX = re.compile(r"^[A-Z]\d+(?:\.\d+)*(?:[a-z]|\[[a-z]\]|\[\.\d+\])?\.?\s*")


def build_tmi_catalogue(text: str) -> dict:
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    required = {"code", "1st ed.", "MOTIF", "bibliographies", *HEADING_COLUMNS}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("The TMI CSV is missing required columns.")
    nodes: dict[str, dict] = {}
    records: list[tuple[int, dict, str | None]] = []

    def add(identifier: str, code: str, label: str, kind: str, parent: str | None, basis: str) -> dict:
        node = {
            "id": identifier, "code": code, "text": label, "kind": kind,
            "parent_id": parent, "chapter": code[0], "link_basis": basis,
            "source_rows": [], "source_texts": [], "alternate_texts": [],
            "bibliographies": [], "edition": None, "first_edition_code": None,
        }
        nodes[identifier] = node
        return node

    # Read editorial headings first so full entries can replace section labels.
    try:
        for row_number, row in enumerate(reader, 2):
            if None in row or any(row.get(column) is None for column in required):
                raise ValueError(f"Invalid TMI CSV record {row_number}.")
            parent = None
            for column in HEADING_COLUMNS:
                value = row[column].replace(".–", "–").strip()
                if not value:
                    continue
                match = HEADING.fullmatch(value)
                if not match:
                    raise ValueError(f"Invalid TMI heading in record {row_number}: {value}")
                code, label = match.groups()
                code = code.replace("–", "-")
                label = re.sub(r"motifs", "tropes", label, flags=re.IGNORECASE).rstrip(".")
                kind = "chapter" if column == "chapter" else "division" if "division" in column else "heading"
                if code in nodes:
                    if nodes[code]["parent_id"] != parent:
                        raise ValueError(f"Conflicting TMI heading placement: {code}")
                else:
                    add(code, code, label, kind, parent, "source_structure")
                parent = code
            records.append((row_number, row, parent))
    except csv.Error as exc:
        raise ValueError("Invalid TMI CSV syntax.") from exc

    placements: dict[str, str] = {}
    for row_number, row, parent in records:
        code = row["code"].strip() or row["1st ed."].strip()
        if not CODE.fullmatch(code) or not row["MOTIF"].strip() or parent is None or code[0] not in nodes:
            raise ValueError(f"Invalid TMI entry in CSV record {row_number}.")
        # Blank revised-edition codes denote historical entries, not missing data.
        identifier = code if row["code"].strip() else f"first-ed:{code}"
        label = DESCRIPTION_PREFIX.sub("", row["MOTIF"], count=1)
        if identifier not in nodes:
            add(identifier, code, label, "trope", None, "source_structure")
        node = nodes[identifier]
        placement = nodes[parent]["parent_id"] if parent == identifier else parent
        if placement is None or (identifier in placements and placements[identifier] != placement):
            raise ValueError(f"Conflicting TMI entry placement: {code}")
        placements[identifier] = placement
        if node["source_rows"]:
            if label != node["text"] and label not in node["alternate_texts"]:
                node["alternate_texts"].append(label)
        else:
            node.update(text=label, kind="trope",
                        edition="first" if row["MOTIF"].startswith("[First Edition") or not row["code"].strip() else "revised",
                        first_edition_code=row["1st ed."].strip() or None)
        node["source_rows"].append(row_number)
        for key, value in (("source_texts", row["MOTIF"]), ("bibliographies", row["bibliographies"])):
            if value and value not in node[key]:
                node[key].append(value)

    def attach(identifier: str) -> None:
        node = nodes[identifier]
        if node["kind"] in {"chapter", "division", "heading"}:
            return
        # Bracketed decimal additions keep the published code (M202.1[.1]),
        # but its immediate decimal prefix is M202.1, not a partial bracket.
        decimal_code = re.sub(r"\[(\.\d+)\]", r"\1", node["code"])
        if "." in decimal_code and not identifier.startswith("first-ed:"):
            parent = decimal_code.rsplit(".", 1)[0]
            if parent not in nodes:
                add(parent, parent, "Intermediate code absent from the source CSV", "gap", None, "code")
                placements[parent] = placements[identifier]
                attach(parent)
            node["parent_id"] = parent
            node["link_basis"] = "code"
        else:
            node["parent_id"] = placements[identifier]
            node["link_basis"] = "source_structure"

    for identifier in list(nodes):
        attach(identifier)

    # Validate all ancestry once; the frontend can safely traverse every branch.
    validated: set[str] = set()
    for identifier in nodes:
        path: set[str] = set()
        current = identifier
        while current is not None and current not in validated:
            if current in path or current not in nodes:
                raise ValueError(f"Invalid TMI ancestry at {current}.")
            path.add(current)
            current = nodes[current]["parent_id"]
        validated.update(path)

    ordered = list(nodes.values())
    return {
        "nodes": ordered,
        "roots": [node["id"] for node in ordered if node["parent_id"] is None],
        "stats": {
            "source_rows": len(records),
            "trope_count": sum(node["kind"] == "trope" for node in ordered),
            "chapter_count": sum(node["kind"] == "chapter" for node in ordered),
            "gap_count": sum(node["kind"] == "gap" for node in ordered),
            "first_edition_count": sum(node["edition"] == "first" for node in ordered),
            "conflicting_codes": sum(bool(node["alternate_texts"]) for node in ordered),
            "duplicate_rows": sum(max(0, len(node["source_rows"]) - 1) for node in ordered),
        },
    }
