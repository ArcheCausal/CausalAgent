import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
import chromadb
from langchain_chroma import Chroma
from langchain_community.document_loaders import PyMuPDFLoader, PyPDFLoader
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[1]
MODEL_PATH = BASE_DIR / "models" / "bge-small-zh-v1.5"
SOURCE_DIRECTORY = BASE_DIR / "source"
PERSIST_DIRECTORY = Path(os.environ.get("RAG_VECTOR_DB_DIR", str(BASE_DIR / "db")))
COLLECTION_NAME = os.environ.get("RAG_COLLECTION_NAME", "causal_agent_default")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Agent.knowledge_base.embedding_runtime import resolve_embedding_runtime_config
from Agent.knowledge_base.rag.rag_config import MEDICAL_KNOWLEDGE_BUILD_CONFIG

MEDICAL_CORPUS_PATH = Path(MEDICAL_KNOWLEDGE_BUILD_CONFIG["corpus_path"])


def _load_project_env() -> None:
    """加载项目根目录 .env，便于本地填写 API embedding 配置。"""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)


def _slugify(value: str) -> str:
    """把标题或文件名转换成稳定 doc_id 片段。"""
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value.lower())
    return safe.strip("_") or "unknown_doc"


def _detect_corpus(file_name: str) -> str:
    """根据文件名粗略识别默认因果知识库语料类型。"""
    return "test" if "test" in file_name.lower() else "official"


def _build_base_metadata(file_path: Path, file_type: str) -> Dict[str, object]:
    """为默认因果知识库文档构造基础 metadata。"""
    source_name = file_path.name
    title = file_path.stem
    return {
        "source": str(file_path),
        "source_name": source_name,
        "title": title,
        "doc_id": _slugify(title),
        "file_type": file_type,
        "doc_type": "reference_pdf" if file_type == "pdf" else "note",
        "corpus": _detect_corpus(source_name),
        "language": "zh_or_mixed",
    }


def _load_pdf_documents(file_path: Path) -> List[Document]:
    """加载 PDF 文档，优先使用 PyMuPDFLoader，失败后退回 PyPDFLoader。"""
    loader_errors: List[str] = []
    try:
        import pymupdf  # noqa: F401
    except Exception as exc:
        try:
            import fitz as pymupdf  # type: ignore

            sys.modules["pymupdf"] = pymupdf
        except Exception as fallback_exc:
            loader_errors.append(f"PyMuPDFLoader preflight failed: {exc}; fitz fallback failed: {fallback_exc}")
    else:
        try:
            return PyMuPDFLoader(str(file_path)).load()
        except Exception as exc:
            loader_errors.append(f"PyMuPDFLoader: {exc}")

    if "pymupdf" in sys.modules:
        try:
            return PyMuPDFLoader(str(file_path)).load()
        except Exception as exc:
            loader_errors.append(f"PyMuPDFLoader: {exc}")

    try:
        return PyPDFLoader(str(file_path)).load()
    except Exception as exc:
        loader_errors.append(f"PyPDFLoader: {exc}")

    raise RuntimeError(" | ".join(loader_errors))


def _load_default_documents() -> List[Document]:
    """加载当前默认 Pearl/因果知识库 source 目录下的 TXT/PDF 文档。"""
    documents: List[Document] = []
    print(f"将从以下目录加载默认知识库文档: {SOURCE_DIRECTORY}")

    for root, _, files in os.walk(SOURCE_DIRECTORY):
        root_path = Path(root)
        for file_name in files:
            file_path = root_path / file_name
            file_type = file_path.suffix.lower().lstrip(".")
            base_metadata = _build_base_metadata(file_path, file_type)

            if file_path.suffix.lower() == ".txt":
                try:
                    text = file_path.read_text(encoding="utf-8")
                    documents.append(Document(page_content=text, metadata=base_metadata))
                    print(f"成功加载 TXT 文件: {file_path}")
                except Exception as exc:
                    print(f"加载 TXT 文件 {file_path} 时出错: {exc}")
                continue

            if file_path.suffix.lower() == ".pdf":
                try:
                    pdf_docs = _load_pdf_documents(file_path)
                    for page_doc in pdf_docs:
                        merged_metadata = dict(base_metadata)
                        merged_metadata.update(page_doc.metadata or {})
                        merged_metadata["source"] = str(file_path)
                        merged_metadata["source_name"] = base_metadata["source_name"]
                        merged_metadata["title"] = merged_metadata.get("title") or base_metadata["title"]
                        merged_metadata["doc_id"] = base_metadata["doc_id"]
                        merged_metadata["file_type"] = "pdf"
                        merged_metadata["doc_type"] = "reference_pdf"
                        merged_metadata["corpus"] = base_metadata["corpus"]
                        page_doc.metadata = merged_metadata
                    documents.extend(pdf_docs)
                    print(f"成功加载 PDF 文件: {file_path}，页数: {len(pdf_docs)}")
                except Exception as exc:
                    print(f"加载 PDF 文件 {file_path} 时出错: {exc}")

    return documents


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """读取 JSONL 文件。"""
    rows = []
    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            row = json.loads(stripped)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number} must be a JSON object.")
            rows.append(row)
    return rows


