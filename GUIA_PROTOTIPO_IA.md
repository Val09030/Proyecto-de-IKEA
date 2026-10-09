# IKEA AI Companion — Guía del prototipo (enfoque de Sistemas de IA)

> Documento de apoyo para explicar el proyecto en clase. No es un manual de
> programación: el objetivo es mostrar **qué componentes de inteligencia
> artificial** lo forman, cómo se conectan y cómo "razona" el asistente.

---

## 1. ¿Qué es el prototipo?

Un **asistente de compras conversacional para IKEA México**. El usuario navega un
catálogo de productos y, en un panel de chat, puede:

- **Escribir** lo que busca ("necesito un sofá para una sala pequeña").
- **Subir una foto** de su espacio para recibir recomendaciones acordes a su estilo.

El asistente responde en lenguaje natural y sugiere productos **reales del catálogo**,
con nombre, precio y por qué encajan.

En términos de la materia, el prototipo integra **cuatro temas del curso en un solo
sistema**: chatbot (PLN), visión por computadora, un algoritmo de
búsqueda/recomendación, y la lógica de un **agente** que percibe, mantiene estado y actúa.

---

## 2. Mapa: concepto de clase → cómo se implementa aquí

| Tema del curso | Dónde vive en el prototipo | Idea central |
|---|---|---|
| **Chatbot / PLN** | Endpoint `POST /chat` + LLM de texto | Un modelo de lenguaje genera las respuestas, guiado por un *system prompt*. |
| **Visión por computadora** | Endpoint `POST /analizar-foto` + LLM multimodal | Una foto se convierte en una descripción estructurada del espacio. |
| **Algoritmos de búsqueda** | Función `filtrar_catalogo()` | Búsqueda por **puntuación heurística** sobre el catálogo (recomendador basado en contenido). |
| **Agente** | *System prompt* + estado de sesión (`SESSION`) | Percepción → estado → política (reglas) → acción (respuesta/recomendación). |
| **Base de conocimiento** | `catalog.json` + `preprocess_catalog.py` | Los datos que "aterrizan" (grounding) al agente para que no invente productos. |

---

## 3. Arquitectura y flujo de datos

```
                          ┌──────────────────────────────┐
                          │   FRONTEND (navegador)        │
                          │   index.html — HTML/CSS/JS     │
                          │   · grid de productos          │
                          │   · panel de chat              │
                          └───────────┬──────────────────┘
                                      │  fetch() (HTTP/JSON)
              ┌───────────────────────┼────────────────────────┐
              │                       │                        │
        GET /catalog            POST /chat              POST /analizar-foto
              │                       │                        │
              ▼                       ▼                        ▼
      ┌────────────────────────────────────────────────────────────────┐
      │                    BACKEND — Flask (server.py)                  │
      │                                                                 │
      │  catalog.json ──► system prompt (catálogo compacto) ──► LLM     │
      │       ▲                                  texto │  visión        │
      │       │                                        ▼     ▼          │
      │  preprocess_catalog.py              ┌─────────────────────┐     │
      │  (pipeline de datos, 1 sola vez)    │  OpenRouter (API)   │     │
      │                                     │  · chat (texto)     │     │
      │  SESSION = { room_profile }         │  · visión (Gemini)  │     │
      │  (memoria de la sesión)             └─────────────────────┘     │
      └────────────────────────────────────────────────────────────────┘
```

Idea clave: **el backend no "sabe" de muebles por sí mismo**. El conocimiento vive en
`catalog.json` y se inyecta al modelo dentro del *prompt*. El modelo aporta el
**razonamiento y el lenguaje**; el catálogo aporta los **hechos**.

---

## 4. Los tres "cerebros" de IA

### 4.1 Chatbot conversacional (modelo de lenguaje)

- **Qué hace:** convierte la conversación en una respuesta útil y con personalidad de
  asesor IKEA.
- **Cómo:** en `POST /chat` se arma una lista de mensajes
  `[system_prompt] + historial` y se envía a un **LLM de texto** (vía OpenRouter,
  modelo `poolside/laguna-m.1`).
- **System prompt = la "política" del agente.** Define rol, idioma, tono, longitud,
  y reglas duras como *"recomienda únicamente productos del catálogo, no inventes
  precios"*. Aquí es donde se hace **ingeniería de prompt**.
