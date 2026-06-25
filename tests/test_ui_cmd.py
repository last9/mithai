"""Tests for `mithai ui` command safety defaults."""

from click.testing import CliRunner

from mithai.cli.ui_cmd import ui


def test_ui_refuses_public_bind_without_auth(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
adapter:
  type: cli
llm:
  provider: anthropic
ui:
  host: 0.0.0.0
  port: 8420
  auth_token: ${MITHAI_UI_TOKEN}
"""
    )

    result = CliRunner().invoke(ui, ["--config", str(config_path)])

    assert result.exit_code != 0
    assert "Refusing to bind the Control Room UI publicly" in result.output
