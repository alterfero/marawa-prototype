"""Specificity calibration from the lead researcher's November 2026 workbook."""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
from importlib.resources import files
import json
import re

from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

from app.core.parsing import normalize_text

RATING_SOURCES = ("researcher", "curator", "provisional", "disputed")


def wording_features(text: str) -> dict[str, float]:
    words = re.findall(r"[\w]+(?:[-'][\w]+)*", text.lower())
    features = {f"word:{w}": 0.55 for w in words}
    features.update({f"phrase:{a} {b}": 0.35 for a, b in zip(words, words[1:])})
    groups = {
        "unspecified": {"someone", "something", "anyone", "somebody", "somewhere"},
        "broad role": {"human", "humans", "non-human", "people", "animal", "animals", "person", "object"},
        "kin": {"brother", "sister", "father", "mother", "husband", "wife", "daughter", "son"},
        "qualified": {"because", "until", "after", "before", "when", "unless", "while", "without", "using"},
        "two events": {"then", "and", "who", "which"},
        "relationship": {"their", "his", "her", "its"},
    }
    for key, vocabulary in groups.items():
        features[key] = min(3, sum(word in vocabulary for word in words))
    features["length"] = min(len(words), 30) / 8
    return features


@lru_cache(maxsize=1)
def rating_votes() -> dict[str, Counter[int]]:
    data = json.loads(files(__package__).joinpath("specificity_ratings.json").read_text(encoding="utf-8"))
    votes: dict[str, Counter[int]] = defaultdict(Counter)
    for records in data["stories"].values():
        for text, level in records:
            if level is not None:
                votes[normalize_text(text)][level] += 1
    return dict(votes)


@lru_cache(maxsize=1)
def _model():
    # Each agreed wording contributes once, including wordings absent from the active catalogue.
    examples = sorted((text, next(iter(votes))) for text, votes in rating_votes().items() if len(votes) == 1)
    model = make_pipeline(DictVectorizer(), LogisticRegression(class_weight="balanced", max_iter=400))
    model.fit([wording_features(t) for t, _ in examples], [v for _, v in examples])
    return model


@lru_cache(maxsize=16000)
def classify(text: str) -> tuple[int, str]:
    marker = normalize_text(text)
    votes = rating_votes().get(marker)
    if votes:
        # For a tie use the broader supported level until the curator settles it.
        level = min(votes, key=lambda n: (-votes[n], n))
        return level, "researcher" if len(votes) == 1 else "disputed"
    return int(_model().predict([wording_features(marker)])[0]), "provisional"
