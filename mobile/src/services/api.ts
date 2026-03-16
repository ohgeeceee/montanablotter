import { API_BASE_URL } from '../constants';
import { Post, PostsResponse, County, Agency, BlogPost, StatsResponse } from '../types';
import { captureApiFailure } from './monitoring';

async function fetchAPI<T>(endpoint: string, params: Record<string, string | number> = {}): Promise<T> {
  const url = new URL(`${API_BASE_URL}${endpoint}`);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.append(key, String(value));
    }
  });

  let response: Response;

  try {
    response = await fetch(url.toString());
  } catch (error) {
    captureApiFailure(error, {
      endpoint,
      method: 'GET',
      queryKeys: Object.keys(params).filter((key) => params[key] !== undefined && params[key] !== null && params[key] !== ''),
    });
    throw error;
  }

  if (!response.ok) {
    const error = new Error(`API Error: ${response.status}`);

    if (response.status >= 500) {
      captureApiFailure(error, {
        endpoint,
        method: 'GET',
        status: response.status,
        queryKeys: Object.keys(params).filter((key) => params[key] !== undefined && params[key] !== null && params[key] !== ''),
      });
    }

    throw error;
  }
  return response.json();
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
    return fetchAPI<PostsResponse>('/api/posts', params);
  },

  getPost: async (id: number): Promise<Post> => {
    return fetchAPI<Post>(`/api/posts/${id}`);
  },

  getCounties: async (): Promise<{ counties: County[] }> => {
    return fetchAPI<{ counties: County[] }>('/api/counties');
  },

  getAgencies: async (): Promise<{ agencies: Agency[] }> => {
    return fetchAPI<{ agencies: Agency[] }>('/api/agencies');
  },

  getStats: async (): Promise<StatsResponse> => {
    return fetchAPI<StatsResponse>('/api/stats');
  },
};

export const blogApi = {
  getPosts: async (page: number = 1): Promise<{ posts: BlogPost[]; total: number; page: number; total_pages: number }> => {
    return fetchAPI<{ posts: BlogPost[]; total: number; page: number; total_pages: number }>('/api/blog', { page });
  },

  getPost: async (slug: string): Promise<BlogPost> => {
    return fetchAPI<BlogPost>(`/api/blog/${slug}`);
  },
};
