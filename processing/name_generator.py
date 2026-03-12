"""Generate a dreamlike name for each print via Ollama, with a fallback list."""
import random
import threading

import requests

_OLLAMA_URL = "http://localhost:11434/api/generate"
_TEXT_MODEL = "llama3.2:1b"  # pull with: ollama pull llama3.2:1b (~1 GB)

_FALLBACK_NAMES = [
    "Cinder", "Vessel", "Hinge", "Fleece", "Margin",
    "Flicker", "Lapse", "Brine", "Tender", "Gust",
    "Ember", "Hollow", "Drift", "Canopy", "Settle",
    "Timber", "Linen", "Gravel", "Current", "Mantle",
    "Fallow", "Cobble", "Wander", "Shelter", "Amber",
]

_TEXT_PROMPT = (
    "Choose a single ordinary English word to use as a portrait title at an art event. "
    "It should feel quietly poetic — a common noun or adjective that takes on new meaning as a name. "
    "Warm, grounded, human. Like naming a feeling or a moment. "
    "Examples: Cinder, Vessel, Hinge, Fleece, Margin, Flicker, Lapse, Brine, Tender, Gust. "
    "Do NOT use any word related to gender, race, ethnicity, religion, or body parts. "
    "Do NOT use names of real people or places. "
    "Reply with ONLY the word. Capitalise the first letter only. Letters only, no numbers or symbols."
)


def _sanitise(raw: str) -> str:
    clean = "".join(c for c in raw.strip() if c.isalpha())[:12]
    return clean.capitalize() if clean else ""


def _warmup_model() -> None:
    try:
        requests.post(
            _OLLAMA_URL,
            json={"model": _TEXT_MODEL, "prompt": " ", "stream": False,
                  "options": {"num_predict": 1}},
            timeout=60,
        )
        print(f"[name] warmed up {_TEXT_MODEL}")
    except Exception as e:
        print(f"[name] warmup failed: {e}")


def warmup() -> None:
    """Pre-load the text model in the background so first request is fast."""
    threading.Thread(target=_warmup_model, daemon=True).start()


def generate_name(image_b64: str | None = None) -> str:  # noqa: ARG001
    """Generate a dreamlike name via llama3.2:1b, falling back to a curated list."""
    try:
        resp = requests.post(
            _OLLAMA_URL,
            json={
                "model": _TEXT_MODEL,
                "prompt": _TEXT_PROMPT,
                "stream": False,
                "options": {
                    "temperature": 1.4,
                    "top_p": 0.95,
                    "repeat_penalty": 1.3,
                    "seed": random.randint(1, 2**31),
                    "num_predict": 15,
                },
            },
            timeout=30,
        )
        name = _sanitise(resp.json().get("response", ""))
        if name:
            print(f"[name] {_TEXT_MODEL}: {name}")
            return name
    except Exception as e:
        print(f"[name] model failed: {e}")

    name = random.choice(_FALLBACK_NAMES)
    print(f"[name] fallback: {name}")
    return name