def _load_medical_documents(corpus_path: Path = MEDICAL_CORPUS_PATH) -> List[Document]:
    """加载 active medical corpus，正文只使用 evidence text，避免答案泄漏。"""
    if not corpus_path.exists():
        raise FileNotFoundError(f"medical corpus not found: {corpus_path}")
    documents = []
    for row in _load_jsonl(corpus_path):
        doc_id = str(row["doc_id"])
        source_dataset = row.get("source_dataset", "medical_corpus")
        metadata = {
            "doc_id": doc_id,
            "source": source_dataset,
            "source_name": source_dataset,
            "title": doc_id,
            "page": row.get("page", ""),
            "doc_type": "medical_context",
            "corpus": "medical",
            "dataset": row.get("dataset", "medical"),
            "reference": row.get("reference", ""),
            "source_row_index": row.get("source_row_index"),
        }
        documents.append(Document(page_content=row["text"], metadata=metadata))
    print(f"成功加载医疗 corpus: {corpus_path}，文档数: {len(documents)}")
    return documents


def _attach_chunk_metadata(split_docs: List[Document]) -> List[Document]:
    """为切分后的文档补齐 chunk_index、chunk_id 和 section。"""
    chunk_counts: Dict[str, int] = {}
    for document in split_docs:
        metadata = dict(document.metadata or {})
        doc_id = str(metadata.get("doc_id", "unknown_doc"))
        page = metadata.get("page")
        page_fragment = page if page not in (None, "") else "na"
        chunk_index = chunk_counts.get(doc_id, 0)
        chunk_counts[doc_id] = chunk_index + 1
        metadata["chunk_index"] = chunk_index
        metadata["chunk_id"] = f"{doc_id}#p{page_fragment}#c{chunk_index}"
        metadata["section"] = metadata.get("section", "")
        document.metadata = metadata
    return split_docs


