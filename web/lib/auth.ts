// Talks to the API's /auth routes. The bearer token lives in localStorage, so these only run in the browser.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const TOKEN_KEY = "basket.token";
// The thesis chat (see ChatContext). Cleared with every token change so it never outlives the session.
export const CHAT_KEY = "basket.chat";

export interface User {
  id: number;
  email: string;
  created_at: string;
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function setToken(token: string | null) {
  try {
    localStorage.removeItem(CHAT_KEY);
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {}
}

// FastAPI sends `detail` as a string, or as a list of pydantic errors on a 422.
export async function errorMessage(res: Response): Promise<string> {
  const body = await res.json().catch(() => null);
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg).replace(/^Value error, /, "");
  return `Request failed (${res.status})`;
}

async function authenticate(path: "login" | "signup", email: string, password: string): Promise<User> {
  const res = await fetch(`${API_URL}/auth/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error(await errorMessage(res));
  const { token, user } = await res.json();
  setToken(token);
  return user;
}

export const logIn = (email: string, password: string) => authenticate("login", email, password);
export const register = (email: string, password: string) => authenticate("signup", email, password);

export async function logOut() {
  const token = getToken();
  setToken(null);
  if (token) {
    await fetch(`${API_URL}/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
  }
}

// fetch() against the API with the bearer token attached. A 401 clears the stored token.
export async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (res.status === 401) setToken(null);
  return res;
}

export class UnauthorizedError extends Error {}

// authFetch that parses the JSON body, throwing the API's error message on failure.
export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await authFetch(path, init);
  if (res.status === 401) throw new UnauthorizedError("Not signed in");
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.status === 204 ? (undefined as T) : res.json();
}

// The signed-in user, or null if there's no token or the API rejects it.
export async function getMe(): Promise<User | null> {
  const token = getToken();
  if (!token) return null;
  const res = await fetch(`${API_URL}/auth/me`, { headers: { Authorization: `Bearer ${token}` } }).catch(() => null);
  if (res?.status === 401) setToken(null);
  return res?.ok ? res.json() : null;
}
