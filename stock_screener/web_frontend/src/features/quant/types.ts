export type QuantBacktestRequest = {
  market: string
  symbols: string[]
  strategy_source: 'rule_chain' | 'meta_rules' | 'option_lab'
  entry_chain_key: string
  exit_policy: Record<string, unknown>
  start: string
  end: string
  initial_cash: number
  quantity: number
  commission_rate: number
  slippage_rate: number
  max_position_weight: number
}

export type QuantBacktestSubmitResponse = {
  run_id: string
  status: string
}
