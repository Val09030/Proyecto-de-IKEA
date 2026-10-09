# IKEA AI Companion — Prototipo

Asistente de compras conversacional para IKEA México. Integra **chatbot (LLM)**,
**visión por computadora**, un **recomendador basado en búsqueda heurística** y la
lógica de un **agente**. Ver la explicación completa en
[`GUIA_PROTOTIPO_IA.md`](GUIA_PROTOTIPO_IA.md).

## Contenido

| Archivo | Qué es |
|---|---|
| `server.py` | Backend Flask: endpoints de catálogo, chat y análisis de foto. |
| `index.html` | Frontend de una sola página (HTML/CSS/JS). |
| `catalog.json` | Base de conocimiento: 50 productos IKEA con imágenes reales. |
| `preprocess_catalog.py` | Pipeline que generó `catalog.json` (se corre 1 sola vez). |
| `test_server.py` | Pruebas del backend (pytest). |
| `GUIA_PROTOTIPO_IA.md` | Explicación del prototipo desde la óptica de Sistemas de IA. |

## Cómo correrlo

1. **Crear entorno e instalar dependencias**

   ```bash
   python -m venv venv
   venv\Scripts\activate          # Windows
   # source venv/bin/activate     # macOS / Linux
   pip install -r requirements.txt
   ```

2. **Configurar la API key**

   Copia `.env.example` a `.env` y pon tu `OPENROUTER_API_KEY`
   (gratis en https://openrouter.ai/keys).

3. **Iniciar el servidor**

   ```bash
   python server.py
   ```

   Debe imprimir `Catálogo cargado: 50 productos`. Abre
   **http://localhost:5000** en el navegador.

## Pruebas

```bash
python -m pytest test_server.py -v
```

## Notas

- `catalog.json` ya viene generado; **no necesitas** correr `preprocess_catalog.py`
  (ese paso requiere `kagglehub` + `pandas` y descarga el dataset de Kaggle).
- El chat usa el modelo `poolside/laguna-m.1` y la visión `google/gemini-2.5-flash`,
  ambos vía OpenRouter.

## Modelos usados

| Función | Modelo (OpenRouter) |
|---|---|
| Chat de texto | `poolside/laguna-m.1:free` |
| Análisis de foto (visión) | `google/gemini-2.5-flash` |
