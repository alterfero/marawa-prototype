export interface JobSummary {
  id: string;
  status: string;
  job_type: string;
}

export type UserRole = "guest" | "contributor" | "admin";
export type UserStatus = "active" | "inactive" | "pending_invite";
export type ReviewStatus = "pending" | "approved" | "rejected";
export type ReviewType = "story_created" | "story_updated" | "trope_pending" | "keyword_pending";

export interface CurrentUser {
  id: string;
  email: string;
  display_name: string;
  role: UserRole;
  status: UserStatus;
  last_login_at: string | null;
  deactivated_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AuthSessionResponse {
  user: CurrentUser;
  csrf_token: string;
  expires_at: string;
}

export interface EmbeddingStatus {
  state: string;
  ready: boolean;
  current: boolean;
  model_name: string;
  artifact_version: number | null;
  rebuilt_dataset_version: number | null;
  indexed_trope_count: number;
  indexed_keyword_count: number;
  last_built_at: string | null;
  last_error_message: string | null;
  latest_rebuild_job: JobSummary | null;
}

export interface JobDetail {
  id: string;
  dataset_id: string | null;
  job_type: string;
  status: string;
  attempts: number;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
}

export interface DatasetStatus {
  story_count: number;
  trope_count: number;
  keyword_count: number;
  active_dataset_version: number | null;
  latest_job: JobSummary | null;
  embedding_status: EmbeddingStatus;
}

export interface DatasetUploadResponse {
  dataset_id: string;
  dataset_version: number;
  dataset_status: string;
  active_dataset_version: number | null;
  latest_job: JobSummary | null;
}

export interface DatasetRebuildResponse {
  dataset_id: string;
  dataset_version: number;
  dataset_status: string;
  active_dataset_version: number | null;
  created: boolean;
  queued_job: JobSummary;
}

export interface StorySummary {
  id: string;
  dataset_id: string;
  source_row_number: number | null;
  version: number;
  title: string;
  territory: string;
  summary: string;
  has_location: boolean;
  trope_count: number;
  keyword_count: number;
}

export interface StoryListResponse {
  items: StorySummary[];
  total: number;
}

export interface StoryTrope {
  id: string;
  text: string;
  story_count: number;
  origin: string;
  status: string;
  position: number | null;
}

export interface StoryKeyword {
  id: string;
  text: string;
  position: number | null;
}

export interface StoryDetail {
  id: string;
  dataset_id: string;
  source_row_number: number | null;
  version: number;
  created_at: string;
  updated_at: string;
  fields: Record<string, string>;
  tropes: StoryTrope[];
  keywords: StoryKeyword[];
}

export interface CreateStoryResponse {
  story: StoryDetail;
  dataset_version: number;
  queued_job: JobSummary | null;
}

export interface StoryTropesResponse {
  story_id: string;
  story_version: number;
  items: StoryTrope[];
}

export interface StoryTropeMutationResponse {
  story_id: string;
  story_version: number;
  dataset_version: number;
  trope: StoryTrope;
  queued_job: JobSummary | null;
}

export interface DeleteStoryTropeResponse {
  story_id: string;
  story_version: number;
  dataset_version: number;
  deleted_trope_id: string;
  queued_job: JobSummary | null;
}

export interface StoryKeywordMutationResponse {
  story_id: string;
  story_version: number;
  dataset_version: number;
  keyword: StoryKeyword;
  queued_job: JobSummary | null;
}

export interface DeleteStoryKeywordResponse {
  story_id: string;
  story_version: number;
  dataset_version: number;
  deleted_keyword_id: string;
  queued_job: JobSummary | null;
}

export interface TropeSummary {
  id: string;
  text: string;
  story_count: number;
}

export interface NearDuplicateTropePair {
  source_trope: TropeSummary;
  target_trope: TropeSummary;
  similarity_score: number;
  metadata: Record<string, unknown>;
}

export interface NearDuplicateTropeListResponse {
  items: NearDuplicateTropePair[];
  artifact_version: number | null;
  model_name: string;
  total: number;
}

export interface MergeTropesResponse {
  source_trope_id: string;
  target_trope_id: string;
  affected_story_count: number;
  dataset_version: number;
  queued_job: JobSummary | null;
}

export interface AppliedMergeSummary {
  source_trope_id: string;
  target_trope_id: string;
  affected_story_count: number;
}

export interface ValidateTropesResponse {
  applied_merges: AppliedMergeSummary[];
  merge_count: number;
  affected_story_count: number;
  dataset_version: number;
  queued_job: JobSummary | null;
}

export interface DeleteTropeResponse {
  deleted_trope_id: string;
  affected_story_count: number;
  dataset_version: number;
  queued_job: JobSummary | null;
}

export interface CreateTropeResponse {
  trope: TropeSummary;
  created: boolean;
}

export interface CanonicalKeywordListItem {
  id: string;
  text: string;
  story_count: number;
}

export interface KeywordStorySummary {
  id: string;
  title: string;
  source_row_number: number | null;
}

export interface KeywordDetail {
  id: string;
  text: string;
  story_count: number;
  stories: KeywordStorySummary[];
}

export interface ExplorationCandidate {
  id: string;
  text: string;
  story_count: number;
  score: number;
}

export interface ExplorationMatchedTrope {
  id: string;
  text: string;
  story_count: number;
  score: number;
}

export interface ExplorationStoryTrope {
  id: string;
  text: string;
  story_count: number;
}

export interface ExplorationMarker {
  story_id: string;
  source_row_number: number | null;
  coordinates: number[] | null;
  kind: string;
  similarity: number;
  matched_tropes: ExplorationMatchedTrope[];
  story_tropes: ExplorationStoryTrope[];
  color: string;
  title: string;
  hover_title: string;
  abstract: string;
  has_location: boolean;
}

export interface ExplorationConnection {
  source_story_id: string;
  target_story_id: string;
  source_coordinates: number[];
  target_coordinates: number[];
  similarity: number;
  color: string;
}

export interface ExplorationSelectedTrope {
  id: string;
  text: string;
  story_count: number;
}

export interface ExplorationNetworkResponse {
  selected_trope: ExplorationSelectedTrope | null;
  selected_trope_candidates: ExplorationCandidate[];
  related_tropes: ExplorationCandidate[];
  original_markers: ExplorationMarker[];
  related_markers: ExplorationMarker[];
  connections: ExplorationConnection[];
  bounds: number[][] | null;
  missing_original_coords: number;
  missing_related_coords: number;
}

export interface SearchExplanation {
  method: string;
  model_name: string;
  artifact_version: number;
  vector_dimension: number | null;
  cache_hit: boolean;
  matched_query_exactly: boolean;
  near_duplicate: boolean;
}

export interface SearchItem {
  id: string;
  text: string;
  story_count: number;
  score: number;
  explanation: SearchExplanation;
}

export interface SearchResponse {
  items: SearchItem[];
  model_name: string;
  artifact_version: number | null;
}

export type TropeSearchItem = SearchItem;
export type TropeSearchResponse = SearchResponse;

export interface CanonicalTropeListItem {
  id: string;
  text: string;
  story_count: number;
}

export interface TropeStorySummary {
  id: string;
  title: string;
  source_row_number: number | null;
}

export interface TropeDetail {
  id: string;
  text: string;
  story_count: number;
  stories: TropeStorySummary[];
}

export interface TropeReference {
  id: string;
  text: string;
  story_count: number;
}

export interface TropeSequenceGraphSelectedTrope {
  id: string;
  text: string;
  score: number;
}

export interface TropeSequenceGraphLayoutBasis {
  selected_trope: TropeSequenceGraphSelectedTrope;
  query: string | null;
  similarity_threshold: number;
  max_stories: number;
  max_links_per_node: number;
  sequence_axis_label: string;
}

export interface TropeSequenceGraphNode {
  id: string;
  kind: string;
  story_id: string;
  story_title: string;
  source_row_number: number | null;
  story_match_score?: number | null;
  occurrence_count?: number | null;
  trope_id?: string | null;
  trope_text?: string | null;
  sequence_index?: number | null;
  anchor_x?: number | null;
  anchor_y?: number | null;
  target_z?: number | null;
  lat: number;
  lon: number;
  x: number;
  y: number;
  z: number;
  fx?: number;
  fy?: number;
  fz?: number;
  has_location: boolean;
  status?: string | null;
  origin?: string | null;
  is_selected_trope?: boolean | null;
  selected_similarity_score?: number | null;
}

export interface ReviewSubjectPreview {
  id: string;
  title?: string | null;
  text?: string | null;
  source_row_number?: number | null;
  version?: number | null;
  review_status?: string | null;
  story_count?: number | null;
}

export interface ReviewItem {
  id: string;
  dataset_id: string | null;
  review_type: ReviewType;
  subject_table: string;
  subject_id: string;
  status: ReviewStatus;
  created_by_user_id: string | null;
  resolved_by_user_id: string | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  metadata: Record<string, unknown>;
  subject_preview: ReviewSubjectPreview | null;
}

export interface CreateUserResponse {
  user: CurrentUser;
  invite_token: string;
  token_kind: string;
  expires_at: string;
}

export interface PasswordResetResponse {
  user: CurrentUser;
  reset_token: string;
  token_kind: string;
  expires_at: string;
}

export interface UserLifecycleResponse {
  user: CurrentUser;
  revoked_session_count?: number | null;
}

export interface TropeSequenceGraphLink {
  source: string;
  target: string;
  kind: string;
  strength: number;
  similarity?: number | null;
}

export interface TropeSequenceGraphResponse {
  layout_basis: TropeSequenceGraphLayoutBasis;
  nodes: TropeSequenceGraphNode[];
  links: TropeSequenceGraphLink[];
  warnings: string[];
}
