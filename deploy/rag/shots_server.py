"""RAG 참고 사례(shots) 서버. GPU 호스트에서 백엔드 컨테이너 밖으로 실행합니다.

실행: /home1/ncloud/smishing/.venv/bin/python deploy/rag/shots_server.py
환경 변수: RAG_HOST(0.0.0.0), RAG_PORT(8100), RAG_QUERY_DIR, RAG_CORPUS,
RAG_EMBED_MODEL.
"""

import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, os.environ.get("RAG_QUERY_DIR", "/home1/ncloud/smishing/model"))
from rag_query import (  # noqa: E402
    create_windows,
    load_corpus,
    load_or_create_embeddings,
    normalize_whitespace,
    top_k_per_window,
)
from sentence_transformers import SentenceTransformer  # noqa: E402

MAX_BODY = 64 * 1024
MAX_CHARS = 20000
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("gk-rag")

corpus_path = Path(
    os.environ.get("RAG_CORPUS", "/home1/ncloud/smishing/model/out/train.jsonl")
)
normal, fraud = load_corpus(corpus_path)
try:
    device = "cuda"
    model = SentenceTransformer(
        os.environ.get("RAG_EMBED_MODEL", "intfloat/multilingual-e5-base"),
        device=device,
    )
except Exception:
    device = "cpu"
    model = SentenceTransformer(
        os.environ.get("RAG_EMBED_MODEL", "intfloat/multilingual-e5-base"),
        device=device,
    )
normal_matrix, fraud_matrix = load_or_create_embeddings(
    model, normal, fraud, corpus_path.parent / "rag_corpus_cache.npz"
)
lock = threading.Lock()


def find_shots(text: str, sms: bool, k: int) -> list[dict[str, Any]]:
    """문자는 전체를 한 창으로, 통화는 슬라이딩 창으로 유사 사례를 찾습니다."""
    text = normalize_whitespace(text[:MAX_CHARS])
    windows = [text] if sms else create_windows(text)
    with lock:
        vectors = model.encode(
            ["query: " + w for w in windows],
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).astype("float32")
    hits = top_k_per_window(vectors, normal, normal_matrix, fraud, fraud_matrix, k)
    return [
        {"text": h["text"], "label": int(h["label"]), "sim": float(h["sim"])}
        for h in hits
    ]


class Handler(BaseHTTPRequestHandler):
    """/health 와 /shots 요청을 처리합니다."""

    def reply(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            return self.reply(404, {"error": "not found"})
        self.reply(
            200,
            {"ok": True, "normal": len(normal), "fraud": len(fraud), "device": device},
        )

    def do_POST(self) -> None:
        if self.path != "/shots":
            return self.reply(404, {"error": "not found"})
        try:
            size = int(self.headers.get("Content-Length", ""))
            if not 0 < size <= MAX_BODY:
                raise ValueError
            payload = json.loads(self.rfile.read(size))
            text, sms = payload["text"], bool(payload.get("sms", True))
            k = max(1, min(10, int(payload.get("k", 3))))
            if not isinstance(text, str) or not text.strip():
                raise ValueError
        except Exception:  # 잘못된 JSON·형식은 모두 400
            return self.reply(400, {"error": "bad request"})
        start = time.perf_counter()
        shots = find_shots(text, sms, k)
        log.info(
            "shots count=%d ms=%d", len(shots), (time.perf_counter() - start) * 1000
        )
        self.reply(200, {"shots": shots})

    def log_message(self, format: str, *args: Any) -> None:
        """기본 접근 로그를 끕니다."""


if __name__ == "__main__":
    address = (
        os.environ.get("RAG_HOST", "0.0.0.0"),
        int(os.environ.get("RAG_PORT", "8100")),
    )
    log.info("listening %s:%d device=%s", *address, device)
    ThreadingHTTPServer(address, Handler).serve_forever()
