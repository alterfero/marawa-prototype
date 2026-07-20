import type {
  AuthSessionResponse,
  CanonicalKeywordListItem,
  CreateTropeResponse,
  CreateStoryResponse,
  CreateUserResponse,
  CurrentUser,
  DatasetStatus,
  DatasetRebuildResponse,
  DatasetUploadResponse,
  CanonicalTropeListItem,
  DeleteStoryKeywordResponse,
  DeleteStoryTropeResponse,
  DeleteTropeResponse,
  ExplorationNetworkResponse,
  JobDetail,
  KeywordDetail,
  MergeTropesResponse,
  NearDuplicateTropeListResponse,
  PasswordResetResponse,
  ReviewItem,
  ReviewStatus,
  StoryCompleteness,
  StoryDetail,
  StoryKeywordMutationResponse,
  StoryListResponse,
  StoryTropeMutationResponse,
  StoryTropesResponse,
  UserLifecycleResponse,
  ValidateTropesResponse,
  SearchResponse,
  TropeSequenceGraphResponse,
  TropeDetail,
  TropeSearchResponse,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/$/, "");
const CSRF_COOKIE_NAME = import.meta.env.VITE_CSRF_COOKIE_NAME || "marawa_csrf";
const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

let csrfTokenCache: string | null = null;

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

function readCookie(name: string): string | null {
  const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = document.cookie.match(new RegExp(`(?:^|; )${escapedName}=([^;]*)`));
  if (!match) {
    return null;
  }
  return decodeURIComponent(match[1]);
}

function getCsrfToken(): string | null {
  return csrfTokenCache || readCookie(CSRF_COOKIE_NAME);
}

export function setCsrfToken(token: string | null): void {
  csrfTokenCache = token?.trim() || null;
}

