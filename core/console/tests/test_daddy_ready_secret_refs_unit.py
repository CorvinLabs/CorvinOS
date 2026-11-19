import pytest
from fastapi import HTTPException
from corvin_console.routes import plugins_daddy_ready_api as api


def test_a_literal_secret_is_refused_and_a_reference_is_fine():
    with pytest.raises(HTTPException) as e:
        api._check_secret_references({"delivery": {"webhook_url": "https://discord.com/api/webhooks/1/T"}})
    assert e.value.status_code == 422 and "reference" in e.value.detail
    api._check_secret_references({"delivery": {"webhook_url": "${DADDY_DISCORD_WEBHOOK}"}, "video": {"template_openai_key": "file:/x/k"}})
    api._check_secret_references({})
