"""
embed_server.py — 向量嵌入独立服务
====================================
职责：加载一次 MiniLM 模型，通过 HTTP 提供 embed / embed_batch 接口。
其余所有进程只需 HTTP 调用，不再各自加载 ~470 MB 模型。

启动方式：
    python embed_server.py

环境变量（可选）：
    MEMORY_EMBED_MODEL   模型名称，默认 paraphrase-multilingual-MiniLM-L12-v2
    EMBED_SERVER_HOST    监听地址，默认 127.0.0.1
    EMBED_SERVER_PORT    监听端口，默认 7731
"""

import os
import logging
import sys

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ── 日志 ──────────────────────────────────────────────────────────────────────
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [embed-server] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("embed-server")

# ── 配置 ──────────────────────────────────────────────────────────────────────
MODEL_NAME = os.environ.get(
    "MEMORY_EMBED_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"
)
HOST = os.environ.get("EMBED_SERVER_HOST", "127.0.0.1")
PORT = int(os.environ.get("EMBED_SERVER_PORT", "7731"))

# ── 应用 & 全局模型 ───────────────────────────────────────────────────────────
app = FastAPI(title="Embedding Service", version="1.0.0")
_model = None  # 在 startup 事件中赋值


@app.on_event("startup")
def _load_model():
    """进程启动时加载模型并做一次预热推理，之后接口即可无延迟响应。"""
    global _model
    log.info("Loading model: %s  (first run downloads ~470 MB)", MODEL_NAME)
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
        _model.encode("warmup", normalize_embeddings=True)  # 预热，避免首次请求慢
        log.info("Model ready. Listening on http://%s:%s", HOST, PORT)
    except Exception as exc:
        log.error("Failed to load model: %s", exc)
        sys.exit(1)


# ── 请求/响应 Schema ──────────────────────────────────────────────────────────
class EmbedRequest(BaseModel):
    text: str


class EmbedBatchRequest(BaseModel):
    texts: list[str]
    batch_size: int = 32


# ── 接口 ──────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    """健康探针。调用方可在启动前 poll 这个接口，确认服务就绪后再继续。"""
    if _model is None:
        raise HTTPException(status_code=503, detail="model not ready")
    return {"status": "ok", "model": MODEL_NAME}


@app.post("/embed")
def embed(req: EmbedRequest):
    """对单条文本生成归一化向量。"""
    if _model is None:
        raise HTTPException(status_code=503, detail="model not ready")
    vec = _model.encode(req.text, normalize_embeddings=True)
    return {"embedding": vec.tolist()}


@app.post("/embed_batch")
def embed_batch(req: EmbedBatchRequest):
    """批量生成归一化向量，比逐条调用 /embed 快约 3–5×。"""
    if _model is None:
        raise HTTPException(status_code=503, detail="model not ready")
    if not req.texts:
        return {"embeddings": []}
    vecs = _model.encode(
        req.texts,
        normalize_embeddings=True,
        batch_size=req.batch_size,
    )
    return {"embeddings": [v.tolist() for v in vecs]}


# ── 入口 ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="warning",   # uvicorn 自身日志收敛，避免与上面的日志重复
    )
