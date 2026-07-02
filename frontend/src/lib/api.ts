/**
 * Клиент к FastAPI-бэкенду.
 * Все запросы идут через /api/* — Vite в dev-режиме проксирует на :8000.
 */

export interface PublishedGroup {
  key: string;
  label: string;
  count: number;
  ids: number[];
  copy: string;
}
export interface PublishedHistory {
  period: string;
  total: number;
  groups: PublishedGroup[];
}

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
}

export interface InfoResponse {
  uploads: number;
  outputs: number;
  assets: number;
  configs: number;
}

export interface StorageClearResult {
  scope: string;
  uploads_removed: number;
  output_cleared: number;
  outputs_cleared: number;
}

export interface Geo {
  id: string;
  country_name: string;
  currency: string;
  lang: string;
  lang_html: string;
}

export interface Vertical {
  id: string;
  label: string;
  exclude_word: string;
}

export interface LogLine {
  text: string;
  level: 'plain' | 'success' | 'warning' | 'error' | 'info' | 'section' | 'dim';
}


export interface OptimizeImage {
  path: string;
  name: string;
  size_kb: number;
}

export interface OptimizeScan {
  images: OptimizeImage[];
  skipped: string[];
  total: number;
}

export interface ProcessResult {
  success: boolean;
  result_url: string | null;
  result_name: string | null;
  log: LogLine[];
}

export interface ProductCandidate {
  word: string;
  count: number;
}

export interface ImageInfo {
  path: string;       // путь внутри zip
  name: string;       // только имя файла
  size: number;       // размер в байтах
  is_product: boolean; // вероятно фото продукта
}

export interface ScanDetection {
  product: string | null;
  product_candidates: ProductCandidate[];
  cur_sym: string | null;
  price_new_str: string | null;
  price_old_str: string | null;
  widget_price_new: string | null;
  widget_price_old: string | null;
  detected_country: {
    data_country: string[];
    data_language: string[];
    input_country: string[];
    input_language: string[];
    lang_html: string | null;
    exclude_word: string | null;
  };
  prod_images: string[];
  all_images: ImageInfo[];
}

export interface ScanResponse {
  upload_id: string;
  detection: ScanDetection;
}

export interface OutputFile {
  name: string;
  size: number;
  modified: number;
  url: string;
}

export interface BatchWidgetResult {
  file_id: string;
  source_name: string;
  success: boolean;
  status: string;       // inserted | updated | replaced_old | error | no_html
  error: string | null;
  detected: {
    country?: string;
    language?: string;
    exclude_word?: string;
    product_name?: string;
    from_html?: any;
    from_name?: any;
  };
  log: string[];
}

export interface BatchWidgetResponse {
  total: number;
  success: number;
  failed: number;
  results: BatchWidgetResult[];
  batch_url: string | null;
  batch_name: string | null;
  log: LogLine[];
}


// ── Задачи (AdRobot) ───────────────────────────────────
export interface TaskSummary {
  uid: string;
  url: string;
  title: string;
  created_by: string;
  assigned_to: string;
  status: string;
  offer: string;
  category: string;
  deadline: string;
}

export interface CommentAttachment {
  url: string;
  filename: string;
  kind: 'image' | 'archive' | 'file' | 'site';
}

export interface TaskComment {
  author: string;
  time: string;
  text: string;
  attachments: CommentAttachment[];
}

export interface TaskDetail {
  uid: string;
  url: string;
  title: string;
  fields: Record<string, string>;
  variants: string[];
  activity: { author: string; time: string; text: string }[];
  comments: TaskComment[];
  attachments: CommentAttachment[];
  actions: string[];
}

// Кластер задач на один оффер (кандидат на объединение в одну сессию).
export interface TaskGroup {
  offer: string;
  offer_key: string;
  count: number;
  tasks: TaskSummary[];
}

export interface SessionTaskRef {
  uid: string;
  title: string;
  url?: string;
}

// Медиа-ресурс ленда (фото/гиф/видео), который можно заменить.
export interface LanderMedia {
  path: string;
  name: string;
  size: number;
  kind: 'image' | 'video';
  is_product: boolean;
  used?: boolean;
}

// Изолированная по задаче медиа-замена.
export interface Replacement {
  name: string;
  size: number;
}

export interface LanderReplacements {
  replacements: Replacement[];
  comment_images: { url: string; filename: string }[];
}