export function clearCsrfToken(): void {
  csrfTokenCache = null;
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
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");

  const method = (init?.method || "GET").toUpperCase();
  if (MUTATING_METHODS.has(method) && !headers.has("X-CSRF-Token")) {
    const csrfToken = getCsrfToken();
    if (csrfToken) {
      headers.set("X-CSRF-Token", csrfToken);
    }
  }

  const response = await fetch(buildUrl(path), {
    ...init,
    credentials: "include",
    headers,
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

export async function login(payload: { email: string; password: string }): Promise<AuthSessionResponse> {
  const response = await request<AuthSessionResponse>("/auth/login", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  setCsrfToken(response.csrf_token);
  return response;
}

export async function redeemAuthToken(payload: {
  token: string;
  new_password: string;
  display_name?: string;
}): Promise<AuthSessionResponse> {
  const response = await request<AuthSessionResponse>("/auth/redeem-token", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  setCsrfToken(response.csrf_token);
  return response;
}

export async function logout(): Promise<void> {
  await request<{ ok: boolean }>("/auth/logout", {
    method: "POST",
  });
  clearCsrfToken();
}

export function getCurrentUser(): Promise<CurrentUser> {
  return request<CurrentUser>("/auth/me");
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

export function createCanonicalTrope(text: string): Promise<CreateTropeResponse> {
  return request<CreateTropeResponse>("/tropes", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text }),
  });
}

export async function uploadDataset(file: File): Promise<DatasetUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  return request<DatasetUploadResponse>("/dataset/upload", {
    method: "POST",
    body: formData,
  });
}

export function requestDatasetRebuild(): Promise<DatasetRebuildResponse> {
  return request<DatasetRebuildResponse>("/dataset/rebuild", {
    method: "POST",
  });
}

export function getStories(): Promise<StoryListResponse> {
  return request<StoryListResponse>("/stories");
}

export function getStory(storyId: string): Promise<StoryDetail> {
  return request<StoryDetail>(`/stories/${storyId}`);
}

export function updateStory(payload: {
  story_id: string;
  expected_story_version: number;
  fields: Record<string, string>;
  completeness?: StoryCompleteness;
}): Promise<CreateStoryResponse> {
  return request<CreateStoryResponse>(`/stories/${payload.story_id}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      expected_story_version: payload.expected_story_version,
      fields: payload.fields,
      completeness: payload.completeness,
    }),
  });
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

export function replaceStoryTrope(
  storyId: string,
  tropeId: string,
  payload: { expected_story_version: number; trope_id?: string; text?: string },
): Promise<StoryTropeMutationResponse> {
  return request<StoryTropeMutationResponse>(`/stories/${storyId}/tropes/${tropeId}`, {
    method: "PUT",
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

export function addStoryKeyword(
  storyId: string,
  payload: { expected_story_version: number; keyword_id?: string; text?: string },
): Promise<StoryKeywordMutationResponse> {
  return request<StoryKeywordMutationResponse>(`/stories/${storyId}/keywords`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export function replaceStoryKeyword(
  storyId: string,
  keywordId: string,
  payload: { expected_story_version: number; keyword_id?: string; text?: string },
): Promise<StoryKeywordMutationResponse> {
  return request<StoryKeywordMutationResponse>(`/stories/${storyId}/keywords/${keywordId}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export function deleteStoryKeyword(
  storyId: string,
  keywordId: string,
  expectedStoryVersion: number,
): Promise<DeleteStoryKeywordResponse> {
  return request<DeleteStoryKeywordResponse>(`/stories/${storyId}/keywords/${keywordId}`, {
    method: "DELETE",
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

export function validateTropeMerges(payload: {
  merges: Array<{
    source_trope_id: string;
    target_trope_id: string;
  }>;
}): Promise<ValidateTropesResponse> {
  return request<ValidateTropesResponse>("/curation/validate-merges", {
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

export function getCanonicalKeywords(payload?: {
  unused_only?: boolean;
  q?: string;
  limit?: number;
}): Promise<CanonicalKeywordListItem[]> {
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
  return request<CanonicalKeywordListItem[]>(`/keywords${suffix}`);
}

export function getKeywordDetail(keywordId: string): Promise<KeywordDetail> {
  return request<KeywordDetail>(`/keywords/${keywordId}`);
}

export function buildExplorationNetwork(payload: {
  selected_trope_id?: string | null;
  query?: string | null;
  story_filters?: Array<{ field: string; selected_values: string[] }>;
  story_filter_sets?: Array<{
    id: string;
    label: string;
    color: string;
    filters: Array<{ field: string; selected_values: string[] }>;
    selected_tropes?: Array<{ id: string; text: string }>;
  }>;
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

export function getReviewItems(payload?: {
  status?: ReviewStatus | "all";
  limit?: number;
}): Promise<ReviewItem[]> {
  const params = new URLSearchParams();
  if (payload?.status && payload.status !== "all") {
    params.set("status", payload.status);
  }
  if (typeof payload?.limit === "number") {
    params.set("limit", String(payload.limit));
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return request<ReviewItem[]>(`/review/items${suffix}`);
}

export function getReviewItem(reviewId: string): Promise<ReviewItem> {
  return request<ReviewItem>(`/review/items/${reviewId}`);
}

export function approveReviewItem(reviewId: string, note?: string): Promise<ReviewItem> {
  return request<ReviewItem>(`/review/items/${reviewId}/approve`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ note: note?.trim() || null }),
  });
}

export function rejectReviewItem(payload: {
  review_id: string;
  note?: string;
  merge_target_id?: string | null;
  remove_from_all_stories?: boolean;
}): Promise<ReviewItem> {
  return request<ReviewItem>(`/review/items/${payload.review_id}/reject`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      note: payload.note?.trim() || null,
      merge_target_id: payload.merge_target_id || null,
      remove_from_all_stories: Boolean(payload.remove_from_all_stories),
    }),
  });
}

export function getUsers(): Promise<CurrentUser[]> {
  return request<CurrentUser[]>("/admin/users");
}

export function createUser(payload: {
  email: string;
  display_name: string;
  role: CurrentUser["role"];
}): Promise<CreateUserResponse> {
  return request<CreateUserResponse>("/admin/users", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export function updateUser(payload: {
  user_id: string;
  display_name?: string;
  role?: CurrentUser["role"];
}): Promise<CurrentUser> {
  return request<CurrentUser>(`/admin/users/${payload.user_id}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      display_name: payload.display_name,
      role: payload.role,
    }),
  });
}

export function deactivateUser(userId: string): Promise<UserLifecycleResponse> {
  return request<UserLifecycleResponse>(`/admin/users/${userId}/deactivate`, {
    method: "POST",
  });
}

export function activateUser(userId: string): Promise<UserLifecycleResponse> {
  return request<UserLifecycleResponse>(`/admin/users/${userId}/activate`, {
    method: "POST",
  });
}

export function issuePasswordReset(userId: string): Promise<PasswordResetResponse> {
  return request<PasswordResetResponse>(`/admin/users/${userId}/reset-password`, {
    method: "POST",
  });
}
