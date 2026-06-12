from app.core.csv_schema import CSV_COLUMNS, CSV_IMPORT_ALIASES, KEYWORD_FIELD, TROPE_FIELD, TROPE_PROPOSAL_FIELD
from app.core.parsing import (
    clean_text,
    dedupe_preserve_order,
    normalize_text,
    serialize_keywords,
    serialize_tropes,
    split_keywords,
    split_tropes,
)


def test_csv_schema_preserves_exact_legacy_field_names_and_order() -> None:
    assert KEYWORD_FIELD == "Keywords (Eng)"
    assert TROPE_FIELD == "Motifs (Eng)"
    assert TROPE_PROPOSAL_FIELD == "proposition de nouveaux motifs"
    assert CSV_IMPORT_ALIASES == {"motifs inhabituels à une version": TROPE_PROPOSAL_FIELD}
    assert CSV_COLUMNS == [
        "Entered by",
        "Source first or second hand",
        "Source",
        "pages",
        "Other source",
        "URL ?",
        "territory",
        "lg group",
        "original language",
        "lg of publication",
        "bilingual?",
        "storyteller",
        "date of recording",
        "place of recording",
        "space coord",
        "editor",
        "translator",
        "Story title (Eng)",
        "Story title (French)",
        "Story title (other)",
        "1-sentence summary",
        "Abstract (Eng)",
        "Abstract (Fr)",
        "Keywords (Eng)",
        "Motifs (Eng)",
        TROPE_PROPOSAL_FIELD,
        "species",
        "non-human",
        "placenames",
        "named characters",
        "external link",
        "description of link",
        "Connection to other stories",
        "Megamotifs",
        "Thème",
        "Conte type",
        "Autres infos données dans le texte, pour la fiche conte",
        "ATU conte-type(AI ?)",
        "ATU motifs (AI?)",
    ]


def test_clean_text_removes_bom_and_trims_whitespace() -> None:
    assert clean_text("\ufeff  hello world  ") == "hello world"
    assert clean_text(None) == ""


def test_normalize_text_collapses_whitespace_and_lowercases() -> None:
    assert normalize_text("  Sky\t Woman \n ") == "sky woman"


def test_dedupe_preserve_order_uses_normalized_comparison() -> None:
    values = ["  Moon", "moon", "River", "river ", "", "Moon  "]
    assert dedupe_preserve_order(values) == ["Moon", "River"]


def test_split_tropes_handles_section_markers() -> None:
    text = "§§ first trope\n§§ second trope\n§§ first trope"
    assert split_tropes(text) == ["first trope", "second trope"]


def test_split_tropes_falls_back_to_semicolons_and_newlines() -> None:
    text = "first trope ; second trope\nfirst trope"
    assert split_tropes(text) == ["first trope", "second trope"]


def test_serialize_tropes_uses_legacy_section_marker_lines() -> None:
    assert serialize_tropes(["first trope", "second trope", "first trope"]) == (
        "§§ first trope\n§§ second trope"
    )


def test_split_keywords_deduplicates_values() -> None:
    text = "wolf ; moon\nwolf;  river"
    assert split_keywords(text) == ["wolf", "moon", "river"]


def test_serialize_keywords_uses_legacy_separator() -> None:
    assert serialize_keywords(["wolf", "moon", "wolf"]) == "wolf ; moon"