def _build_default_embedding() -> HuggingFaceEmbeddings:
    """构造默认因果知识库的本地 embedding。"""
    print("正在加载本地 embedding 模型...")
    return HuggingFaceEmbeddings(
        model_name=str(MODEL_PATH),
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


def _required_env(name: str) -> str:
    """读取必需环境变量。"""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _build_medical_embedding() -> Any:
    """按 RAG_EMBEDDING_PROVIDER 构造 active medical 知识库 embedding。"""
    _load_project_env()
    embedding_config = resolve_embedding_runtime_config()
    if embedding_config["mode"] == "local":
        print(f"正在加载医疗本地 embedding: {embedding_config['model']}")
        return HuggingFaceEmbeddings(
            model_name=embedding_config["path"],
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
    if embedding_config["status"] != "ready":
        raise ValueError(embedding_config["message"])
    print(f"正在加载医疗 API embedding: {embedding_config['model']}")
    return OpenAIEmbeddings(
        api_key=_required_env(embedding_config["api_key_env"]),
        base_url=_required_env(embedding_config["base_url_env"]),
        model=embedding_config["model"],
        tiktoken_enabled=False,
        check_embedding_ctx_length=False,
    )


def _profile_settings(profile: str) -> Dict[str, Any]:
    """返回指定 profile 的加载器、embedding 和切分配置。"""
    if profile == "default":
        return {
            "documents": _load_default_documents(),
            "embedding": _build_default_embedding(),
            "chunk_size": 500,
            "chunk_overlap": 100,
            "collection_name": COLLECTION_NAME,
            "persist_directory": PERSIST_DIRECTORY,
            "batch_size": 0,
        }
    if profile == "medical":
        persist_directory = Path(
            os.environ.get("RAG_VECTOR_DB_DIR", MEDICAL_KNOWLEDGE_BUILD_CONFIG["persist_directory"])
        )
        return {
            "documents": _load_medical_documents(Path(MEDICAL_KNOWLEDGE_BUILD_CONFIG["corpus_path"])),
            "embedding": _build_medical_embedding(),
            "chunk_size": int(MEDICAL_KNOWLEDGE_BUILD_CONFIG["chunk_size"]),
            "chunk_overlap": int(MEDICAL_KNOWLEDGE_BUILD_CONFIG["chunk_overlap"]),
            "collection_name": os.environ.get(
                "RAG_COLLECTION_NAME",
                str(MEDICAL_KNOWLEDGE_BUILD_CONFIG["collection_name"]),
            ),
            "persist_directory": persist_directory,
            "batch_size": int(MEDICAL_KNOWLEDGE_BUILD_CONFIG["embedding_config"]["batch_size"]),
        }
    raise ValueError(f"Unsupported build profile: {profile}")


def _existing_collection_count(persist_directory: Path, collection_name: str) -> Optional[int]:
    """返回目标 Chroma collection 现有向量数量；collection 不存在时返回 None。"""
    if not persist_directory.exists():
        return None
    client = chromadb.PersistentClient(path=str(persist_directory))
    collection_names = {collection.name for collection in client.list_collections()}
    if collection_name not in collection_names:
        return None
    return client.get_collection(collection_name).count()


def _ensure_append_allowed(settings: Dict[str, Any], persist_directory: Path, allow_append: bool) -> None:
    """阻止误向非空 collection 追加写入，除非用户显式允许追加。"""
    collection_name = str(settings["collection_name"])
    existing_count = _existing_collection_count(persist_directory, collection_name)
    if not existing_count:
        return
    if allow_append:
        return
    message = (
        "Refusing to append to non-empty Chroma collection. "
        f"persist_directory={persist_directory}, collection={collection_name}, existing_count={existing_count}. "
        "Use --allow-append only when intentional."
    )
    raise ValueError(message)


def _write_chroma_documents(settings: Dict[str, Any], documents: List[Document], persist_directory: Path) -> Chroma:
    """按配置把切分后的文档写入 Chroma，避免 API embedding 单批超限。"""
    batch_size = int(settings.get("batch_size") or 0)
    if batch_size <= 0:
        return Chroma.from_documents(
            documents,
            settings["embedding"],
            persist_directory=str(persist_directory),
            collection_name=settings["collection_name"],
        )

    db = Chroma(
        persist_directory=str(persist_directory),
        embedding_function=settings["embedding"],
        collection_name=settings["collection_name"],
    )
    for start in range(0, len(documents), batch_size):
        batch = documents[start : start + batch_size]
        db.add_documents(batch)
        written = min(start + batch_size, len(documents))
        print(f"已写入 chunk: {written}/{len(documents)}")
    return db


def build(profile: str = "default", allow_append: bool = False) -> Dict[str, Any]:
    """构建知识库：加载文档 -> 切分 -> embedding -> 写入原持久化目录。"""
    settings = _profile_settings(profile)
    persist_directory = Path(settings["persist_directory"])
    _ensure_append_allowed(settings, persist_directory, allow_append)
    print(f"开始构建知识库，profile={profile}，persist_directory={persist_directory}")
    documents = settings["documents"]
    if not documents:
        raise ValueError("未加载到任何文档，请检查数据源路径。")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=int(settings["chunk_size"]),
        chunk_overlap=int(settings["chunk_overlap"]),
    )
    split_docs = _attach_chunk_metadata(splitter.split_documents(documents))
    print(f"文档已切分为 {len(split_docs)} 个 chunk。")

    persist_directory.mkdir(parents=True, exist_ok=True)
    db = _write_chroma_documents(settings, split_docs, persist_directory)
    if hasattr(db, "persist"):
        db.persist()

    result = {
        "status": "pass",
        "profile": profile,
        "persist_directory": str(persist_directory.resolve()),
        "source_doc_count": len(documents),
        "chunk_count": len(split_docs),
        "collection_name": settings["collection_name"],
        "allow_append": allow_append,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="Build CausalAgent knowledge base.")
    parser.add_argument(
        "--profile",
        choices=["default", "medical"],
        default=os.environ.get("KNOWLEDGE_BUILD_PROFILE", "default"),
        help="default builds Pearl/causal source files; medical builds the active PubMedQA corpus into the same persist directory.",
    )
    parser.add_argument(
        "--allow-append",
        action="store_true",
        help="Allow writing to a non-empty Chroma collection. By default the build fails to prevent duplicate chunks.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    build(profile=args.profile, allow_append=args.allow_append)
