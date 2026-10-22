// לקוח ה-API — מקביל לנתיבים ב-backend/app/api. כל המבנים מוגדרים ב-backend/app/contracts.py

// אסימון ההתחברות נשמר בדפדפן כדי לשחזר את החשבון עד שפג תוקף ה-session בשרת.
const CODE_KEY = "access_code";
const TOKEN_KEY = "employee_access_token";
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

export function getSessionToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setSessionToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* Authentication cannot be persisted in private browsing mode. */
  }
}

export function getAccessCode() {
  try {
    return sessionStorage.getItem(CODE_KEY);
  } catch {
    return null;
  }
}

export function setAccessCode(code) {
  try {
    if (code) sessionStorage.setItem(CODE_KEY, code);
    else sessionStorage.removeItem(CODE_KEY);
  } catch {
    /* מצב פרטי — יבקשו את הקוד שוב */
  }
}

async function request(path, options = {}) {
  const code = getAccessCode();
  const token = getSessionToken();
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  const res = await fetch(`${API_BASE_URL}/api${path}`, {
    ...options,
    headers: {
      ...(!isFormData ? { "Content-Type": "application/json" } : {}),
      ...(code ? { "X-Access-Code": code } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
    body: options.body ? (isFormData ? options.body : JSON.stringify(options.body)) : undefined,
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail;
    // detail יכול להיות מחרוזת, או {message, problems} בשגיאת ולידציה של הכללים
    const err = new Error(typeof detail === "string" ? detail : detail?.message ?? `שגיאה ${res.status}`);
    err.problems = detail?.problems ?? [];
    err.status = res.status;
    throw err;
  }
  // תגובה ריקה או לא-JSON פירושה שהבקשה לא הגיעה לשרת ה-API (למשל VITE_API_BASE_URL לא הוגדר בבנייה)
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    throw new Error("לא התקבלה תשובה תקינה משרת ה-API. ודאו שכתובת השרת (VITE_API_BASE_URL) מוגדרת.");
  }
}

export const api = {
  createRepInvitation: (email, fullName, mobileNumber) =>
    request("/auth/invitations", {
      method: "POST",
      body: { email, full_name: fullName, mobile_number: mobileNumber || null },
    }),
  registerEmployee: (email, password, fullName, mobileNumber) =>
    request("/auth/employees/register", {
      method: "POST",
      body: { email, password, full_name: fullName, mobile_number: mobileNumber || null },
    }),
  registerRep: (email, code, password) =>
    request("/auth/representatives/register", { method: "POST", body: { email, code, password } }),
  insights: (minCount) => request(`/admin/insights?min_count=${minCount}`),
  insightReports: () => request("/admin/insights/reports"),
  insightReport: (month, minCount) => request(`/admin/insights/reports/${month}?min_count=${minCount}`),
  login: (email, password, role) => request("/auth/login", { method: "POST", body: { email, password, role } }),
  currentUser: () => request("/auth/me"),
  logout: () => request("/auth/logout", { method: "POST" }),
  catalog: () => request("/catalog"),
  occupations: () => request("/occupations"),
  demoEmployees: () => request("/demo-employees"),
  compare: (profile, alternatives) => request("/compare", { method: "POST", body: { profile, alternatives } }),
  compareAll: (profile) => request("/compare/all", { method: "POST", body: { profile } }),
  demoCompare: (employeeId) => request(`/demo/compare/${employeeId}`),
  chat: (conversationId, message) =>
    request("/chat", { method: "POST", body: { conversation_id: conversationId, message } }),
  conversation: (id) => request(`/conversations/${id}`),
  explore: (id) => request(`/conversations/${id}/explore`, { method: "POST" }),
  correctProfile: (id, fields, currentParticipation) =>
    request(`/conversations/${id}/profile`, {
      method: "PATCH",
      body: { fields, current_participation: currentParticipation ?? null },
    }),
  extractPayslip: (file, conversationId, consent) => {
    const body = new FormData();
    body.append("file", file);
    body.append("consent", String(consent));
    if (conversationId) body.append("conversation_id", conversationId);
    return request("/payslip/extract", { method: "POST", body });
  },
  confirmPayslip: (conversationId, fields) =>
    request(`/conversations/${conversationId}/payslip/confirm`, { method: "POST", body: { fields } }),
  loadDemo: (conversationId, employeeId) =>
    request(`/conversations/${conversationId ?? "new"}/demo/${employeeId}`, { method: "POST" }),
  checkAccess: (role, code) => request(`/auth/check/${role}`, { headers: { "X-Access-Code": code } }),
  repQuestions: (status) => request(`/rep/questions${status ? `?status=${status}` : ""}`),
  answerQuestion: (id, answer) => request(`/rep/questions/${id}/answer`, { method: "POST", body: answer }),
  searchVehicles: (q, year) =>
    request(`/vehicles/search?q=${encodeURIComponent(q)}${year ? `&year=${year}` : ""}`),
  rules: () => request("/rules"),
  previewRules: (rules) => request("/rules/preview", { method: "POST", body: { rules } }),
  saveRules: (rules) => request("/rules", { method: "PUT", body: { rules } }),
  undoRules: () => request("/rules/undo", { method: "POST" }),
};
