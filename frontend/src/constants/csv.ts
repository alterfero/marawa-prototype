export const TROPE_FIELD = "Motifs (Eng)";
export const KEYWORD_FIELD = "Keywords (Eng)";

export const LEGACY_METADATA_SECTIONS: Array<{ title: string; fields: string[] }> = [
  {
    title: "Source and provenance",
    fields: [
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
    ],
  },
  {
    title: "Story text",
    fields: [
      "Story title (Eng)",
      "Story title (French)",
      "Story title (other)",
      "1-sentence summary",
      "Abstract (Eng)",
      "Abstract (Fr)",
    ],
  },
  {
    title: "Classification and notes",
    fields: [
      "proposition de nouveaux motifs",
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
    ],
  },
];

export const LONG_TEXT_FIELDS = new Set([
  "1-sentence summary",
  "Abstract (Eng)",
  "Abstract (Fr)",
  "proposition de nouveaux motifs",
  "description of link",
  "Connection to other stories",
  "Autres infos données dans le texte, pour la fiche conte",
  "ATU motifs (AI?)",
]);

const ALL_METADATA_FIELDS = LEGACY_METADATA_SECTIONS.flatMap((section) => section.fields);

export function buildBlankStoryFields(): Record<string, string> {
  return Object.fromEntries(ALL_METADATA_FIELDS.map((field) => [field, ""]));
}

export function getStoryFieldLabel(field: string): string {
  if (field === "Thème") {
    return "Theme";
  }
  return field;
}

export function normalizeDraftText(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

export function dedupeDraftValues(values: string[]): string[] {
  const nextValues: string[] = [];
  const seen = new Set<string>();

  values.forEach((value) => {
    const item = value.trim();
    if (!item) {
      return;
    }
    const marker = normalizeDraftText(item);
    if (seen.has(marker)) {
      return;
    }
    seen.add(marker);
    nextValues.push(item);
  });

  return nextValues;
}

export function splitKeywordText(value: string): string[] {
  return dedupeDraftValues(value.split(/[;\n]+/));
}
