"""Regression checks for the static Vite GitHub Pages deployment."""

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "jekyll-gh-pages.yml"
VITE_CONFIG = REPOSITORY_ROOT / "stock_screener" / "web_frontend" / "vite.config.ts"


class GitHubPagesWorkflowTests(unittest.TestCase):
    def test_pages_workflow_builds_and_uploads_vite_dist(self):
        content = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("actions/setup-node@v4", content)
        self.assertIn("npm ci", content)
        self.assertIn("npm run build", content)
        self.assertIn("stock_screener/web_frontend", content)
        self.assertIn("stock_screener/web_frontend/dist", content)
        self.assertNotIn("jekyll-build-pages", content)

    def test_vite_uses_configurable_pages_base_path(self):
        content = VITE_CONFIG.read_text(encoding="utf-8")

        self.assertIn("VITE_BASE_PATH", content)


if __name__ == "__main__":
    unittest.main()
