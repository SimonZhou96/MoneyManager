package workflow

type State struct {
	JobID           string            `json:"job_id"`
	JobType         string            `json:"job_type,omitempty"`
	Markets         []string          `json:"markets,omitempty"`
	Code            string            `json:"code,omitempty"`
	NormalizedCode  string            `json:"normalized_code,omitempty"`
	Timeframe       string            `json:"timeframe,omitempty"`
	ChainKey        string            `json:"chain_key,omitempty"`
	Trace           []string          `json:"trace,omitempty"`
	EvidenceStatus  string            `json:"evidence_status,omitempty"`
	Warnings        []string          `json:"warnings,omitempty"`
	MarketResults   []MarketResult    `json:"market_results,omitempty"`
	Evidence        []EvidenceSummary `json:"evidence,omitempty"`
	Options         map[string]any    `json:"options,omitempty"`
	StartedByAgent  string            `json:"started_by_agent,omitempty"`
	ExternalRunMode string            `json:"external_run_mode,omitempty"`
}

type MarketResult struct {
	Market      string `json:"market"`
	TaskID      string `json:"task_id,omitempty"`
	PassedCount int    `json:"passed_count"`
	TotalCount  int    `json:"total_count,omitempty"`
	Status      string `json:"status,omitempty"`
	Warning     string `json:"warning,omitempty"`
}

type EvidenceSummary struct {
	Market  string         `json:"market,omitempty"`
	Tool    string         `json:"tool"`
	Status  string         `json:"status"`
	Warning string         `json:"warning,omitempty"`
	Data    map[string]any `json:"data,omitempty"`
}

func (s State) WithTrace(step string) State {
	s.Trace = append(s.Trace, step)
	return s
}

func (s State) WithWarning(warning string) State {
	if warning != "" {
		s.Warnings = append(s.Warnings, warning)
	}
	return s
}
