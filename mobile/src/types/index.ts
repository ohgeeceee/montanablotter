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
  source_pdf_name?: string | null;
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

export interface StatsBucket {
  county?: string;
  incident_type?: string;
  count: number;
}

export interface StatsResponse {
  total_records: number;
  total_posts: number;
  total_blotters: number;
  total_counties: number;
  total_agencies: number;
  latest_blotter?: {
    county: string;
    upload_date: string;
  };
  date_range?: {
    earliest: string | null;
    latest: string | null;
  };
  top_counties: StatsBucket[];
  top_incident_types: StatsBucket[];
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

export interface JailBooking {
  id: number;
  person_name: string;
  age: number | null;
  booking_number: string | null;
  booking_at: string | null;
  booking_at_label: string;
  booking_status: string;
  booking_status_label: string;
  charges_summary: string;
  arresting_agency: string | null;
  county_name: string;
  county_slug: string;
  facility_name: string | null;
  is_current: number;
  is_new_24h: boolean;
  is_new_72h: boolean;
  source_url: string | null;
}

export interface JailBookingsResponse {
  bookings: JailBooking[];
  filters: {
    county: string | null;
    status: string;
    q: string | null;
  };
  summary: {
    tracked_counties: number;
    current_bookings: number;
    new_24h: number;
    featured_sources: number;
  };
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

export interface Warrant {
  id: number;
  source_record_id: string;
  county: string;
  city: string;
  person_name: string;
  dob: string;
  warrant_type: string;
  charges_text: string;
  issued_by: string;
  issue_date: string;
  bond_amount: string;
  bond_type: string;
  status: string;
  source_url: string;
  mugshot_url: string;
  photo_url: string;
  first_seen_at: string;
  updated_at: string;
}

export interface WarrantsResponse {
  warrants: Warrant[];
  filters: {
    county: string | null;
    q: string | null;
    status: string;
    warrant_type: string | null;
  };
  total: number;
}

export interface CourtCase {
  id: number;
  case_number: string;
  court_name: string;
  court_county: string;
  case_type: string;
  status: string;
  filed_date: string;
  charges_text: string;
  plea: string;
  disposition: string;
  sentence_text: string;
  sentence_date: string;
  sentencing_judge: string;
  original_court: string;
  original_case_number: string;
  outcome_scraped_at: string;
  source_url: string;
  related_jail_bookings?: {
    id: number;
    person_name: string;
    age: number | null;
    booking_number: string;
    booking_at: string;
    release_at: string;
    charges_summary: string;
    arresting_agency: string;
    county_name: string;
    facility_name: string;
    booking_status: string;
    is_current: number;
    source_url: string;
  }[];
}

export interface CourtPersonMatch {
  match_type: string;
  confidence: number;
  person: {
    name: string;
    name_slug: string;
    county: string;
    display_name: string;
  };
  court_cases: CourtCase[];
}

export interface CourtLookupResponse {
  query: {
    name: string | null;
    county: string | null;
    case_number: string | null;
  };
  match_count: number;
  matches: CourtPersonMatch[];
  data_as_of: string | null;
  warnings: string[];
}

export interface MissingPerson {
  id: number;
  full_name: string;
  slug: string;
  status: string;
  status_label: string;
  age: number | null;
  age_missing: number | null;
  gender: string;
  race: string;
  hair_color: string;
  eye_color: string;
  height_label: string;
  weight_lbs: number | null;
  height_weight: string;
  missing_from: string;
  last_seen_location: string;
  city: string;
  county: string;
  date_last_seen: string;
  last_seen_at_label: string;
  summary: string;
  case_number: string;
  investigating_agency: string;
  photo_url: string;
  photos: { url: string; label?: string }[];
  is_active: boolean;
  is_indigenous: number;
  is_child: number;
  public_href: string;
  created_at: string;
  updated_at: string;
}

export interface MissingPersonsResponse {
  people: MissingPerson[];
  filters: {
    status: string | null;
    county: string | null;
    q: string | null;
    sort: string;
  };
  total_active: number;
}
