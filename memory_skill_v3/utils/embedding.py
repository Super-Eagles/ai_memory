from .. import config
import logging

logging.getLogger("sentence_transformers").setLevel(logging.ERROR)

_local_model = None


def _service_url() -> str:
    return (getattr(config, "EMBED_SERVICE_URL", None) or "").rstrip("/")


def _remote_embed(text: str) -> list:
    import requests

    url = _service_url()
    try:
        resp = requests.post(
            f"{url}/embed",
            json={"text": text},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    except Exception as exc:
        raise RuntimeError(
            f"[memory-skill] Embedding service unreachable at {url}. "
            "Run `python embed_server.py` first, or unset MEMORY_EMBED_SERVICE_URL "
            "to fall back to local mode."
        ) from exc


def _remote_embed_batch(texts: list) -> list:
    import requests

    url = _service_url()
    try:
        resp = requests.post(
            f"{url}/embed_batch",
            json={"texts": texts},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["embeddings"]
    except Exception as exc:
        raise RuntimeError(
            f"[memory-skill] Embedding service unreachable at {url}. "
            "Run `python embed_server.py` first, or unset MEMORY_EMBED_SERVICE_URL "
            "to fall back to local mode."
        ) from exc


def _get_local_model():
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer

        _local_model = SentenceTransformer(config.EMBED_MODEL)
    return _local_model


def embed(text: str) -> list:
    if _service_url():
        return _remote_embed(text)
    vec = _get_local_model().encode(text, normalize_embeddings=True)
    return vec.tolist()


def embed_batch(texts: list) -> list:
    if _service_url():
        return _remote_embed_batch(texts)
    vecs = _get_local_model().encode(
        texts, normalize_embeddings=True, batch_size=32
    )
    return [v.tolist() for v in vecs]


def ping_service() -> bool:
    url = _service_url()
    if not url:
        return True

    import requests

    try:
        resp = requests.get(f"{url}/health", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False
