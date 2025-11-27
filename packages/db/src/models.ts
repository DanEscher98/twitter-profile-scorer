export interface TwitterXapiUser {
  __typename: string;
  id: string;
  rest_id: string;
  affiliates_highlighted_label: object;
  has_graduated_access: boolean;
  is_blue_verified: boolean;
  legacy: {
    following: boolean;
    can_dm: boolean;
    can_media_tag: boolean;
    created_at: string;
    default_profile: boolean;
    default_profile_image: boolean;
    description: string;
    entities: {
      description: {
        urls: Array<string>;
      };
    };
    fast_followers_count: number;
    favourites_count: number;
    followers_count: number;
    friends_count: number;
    has_custom_timelines: boolean;
    is_translator: boolean;
    listed_count: number;
    location: string;
    media_count: number;
    name: string;
    normal_followers_count: number;
    pinned_tweet_ids_str: string[];
    possibly_sensitive: boolean;
    profile_image_url_https: string;
    profile_interstitial_type: string;
    screen_name: string;
    statuses_count: number;
    translator_type: string;
    verified: boolean;
    want_retweets: boolean;
    withheld_in_countries: string[];
  };
  professional: {
    rest_id: string,
    professional_type: string,
    category: {
      id: number,
      name: string,
      icon_name: string
    }[]
  } | null,
  parody_commentary_fan_label: string;
  profile_image_shape: string;
  tipjar_settings: object;
}

export interface TwitterProfile {
  twitter_id: string;
  username: string;
  display_name: string | null;
  bio: string | null;
  created_at: string;
  follower_count: number | null;
  can_dm: boolean;
  location: string | null;
  category: string | null;
  human_score: number;
  likely_is: TwitterUserType;
}

export interface ParamsScoreProfile {
  username: string;
  display_name: string | null;
  bio: string | null;
  likely_is: string; //TwitterUserType
  category: string | null;
}

export enum TwitterUserType {
  Human = "Human",
  Creator = "Creator",
  Entity = "Entity",
  Other = "Other",
  Bot = "Bot"
}

export interface ScoredUser {
  username: string;
  score: number;
  reason: string;
}

export interface UserScore {
  score: number;
  likely_is: TwitterUserType;
}

export interface TwitterXapiMetadata {
  id: string;
  ids_hash: string | null;
  keyword: string;
  items: number;
  retries: number;
  next_page: string | null;
  page: number;
}