// Сообщение чата-агента (OpenAI-формат + ts/cost).
export interface ChatMessage {
  role: 'user' | 'assistant' | 'tool' | 'system';
  content: string | null;
  tool_calls?: { id: string; type: string; function: { name: string; arguments: string } }[];
  tool_call_id?: string;
  name?: string;
  ts?: number;
  cost_rub?: number;
}

export interface AiStatus {
  configured: boolean;
  model: string | null;
  balance: number | null;
  models?: { id: string; label: string }[];
}

// Результат перевода ленда.
export interface TranslateDiff {
  file: string;
  original: string;
  translated: string;
}
export interface TranslateResult {
  lang: string;
  model?: string;
  diff: TranslateDiff[];
  applied: number;
  mode?: string;
  note?: string;
}

// План/результат заливки ленда в Keitaro.
export interface KeitaroPlan {
  sid: string;
  lid: string;
  zip_path: string;
  group: string;
  product: string;
  geo_id: string;
  country_query: string;
  lang: string;
  site_type: 'land' | 'pl' | 'vsl';
  name_template: string;
  vertical_code: string;
  vertical_full?: string;
  product_search?: string;
  country_name?: string;
  bracket?: string;
  mode: string;
  note?: string;
  // присутствуют после реальной заливки (execute)
  offer_id?: number;
  network?: string | null;
  final_name?: string;
  // mode=created_pending_rename — оффер создан, ждём подтверждения id
  name_no_id?: string;
  id_candidates?: { id: number; name: string; has_id_prefix: boolean }[];
  id_best?: number | null;
  id_confident?: boolean;
  proposed_name?: string | null;
}

export interface KeitaroRenameResult {
  offer_id: number;
  final_name: string;
  mode: string;
}

// Событие стриминга чата-агента.
export type ChatStreamEvent =
  | { type: 'token'; text: string }
  | { type: 'tool_call'; name: string }
  | { type: 'tool_result'; name: string; content: string }
  | { type: 'assistant_message'; message: ChatMessage }
  | { type: 'done'; lander: LanderState }
  | { type: 'error'; error: string };

// Событие стриминга перевода.
export type TranslateEvent =
  | { type: 'start'; lang: string; lang_name: string; model: string; total: number; rtl: boolean }
  | { type: 'block'; items: { original: string; translated: string }[]; done: number; total: number }
  | { type: 'progress'; done: number; total: number; warn?: string }
  | { type: 'done'; applied: number; translated?: number }
  | { type: 'error'; error: string };

/** Стриминговый перевод (сразу применяет). Вызывает onEvent на каждое SSE-событие. */
export async function translateStream(
  sid: string,
  lid: string,
  targetLang: string | undefined,
  onEvent: (ev: TranslateEvent) => void,
): Promise<void> {
  const res = await fetch(
    `/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/translate/stream`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_lang: targetLang }),
    },
  );
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status}: ${text}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split('\n\n');
    buf = parts.pop() || '';
    for (const part of parts) {
      const line = part.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;
      try { onEvent(JSON.parse(line.slice(5).trim())); } catch { /* пропуск */ }
    }
  }
}

/** Стриминговый чат: вызывает onEvent для каждого SSE-события. */
export async function chatStream(
  sid: string,
  lid: string,
  message: string,
  onEvent: (ev: ChatStreamEvent) => void,
  signal?: AbortSignal,
  model?: string,
): Promise<void> {
  const res = await fetch(
    `/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/chat/stream`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, model: model || null }),
      signal,
    },
  );
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status}: ${text}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split('\n\n');
    buf = parts.pop() || '';
    for (const part of parts) {
      const line = part.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(5).trim()));
      } catch { /* пропускаем неполный кусок */ }
    }
  }
}

export interface KeitaroUploadEvent {
  type: 'step' | 'done' | 'error';
  message?: string;
  result?: any;
  error?: string;
}

/** Заливка в Keitaro со стримом шагов (SSE). */
export async function keitaroUploadStream(
  sid: string,
  lid: string,
  opts: { type?: string; network?: string },
  onEvent: (ev: KeitaroUploadEvent) => void,
): Promise<void> {
  const res = await fetch(
    `/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/keitaro-upload/stream`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: opts.type || null, network: opts.network || null }),
    },
  );
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status}: ${text}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split('\n\n');
    buf = parts.pop() || '';
    for (const part of parts) {
      const line = part.split('\n').find((l) => l.startsWith('data:'));
      if (!line) continue;
      try { onEvent(JSON.parse(line.slice(5).trim())); } catch { /* пропуск */ }
    }
  }
}

