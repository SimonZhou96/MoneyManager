package rules

import (
	"encoding/json"
	"math"
	"strconv"
	"strings"
)

func optionalFloat(params map[string]any, key string) *float64 {
	if params == nil {
		return nil
	}
	value, ok := params[key]
	if !ok || value == nil {
		return nil
	}
	number, ok := toFloat(value)
	if !ok {
		return nil
	}
	return &number
}

func floatParam(params map[string]any, key string, fallback float64) float64 {
	if value := optionalFloat(params, key); value != nil {
		return *value
	}
	return fallback
}

func intParam(params map[string]any, key string, fallback int) int {
	if value := optionalFloat(params, key); value != nil {
		return int(*value)
	}
	return fallback
}

func boolParam(params map[string]any, key string, fallback bool) bool {
	if params == nil {
		return fallback
	}
	value, ok := params[key]
	if !ok || value == nil {
		return fallback
	}
	switch typed := value.(type) {
	case bool:
		return typed
	case string:
		parsed, err := strconv.ParseBool(strings.TrimSpace(typed))
		if err == nil {
			return parsed
		}
	case int:
		return typed != 0
	case int64:
		return typed != 0
	case float64:
		return typed != 0
	case json.Number:
		parsed, err := typed.Float64()
		if err == nil {
			return parsed != 0
		}
	}
	return fallback
}

func stringFromMap(values map[string]any, key string) (string, bool) {
	if values == nil {
		return "", false
	}
	value, ok := values[key]
	if !ok || value == nil {
		return "", false
	}
	text, ok := value.(string)
	return text, ok
}

func toFloat(value any) (float64, bool) {
	switch typed := value.(type) {
	case float64:
		return typed, true
	case float32:
		return float64(typed), true
	case int:
		return float64(typed), true
	case int8:
		return float64(typed), true
	case int16:
		return float64(typed), true
	case int32:
		return float64(typed), true
	case int64:
		return float64(typed), true
	case uint:
		return float64(typed), true
	case uint8:
		return float64(typed), true
	case uint16:
		return float64(typed), true
	case uint32:
		return float64(typed), true
	case uint64:
		return float64(typed), true
	case json.Number:
		parsed, err := typed.Float64()
		return parsed, err == nil
	case string:
		parsed, err := strconv.ParseFloat(strings.TrimSpace(typed), 64)
		return parsed, err == nil
	default:
		return 0, false
	}
}

func finite(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0)
}