- **Grounding (aterrizaje):** el *system prompt* incluye un **resumen compacto del
  catálogo** (nombre · categoría · precio · estilo · colores). Es una forma ligera de
  RAG: en vez de buscar en una base vectorial, se le entrega el catálogo entero al
  modelo porque son pocos productos (50). Esto evita las "alucinaciones".
- **Memoria conversacional:** el frontend mantiene el `historial` y lo reenvía en cada
  turno, así el modelo recuerda lo dicho antes.

### 4.2 Visión por computadora (modelo multimodal)

- **Qué hace:** mira la foto del espacio del usuario y la **entiende**.
- **Cómo:** en `POST /analizar-foto` la imagen se codifica (base64) y se manda a un
  **LLM con visión** (`google/gemini-2.5-flash`) junto con una instrucción muy
  específica.
- **Salida estructurada (lo importante):** no se pide texto libre, se pide un **JSON
  con esquema fijo**:

  ```json
  {
    "estilo": "escandinavo",
    "colores_dominantes": ["blanco", "madera"],
    "muebles_detectados": ["sofá", "mesa de centro"],
    "ambiente": "sala",
    "notas": "espacio luminoso, ideal para tonos claros"
  }
  ```

  Esto es **percepción → representación simbólica**: la imagen (datos no
  estructurados) se transforma en variables que el resto del sistema puede usar para
  decidir. A este resultado lo llamamos `room_profile`.

### 4.3 Motor de recomendación / búsqueda (`filtrar_catalogo`)

- **Qué hace:** dado el `room_profile`, **busca** en el catálogo los productos más
  compatibles.
- **Cómo (heurística de puntuación):** recorre los 50 productos y calcula un *score*:
  - `+2` si el **estilo** del producto coincide con el estilo detectado.
  - `+1` por cada **color** en común.
  - Ordena de mayor a menor y devuelve el **top-k** (5).
  - **Fallback:** si nada puntúa, devuelve algunos productos para que la demo no salga
    vacía.
- **Conexión con la clase:** es una **búsqueda informada por una heurística**, no por
  fuerza bruta: la función de *score* hace el papel de la heurística que ordena el
  espacio de soluciones (los productos). Es un **recomendador basado en contenido**
  (content-based filtering): compara atributos del "ítem objetivo" (el cuarto) contra
  los atributos de cada producto.

---

## 5. ¿Cómo "trabaja" el agente?

El asistente se comporta como un **agente sencillo** que sigue el ciclo clásico:

```
   PERCEPCIÓN          ESTADO            POLÍTICA              ACCIÓN
 ┌────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
 │ foto / texto│→ │ room_profile │→ │ system prompt │→ │ responder y/o     │
 │ del usuario │   │ + historial  │   │ + reglas      │   │ recomendar top-k  │
 └────────────┘   └──────────────┘   └──────────────┘   └──────────────────┘
```

1. **Percibe** dos tipos de entrada: lenguaje (texto) e imágenes (visión).
2. **Mantiene estado** en `SESSION["room_profile"]`: una vez que analizó una foto, el
   agente "recuerda" cómo es el espacio y **cambia de comportamiento** —usa
   `build_system_prompt()` en lugar del prompt base— para personalizar todo lo que diga
   después.