// ── Сессии адаптации ───────────────────────────────────
export interface LanderState {
  lander_id: string;
  status: string;
  task_uid?: string | null;
  task_title?: string | null;
  zip_path?: string | null;
  zip_name?: string | null;
  size?: number | null;
  offer_name?: string | null;
  scan?: ScanDetection | null;
  output_name?: string | null;
  output_url?: string | null;
  adapt_params?: Record<string, any> | null;
  adapt_log?: LogLine[];
  error?: string | null;
  chat?: any[];
  offer_override?: string | null;
  history?: LanderVersion[];
}

export interface LanderVersion {
  id: string;
  step: number;
  label: string;
  created_at?: number | null;
  size?: number | null;
  available: boolean;
}

export interface SessionSummary {
  id: string;
  task_title: string;
  offer: string;
  status: string;
  created_at: number;
  archived_at?: number | null;
  expires_at?: number | null;
  task_count?: number;
  tasks?: SessionTaskRef[];
  landers: Record<string, { lander_id: string; status: string; task_uid?: string | null }>;
}

export interface SessionFull {
  id: string;
  task_uid: string | null;
  task_title: string;
  offer: string;
  fields: Record<string, string>;
  tasks?: { uid: string; title: string; offer: string; url: string; fields: Record<string, string> }[];
  landers: Record<string, LanderState>;
  status: string;
  created_at: number;
  archived_at?: number | null;
  expires_at?: number | null;
}

export interface SuggestParams {
  geo_id: string;
  product_old: string;
  product_new: string;
  price_new: string;
  price_old: string;
  price_new_num: string;
  price_new_cur: string;
  price_old_num: string;
  price_old_cur: string;
  src_price_new_num: string;
  src_price_new_cur: string;
  src_price_old_num: string;
  src_price_old_cur: string;
  exclude_word: string;
  image_map: Record<string, string>;
  custom_replacements: string;
  group?: string;
  _hints?: any;
}

export interface AdaptResult {
  success: boolean;
  status: string;
  output_name: string | null;
  output_url: string | null;
  log: LogLine[];
  error: string | null;
}


class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  // timeoutMs — прерывать «зависшие» запросы (напр. фоновый polling), чтобы
  // медленный/висящий бэкенд не накапливал подвисшие соединения во вкладке
  // (раньше из-за этого после фриза приходилось делать хард-рефреш).
  const { timeoutMs, ...rest } = init || {};
  let signal = rest.signal ?? undefined;
  if (timeoutMs && !signal && typeof AbortSignal !== 'undefined' && 'timeout' in AbortSignal) {
    signal = (AbortSignal as any).timeout(timeoutMs);
  }
  const res = await fetch(path, { ...rest, signal });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new ApiError(res.status, `${res.status} ${res.statusText}: ${text}`);
  }
  return res.json();
}

async function postFormData<T>(path: string, formData: FormData): Promise<T> {
  return request<T>(path, { method: 'POST', body: formData });
}


