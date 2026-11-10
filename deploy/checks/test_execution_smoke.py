import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "execution_smoke", Path(__file__).with_name("execution-smoke.py")
)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class ExecutionImageDrillTests(unittest.TestCase):
    def test_the_drill_probes_only_the_http_runner_in_the_image(self):
        source = Path(smoke.__file__).read_text()
        # The offline archive and the MCP and A2A runners were retired (ADR-0014).
        for retired in ("verify_airgap_bundle", "MCP_PROBE", "A2A_PROBE", "images.tar"):
            self.assertNotIn(retired, source)
        self.assertIn("/app/protocol-adapters/src/http-runner.mjs", smoke.HTTP_PROBE)

    def test_the_embedded_probe_stays_on_loopback_without_credentials(self):
        self.assertIn("127.0.0.1", smoke.HTTP_PROBE)
        self.assertNotIn("docker.sock", smoke.HTTP_PROBE)
        self.assertNotIn("LOTUSPOND_API_KEY", smoke.HTTP_PROBE)

    def test_containers_run_without_a_network_and_carry_the_drill_label(self):
        calls = []
        original = smoke.command
        smoke.command = lambda *args, **kwargs: calls.append(args)
        try:
            smoke.docker_run("sha256:" + "a" * 64, "run", ["help"])
        finally:
            smoke.command = original
        (args,) = calls
        self.assertEqual(args[:3], ("docker", "run", "--rm"))
        self.assertIn("none", args)
        self.assertIn(f"{smoke.LABEL}=run", args)


if __name__ == "__main__":
    unittest.main()