3. **Aplica una política** definida por reglas en el *system prompt*: a quién sirve, qué
   puede y qué no puede hacer, cómo reaccionar ante la indecisión ("si el usuario duda,
   ofrece una comparación concreta").
4. **Actúa**: genera texto y, en el caso de la foto, **combina dos herramientas**
   (visión + recomendador) para entregar mensaje + tarjetas de producto.

**¿Qué tan "agente" es?** Es un agente **reactivo y guiado por prompt**, de una sola
sesión. No planifica varios pasos por su cuenta ni decide autónomamente qué herramienta
llamar (eso lo orquesta el backend). Sí cumple lo esencial: percibe, tiene memoria de
estado, sigue una política y produce acciones. Es un buen punto de partida para discutir
en clase la diferencia entre un **asistente prompt-grounded** y un **agente autónomo con
uso de herramientas**.

---

## 6. Backend de productos (la base de conocimiento)

El catálogo es lo que hace creíble al agente. Se construye **una sola vez** con
`preprocess_catalog.py` (un *pipeline de datos*, no se corre en tiempo de chat):

1. **Fuente:** dataset público de Kaggle (`crawlfeeds/ikea-us-products-dataset`,
   ~11,800 productos reales de IKEA US).
2. **Limpieza:** se descartan productos sin precio, sin URL o sin categoría válida; se
   eliminan duplicados.
3. **Categorización:** se toma el departamento (primer nivel de la ruta de migajas) y se
   **traduce al español** (Muebles, Almacenamiento, Iluminación, Decoración…). Esto
   evita categorías vacías, texto corrupto y la fragmentación en decenas de categorías
   de un solo producto.
4. **Muestreo balanceado:** se eligen ~50 productos repartidos entre categorías
   (round-robin) para que la demo tenga variedad y no 50 gabinetes de cocina.
5. **Enriquecimiento de imágenes:** el dataset **no trae imágenes**. Como las URLs de
   imagen de IKEA no son predecibles (llevan un id interno), por cada producto se visita
   su ficha y se extrae la **imagen real** desde la metadata de la página (`og:image`).
   Resultado: 50/50 con foto real.
6. **Inferencia de atributos:** a partir del nombre/descripción se infieren `estilo` y
   `colores` (los atributos que luego usa el recomendador).
7. **Salida:** `catalog.json`, con campos
   `id, nombre, categoria, precio_mxn, descripcion, estilo, colores, image_url, product_url`.

En tiempo de ejecución el servidor solo **lee** ese JSON y lo expone en `GET /catalog`.
Es la **memoria de largo plazo** del sistema (separada de la memoria de sesión).

---

## 7. Frontend (stack, en breve)

No es el foco del proyecto, pero conviene listarlo:

- **HTML + CSS + JavaScript "vanilla"** (sin frameworks como React/Vue). Una sola
  página (`index.html`).
- **Layout de 3 columnas:** filtros · grid de productos · panel del chatbot.
- **Comunicación con el backend:** `fetch()` a los endpoints REST que devuelven JSON.
- **Detalles de UX relevantes al chat:** render de *markdown* en las respuestas del bot,
  miniatura de la foto que sube el usuario, indicador de "escribiendo…", y tarjetas de
  producto con imagen, precio y enlace a la ficha real de IKEA.
- **Servido por el mismo Flask** (`GET /`), así no hay que montar un servidor web aparte.

---

## 8. Glosario rápido (para conectar con la teoría)

- **LLM (Large Language Model):** modelo que predice texto; aquí es el "motor de
  razonamiento y lenguaje".
- **Modelo multimodal:** LLM que además entiende imágenes (visión).
- **System prompt:** instrucciones fijas que definen el comportamiento del agente; es su
  "política".
- **Grounding / RAG-lite:** darle al modelo datos reales (el catálogo) para que sus
  respuestas estén ancladas a hechos y no alucine.
- **Salida estructurada:** forzar al modelo a responder en un formato (JSON) que el
  sistema pueda procesar.
- **Recomendador basado en contenido:** sugiere ítems comparando atributos (estilo,
  color) en vez de usar el comportamiento de otros usuarios.
- **Heurística:** función que estima "qué tan buena" es una opción para guiar la
  búsqueda sin probarlas todas a ciegas.
- **Estado del agente:** lo que el sistema recuerda entre pasos (`room_profile`,
  historial).

---

## 9. Limitaciones y posibles extensiones (buen material para preguntas)

- **Una sola sesión global:** el estado vive en memoria del servidor; con varios
  usuarios se mezclaría. Extensión: sesiones por usuario.
- **El recomendador es simple:** solo estilo + color. Extensión: incluir presupuesto,
  ambiente (sala/recámara) y categoría; o pasar a **embeddings** + búsqueda por
  similitud (vecinos más cercanos) para un recomendador semántico.
- **Grounding por "catálogo completo en el prompt":** funciona con 50 productos; con
  miles habría que usar **RAG real** (base vectorial + recuperación).
- **El agente no planifica ni elige herramientas solo:** el siguiente paso natural es un
  **agente con uso de herramientas** (function calling) que decida cuándo analizar una
  foto, cuándo buscar en el catálogo y cuándo comparar.
- **Visión de una sola pasada:** no segmenta objetos ni mide; usa un LLM multimodal como
  caja negra. Extensión: detección/segmentación con un modelo de visión dedicado.

---

### Resumen en una frase

> Un **LLM razona y conversa**, un **modelo de visión convierte una foto en datos**, una
> **heurística de búsqueda recomienda** productos de una **base de conocimiento
> aterrizada**, y un **system prompt + estado de sesión** orquestan todo como un agente.
