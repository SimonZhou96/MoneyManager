package workflow

import (
	"context"
	"fmt"

	"github.com/cloudwego/eino/compose"
)

const (
	EvidenceStatusCompleted = "completed"
	EvidenceStatusSkipped   = "skipped"
)

type RuleExecutor interface {
	ExecuteRules(context.Context, State) (State, error)
}

type EvidenceCollector interface {
	CollectEvidence(context.Context, State) (State, error)
}

type Runner struct {
	runnable compose.Runnable[State, State]
}

func NewRunner(executor RuleExecutor, evidence EvidenceCollector) (*Runner, error) {
	if executor == nil {
		executor = NoopRuleExecutor{}
	}
	if evidence == nil {
		evidence = NoopEvidenceCollector{}
	}

	wf := compose.NewWorkflow[State, State]()
	wf.AddLambdaNode("plan", compose.InvokableLambda(func(_ context.Context, input State) (State, error) {
		input.Trace = append(input.Trace, "plan")
		if input.Timeframe == "" {
			input.Timeframe = "1d"
		}
		return input, nil
	})).AddInput(compose.START)
	wf.AddLambdaNode("execute_rules", compose.InvokableLambda(func(ctx context.Context, input State) (State, error) {
		return executor.ExecuteRules(ctx, input)
	})).AddInput("plan")
	wf.AddLambdaNode("collect_evidence", compose.InvokableLambda(func(ctx context.Context, input State) (State, error) {
		output, err := evidence.CollectEvidence(ctx, input)
		if err != nil {
			input.EvidenceStatus = EvidenceStatusSkipped
			input.Warnings = append(input.Warnings, fmt.Sprintf("mcp evidence skipped: %v", err))
			return input, nil
		}
		if output.EvidenceStatus == "" {
			output.EvidenceStatus = EvidenceStatusCompleted
		}
		return output, nil
	})).AddInput("execute_rules")
	wf.AddLambdaNode("observe", compose.InvokableLambda(func(_ context.Context, input State) (State, error) {
		input.Trace = append(input.Trace, "observe")
		if len(input.MarketResults) == 0 {
			input.Warnings = append(input.Warnings, "no market results produced")
		}
		return input, nil
	})).AddInput("collect_evidence")
	wf.AddLambdaNode("replan", compose.InvokableLambda(func(_ context.Context, input State) (State, error) {
		input.Trace = append(input.Trace, "replan")
		return input, nil
	})).AddInput("observe")
	wf.AddLambdaNode("persist", compose.InvokableLambda(func(_ context.Context, input State) (State, error) {
		input.Trace = append(input.Trace, "persist")
		return input, nil
	})).AddInput("replan")
	wf.End().AddInput("persist")

	runnable, err := wf.Compile(context.Background(), compose.WithGraphName("stock_screening_workflow"))
	if err != nil {
		return nil, err
	}
	return &Runner{runnable: runnable}, nil
}

func (r *Runner) Run(ctx context.Context, state State) (State, error) {
	if r == nil || r.runnable == nil {
		return State{}, fmt.Errorf("workflow runner is not initialized")
	}
	return r.runnable.Invoke(ctx, state)
}

type NoopRuleExecutor struct{}

func (NoopRuleExecutor) ExecuteRules(_ context.Context, state State) (State, error) {
	state.Trace = append(state.Trace, "execute_rules")
	if len(state.MarketResults) == 0 {
		for _, market := range state.Markets {
			state.MarketResults = append(state.MarketResults, MarketResult{
				Market: market,
				Status: "skipped",
			})
		}
	}
	return state, nil
}

type NoopEvidenceCollector struct{}

func (NoopEvidenceCollector) CollectEvidence(_ context.Context, state State) (State, error) {
	state.Trace = append(state.Trace, "collect_evidence")
	state.EvidenceStatus = EvidenceStatusSkipped
	state.Warnings = append(state.Warnings, "external evidence collector is not configured")
	return state, nil
}
