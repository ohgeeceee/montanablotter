import { fetchAPI } from './api';
import { Post } from '../types';

export interface AlertProfile {
  id: number;
  name: string;
  alert_types: string[];
  counties: string[] | null;
  cities: string[] | null;
  neighborhoods: string[] | null;
  radius_miles: number | null;
  center_lat: number | null;
  center_lng: number | null;
  severity_threshold: string;
  frequency: string;
  delivery_channel: string;
  webhook_url: string | null;
  slack_channel: string | null;
  teams_webhook: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

async function meFetch<T>(
  endpoint: string,
  options: RequestInit = {},
  params: Record<string, string | number> = {},
): Promise<T> {
  return fetchAPI<T>(endpoint, params, options);
}

export const watchlistApi = {
  get: async (): Promise<{ posts: Post[] }> => {
    return meFetch<{ posts: Post[] }>('/api/me/watchlist');
  },
  add: async (postId: number): Promise<{ message: string }> => {
    return meFetch<{ message: string }>('/api/me/watchlist', {
      method: 'POST',
      body: JSON.stringify({ post_id: postId }),
    });
  },
  remove: async (postId: number): Promise<{ message: string }> => {
    return meFetch<{ message: string }>(`/api/me/watchlist/${postId}`, {
      method: 'DELETE',
    });
  },
};

export const alertProfilesApi = {
  list: async (): Promise<{ profiles: AlertProfile[] }> => {
    return meFetch<{ profiles: AlertProfile[] }>('/api/me/alert-profiles');
  },
  create: async (profile: Partial<AlertProfile>): Promise<{ id: number; message: string }> => {
    return meFetch<{ id: number; message: string }>('/api/me/alert-profiles', {
      method: 'POST',
      body: JSON.stringify(profile),
    });
  },
  update: async (
    profileId: number,
    profile: Partial<AlertProfile>,
  ): Promise<{ message: string }> => {
    return meFetch<{ message: string }>(`/api/me/alert-profiles/${profileId}`, {
      method: 'PUT',
      body: JSON.stringify(profile),
    });
  },
  remove: async (profileId: number): Promise<{ message: string }> => {
    return meFetch<{ message: string }>(`/api/me/alert-profiles/${profileId}`, {
      method: 'DELETE',
    });
  },
};
