CREATE TABLE sell_signals (
  id                    SERIAL PRIMARY KEY,
  buy_recommendation_id INTEGER NOT NULL UNIQUE REFERENCES recommendations(id),
  stock_id              INTEGER NOT NULL REFERENCES stocks(id),
  current_score         DECIMAL(8,4) NOT NULL,
  entry_price           DECIMAL(15,4) NOT NULL,
  exit_price            DECIMAL(15,4),
  reasons               JSONB NOT NULL DEFAULT '[]',
  generated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX sell_signals_stock_id_generated_at_idx ON sell_signals (stock_id, generated_at DESC);
