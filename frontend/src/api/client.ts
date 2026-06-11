import type {
  CreateStoryResponse,
  DatasetStatus,
  DatasetUploadResponse,
  CanonicalTropeListItem,
  DeleteStoryTropeResponse,
  DeleteTropeResponse,
  ExplorationNetworkResponse,
  JobDetail,
  MergeTropesResponse,
  NearDuplicateTropeListResponse,
  StoryDetail,
  StoryListResponse,
  StoryTropeMutationResponse,
  StoryTropesResponse,
  SearchResponse,
  TropeSequenceGraphResponse,
  TropeDetail,
  TropeSearchResponse,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(message: string, status: number, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function buildUrl(path: string): string {
  return `${API_BASE}${path}`;
}

async function parseJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function errorMessageFromDetail(detail: unknown): string {
  if (typeof detail === "string") {
    return detail;
  }
  if (detail && typeof detail === "object") {
    const maybeMessage = (detail as { message?: unknown }).message;
    if (typeof maybeMessage === "string") {
      return maybeMessage;
    }
  }
  return "The request failed.";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(buildUrl(path), {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers || {}),
    },
  });

  if (!response.ok) {
    const detail = await parseJson(response);
    throw new ApiError(errorMessageFromDetail(detail), response.status, detail);
  }

  return (await parseJson(response)) as T;
}

export function getDatasetExportUrl(): string {
  return buildUrl("/dataset/export.csv");
}

export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Unexpected error.";
}

export function getDatasetStatus(): Promise<DatasetStatus> {
  return request<DatasetStatus>("/dataset/status");
}

export function clearDatasetData(): Promise<DatasetStatus> {
  return request<DatasetStatus>("/dataset", {
    method: "DELETE",
  });
}

export function getJob(jobId: string): Promise<JobDetail> {
  return request<JobDetail>(`/jobs/${jobId}`);
}

export function getCanonicalTropes(payload?: {
  unused_only?: boolean;
  q?: string;
  limit?: number;
}): Promise<CanonicalTropeListItem[]> {
  const params = new URLSearchParams();
  if (payload?.unused_only) {
    params.set("unused_only", "true");
  }
  if (payload?.q?.trim()) {
    params.set("q", payload.q.trim());
  }
  if (typeof payload?.limit === "number") {
    params.set("limit", String(payload.limit));
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return request<CanonicalTropeListItem[]>(`/tropes${suffix}`);
}

export function getTropeDetail(tropeId: string): Promise<TropeDetail> {
  return request<TropeDetail>(`/tropes/${tropeId}`);
}

export async function uploadDataset(file: File): Promise<DatasetUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(buildUrl("/dataset/upload"), {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const detail = await parseJson(response);
    throw new ApiError(errorMessageFromDetail(detail), response.status, detail);
  }

  return (await parseJson(response)) as DatasetUploadResponse;
}

export function getStories(): Promise<StoryListResponse> {
  return request<StoryListResponse>("/stories");
}

export function getStory(storyId: string): Promise<StoryDetail> {
  return request<StoryDetail>(`/stories/${storyId}`);
}

export function createStory(payload: {
  expected_dataset_version: number;
  fields: Record<string, string>;
  tropes: string[];
  keywords: string[];
}): Promise<CreateStoryResponse> {
  return request<CreateStoryResponse>("/stories", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export function getStoryTropes(storyId: string): Promise<StoryTropesResponse> {
  return request<StoryTropesResponse>(`/stories/${storyId}/tropes`);
}

export function addStoryTrope(
  storyId: string,
  payload: { expected_story_version: number; trope_id?: string; text?: string; origin?: string },
): Promise<StoryTropeMutationResponse> {
  return request<StoryTropeMutationResponse>(`/stories/${storyId}/tropes`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export function deleteStoryTrope(
  storyId: string,
  tropeId: string,
  expectedStoryVersion: number,
): Promise<DeleteStoryTropeResponse> {
  return request<DeleteStoryTropeResponse>(`/stories/${storyId}/tropes/${tropeId}`, {
    method: "DELETE",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ expected_story_version: expectedStoryVersion }),
  });
}

export function validateStoryTrope(
  storyId: string,
  tropeId: string,
  expectedStoryVersion: number,
): Promise<StoryTropeMutationResponse> {
  return request<StoryTropeMutationResponse>(`/stories/${storyId}/tropes/${tropeId}/validate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ expected_story_version: expectedStoryVersion }),
  });
}

export function getNearDuplicateTropes(): Promise<NearDuplicateTropeListResponse> {
  return request<NearDuplicateTropeListResponse>("/curation/near-duplicate-tropes");
}

export function mergeTropes(payload: {
  source_trope_id: string;
  target_trope_id: string;
}): Promise<MergeTropesResponse> {
  return request<MergeTropesResponse>("/curation/merge-tropes", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export function deleteTrope(tropeId: string, removeFromAllStories: boolean): Promise<DeleteTropeResponse> {
  const query = removeFromAllStories ? "?remove_from_all_stories=true" : "";
  return request<DeleteTropeResponse>(`/tropes/${tropeId}${query}`, {
    method: "DELETE",
  });
}

export function buildExplorationNetwork(payload: {
  selected_trope_id?: string | null;
  query?: string | null;
  min_similarity?: number;
  related_limit?: number;
  candidate_limit?: number;
}): Promise<ExplorationNetworkResponse> {
  return request<ExplorationNetworkResponse>("/exploration/network", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export function searchTropes(payload: { query: string; limit?: number }): Promise<TropeSearchResponse> {
  return request<TropeSearchResponse>("/search/tropes", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export function buildTropeSequenceGraph(payload: {
  query?: string | null;
  selected_trope_id?: string | null;
  similarity_threshold?: number;
  max_stories?: number;
  max_links_per_node?: number;
  vertical_spacing?: number;
}): Promise<TropeSequenceGraphResponse> {
  return request<TropeSequenceGraphResponse>("/visualizations/trope-sequence-graph", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export function searchKeywords(payload: { query: string; limit?: number }): Promise<SearchResponse> {
  return request<SearchResponse>("/search/keywords", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}
