def test_salud(client):
    assert client.get("/salud").json() == {"ok": True}


def test_db_conecta(db):
    from sqlalchemy import text

    assert db.execute(text("SELECT 1")).scalar() == 1
