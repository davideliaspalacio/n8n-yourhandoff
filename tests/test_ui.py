async def test_ui_shell_is_public(api):
    r = await api.get("/ui/")
    assert r.status_code == 200
    assert "Handoff Flows" in r.text
