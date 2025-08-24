-- YouTube数据分析项目数据库表结构
-- 数据库: show_ytb_data

-- 用户表
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(80) NOT NULL UNIQUE,
    email VARCHAR(120) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(100),
    avatar_url VARCHAR(500),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    timezone VARCHAR(50) NOT NULL DEFAULT 'UTC',
    language VARCHAR(10) NOT NULL DEFAULT 'zh-CN',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMP WITHOUT TIME ZONE
);

-- 频道表
CREATE TABLE channels (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    channel_id VARCHAR(100) NOT NULL,
    url VARCHAR(255) NOT NULL,
    cpm DECIMAL(5,2) NOT NULL DEFAULT 1.00,
    category VARCHAR(50),
    sort INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_user_channel UNIQUE (user_id, channel_id)
);

-- 频道数据表
CREATE TABLE channel_data (
    id SERIAL PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    date DATE NOT NULL DEFAULT CURRENT_DATE,
    subscribers INTEGER NOT NULL DEFAULT 0,
    total_views BIGINT NOT NULL DEFAULT 0,
    video_count INTEGER NOT NULL DEFAULT 0,
    subscriber_change INTEGER NOT NULL DEFAULT 0,
    view_change INTEGER NOT NULL DEFAULT 0,
    video_change INTEGER NOT NULL DEFAULT 0,
    estimated_revenue DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_channel_date UNIQUE (channel_id, date)
);

-- 视频表
CREATE TABLE videos (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    video_id VARCHAR(100) NOT NULL,
    channel_id INTEGER NOT NULL REFERENCES channels(id),
    title VARCHAR(500) NOT NULL,
    description TEXT,
    published_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    duration VARCHAR(20),
    view_count BIGINT NOT NULL DEFAULT 0,
    like_count BIGINT NOT NULL DEFAULT 0,
    comment_count BIGINT NOT NULL DEFAULT 0,
    tags TEXT,
    thumbnail_url VARCHAR(500),
    category_id VARCHAR(10),
    category_name VARCHAR(100),
    language VARCHAR(10),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_user_video UNIQUE (user_id, video_id)
);

-- 视频数据表
CREATE TABLE video_data (
    id SERIAL PRIMARY KEY,
    video_id INTEGER NOT NULL REFERENCES videos(id),
    date DATE NOT NULL,
    view_count BIGINT NOT NULL DEFAULT 0,
    like_count BIGINT NOT NULL DEFAULT 0,
    comment_count BIGINT NOT NULL DEFAULT 0,
    view_change BIGINT NOT NULL DEFAULT 0,
    like_change BIGINT NOT NULL DEFAULT 0,
    comment_change BIGINT NOT NULL DEFAULT 0,
    engagement_rate DECIMAL(5,2) NOT NULL DEFAULT 0.00,
    estimated_revenue DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

-- 收藏视频表
CREATE TABLE favorite_videos (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    video_id VARCHAR(20) NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    thumbnail_url VARCHAR(500),
    duration VARCHAR(20),
    channel_id VARCHAR(30) NOT NULL,
    channel_title VARCHAR(200) NOT NULL,
    channel_thumbnail_url VARCHAR(500),
    channel_published_at TIMESTAMP WITHOUT TIME ZONE,
    view_count BIGINT NOT NULL DEFAULT 0,
    like_count BIGINT NOT NULL DEFAULT 0,
    comment_count BIGINT NOT NULL DEFAULT 0,
    channel_subscriber_count BIGINT NOT NULL DEFAULT 0,
    channel_video_count BIGINT NOT NULL DEFAULT 0,
    channel_view_count BIGINT NOT NULL DEFAULT 0,
    published_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    favorited_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    hot_score REAL NOT NULL DEFAULT 0.0,
    note TEXT,
    tags TEXT,
    CONSTRAINT unique_user_video_fav UNIQUE (user_id, video_id)
);

-- 创建索引
CREATE INDEX idx_channel_user_id ON channels(user_id);
CREATE INDEX idx_channel_data_date ON channel_data(date);
CREATE INDEX idx_channel_data_channel_id ON channel_data(channel_id);
CREATE INDEX idx_video_user_id ON videos(user_id);
CREATE INDEX idx_video_channel_id ON videos(channel_id);
CREATE INDEX idx_video_date ON video_data(video_id, date);
CREATE INDEX idx_user_favorited ON favorite_videos(user_id, favorited_at);
CREATE INDEX idx_video_id ON favorite_videos(video_id);