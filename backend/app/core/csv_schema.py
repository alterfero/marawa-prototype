"""Legacy-compatible CSV schema constants."""

TROPE_FIELD = "Motifs (Eng)"
KEYWORD_FIELD = "Keywords (Eng)"
THEME_FIELD = "Thème"
TROPE_PROPOSAL_FIELD = "proposition de nouveaux motifs"
DATE_OF_RECORDING_FIELD = "date of recording"
MARAWA_STORY_METADATA_FIELD = "Marawa story metadata"
MARAWA_TERM_CATALOG_FIELD = "Marawa term catalog"
CSV_IMPORT_ALIASES = {
    "motifs inhabituels à une version": TROPE_PROPOSAL_FIELD,
}

CSV_COLUMNS = [
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
    DATE_OF_RECORDING_FIELD,
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
    KEYWORD_FIELD,
    TROPE_FIELD,
    TROPE_PROPOSAL_FIELD,
    "species",
    "non-human",
    "placenames",
    "named characters",
    "external link",
    "description of link",
    "Connection to other stories",
    "Megamotifs",
    THEME_FIELD,
    "Conte type",
    "Autres infos données dans le texte, pour la fiche conte",
    "ATU conte-type(AI ?)",
    "ATU motifs (AI?)",
]

# These columns are deliberately appended after the legacy contract. They are
# used only by the lossless Marawa export; the legacy export retains the exact
# header above for interoperability with existing research workflows.
FULL_EXPORT_COLUMNS = [
    *CSV_COLUMNS,
    MARAWA_STORY_METADATA_FIELD,
    MARAWA_TERM_CATALOG_FIELD,
]
