package rules

import (
	"context"
	"fmt"
)

func evaluateExpression(ctx context.Context, expression map[string]any, execution *executionContext) bool {
	if len(expression) == 0 {
		return false
	}

	if value, ok := expression["ref"]; ok {
		return execution.execute(ctx, toRuleKey(value))
	}

	if value, ok := expression["and"]; ok {
		items := asList(value)
		if len(items) == 0 {
			return true
		}
		passed := true
		for _, item := range items {
			if !evaluateExpression(ctx, asExpression(item), execution) {
				passed = false
			}
		}
		return passed
	}

	if value, ok := expression["any"]; ok {
		items := asList(value)
		if len(items) == 0 {
			return false
		}
		passed := false
		for _, item := range items {
			if evaluateExpression(ctx, asExpression(item), execution) {
				passed = true
			}
		}
		return passed
	}

	if value, ok := expression["all_enabled"]; ok {
		ruleKeys := enabledRuleKeys(value, execution.metadataByKey)
		if len(ruleKeys) == 0 {
			return true
		}
		passed := true
		for _, ruleKey := range ruleKeys {
			if !execution.execute(ctx, ruleKey) {
				passed = false
			}
		}
		return passed
	}

	if value, ok := expression["any_enabled"]; ok {
		ruleKeys := enabledRuleKeys(value, execution.metadataByKey)
		if len(ruleKeys) == 0 {
			return false
		}
		passed := false
		for _, ruleKey := range ruleKeys {
			if execution.execute(ctx, ruleKey) {
				passed = true
			}
		}
		return passed
	}

	return false
}

func collectRuleKeys(expression map[string]any) map[string]struct{} {
	keys := map[string]struct{}{}
	collectRuleKeysInto(expression, keys)
	return keys
}

func collectRuleKeysInto(expression map[string]any, keys map[string]struct{}) {
	if len(expression) == 0 {
		return
	}
	if value, ok := expression["ref"]; ok {
		keys[toRuleKey(value)] = struct{}{}
	}
	for _, field := range []string{"and", "any"} {
		if value, ok := expression[field]; ok {
			for _, item := range asList(value) {
				collectRuleKeysInto(asExpression(item), keys)
			}
		}
	}
	for _, field := range []string{"all_enabled", "any_enabled"} {
		if value, ok := expression[field]; ok {
			for _, item := range asList(value) {
				keys[toRuleKey(item)] = struct{}{}
			}
		}
	}
}

func enabledRuleKeys(value any, metadataByKey map[string]RuleMetadata) []string {
	items := asList(value)
	ruleKeys := make([]string, 0, len(items))
	for _, item := range items {
		ruleKey := toRuleKey(item)
		metadata, ok := metadataByKey[ruleKey]
		if ok && metadata.Enabled {
			ruleKeys = append(ruleKeys, ruleKey)
		}
	}
	return ruleKeys
}

func asExpression(value any) map[string]any {
	if typed, ok := value.(map[string]any); ok {
		return typed
	}
	return map[string]any{}
}

func asList(value any) []any {
	if typed, ok := value.([]any); ok {
		return typed
	}
	if typed, ok := value.([]string); ok {
		out := make([]any, 0, len(typed))
		for _, item := range typed {
			out = append(out, item)
		}
		return out
	}
	if typed, ok := value.([]map[string]any); ok {
		out := make([]any, 0, len(typed))
		for _, item := range typed {
			out = append(out, item)
		}
		return out
	}
	return nil
}

func toRuleKey(value any) string {
	if value == nil {
		return ""
	}
	if typed, ok := value.(string); ok {
		return typed
	}
	return fmt.Sprint(value)
}
