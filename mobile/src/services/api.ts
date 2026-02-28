import { API_BASE_URL } from '../constants';
import { Post, PostsResponse, County, Agency, BlogPost } from '../types';

async function fetchAPI<T>(endpoint: string, params: Record<string, string | number> = {}): Promise<T> {
  const url = new URL(`${API_BASE_URL}${endpoint}`);
  Object.entries(params).forEach(([key, value]) => {
    if (value) url.searchParams.append(key, String(value));
  });

  const response = await fetch(url.toString());
  if (!response.ok) {
    throw new Error(`API Error: ${response.status}`);
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

  getStats: async () => {
    return fetchAPI('/api/stats');
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
