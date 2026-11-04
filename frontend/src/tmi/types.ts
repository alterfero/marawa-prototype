export interface TmiNode {
  id: string;
  code: string;
  text: string;
  kind: "trope" | "chapter" | "division" | "heading" | "gap";
  parent_id: string | null;
  chapter: string;
  link_basis: "code" | "source_structure";
  source_rows: number[];
  source_texts: string[];
  bibliographies: string[];
  edition: "first" | "revised" | null;
  first_edition_code: string | null;
  alternate_texts: string[];
}

export interface TmiCatalogue {
  nodes: TmiNode[];
  roots: string[];
  stats: {
    source_rows: number;
    trope_count: number;
    chapter_count: number;
    first_edition_count: number;
    gap_count: number;
    conflicting_codes: number;
    duplicate_rows: number;
  };
  source: {
    filename: string;
    title: string;
    url: string;
    doi: string;
    license: string;
    license_url: string;
    sha256: string;
  };
}
