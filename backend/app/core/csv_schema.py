"""Legacy-compatible CSV schema constants."""

TROPE_FIELD = "Motifs (Eng)"
KEYWORD_FIELD = "Keywords (Eng)"
TROPE_PROPOSAL_FIELD = "proposition de nouveaux motifs"
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
    "Thème",
    "Conte type",
    "Autres infos données dans le texte, pour la fiche conte",
    "ATU conte-type(AI ?)",
    "ATU motifs (AI?)",
]
