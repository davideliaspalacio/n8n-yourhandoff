async def test_ui_shell_is_public(api):
    r = await api.get("/ui/")
    assert r.status_code == 200
    assert "Handoff Flows" in r.text


async def test_ui_ships_pipeline_view(api):
    r = await api.get("/ui/")
    assert "pipelineHtml" in r.text
    assert "flows/" in r.text
