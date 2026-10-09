from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv
import os
import json
import base64

# Carga la API key (OpenRouter) desde el .env que está junto a este archivo.
# Copia .env.example a .env y pon tu OPENROUTER_API_KEY.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

app = Flask(__name__, static_folder='.')
CORS(app)

# Una sola key para todo: chat de texto Y análisis de visión (ambos por OpenRouter)
API_KEY = os.getenv("OPENROUTER_API_KEY") or "TU_API_KEY_AQUI"

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=API_KEY
)

CHAT_MODEL = "poolside/laguna-m.1:free"   # mismo modelo de chat que Equipo2_Tarea1
VISION_MODEL = "google/gemini-2.5-flash"  # Gemini 2.5 Flash vía OpenRouter (soporta visión)

# --- Catálogo cargado desde catalog.json (generado por preprocess_catalog.py) ---
_CATALOG_PATH = os.path.join(_BASE_DIR, "catalog.json")
try:
    with open(_CATALOG_PATH, encoding="utf-8") as _f:
        CATALOGO = json.load(_f)
    print(f"Catálogo cargado: {len(CATALOGO)} productos")
except FileNotFoundError:
    print("ADVERTENCIA: catalog.json no encontrado. Ejecuta preprocess_catalog.py primero.")
    CATALOGO = []

# --- Estado de sesión en memoria (prototipo: una sola sesión) ---
SESSION = {"room_profile": None}

# Prompt que se le manda a la visión
PROMPT_ANALISIS = (
    "Analiza esta imagen de un espacio interior y responde ÚNICAMENTE con un JSON "
    "con esta estructura exacta, sin texto adicional:\n"
    "{\n"
    '  "estilo": string (uno de: escandinavo, minimalista, industrial, moderno, tradicional, ecléctico),\n'
    '  "colores_dominantes": array de 2-3 strings (ej: ["blanco", "gris", "madera"]),\n'
    '  "muebles_detectados": array de strings con lo que ves,\n'
    '  "ambiente": string (uno de: sala, recámara, comedor, oficina, otro),\n'
    '  "notas": string con observación relevante para recomendación de muebles, máximo 1 oración\n'
    "}"
)

REGLAS_BOT = """Reglas:
- Responde siempre en español, con tono amigable, cercano y conciso.
- Recomienda ÚNICAMENTE productos del catálogo de abajo (no inventes productos ni precios).
- Al recomendar, menciona el nombre exacto, el precio en MXN y por qué encaja con el usuario.
- Haz preguntas sobre el espacio (medidas, estilo, presupuesto) para personalizar.
- Si detectas indecisión ("no sé", "tal vez", preguntas repetidas), ofrece proactivamente una comparación concreta entre 2 productos.
- Mantén respuestas cortas: máximo 3-4 oraciones por turno.
- Puedes usar **negritas** para resaltar nombres y precios; el chat las renderiza.
- Cuando sea natural, cierra con una acción: ver el producto, comparar modelos o filtrar el catálogo."""


def catalogo_compacto():
    """Resumen del catálogo para el prompt: solo lo necesario para recomendar,
    sin URLs ni descripciones largas que gastan tokens."""
    return "\n".join(
        f"- {p['nombre']} | {p['categoria']} | ${p['precio_mxn']} MXN | "
        f"estilo: {', '.join(p['estilo'])} | colores: {', '.join(p['colores'])}"
        for p in CATALOGO
    )


# System prompt base (cuando aún no hay foto analizada)
SYSTEM_PROMPT = (
    "Eres el asistente virtual de IKEA México, experto en muebles y decoración.\n"
    + REGLAS_BOT
    + "\n\nCatálogo disponible (" + str(len(CATALOGO)) + " productos):\n"
    + catalogo_compacto()
)


def build_system_prompt():
    """System prompt usado cuando ya hay un room_profile en sesión."""
    room_profile = SESSION["room_profile"]
    return (
        "Eres el asistente de compras de IKEA México. Ya analizaste una foto del "
        "espacio del usuario, con estas características: "
        + json.dumps(room_profile, ensure_ascii=False) + ".\n"
        + REGLAS_BOT
        + "\n\nPrioriza productos cuyo estilo y colores sean coherentes con ese espacio.\n\n"
        + "Catálogo disponible (" + str(len(CATALOGO)) + " productos):\n"
        + catalogo_compacto()
    )


def parse_json_seguro(texto):
    """Limpia fences de markdown y parsea el JSON que devuelve el modelo."""
    t = texto.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip("`").strip()
    return json.loads(t)


def filtrar_catalogo(room_profile, top=5):
    """Filtro simple por coincidencia de estilo y colores. Devuelve top productos."""
    estilo_detectado = (room_profile.get("estilo") or "").lower()
    colores_detectados = [c.lower() for c in room_profile.get("colores_dominantes", [])]

    puntuados = []
    for p in CATALOGO:
        score = 0
        if estilo_detectado and estilo_detectado in [e.lower() for e in p["estilo"]]:
            score += 2
        for c in p["colores"]:
            if c.lower() in colores_detectados:
                score += 1
        if score > 0:
            puntuados.append((score, p))

    puntuados.sort(key=lambda x: x[0], reverse=True)
    resultado = [p for _, p in puntuados[:top]]

    # Fallback: si nada coincide, devolvemos algunos para que la demo no salga vacía
    if not resultado:
        resultado = CATALOGO[:3]
    return resultado


@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/catalog', methods=['GET'])
def get_catalog():
    return jsonify(CATALOGO)


@app.route('/analizar-foto', methods=['POST'])
def analizar_foto():
    if 'foto' not in request.files:
        return jsonify({"error": "No se envió ninguna foto"}), 400

    try:
        archivo = request.files['foto']
        raw = archivo.read()
        mime = archivo.mimetype or "image/jpeg"
        data_url = f"data:{mime};base64," + base64.b64encode(raw).decode()

        # Gemini 2.5 Flash con visión, vía OpenRouter. max_tokens acotado por crédito.
        response = client.chat.completions.create(
            model=VISION_MODEL,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT_ANALISIS},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
        )
        room_profile = parse_json_seguro(response.choices[0].message.content)

        SESSION["room_profile"] = room_profile

        colores = ", ".join(room_profile.get("colores_dominantes", []))
        mensaje = (
            f"Veo un espacio {room_profile.get('ambiente', 'interior')} con estilo "
            f"{room_profile.get('estilo', 'definido')} y tonos {colores}. "
            "Con base en eso, aquí tienes algunas ideas:"
        )

        recomendaciones = filtrar_catalogo(room_profile)

        return jsonify({
            "room_profile": room_profile,
            "mensaje": mensaje,
            "recomendaciones": recomendaciones
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    historial = data.get('historial', [])

    # Si ya analizamos una foto, usamos el system prompt con el room_profile
    system = build_system_prompt() if SESSION["room_profile"] else SYSTEM_PROMPT
    messages = [{"role": "system", "content": system}] + historial

    try:
        response = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=messages
        )
        respuesta = response.choices[0].message.content
        return jsonify({"respuesta": respuesta})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    print("Servidor IKEA AI corriendo en http://localhost:5000")
    app.run(debug=True, port=5000)
