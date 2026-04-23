from pathlib import Path


def test_docker_compose_binds_local_services_to_loopback() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert '127.0.0.1:7474:7474' in compose
    assert '127.0.0.1:7687:7687' in compose
    assert '127.0.0.1:16686:16686' in compose
    assert '127.0.0.1:4317:4317' in compose
    assert '127.0.0.1:4318:4318' in compose
    assert '127.0.0.1:8889:8889' in compose
    assert '127.0.0.1:9090:9090' in compose
    assert '127.0.0.1:3001:3000' in compose
