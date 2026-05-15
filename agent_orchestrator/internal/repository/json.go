package repository

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"time"
)

func jsonOrNil(value any) ([]byte, error) {
	if value == nil {
		return nil, nil
	}
	data, err := json.Marshal(value)
	if err != nil {
		return nil, fmt.Errorf("marshal json payload: %w", err)
	}
	return data, nil
}

func decodeStringSlice(raw sql.NullString) []string {
	if !raw.Valid || raw.String == "" {
		return nil
	}
	var values []string
	if err := json.Unmarshal([]byte(raw.String), &values); err == nil {
		return values
	}
	return nil
}

func decodeMap(raw sql.NullString) map[string]any {
	if !raw.Valid || raw.String == "" {
		return nil
	}
	var value map[string]any
	if err := json.Unmarshal([]byte(raw.String), &value); err == nil {
		return value
	}
	return nil
}

func stringFromNullTime(value sql.NullTime) string {
	if !value.Valid {
		return ""
	}
	return value.Time.UTC().Format(time.RFC3339Nano)
}

func uint64Ptr(value sql.NullInt64) *uint64 {
	if !value.Valid || value.Int64 < 0 {
		return nil
	}
	out := uint64(value.Int64)
	return &out
}

func stringValue(values map[string]any, key string) string {
	if values == nil {
		return ""
	}
	value, ok := values[key]
	if !ok || value == nil {
		return ""
	}
	return fmt.Sprint(value)
}
