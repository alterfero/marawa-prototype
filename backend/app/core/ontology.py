"""Directed links go from a narrower trope to a broader trope."""
from graphlib import CycleError, TopologicalSorter


ACTIVE_LINKS = frozenset({"approved", "inferred"})
LINK_STATUSES = ACTIVE_LINKS | {"pending", "rejected"}


def validate_graph(levels: dict[str, int], links: list[tuple[str, str, str]]) -> None:
    graph = TopologicalSorter()
    seen = set()
    for child, parent, status in links:
        if child not in levels or parent not in levels or child == parent:
            raise ValueError("A link needs two different tropes from this dataset.")
        if status not in LINK_STATUSES or (child, parent) in seen:
            raise ValueError("Invalid or duplicate ontology link.")
        seen.add((child, parent))
        if status in ACTIVE_LINKS:
            if levels[child] < levels[parent]:
                raise ValueError("The broader trope must have an equal or lower specificity level.")
            graph.add(child, parent)
    try:
        graph.prepare()
    except CycleError as exc:
        raise ValueError("These broader links would form a cycle.") from exc


# Each phrase entails the preceding phrase. Ratings alone never imply an edge.
REFERENCE_CHAINS = (
    ("a non-human follows a human", "a non-human follows a human after being disturbed",
     "an animal follows a human after being disturbed", "a snake follows a human after being disturbed"),
    ("a non-human turns into a man", "an animal turns into a man", "a snake turns into a man"),
    ("someone groups people into clans", "someone groups people into clans by applying a specific criterion",
     "someone groups people into clans according to their place of origin"),
    ("someone groups people into clans by applying a specific criterion", "someone groups people into clans by the food they bring"),
    ("an animal digs humans out of the earth", "a dog digs humans out of the earth"),
    ("a woman marries an animal-man", "a woman marries a snake-man"),
    ("someone spies on someone else bathing", "someone spies on an animal-man bathing", "someone spies on a snake-man bathing"),
    ("someone removes their own animal form", "someone removes their animal form before bathing"),
    ("a human refuses to marry a non-human", "a human refuses to marry a snake", "a woman refuses to marry a snake"),
    ("one of two sisters has to marry a non-human", "one of two sisters has to marry a snake"),
)

# Substitute only a whole, initial subject. Exact matches elsewhere in the
# catalogue become review suggestions, never automatically traversable links.
BROADER_SUBJECTS = {
    "a snake": "an animal", "a dog": "an animal", "a turtle": "an animal",
    "a bird": "an animal", "a swallow": "a bird", "a heron": "a bird",
    "an animal": "a non-human", "a girl": "a child", "a boy": "a child",
    "a woman": "someone", "a man": "someone", "a child": "someone",
}


def candidate_links(terms: set[str]):
    proposed = {}
    for text in sorted(terms):
        for subject, broader in BROADER_SUBJECTS.items():
            if text.startswith(subject + " "):
                parent = broader + text[len(subject):]
                if parent in terms:
                    proposed[text, parent] = "pending"
    for chain in REFERENCE_CHAINS:
        available = [text for text in chain if text in terms]
        for parent, child in zip(available, available[1:]):
            proposed[child, parent] = "inferred"
    return proposed
