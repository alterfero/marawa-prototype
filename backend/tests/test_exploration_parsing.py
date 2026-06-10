from app.core.coordinates import parse_space_coord
from app.services.exploration import StoryEntry, english_story_title, primary_abstract


def make_entry(
    story_id: str,
    *,
    tropes: list[dict] | None = None,
    coord: str,
    title: str,
    abstract: str = "",
    summary: str = "",
) -> StoryEntry:
    return StoryEntry(
        story_id=story_id,
        source_row_number=None,
        tropes=tropes or [],
        fields={
            "Story title (Eng)": title,
            "space coord": coord,
            "Abstract (Eng)": abstract,
            "1-sentence summary": summary,
            "territory": "Test territory",
        },
    )


def test_parse_space_coord_accepts_decimal_coordinates() -> None:
    assert parse_space_coord("-20.859062, 165.258667") == (-20.859062, 165.258667)


def test_parse_space_coord_accepts_directional_coordinates() -> None:
    assert parse_space_coord("≈ 16.0° S, 168.4° E") == (-16.0, 168.4)


def test_parse_space_coord_accepts_semicolon_coordinates() -> None:
    assert parse_space_coord("22.2994° ; 166.7483°") == (22.2994, 166.7483)


def test_parse_space_coord_accepts_decimal_commas() -> None:
    assert parse_space_coord("-4,198,\n152,163") == (-4.198, 152.163)


def test_parse_space_coord_treats_zero_zero_as_unknown() -> None:
    assert parse_space_coord("0,0") is None
    assert parse_space_coord("0.0, 0.0") is None


def test_primary_abstract_prefers_abstract_over_summary() -> None:
    entry = make_entry(
        "story-1",
        coord="-20.0, 165.0",
        title="Story",
        abstract="Long abstract",
        summary="Short summary",
    )
    assert primary_abstract(entry) == "Long abstract"


def test_english_story_title_prefers_english_title() -> None:
    entry = make_entry(
        "story-1",
        coord="-20.0, 165.0",
        title="English title",
    )
    entry.fields["Story title (French)"] = "Titre"
    assert english_story_title(entry) == "English title"
