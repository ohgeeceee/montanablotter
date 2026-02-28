export interface Post {
  id: number;
  title: string;
  summary: string;
  county: string;
  agency_name: string;
  agency_type: string;
  incident_date: string;
  incident_type: string;
  created_at: string;
}

export interface PostsResponse {
  posts: Post[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface County {
  county: string;
  post_count: number;
  record_count: number;
}

export interface Agency {
  agency_name: string;
  agency_type: string;
  county: string;
  post_count: number;
}

export interface BlogPost {
  id: number;
  title: string;
  slug: string;
  excerpt: string;
  body: string;
  author: string;
  created_at: string;
}

export interface JailRoster {
  name: string;
  url: string | null;
  phone: string;
  hasOnline: boolean;
}

export interface LawSection {
  id: string;
  title: string;
  description: string;
}

export interface LawCategory {
  id: string;
  title: string;
  subtitle: string;
  icon: string;
  color: string;
  laws: LawSection[];
}
