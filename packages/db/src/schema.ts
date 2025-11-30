import {
  boolean,
  index,
  integer,
  numeric,
  pgEnum,
  pgTable,
  text,
  timestamp,
  uniqueIndex,
  uuid,
  varchar,
} from "drizzle-orm/pg-core";

import { TwitterUserType } from "./models";

export const twitterUserType = pgEnum(
  "twitter_user_type",
  Object.values(TwitterUserType) as [string, ...string[]]
);

export const userProfiles = pgTable(
  "user_profiles",
  {
    twitterId: varchar("twitter_id", { length: 25 }).primaryKey(),
    handle: varchar("handle", { length: 255 }).notNull(),
    name: varchar("name", { length: 255 }).notNull(),
    bio: text("bio"),
    createdAt: varchar("created_at", { length: 100 }).notNull(),
    followerCount: integer("follower_count"),
    location: varchar("location", { length: 255 }),
    updatedAt: timestamp("updated_at").defaultNow().notNull(),
    gotByKeywords: text("got_by_keywords").array().$type<string[]>(),
    canDm: boolean("can_dm"),
    category: varchar("category", { length: 255 }),
    humanScore: numeric("human_score"),
    likelyIs: twitterUserType("likely_is"),
  },
  (table) => [uniqueIndex("uq_handle").on(table.handle)]
);

export const profileScores = pgTable(
  "profile_scores",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    twitterId: varchar("twitter_id", { length: 25 })
      .notNull()
      .references(() => userProfiles.twitterId, {
        onDelete: "cascade",
        onUpdate: "cascade",
      }),
    label: boolean("label"), // true=good, false=bad, null=uncertain
    reason: text("reason"),
    scoredAt: timestamp("scored_at", {
      withTimezone: false,
      mode: "string",
    }).defaultNow(),
    scoredBy: varchar("scored_by", { length: 100 }).notNull(),
    audience: varchar("audience", { length: 100 }), // Audience config name (e.g., "thelai_customers.v1")
  },
  (table) => [
    uniqueIndex("uq_profile_model").on(table.twitterId, table.scoredBy),
    index("idx_scored_at").on(table.scoredAt),
    index("idx_twitter_id").on(table.twitterId),
    index("idx_scored_by").on(table.scoredBy),
    index("idx_audience").on(table.audience),
  ]
);

export const xapiSearchUsage = pgTable(
  "xapi_usage_search",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    idsHash: varchar("ids_hash", { length: 16 }).notNull(),
    keyword: varchar("keyword", { length: 255 }).notNull(),
    items: integer("items").default(20).notNull(),
    retries: integer("retries").default(1).notNull(),
    nextPage: text("next_page"),
    page: integer("page").default(0).notNull(),
    newProfiles: integer("new_profiles").notNull(),
    queryAt: timestamp("query_at", {
      withTimezone: false,
      mode: "string",
    }).defaultNow(),
  },
  (table) => [uniqueIndex("uq_xapi_usage_search").on(table.keyword, table.items, table.nextPage)]
);

export const profilesToScore = pgTable(
  "profiles_to_score",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    twitterId: varchar("twitter_id", { length: 25 })
      .unique()
      .references(() => userProfiles.twitterId, {
        onDelete: "cascade",
        onUpdate: "cascade",
      }),
    handle: varchar("handle", { length: 255 }).notNull(),
    addedAt: timestamp("added_at", { withTimezone: false, mode: "string" }).defaultNow().notNull(),
  },
  (table) => [index("idx_added_at").on(table.addedAt)]
);

export const userKeywords = pgTable(
  "user_keywords",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    twitterId: varchar("twitter_id", { length: 25 })
      .notNull()
      .references(() => userProfiles.twitterId, {
        onDelete: "cascade",
        onUpdate: "cascade",
      }),
    keyword: varchar("keyword", { length: 255 }).notNull(),
    searchId: uuid("search_id").references(() => xapiSearchUsage.id, {
      onDelete: "set null",
      onUpdate: "cascade",
    }),
    addedAt: timestamp("added_at", { withTimezone: false, mode: "string" }).defaultNow().notNull(),
  },
  (table) => [
    uniqueIndex("uq_user_keyword").on(table.twitterId, table.keyword),
    index("idx_user_keywords_twitter_id").on(table.twitterId),
    index("idx_user_keywords_keyword").on(table.keyword),
  ]
);

export const userStats = pgTable("user_stats", {
  twitterId: varchar("twitter_id", { length: 25 })
    .primaryKey()
    .references(() => userProfiles.twitterId, {
      onDelete: "cascade",
      onUpdate: "cascade",
    }),
  // Counts
  followers: integer("followers"),
  following: integer("following"),
  statuses: integer("statuses"),
  favorites: integer("favorites"),
  listed: integer("listed"),
  media: integer("media"),
  // Booleans
  verified: boolean("verified"),
  blueVerified: boolean("blue_verified"),
  defaultProfile: boolean("default_profile"),
  defaultImage: boolean("default_image"),
  sensitive: boolean("sensitive"),
  canDm: boolean("can_dm"),
  // Metadata
  updatedAt: timestamp("updated_at", { withTimezone: false, mode: "string" })
    .defaultNow()
    .notNull(),
});

/**
 * Keyword statistics for prioritizing keyword selection.
 * Updated daily by keyword-stats-updater lambda.
 */
export const keywordStats = pgTable("keyword_stats", {
  keyword: varchar("keyword", { length: 255 }).primaryKey(),
  // Semantic categorization
  semanticTags: text("semantic_tags").array().$type<string[]>().default([]),
  // Profile counts
  profilesFound: integer("profiles_found").default(0).notNull(),
  // Quality metrics
  avgHumanScore: numeric("avg_human_score", { precision: 4, scale: 3 }).default("0"),
  labelRate: numeric("label_rate", { precision: 4, scale: 3 }).default("0"), // Percentage of true labels
  // Pagination state
  stillValid: boolean("still_valid").default(true).notNull(),
  pagesSearched: integer("pages_searched").default(0).notNull(),
  // Quality metrics
  highQualityCount: integer("high_quality_count").default(0).notNull(), // HAS > 0.7
  lowQualityCount: integer("low_quality_count").default(0).notNull(), // HAS < 0.4
  // Timestamps
  firstSearchAt: timestamp("first_search_at", { withTimezone: false, mode: "string" }),
  lastSearchAt: timestamp("last_search_at", { withTimezone: false, mode: "string" }),
  updatedAt: timestamp("updated_at", { withTimezone: false, mode: "string" })
    .defaultNow()
    .notNull(),
});
