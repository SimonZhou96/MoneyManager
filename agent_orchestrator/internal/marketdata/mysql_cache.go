package marketdata

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/strategies"
)

type MySQLCacheProvider struct {
	db *sql.DB
}

func NewMySQLCacheProvider(db *sql.DB) *MySQLCacheProvider {
	return &MySQLCacheProvider{db: db}
}

func (p *MySQLCacheProvider) FetchBars(ctx context.Context, query Query) ([]strategies.Bar, string, error) {
	if p == nil || p.db == nil {
		return nil, "mysql_cache", fmt.Errorf("mysql cache provider has no db")
	}
	limit := query.MaxCount
	if limit <= 0 {
		limit = 500
	}
	rows, err := p.db.QueryContext(ctx, `
		SELECT bar_time, open, high, low, close, volume, turnover
		FROM stock_kline_cache
		WHERE market=? AND code=? AND timeframe=?
		ORDER BY bar_time DESC
		LIMIT ?`,
		query.Market,
		query.Code,
		query.Timeframe,
		limit,
	)
	if err != nil {
		return nil, "mysql_cache", err
	}
	defer rows.Close()

	bars := make([]strategies.Bar, 0, limit)
	for rows.Next() {
		var bar strategies.Bar
		var open, high, low, closePrice, volume, turnover sql.NullFloat64
		if err := rows.Scan(&bar.Time, &open, &high, &low, &closePrice, &volume, &turnover); err != nil {
			return nil, "mysql_cache", err
		}
		bar.Open = nullableFloat(open)
		bar.High = nullableFloat(high)
		bar.Low = nullableFloat(low)
		bar.Close = nullableFloat(closePrice)
		bar.Volume = nullableFloat(volume)
		bar.Turnover = nullableFloat(turnover)
		bars = append(bars, bar)
	}
	if err := rows.Err(); err != nil {
		return nil, "mysql_cache", err
	}
	return normalizeBars(bars, query.MaxCount), "mysql_cache", nil
}

func nullableFloat(value sql.NullFloat64) float64 {
	if !value.Valid {
		return 0
	}
	return value.Float64
}
