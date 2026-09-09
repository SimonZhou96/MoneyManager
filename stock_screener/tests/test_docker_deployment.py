"""Regression checks for the Docker Compose deployment contract."""

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCKER_COMPOSE = PROJECT_ROOT / "deploy" / "docker-compose.yml"
DOCKERFILE = PROJECT_ROOT / "Dockerfile"
CONTAINERFILE = PROJECT_ROOT / "deploy" / "Containerfile"


class DockerDeploymentTests(unittest.TestCase):
    def test_root_dockerfile_matches_the_podman_containerfile(self):
        self.assertEqual(
            DOCKERFILE.read_text(encoding="utf-8"),
            CONTAINERFILE.read_text(encoding="utf-8"),
        )

    def test_docker_compose_defines_the_full_web_stack_without_podman_labels(self):
        content = DOCKER_COMPOSE.read_text(encoding="utf-8")

        for service in ("mysql:", "redis:", "web-api:", "web-worker:", "frontend:", "caddy:"):
            self.assertIn(service, content)

        self.assertIn("dockerfile: deploy/Containerfile", content)
        self.assertNotIn(":Z", content)


if __name__ == "__main__":
    unittest.main()
