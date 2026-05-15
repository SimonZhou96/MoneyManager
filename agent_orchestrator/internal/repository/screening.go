package repository

import (
	"context"
	"database/sql"
	"strings"

	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/rules"
	"github.com/SimonZhou96/MoneyManager/agent_orchestrator/internal/screening"
)

func (r *MySQLRepository) LoadRuleMetadata(ctx context.Context, market string) ([]rules.RuleMetadata, error) {
	if err := r.requireDB(); err != nil {
		return nil, err
	}
	rows, err := r.db.QueryContext(ctx, `
		SELECT market, rule_key, rule_name, rule_type, implementation,
		       params_json, enabled, display_order, description
		FROM screening_rule_metadata
		WHERE market=?
		ORDER BY display_order, id`,
		market,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := make([]rules.RuleMetadata, 0)
	for rows.Next() {
		var (
			rowMarket      string
			ruleKey        string
			ruleName       string
			ruleType       string
			implementation string
			paramsJSON     sql.NullString
			enabled        bool
			displayOrder   int
			description    sql.NullString
		)
		if err := rows.Scan(&rowMarket, &ruleKey, &ruleName, &ruleType, &implementation, &paramsJSON, &enabled, &displayOrder, &description); err != nil {
			return nil, err
		}
		metadata, err := rules.RuleMetadataFromRow(map[string]any{
			"market":         rowMarket,
			"rule_key":       ruleKey,
			"rule_name":      ruleName,
			"rule_type":      ruleType,
			"implementation": implementation,
			"params_json":    nullString(paramsJSON),
			"enabled":        enabled,
			"display_order":  displayOrder,
			"description":    description.String,
		})
		if err != nil {
			return nil, err
		}
		out = append(out, metadata)
	}
	return out, rows.Err()
}

func (r *MySQLRepository) LoadRuleChain(ctx context.Context, market string, timeframe string, chainKey string) (rules.RuleChainConfig, error) {
	if err := r.requireDB(); err != nil {
		return rules.RuleChainConfig{}, err
	}
	timeframe = strings.TrimSpace(timeframe)
	if timeframe == "" {
		timeframe = "*"
	}
	var row *sql.Row
	if strings.TrimSpace(chainKey) != "" {
		row = r.db.QueryRowContext(ctx, `
			SELECT market, timeframe, chain_key, chain_name, expression_json,
			       enabled, priority, description
			FROM screening_rule_chains
			WHERE market=? AND chain_key=? AND timeframe IN (?, '*')
			ORDER BY CASE WHEN timeframe=? THEN 0 ELSE 1 END, id ASC
			LIMIT 1`,
			market,
			chainKey,
			timeframe,
			timeframe,
		)
	} else {
		row = r.db.QueryRowContext(ctx, `
			SELECT market, timeframe, chain_key, chain_name, expression_json,
			       enabled, priority, description
			FROM screening_rule_chains
			WHERE market=? AND enabled=1 AND timeframe IN (?, '*')
			ORDER BY CASE WHEN timeframe=? THEN 0 ELSE 1 END, priority ASC, id ASC
			LIMIT 1`,
			market,
			timeframe,
			timeframe,
		)
	}
	chain, err := scanRuleChain(row)
	if err == sql.ErrNoRows {
		return rules.RuleChainConfig{}, nil
	}
	return chain, err
}

func (r *MySQLRepository) ListPoolStocks(ctx context.Context, market string) ([]rules.Stock, error) {
	if err := r.requireDB(); err != nil {
		return nil, err
	}
	stocks, err := r.listMergedPoolStocks(ctx, market)
	if err != nil {
		return nil, err
	}
	if len(stocks) > 0 {
		return stocks, nil
	}
	return r.listMasterStocks(ctx, market)
}

func (r *MySQLRepository) ListStocksByCodes(ctx context.Context, market string, codes []string) ([]rules.Stock, error) {
	if err := r.requireDB(); err != nil {
		return nil, err
	}
	normalized := normalizeCodes(codes)
	if len(normalized) == 0 {
		return nil, nil
	}
	stocks, err := r.listMasterStocksByCodes(ctx, market, normalized)
	if err != nil {
		return nil, err
	}
	found := make(map[string]bool, len(stocks))
	for _, stock := range stocks {
		found[stock.Code] = true
	}
	missing := make([]string, 0)
	for _, code := range normalized {
		if !found[code] {
			missing = append(missing, code)
		}
	}
	if len(missing) == 0 {
		return stocks, nil
	}
	poolStocks, err := r.listPoolStocksByCodes(ctx, market, missing)
	if err != nil {
		return nil, err
	}
	return append(stocks, poolStocks...), nil
}

func (r *MySQLRepository) CreateScreeningTask(ctx context.Context, task screening.Task) error {
	if err := r.requireDB(); err != nil {
		return err
	}
	paramsJSON, err := jsonOrNil(task.Params)
	if err != nil {
		return err
	}
	_, err = r.db.ExecContext(ctx, `
		INSERT INTO screening_tasks
		    (task_id, market, timeframe, status, total_count, completed_count, params_json, check_date)
		VALUES (?,?,?,?,?,?,?,?)`,
		task.TaskID,
		task.Market,
		task.Timeframe,
		"running",
		task.TotalCount,
		0,
		paramsJSON,
		task.CheckDate,
	)
	return err
}

func (r *MySQLRepository) UpdateTaskProgress(ctx context.Context, taskID string, completed int, code string, name string) error {
	if err := r.requireDB(); err != nil {
		return err
	}
	_, err := r.db.ExecContext(ctx, `
		UPDATE screening_tasks
		SET completed_count=?, current_stock_code=?, current_stock_name=?
		WHERE task_id=?`,
		completed,
		nullableString(code),
		nullableString(name),
		taskID,
	)
	return err
}

func (r *MySQLRepository) UpdateTaskStatus(ctx context.Context, taskID string, status string) error {
	if err := r.requireDB(); err != nil {
		return err
	}
	_, err := r.db.ExecContext(ctx, `
		UPDATE screening_tasks
		SET status=?
		WHERE task_id=?`,
		status,
		taskID,
	)
	return err
}

func (r *MySQLRepository) UpsertScreeningResult(ctx context.Context, result screening.Result) error {
	if err := r.requireDB(); err != nil {
		return err
	}
	filterDetails, err := jsonOrNil(result.FilterDetails)
	if err != nil {
		return err
	}
	_, err = r.db.ExecContext(ctx, `
		INSERT INTO screening_results
		    (task_id, market, code, name, check_date, is_passed, filter_summary, filter_details,
		     sector, industry, market_cap, pe_ratio, close_price)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
		ON DUPLICATE KEY UPDATE
		    task_id=VALUES(task_id),
		    name=VALUES(name),
		    is_passed=VALUES(is_passed),
		    filter_summary=VALUES(filter_summary),
		    filter_details=VALUES(filter_details),
		    sector=VALUES(sector),
		    industry=VALUES(industry),
		    market_cap=VALUES(market_cap),
		    pe_ratio=VALUES(pe_ratio),
		    close_price=VALUES(close_price)`,
		result.TaskID,
		result.Market,
		result.Code,
		nullableString(result.Name),
		result.CheckDate,
		boolInt(result.Passed),
		nullableString(result.FilterSummary),
		filterDetails,
		nullableString(result.Sector),
		nullableString(result.Industry),
		sqlFloat(result.MarketCap),
		sqlFloat(result.PERatio),
		sqlFloat(result.ClosePrice),
	)
	return err
}

func scanRuleChain(row rowScanner) (rules.RuleChainConfig, error) {
	var (
		market         string
		timeframe      string
		chainKey       string
		chainName      string
		expressionJSON sql.NullString
		enabled        bool
		priority       int
		description    sql.NullString
	)
	if err := row.Scan(&market, &timeframe, &chainKey, &chainName, &expressionJSON, &enabled, &priority, &description); err != nil {
		return rules.RuleChainConfig{}, err
	}
	return rules.RuleChainConfigFromRow(map[string]any{
		"market":          market,
		"timeframe":       timeframe,
		"chain_key":       chainKey,
		"chain_name":      chainName,
		"expression_json": nullString(expressionJSON),
		"enabled":         enabled,
		"priority":        priority,
		"description":     description.String,
	})
}

func (r *MySQLRepository) listMergedPoolStocks(ctx context.Context, market string) ([]rules.Stock, error) {
	rows, err := r.db.QueryContext(ctx, `
		SELECT code, name, market_cap, price, pe_ratio, turnover, volume, industry_name
		FROM stock_pools
		WHERE market=? AND pool_type IN ('best','index','industry','ipo','etf')
		ORDER BY FIELD(pool_type, 'best', 'index', 'industry', 'ipo', 'etf'), market_cap DESC`,
		market,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	merged := make(map[string]*rules.Stock)
	order := make([]string, 0)
	for rows.Next() {
		var (
			code      string
			name      sql.NullString
			marketCap sql.NullFloat64
			price     sql.NullFloat64
			peRatio   sql.NullFloat64
			turnover  sql.NullFloat64
			volume    sql.NullFloat64
			industry  sql.NullString
		)
		if err := rows.Scan(&code, &name, &marketCap, &price, &peRatio, &turnover, &volume, &industry); err != nil {
			return nil, err
		}
		code = strings.TrimSpace(code)
		if code == "" {
			continue
		}
		stock, ok := merged[code]
		if !ok {
			value := rules.NewStock(market, code, name.String)
			stock = &value
			merged[code] = stock
			order = append(order, code)
		}
		mergeStockFields(stock, name, marketCap, price, peRatio, turnover, volume, industry)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	out := make([]rules.Stock, 0, len(order))
	for _, code := range order {
		out = append(out, *merged[code])
	}
	return out, nil
}

func (r *MySQLRepository) listMasterStocks(ctx context.Context, market string) ([]rules.Stock, error) {
	rows, err := r.db.QueryContext(ctx, `
		SELECT code, name, sector, industry, market_cap, pe_ratio, pb_ratio
		FROM stocks
		WHERE market=?
		ORDER BY code`,
		market,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	stocks := make([]rules.Stock, 0)
	for rows.Next() {
		var (
			code      string
			name      sql.NullString
			sector    sql.NullString
			industry  sql.NullString
			marketCap sql.NullFloat64
			peRatio   sql.NullFloat64
			pbRatio   sql.NullFloat64
		)
		if err := rows.Scan(&code, &name, &sector, &industry, &marketCap, &peRatio, &pbRatio); err != nil {
			return nil, err
		}
		code = strings.TrimSpace(code)
		if code == "" {
			continue
		}
		stock := rules.NewStock(market, code, name.String)
		stock.Sector = sector.String
		stock.Industry = industry.String
		if marketCap.Valid {
			stock.MarketCap = marketCap.Float64
			stock.HasMarketCap = true
		}
		if peRatio.Valid {
			stock.PERatio = peRatio.Float64
			stock.HasPERatio = true
		}
		if pbRatio.Valid {
			stock.PBRatio = pbRatio.Float64
			stock.HasPBRatio = true
		}
		stocks = append(stocks, stock)
	}
	return stocks, rows.Err()
}

func (r *MySQLRepository) listMasterStocksByCodes(ctx context.Context, market string, codes []string) ([]rules.Stock, error) {
	placeholders, args := placeholdersForCodes(market, codes)
	rows, err := r.db.QueryContext(ctx, `
		SELECT code, name, sector, industry, market_cap, pe_ratio, pb_ratio
		FROM stocks
		WHERE market=? AND code IN (`+placeholders+`)
		ORDER BY code`,
		args...,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	stocks := make([]rules.Stock, 0)
	for rows.Next() {
		var (
			code      string
			name      sql.NullString
			sector    sql.NullString
			industry  sql.NullString
			marketCap sql.NullFloat64
			peRatio   sql.NullFloat64
			pbRatio   sql.NullFloat64
		)
		if err := rows.Scan(&code, &name, &sector, &industry, &marketCap, &peRatio, &pbRatio); err != nil {
			return nil, err
		}
		code = strings.TrimSpace(code)
		if code == "" {
			continue
		}
		stock := rules.NewStock(market, code, name.String)
		stock.Sector = sector.String
		stock.Industry = industry.String
		if marketCap.Valid {
			stock.MarketCap = marketCap.Float64
			stock.HasMarketCap = true
		}
		if peRatio.Valid {
			stock.PERatio = peRatio.Float64
			stock.HasPERatio = true
		}
		if pbRatio.Valid {
			stock.PBRatio = pbRatio.Float64
			stock.HasPBRatio = true
		}
		stocks = append(stocks, stock)
	}
	return stocks, rows.Err()
}

func (r *MySQLRepository) listPoolStocksByCodes(ctx context.Context, market string, codes []string) ([]rules.Stock, error) {
	placeholders, args := placeholdersForCodes(market, codes)
	rows, err := r.db.QueryContext(ctx, `
		SELECT code, name, market_cap, price, pe_ratio, turnover, volume, industry_name
		FROM stock_pools
		WHERE market=? AND code IN (`+placeholders+`)
		ORDER BY FIELD(pool_type, 'best', 'index', 'industry', 'ipo', 'etf')`,
		args...,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	merged := make(map[string]*rules.Stock)
	order := make([]string, 0)
	for rows.Next() {
		var (
			code      string
			name      sql.NullString
			marketCap sql.NullFloat64
			price     sql.NullFloat64
			peRatio   sql.NullFloat64
			turnover  sql.NullFloat64
			volume    sql.NullFloat64
			industry  sql.NullString
		)
		if err := rows.Scan(&code, &name, &marketCap, &price, &peRatio, &turnover, &volume, &industry); err != nil {
			return nil, err
		}
		code = strings.TrimSpace(code)
		if code == "" {
			continue
		}
		stock, ok := merged[code]
		if !ok {
			value := rules.NewStock(market, code, name.String)
			stock = &value
			merged[code] = stock
			order = append(order, code)
		}
		mergeStockFields(stock, name, marketCap, price, peRatio, turnover, volume, industry)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	out := make([]rules.Stock, 0, len(order))
	for _, code := range order {
		out = append(out, *merged[code])
	}
	return out, nil
}

func mergeStockFields(stock *rules.Stock, name sql.NullString, marketCap, price, peRatio, turnover, volume sql.NullFloat64, industry sql.NullString) {
	if stock.Name == "" && name.Valid {
		stock.Name = name.String
	}
	if !stock.HasMarketCap && marketCap.Valid {
		stock.MarketCap = marketCap.Float64
		stock.HasMarketCap = true
	}
	if price.Valid {
		if stock.Extra == nil {
			stock.Extra = map[string]any{}
		}
		stock.Extra["price"] = price.Float64
	}
	if !stock.HasPERatio && peRatio.Valid {
		stock.PERatio = peRatio.Float64
		stock.HasPERatio = true
	}
	if turnover.Valid {
		if stock.Extra == nil {
			stock.Extra = map[string]any{}
		}
		stock.Extra["turnover"] = turnover.Float64
	}
	if !stock.HasVolume && volume.Valid {
		stock.Volume = volume.Float64
		stock.HasVolume = true
	}
	if stock.Industry == "" && industry.Valid {
		stock.Industry = industry.String
	}
}

func nullString(value sql.NullString) any {
	if !value.Valid {
		return nil
	}
	return value.String
}

func boolInt(value bool) int {
	if value {
		return 1
	}
	return 0
}

func sqlFloat(value *float64) any {
	if value == nil {
		return nil
	}
	return *value
}

func normalizeCodes(codes []string) []string {
	seen := make(map[string]bool, len(codes))
	out := make([]string, 0, len(codes))
	for _, code := range codes {
		code = strings.TrimSpace(code)
		if code == "" || seen[code] {
			continue
		}
		seen[code] = true
		out = append(out, code)
	}
	return out
}

func placeholdersForCodes(market string, codes []string) (string, []any) {
	placeholders := strings.TrimRight(strings.Repeat("?,", len(codes)), ",")
	args := make([]any, 0, 1+len(codes))
	args = append(args, market)
	for _, code := range codes {
		args = append(args, code)
	}
	return placeholders, args
}
