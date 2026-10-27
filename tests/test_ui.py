async def test_ui_shell_is_public(api):
    r = await api.get("/ui/")
    assert r.status_code == 200
    assert "Handoff Flows" in r.text


async def test_ui_ships_pipeline_view(api):
    r = await api.get("/ui/")
    assert "pipelineHtml" in r.text
    assert "flows/" in r.text


async def test_ui_references_vendored_bootstrap(api):
    r = await api.get("/ui/")
    assert 'href="vendor/bootstrap.min.css"' in r.text
    assert "cdn." not in r.text


async def test_ui_serves_vendored_bootstrap_css(api):
    r = await api.get("/ui/vendor/bootstrap.min.css")
    assert r.status_code == 200
    assert "Bootstrap" in r.text[:200]
