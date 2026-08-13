-- ═══════════════════════════════════════════════════════════════════════════
--  GGselV7 Parser — схема хранения спаршенных товаров
--  Персистентная между запусками панели. Парсер только дописывает/обновляет.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS parsed_products (
    -- Внешний идентификатор товара на ggsel.net (из URL /catalog/.../ID)
    product_id          TEXT PRIMARY KEY,

    -- Оригинальные спаршенные данные
    title               TEXT NOT NULL,
    original_title      TEXT,
    original_desc       TEXT,
    image_url           TEXT,
    price               REAL,
    currency            TEXT DEFAULT 'RUB',
    category            TEXT,
    category_id         INTEGER,
    url                 TEXT,
    seller_name         TEXT,
    seller_id           TEXT,
    seller_rating       REAL,
    rating              REAL,
    sales_count         INTEGER DEFAULT 0,
    reviews_count       INTEGER DEFAULT 0,
    in_stock            INTEGER DEFAULT 1,
    delivery_type       TEXT DEFAULT 'unknown',

    -- Данные магазина продавца
    shop_name               TEXT,
    shop_rating             REAL,
    shop_products_count     INTEGER,
    shop_registered_at      TEXT,
    shop_positive_reviews   INTEGER,
    shop_negative_reviews   INTEGER,
    shop_url                TEXT,

    -- Детали страницы товара (из _parse_product_detail)
    images_json            TEXT,
    properties_json        TEXT,
    quantity_available     INTEGER,
    seller_url             TEXT,
    published_at           TEXT,

    -- AI-обогащённая карточка (через Gemini)
    generated_title     TEXT,
    generated_desc      TEXT,
    generated_tags      TEXT,
    generated_image_url TEXT,
    ai_score            REAL,
    ai_reason           TEXT,
    ai_moderated_at     TEXT,
    ai_error            TEXT DEFAULT '',

    -- Экономика (ШАГ 4, детерминированная формула)
    source_price          REAL,
    sell_price            REAL,
    my_price              REAL,
    ggsel_fee_pct         REAL,
    ggsel_fee_source      TEXT,
    payment_fee_pct       REAL,
    withdrawal_fee_pct    REAL,
    tax_pct               REAL,
    fixed_costs_rub       REAL,
    risk_reserve_pct      REAL,
    total_costs_rub       REAL,
    expected_profit_rub   REAL,
    expected_net_margin_pct REAL,
    calculated_at         TEXT,
    economy_complete      INTEGER DEFAULT 0,
    target_margin_pct     REAL DEFAULT 0.15,
    min_net_profit_rub    REAL DEFAULT 50.0,

    profit_score        REAL,
    -- Жизненный цикл: parsed → economics_checked → ai_recommended → approved_by_owner → draft_created → published → sold → sourced → delivered → closed
    status              TEXT DEFAULT 'parsed',
    status_reason       TEXT DEFAULT '',
    offer_id            TEXT,
    is_top              INTEGER DEFAULT 0,
    tags                TEXT DEFAULT '',
    approval_status     TEXT DEFAULT 'pending',

    -- История
    last_parsed_at      TEXT,
    last_enriched_at    TEXT,
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

-- Таблица order_links
CREATE TABLE IF NOT EXISTS order_links (
    order_id          TEXT PRIMARY KEY,
    my_offer_id       TEXT,
    source_offer_id   TEXT,
    source_seller_id  TEXT,
    source_price      REAL,
    my_price          REAL,
    profit_rub        REAL,
    status            TEXT DEFAULT 'new',
    created_at        TEXT DEFAULT (datetime('now')),
    updated_at        TEXT DEFAULT (datetime('now'))
);

-- Индекс для частых выборок: последние N по дате
CREATE INDEX IF NOT EXISTS idx_parsed_products_updated
    ON parsed_products(updated_at DESC);

-- Индекс по статусу
CREATE INDEX IF NOT EXISTS idx_parsed_products_status
    ON parsed_products(status);

-- Справочник категорий GGSEL V2 + комиссии
CREATE TABLE IF NOT EXISTS categories (
    id           INTEGER PRIMARY KEY,
    title        TEXT NOT NULL,
    parent_id    INTEGER,
    depth        INTEGER DEFAULT 0,
    full_path    TEXT,
    content_type TEXT,
    fee          REAL,
    has_children INTEGER DEFAULT 0,
    updated_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_categories_parent_id
    ON categories(parent_id);

-- Отклонённые товары — не парсить повторно в течение cooldown (7 дней)
CREATE TABLE IF NOT EXISTS rejected_products (
    product_id   TEXT PRIMARY KEY,
    rejected_at  TEXT NOT NULL,
    reject_code     TEXT,
    reject_reason   TEXT,
    source_price    REAL,
    snapshot_json   TEXT,
    seller_name     TEXT,
    category_id     INTEGER
);

-- История запусков парсера (audit log)
CREATE TABLE IF NOT EXISTS parser_runs (
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT DEFAULT 'running',  -- running | done | stopped | error | crashed
    query         TEXT,
    category      TEXT,
    quantity      INTEGER,
    max_pages     INTEGER,
    products_found INTEGER DEFAULT 0,
    products_saved INTEGER DEFAULT 0,
    products_ai_enriched INTEGER DEFAULT 0,
    errors        TEXT DEFAULT '' 
);

-- Лог событий текущего/последнего запуска
CREATE TABLE IF NOT EXISTS parser_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER,
    level       TEXT DEFAULT 'info',
    message     TEXT,
    ts          TEXT DEFAULT (datetime('now'))
);

-- Реальная очередь задач (фоновые процессы, авто-отправка и т.д.)
CREATE TABLE IF NOT EXISTS task_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    status      TEXT DEFAULT 'running', -- running | done | error
    created_at  TEXT DEFAULT (datetime('now')),
    logs        TEXT DEFAULT ''
);

-- ═══════════════════════════════════════════════════════════════════════════
--  Сделки перепродажи (ШАГ 11) — ручной жизненный цикл
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS resale_deals (
    deal_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_order_id   TEXT,
    offer_id         TEXT,
    product_id       TEXT,
    product_url      TEXT,
    seller_name      TEXT,
    source_price_at_decision REAL,
    sell_price       REAL,
    expected_profit_rub REAL,
    actual_profit_rub   REAL,
    actual_net_margin_pct REAL,
    buyer_chat_ref   TEXT,
    seller_chat_ref  TEXT,
    status           TEXT DEFAULT 'new',
    changelog_json   TEXT,
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_resale_deals_status ON resale_deals(status);
CREATE INDEX IF NOT EXISTS idx_resale_deals_updated ON resale_deals(updated_at DESC);

-- ═══════════════════════════════════════════════════════════════════════════
--  Единый журнал событий
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS event_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    time              TEXT NOT NULL,
    entity            TEXT NOT NULL,
    entity_id         TEXT NOT NULL,
    stage             TEXT NOT NULL,
    level             TEXT DEFAULT 'info',
    reason_code       TEXT,
    message           TEXT,
    technical_detail  TEXT,
    action            TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_log_entity ON event_log(entity, entity_id);
CREATE INDEX IF NOT EXISTS idx_event_log_time ON event_log(time DESC);
CREATE INDEX IF NOT EXISTS idx_event_log_stage ON event_log(stage);
CREATE INDEX IF NOT EXISTS idx_event_log_level ON event_log(level);
