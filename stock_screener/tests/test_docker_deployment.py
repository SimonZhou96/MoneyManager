"""Regression checks for the Docker Compose deployment contract."""

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
DOCKER_COMPOSE = PROJECT_ROOT / "deploy" / "docker-compose.yml"
DOCKERFILE = REPOSITORY_ROOT / "Dockerfile"


class DockerDeploymentTests(unittest.TestCase):
    def test_repository_root_has_dockerfile_for_ci_build(self):
        self.assertTrue(
            DOCKERFILE.is_file(),
            "Docker Image CI builds from the repository root and requires Dockerfile there.",
        )

    def test_docker_compose_defines_the_full_web_stack_without_podman_labels(self):
        content = DOCKER_COMPOSE.read_text(encoding="utf-8")

        for service in ("mysql:", "redis:", "web-api:", "web-worker:", "frontend:", "caddy:"):
            self.assertIn(service, content)

        self.assertIn("dockerfile: deploy/Containerfile", content)
        self.assertNotIn(":Z", content)


if __name__ == "__main__":
    unittest.main()
