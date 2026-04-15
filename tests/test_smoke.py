"""Minimal CI smoke tests so pytest does not exit with code 5 (no tests collected)."""


def test_create_app_imports():
    from mindex_api.main import create_app

    app = create_app()
    assert app.title
    assert "/api/mindex" in (app.openapi_url or "")