export const api = {
  // ── Базовое ──────────────────────────────────────────
  health: () => request<HealthResponse>('/api/health'),
  info:   () => request<InfoResponse>('/api/info'),

  clearStorage: (scope: 'temp' | 'all') =>
    request<StorageClearResult>('/api/storage/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope }),
    }),

  // ── Справочники ──────────────────────────────────────
  geos:      () => request<Geo[]>('/api/geos'),
  geoWords:  (uploadId: string, sourceGeo?: string) =>
    request<{ source_geo: string; found_words: string[] }>(
      `/api/geo-words/${encodeURIComponent(uploadId)}${sourceGeo ? `?source_geo=${sourceGeo}` : ''}`
    ),
  verticals: () => request<Vertical[]>('/api/verticals'),
  assets:    () => request<string[]>('/api/assets'),
  outputs:   () => request<OutputFile[]>('/api/outputs'),

  // ── Управление ассетами ──────────────────────────────
  uploadAsset: async (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return postFormData<{ name: string; size: number }>('/api/assets/upload', fd);
  },
  deleteAsset: (filename: string) =>
    request<{ deleted: string }>(`/api/assets/${encodeURIComponent(filename)}`, { method: 'DELETE' }),

  // ── Обработка ────────────────────────────────────────
  inject: async (file: File, params: Record<string, string>) => {
    const fd = new FormData();
    fd.append('file', file);
    Object.entries(params).forEach(([k, v]) => fd.append(k, v));
    return postFormData<ProcessResult>('/api/inject', fd);
  },

  clean: async (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return postFormData<ProcessResult>('/api/clean', fd);
  },

  cleanInject: async (file: File, params: Record<string, string>) => {
    const fd = new FormData();
    fd.append('file', file);
    Object.entries(params).forEach(([k, v]) => fd.append(k, v));
    return postFormData<ProcessResult>('/api/clean-inject', fd);
  },

  anchors: async (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return postFormData<ProcessResult>('/api/anchors', fd);
  },

  scan: async (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return postFormData<ScanResponse>('/api/scan', fd);
  },

  adapt: async (params: Record<string, string>) => {
    const fd = new FormData();
    Object.entries(params).forEach(([k, v]) => fd.append(k, v));
    return postFormData<ProcessResult>('/api/adapt', fd);
  },

  batchWidget: async (files: File[], overrides: Record<string, any> = {}, discount = '50%') => {
    const fd = new FormData();
    files.forEach((f) => fd.append('files', f));
    fd.append('overrides', JSON.stringify(overrides));
    fd.append('discount', discount);
    return postFormData<BatchWidgetResponse>('/api/batch-widget', fd);
  },

  batchWidgetHtmlUrl: (fileId: string, batchName: string) =>
    `/api/batch-widget/html/${encodeURIComponent(fileId)}?batch_name=${encodeURIComponent(batchName)}`,
  optimizeScan: async (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return postFormData<{ upload_id: string; scan: OptimizeScan }>('/api/optimize/scan', fd);
  },

  optimizeRun: async (uploadId: string) => {
    const fd = new FormData();
    fd.append('upload_id', uploadId);
    return postFormData<ProcessResult>('/api/optimize/run', fd);
  },

  // Названия офферов-доноров по ID из Keitaro (подсказка при вводе ID ленда).
  offerNames: (ids: string[]) =>
    request<{ names: Record<string, string | null>; error?: string }>('/api/keitaro/offer-names', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    }),

  // ── Задачи ───────────────────────────────────────────
  tasks:      (refresh = false) => request<TaskSummary[]>(`/api/tasks${refresh ? '?refresh=1' : ''}`),
  taskGroups: (refresh = false) => request<TaskGroup[]>(`/api/tasks/groups${refresh ? '?refresh=1' : ''}`),
  taskDetail: (uid: string) => request<TaskDetail>(`/api/tasks/${encodeURIComponent(uid)}`),
  // Сменить статус задачи (PENDING → IN_PROCESS «Start working»).
  taskChangeStatus: (uid: string, status = 'IN_PROCESS') =>
    request<TaskDetail>(`/api/tasks/${encodeURIComponent(uid)}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    }),
  tasksPoll:  (notify = true) =>
    request<{ new_count: number; new: TaskSummary[] }>(`/api/tasks/poll?notify=${notify ? 1 : 0}`, { method: 'POST' }),
  // URL прокси-вложения (превью/скачивание через авторизованную сессию AdRobot).
  attachmentUrl: (url: string, download = false) =>
    `/api/tasks/attachment?url=${encodeURIComponent(url)}${download ? '&download=1' : ''}`,
  // Импорт картинки из комментария в storage/assets (для замены фото через image_map).
  assetFromUrl: (url: string, filename?: string) =>
    request<{ name: string; size: number }>('/api/assets/from-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, filename }),
    }),

  // ── Сессии ───────────────────────────────────────────
  sessions:   (archived = false) => request<SessionSummary[]>(`/api/sessions${archived ? '?archived=1' : ''}`),
  session:    (sid: string) => request<SessionFull>(`/api/sessions/${encodeURIComponent(sid)}`, { timeoutMs: 15000 }),
  archiveSession:   (sid: string) => request<SessionFull>(`/api/sessions/${encodeURIComponent(sid)}/archive`, { method: 'POST' }),
  unarchiveSession: (sid: string) => request<SessionFull>(`/api/sessions/${encodeURIComponent(sid)}/unarchive`, { method: 'POST' }),
  // URL файла (фото/гиф/видео) из исходного архива ленда — для превью.
  landerFileUrl: (sid: string, lid: string, path: string) =>
    `/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/file?path=${encodeURIComponent(path)}`,
  createSession: (body: { task_uid?: string; task_uids?: string[]; lander_ids?: string[]; offer?: string }) =>
    request<SessionFull>('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  addSessionTask: (sid: string, task_uid: string) =>
    request<SessionFull>(`/api/sessions/${encodeURIComponent(sid)}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_uid }),
    }),
  addLanders: (sid: string, lander_ids: string[]) =>
    request<SessionFull>(`/api/sessions/${encodeURIComponent(sid)}/landers`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lander_ids }),
    }),
  reorderLanders: (sid: string, order: string[]) =>
    request<SessionFull>(`/api/sessions/${encodeURIComponent(sid)}/landers/reorder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order }),
    }),
  deleteLander: (sid: string, lid: string) =>
    request<SessionFull>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}`, {
      method: 'DELETE',
    }),
  addTaskVariant: (sid: string, lid: string) =>
    request<{ task_uid: string; offer_id: number; task_title: string }>(
      `/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/task-variant`,
      { method: 'POST' }),
  moveTaskVariants: (sid: string, lid: string, scope: 'private' | 'public') =>
    request<{ task_uid: string; scope: string }>(
      `/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/task-variants-move`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope }) }),
  submitTaskReview: (sid: string, lid: string) =>
    request<{ task_uid: string; status: string; task_title: string }>(
      `/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/task-review`,
      { method: 'POST' }),
  testCampaign: (sid: string, lid: string) =>
    request<{ campaign_url: string; campaign_name: string; offer_id: number }>(
      `/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/test-campaign`,
      { method: 'POST' }),
  reinstallLander: (sid: string, lid: string) =>
    request<SessionFull>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/reinstall`, {
      method: 'POST',
    }),
  uploadLander: async (sid: string, file: File, landerId = '') => {
    const fd = new FormData();
    fd.append('file', file);
    if (landerId) fd.append('lander_id', landerId);
    return postFormData<LanderState>(`/api/sessions/${encodeURIComponent(sid)}/landers/upload`, fd);
  },
  // Скачать лендинг по ссылке на сайт (Playwright) и добавить в сессию.
  landerFromSite: (sid: string, url: string, taskUid?: string, proxyId?: string) =>
    request<LanderState>(`/api/sessions/${encodeURIComponent(sid)}/landers/from-site`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, task_uid: taskUid, proxy_id: proxyId }),
    }),

  // Прокси для скрапинга гео-защищённых лендов.
  proxies: () => request<{ id: string; label: string; server: string; geo: string }[]>('/api/proxies'),
  addProxy: (proxy: string, label?: string, geo?: string) =>
    request<{ id: string; label: string; server: string; geo: string }>('/api/proxies', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proxy, label, geo }),
    }),
  deleteProxy: (id: string) =>
    request<{ ok: boolean }>(`/api/proxies/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  importDolphinProxies: () =>
    request<{ imported: number; total: number }>('/api/proxies/import-dolphin', { method: 'POST' }),

  // Добавить ленд из архива, прикреплённого в комментарии задачи.
  landerFromUrl: (sid: string, url: string, filename?: string, landerId?: string, taskUid?: string) =>
    request<LanderState>(`/api/sessions/${encodeURIComponent(sid)}/landers/from-url`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, filename, lander_id: landerId, task_uid: taskUid }),
    }),

  // Медиа ленда (фото/гиф/видео) для блока замены. all=true — включая
  // неиспользуемые файлы из архива (по умолчанию — только используемые).
  landerMedia: (sid: string, lid: string, all = false) =>
    request<LanderMedia[]>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/media${all ? '?all=1' : ''}`),
  // Изолированные по задаче замены (+ авто-подгрузка фото оффера при autoload).
  landerReplacements: (sid: string, lid: string, autoload = false) =>
    request<LanderReplacements>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/replacements${autoload ? '?autoload=1' : ''}`),
  importReplacement: (sid: string, lid: string, url: string, filename?: string) =>
    request<{ name: string }>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/replacements/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, filename }),
    }),
  uploadReplacements: (sid: string, lid: string, files: FileList | File[]) => {
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append('files', f));
    return postFormData<{ names: string[] }>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/replacements/upload`, fd);
  },
  replacementFileUrl: (sid: string, lid: string, name: string) =>
    `/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/replacements/file?name=${encodeURIComponent(name)}`,
  deleteReplacement: (sid: string, lid: string, name: string) =>
    request<{ ok: boolean }>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/replacements/file?name=${encodeURIComponent(name)}`, { method: 'DELETE' }),
  // Удалить фон у замены (rembg, локально) → новая замена nobg_*.png.
  removeBgReplacement: (sid: string, lid: string, name: string) =>
    request<{ name: string }>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/replacements/remove-bg`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }),

  // ── Нейро-правка картинки (GPT Image 2) ──────────────
  // refs — необязательные референсные фото (напр. «продукт со второго фото»).
  mediaEdit: (sid: string, lid: string, path: string, prompt: string, quality = 'low', refs: File[] = []) => {
    const fd = new FormData();
    fd.append('path', path);
    fd.append('prompt', prompt);
    fd.append('quality', quality);
    refs.forEach((f) => fd.append('refs', f));
    return postFormData<{ path: string; dimensions: string; size: number; model: string; quality: string; image_map_key?: string; replacement?: string }>(
      `/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/media/edit`, fd);
  },

  // ── Перевод ленда ────────────────────────────────────
  translateLanguages: () => request<{ code: string; name: string }[]>('/api/translate/languages'),
  translatePreview: (sid: string, lid: string, target_lang?: string) =>
    request<TranslateResult>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/translate/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_lang }),
    }),
  translateApply: (sid: string, lid: string, target_lang?: string) =>
    request<TranslateResult>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/translate/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_lang }),
    }),

  // ── История опубликованных лендов ────────────────────
  published: (period: 'day' | 'week' | 'month' | 'all' = 'day') =>
    request<PublishedHistory>(`/api/published?period=${period}`),
  addPublished: (id: number, date?: string) =>
    request<{ id: number; date: string }>('/api/published', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, date }),
    }),
  deletePublished: (id: number) =>
    request<{ ok: boolean }>(`/api/published/${id}`, { method: 'DELETE' }),

  // ── Заливка в Keitaro ────────────────────────────────
  keitaroPlan: (sid: string, lid: string, type?: string) =>
    request<KeitaroPlan>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/keitaro-plan${type ? `?type=${type}` : ''}`),
  keitaroUpload: (sid: string, lid: string, type?: string) =>
    request<KeitaroPlan>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/keitaro-upload`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type }),
    }),
  keitaroRename: (sid: string, lid: string, offer_id: number, type?: string) =>
    request<KeitaroRenameResult>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/keitaro-rename`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ offer_id, type: type || null }),
    }),

  // ── Чат-агент (AITUNNEL / Kimi) ──────────────────────
  aiStatus: () => request<AiStatus>('/api/ai/status'),
  chatHistory: (sid: string, lid: string) =>
    request<{ messages: ChatMessage[] }>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/chat`),
  chatSend: (sid: string, lid: string, message: string) =>
    request<{ new_messages: ChatMessage[]; lander: LanderState }>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    }),
  chatClear: (sid: string, lid: string) =>
    request<{ ok: boolean }>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/chat`, { method: 'DELETE' }),
  suggestParams: (sid: string, lid: string) =>
    request<SuggestParams>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/suggest`),
  setLanderGroup: (sid: string, lid: string, offer: string) =>
    request<SuggestParams>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/group`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ offer }),
    }),
  adaptLander: (sid: string, lid: string, params: Record<string, any>) =>
    request<AdaptResult>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/adapt`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    }),

  // ── История версий ленда (откат «на шаг назад») ──────
  landerHistory: (sid: string, lid: string) =>
    request<{ versions: LanderVersion[]; current: string | null }>(`/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/history`),
  restoreVersion: (sid: string, lid: string, versionId: string) =>
    request<AdaptResult & { restored: { id: string; label: string } }>(
      `/api/sessions/${encodeURIComponent(sid)}/landers/${encodeURIComponent(lid)}/history/${encodeURIComponent(versionId)}/restore`,
      { method: 'POST' }),

};
