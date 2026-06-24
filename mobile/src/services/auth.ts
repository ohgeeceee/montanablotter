import * as SecureStore from 'expo-secure-store';
import { API_BASE_URL } from '../constants';

const TOKEN_KEY = 'mb_auth_token';

export interface PublicUser {
  id: number;
  email: string;
  display_name: string;
  subscription_counties: string;
  subscribe_digest: boolean;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface AuthResponse {
  user: PublicUser;
  token: string;
}

async function authFetch<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`;
  const token = await getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let message = `Auth API Error: ${response.status}`;
    try {
      const body = await response.json();
      message = body.error || body.message || message;
    } catch {
      // ignore
    }
    throw new Error(message);
  }

  if (response.status === 204) {
    return undefined as unknown as T;
  }
  return response.json();
}

export async function setToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(TOKEN_KEY, token);
}

export async function getToken(): Promise<string | null> {
  return SecureStore.getItemAsync(TOKEN_KEY);
}

export async function removeToken(): Promise<void> {
  await SecureStore.deleteItemAsync(TOKEN_KEY);
}

export async function register(
  displayName: string,
  email: string,
  password: string,
): Promise<AuthResponse> {
  return authFetch<AuthResponse>('/api/v1/auth/register', {
    method: 'POST',
    body: JSON.stringify({ display_name: displayName, email, password }),
  });
}

export async function login(email: string, password: string): Promise<AuthResponse> {
  return authFetch<AuthResponse>('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
}

export async function logout(): Promise<void> {
  await authFetch<void>('/api/v1/auth/logout', { method: 'POST' });
  await removeToken();
}

export async function fetchMe(): Promise<PublicUser> {
  const { user } = await authFetch<{ user: PublicUser }>('/api/v1/auth/me');
  return user;
}
