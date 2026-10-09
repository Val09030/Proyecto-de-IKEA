import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def client():
    if "server" in sys.modules:
        del sys.modules["server"]
    from server import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_catalog_returns_200(client):
    response = client.get("/catalog")
    assert response.status_code == 200


def test_catalog_returns_list(client):
    response = client.get("/catalog")
    data = json.loads(response.data)
    assert isinstance(data, list)
    assert len(data) > 0


def test_catalog_items_have_required_fields(client):
    response = client.get("/catalog")
    data = json.loads(response.data)
    required = {"id", "nombre", "categoria", "precio_mxn", "descripcion", "image_url"}
    for item in data:
        missing = required - item.keys()
        assert not missing, f"Faltan campos {missing} en item {item.get('id')}"


def test_catalog_image_urls_are_strings(client):
    response = client.get("/catalog")
    data = json.loads(response.data)
    for item in data:
        assert isinstance(item["image_url"], str), f"image_url no es string en {item.get('id')}"


def test_categories_are_clean(client):
    """No debe haber categorías vacías, nulas ni 'nan' (punto 2 del pulido)."""
    data = json.loads(client.get("/catalog").data)
    for item in data:
        cat = (item["categoria"] or "").strip()
        assert cat and cat.lower() not in {"nan", "none"}, \
            f"categoría inválida {cat!r} en item {item.get('id')}"


def test_image_urls_are_real_ikea(client):
    """Las imágenes deben apuntar a IKEA o a un placeholder explícito (punto 3)."""
    data = json.loads(client.get("/catalog").data)
    for item in data:
        url = item["image_url"]
        assert url.startswith("http"), f"image_url no es URL en {item.get('id')}"
        assert "ikea.com" in url or "placehold" in url, \
            f"image_url inesperada {url!r} en item {item.get('id')}"


def test_products_have_product_url(client):
    data = json.loads(client.get("/catalog").data)
    assert all(item.get("product_url", "").startswith("http") for item in data)


def test_analizar_foto_sin_archivo_responde_400(client):
    """El endpoint de visión valida la entrada sin llamar al modelo."""
    response = client.post("/analizar-foto")
    assert response.status_code == 400
    assert "error" in json.loads(response.data)


def test_system_prompt_incluye_catalogo():
    """El prompt base debe conocer el catálogo real (mejora del system prompt)."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server
    assert "Catálogo disponible" in server.SYSTEM_PROMPT
    if server.CATALOGO:
        assert server.CATALOGO[0]["nombre"] in server.SYSTEM_PROMPT
