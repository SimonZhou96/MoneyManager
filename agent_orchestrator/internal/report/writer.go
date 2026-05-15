package report

import (
	"encoding/csv"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

type PassedStockRecord struct {
	Market         string
	Code           string
	Name           string
	TaskID         string
	PassedCount    int
	EvidenceStatus string
	EvidenceNote   string
}

type EvidenceReport struct {
	JobID     string
	Summaries []EvidenceSummary
	Warnings  []string
}

type EvidenceSummary struct {
	Market  string
	Code    string
	Tool    string
	Status  string
	Summary string
	Warning string
}

func WritePassedCSV(w io.Writer, records []PassedStockRecord) error {
	cw := csv.NewWriter(w)
	if err := cw.Write([]string{
		"market",
		"code",
		"name",
		"task_id",
		"passed_count",
		"evidence_status",
		"evidence_note",
	}); err != nil {
		return err
	}
	for _, record := range records {
		if err := cw.Write([]string{
			record.Market,
			record.Code,
			record.Name,
			record.TaskID,
			strconv.Itoa(record.PassedCount),
			record.EvidenceStatus,
			record.EvidenceNote,
		}); err != nil {
			return err
		}
	}
	cw.Flush()
	return cw.Error()
}

func WritePassedCSVFile(path string, records []PassedStockRecord) error {
	if err := ensureParent(path); err != nil {
		return err
	}
	file, err := os.Create(path)
	if err != nil {
		return err
	}
	defer file.Close()
	return WritePassedCSV(file, records)
}

func WriteEvidenceMarkdown(w io.Writer, report EvidenceReport) error {
	var b strings.Builder
	b.WriteString("# Evidence Summary\n\n")
	if report.JobID != "" {
		fmt.Fprintf(&b, "- Job ID: `%s`\n", report.JobID)
	}
	fmt.Fprintf(&b, "- Evidence items: %d\n", len(report.Summaries))
	if len(report.Warnings) > 0 {
		b.WriteString("\n## Warnings\n\n")
		for _, warning := range report.Warnings {
			fmt.Fprintf(&b, "- %s\n", warning)
		}
	}
	if len(report.Summaries) > 0 {
		b.WriteString("\n## External Evidence\n\n")
		b.WriteString("| Market | Code | Tool | Status | Summary | Warning |\n")
		b.WriteString("| --- | --- | --- | --- | --- | --- |\n")
		for _, item := range report.Summaries {
			fmt.Fprintf(
				&b,
				"| %s | %s | %s | %s | %s | %s |\n",
				escapeMarkdownCell(item.Market),
				escapeMarkdownCell(item.Code),
				escapeMarkdownCell(item.Tool),
				escapeMarkdownCell(item.Status),
				escapeMarkdownCell(item.Summary),
				escapeMarkdownCell(item.Warning),
			)
		}
	}
	_, err := io.WriteString(w, b.String())
	return err
}

func WriteEvidenceMarkdownFile(path string, evidence EvidenceReport) error {
	if err := ensureParent(path); err != nil {
		return err
	}
	file, err := os.Create(path)
	if err != nil {
		return err
	}
	defer file.Close()
	return WriteEvidenceMarkdown(file, evidence)
}

func ensureParent(path string) error {
	dir := filepath.Dir(path)
	if dir == "." || dir == "" {
		return nil
	}
	return os.MkdirAll(dir, 0o755)
}

func escapeMarkdownCell(value string) string {
	value = strings.ReplaceAll(value, "\n", " ")
	value = strings.ReplaceAll(value, "|", "\\|")
	return value
}
