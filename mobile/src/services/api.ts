import { API_BASE_URL } from '../constants';
import { captureApiFailure } from './monitoring';
import { getToken } from './auth';
import { getCached, setCached } from './cache';
import {
  Post,
  PostsResponse,
  County,
  Agency,
  BlogPost,
  StatsResponse,
  JailBookingsResponse,
  WarrantsResponse,
  CourtLookupResponse,
  MissingPersonsResponse,
} from '../types';

interface FetchAPIOptions extends RequestInit {
  localCache?: {
    ttlMs?: number;
  };
}

export async function fetchAPI<T>(
  endpoint: string,
  params: Record<string, string | number | boolean> = {},
  options: FetchAPIOptions = {},
): Promise<T> {
  const url = new URL(`${API_BASE_URL}${endpoint}`);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.append(key, String(value));
    }
  });

  const isReadRequest = (options.method || 'GET').toUpperCase() === 'GET';
  const cacheOptions = isReadRequest ? options.localCache : undefined;

  if (cacheOptions) {
    const cached = await getCached<T>(endpoint, params, cacheOptions.ttlMs);
    if (cached !== null) {
      return cached;
    }
  }

  const token = await getToken();
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (options.body && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }

  let response: Response;

  try {
    response = await fetch(url.toString(), {
      ...options,
      headers,
    });
  } catch (error) {
    captureApiFailure(error, {
      endpoint,
      method: options.method || 'GET',
      queryKeys: Object.keys(params).filter(
        (key) => params[key] !== undefined && params[key] !== null && params[key] !== '',
      ),
    });
    throw error;
  }

  if (!response.ok) {
    const error = new Error(`API Error: ${response.status}`);

    if (response.status >= 500) {
      captureApiFailure(error, {
        endpoint,
        method: options.method || 'GET',
        status: response.status,
        queryKeys: Object.keys(params).filter(
          (key) => params[key] !== undefined && params[key] !== null && params[key] !== '',
        ),
      });
    }

    throw error;
  }

  if (response.status === 204) {
    return undefined as unknown as T;
  }

  const data = (await response.json()) as T;
  if (cacheOptions) {
    setCached(endpoint, data, params).catch(() => {});
  }
  return data;
}

export const api = {
  getPosts: async (params: {
    page?: number;
    per_page?: number;
    county?: string;
    agency_type?: string;
    date_from?: string;
    date_to?: string;
    search?: string;
  } = {}): Promise<PostsResponse> => {
    return fetchAPI<PostsResponse>('/api/posts', params, { localCache: { ttlMs: 60_000 } });
  },

  getPost: async (id: number): Promise<Post> => {
    return fetchAPI<Post>(`/api/posts/${id}`, {}, { localCache: { ttlMs: 300_000 } });
  },

  getCounties: async (): Promise<{ counties: County[] }> => {
    return fetchAPI<{ counties: County[] }>('/api/counties', {}, { localCache: { ttlMs: 300_000 } });
  },

  getAgencies: async (): Promise<{ agencies: Agency[] }> => {
    return fetchAPI<{ agencies: Agency[] }>('/api/agencies', {}, { localCache: { ttlMs: 300_000 } });
  },

  getStats: async (): Promise<StatsResponse> => {
    return fetchAPI<StatsResponse>('/api/stats', {}, { localCache: { ttlMs: 300_000 } });
  },
};

export const blogApi = {
  getPosts: async (
    page: number = 1,
  ): Promise<{ posts: BlogPost[]; total: number; page: number; total_pages: number }> => {
    return fetchAPI<{ posts: BlogPost[]; total: number; page: number; total_pages: number }>(
      '/api/blog',
      { page },
      { localCache: { ttlMs: 300_000 } },
    );
  },

  getPost: async (slug: string): Promise<BlogPost> => {
    return fetchAPI<BlogPost>(`/api/blog/${slug}`, {}, { localCache: { ttlMs: 300_000 } });
  },
};

export interface MapIncident {
  id: number;
  date: string;
  time: string;
  incident: string;
  incident_type: string;
  location: string;
  county: string;
  lat: number;
  lng: number;
  neighborhood: string | null;
}

export const mapApi = {
  getIncidents: async (bounds: {
    sw_lat: number;
    sw_lng: number;
    ne_lat: number;
    ne_lng: number;
  }): Promise<{ incidents: MapIncident[] }> => {
    const boundsStr = `${bounds.sw_lat},${bounds.sw_lng},${bounds.ne_lat},${bounds.ne_lng}`;
    return fetchAPI<{ incidents: MapIncident[] }>(
      '/api/geo/incidents',
      {
        bounds: boundsStr,
        limit: 500,
      },
      { localCache: { ttlMs: 120_000 } },
    );
  },
};

export const jailApi = {
  getBookings: async (params: {
    county?: string;
    status?: 'current' | 'recent' | 'released' | 'all';
    q?: string;
  } = {}): Promise<JailBookingsResponse> => {
    return fetchAPI<JailBookingsResponse>('/api/jail-bookings', params, { localCache: { ttlMs: 120_000 } });
  },
};

export const warrantApi = {
  getWarrants: async (params: {
    county?: string;
    q?: string;
    status?: string;
    warrant_type?: string;
    limit?: number;
  } = {}): Promise<WarrantsResponse> => {
    return fetchAPI<WarrantsResponse>('/api/v1/warrants', params, { localCache: { ttlMs: 120_000 } });
  },
};

export const courtApi = {
  lookup: async (params: {
    name?: string;
    county?: string;
    case_number?: string;
    include_bookings?: boolean;
    limit?: number;
  } = {}): Promise<CourtLookupResponse> => {
    return fetchAPI<CourtLookupResponse>('/api/v1/court/lookup', params, { localCache: { ttlMs: 300_000 } });
  },
};

export const missingPersonsApi = {
  getPeople: async (params: {
    status?: 'missing' | 'located' | '';
    county?: string;
    q?: string;
    sort?: string;
    limit?: number;
  } = {}): Promise<MissingPersonsResponse> => {
    return fetchAPI<MissingPersonsResponse>('/api/v1/missing-persons', params, { localCache: { ttlMs: 120_000 } });
  },
};
