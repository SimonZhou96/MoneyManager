package report

import (
	"bytes"
	"strings"
	"testing"
)

func TestWritePassedCSVIncludesStableHeaderAndEscapesValues(t *testing.T) {
	var buf bytes.Buffer
	records := []PassedStockRecord{
		{
			Market:         "HK",
			Code:           "HK.00001",
			Name:           "Alpha, Inc",
			TaskID:         "task-1",
			PassedCount:    3,
			EvidenceStatus: "completed",
			EvidenceNote:   "volume confirmed",
		},
	}

	if err := WritePassedCSV(&buf, records); err != nil {
		t.Fatalf("WritePassedCSV: %v", err)
	}

	output := buf.String()
	if !strings.Contains(output, "market,code,name,task_id,passed_count,evidence_status,evidence_note") {
		t.Fatalf("missing header: %s", output)
	}
	if !strings.Contains(output, `"Alpha, Inc"`) {
		t.Fatalf("expected csv escaping, got: %s", output)
	}
}

func TestWriteEvidenceMarkdownSummarizesWarnings(t *testing.T) {
	var buf bytes.Buffer
	summary := EvidenceReport{
		JobID: "job-1",
		Summaries: []EvidenceSummary{
			{Market: "HK", Tool: "combined_analysis", Status: "skipped", Warning: "mcp unavailable"},
		},
		Warnings: []string{"HK: mcp unavailable"},
	}

	if err := WriteEvidenceMarkdown(&buf, summary); err != nil {
		t.Fatalf("WriteEvidenceMarkdown: %v", err)
	}

	output := buf.String()
	for _, expected := range []string{"# Evidence Summary", "job-1", "combined_analysis", "mcp unavailable"} {
		if !strings.Contains(output, expected) {
			t.Fatalf("expected %q in markdown: %s", expected, output)
		}
	}
}
