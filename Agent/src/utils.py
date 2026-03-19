import os
from pathlib import Path
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from dotenv import load_dotenv

load_dotenv()

_DEFAULT_CHROMA_DIR = str(Path(__file__).resolve().parent.parent / "chroma_db")

def build_vector_store(ground_truth_path: str, persist_directory: str = _DEFAULT_CHROMA_DIR):
    """Build a chromadb vector store from the ground truth syntax reference file.
    Args:
        ground_truth_path: Path to the ground truth syntax reference file.
        persist_directory: Directory where the chromadb vector store will be persisted.
    Returns:
        A Chroma vector store instance.
    """
    chunks = []

    with open(ground_truth_path, "r") as f:
        file_content = f.read()

    if not file_content:
        print(f"Warning: {ground_truth_path} is empty.")
        return None

    print(f"Loaded {len(file_content)} characters from {ground_truth_path}")

    splitter = RecursiveCharacterTextSplitter(
        separators=["=" * 80, "-" * 80, "\n\n", "\n"],
        chunk_size=500,
        chunk_overlap=50
    )

    chunk = splitter.create_documents(
    texts=[file_content],
    metadatas=[{"source": "pine_syntax_reference", "doc_type": "ground_truth"}]
)

    chunks.extend(chunk)
    print(f"  → {len(chunks)} chunks")

    print(f"\nTotal chunks: {len(chunks)}")
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    vector_store = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=persist_directory,
        collection_name="ground_truth_syntax"
    )
    print(f"Vector store built and persisted to {persist_directory}")
    return vector_store

def load_vector_store(persist_directory: str = _DEFAULT_CHROMA_DIR):
    """Load the chromadb vector store from the specified directory.
    Args:
        persist_directory: Directory where the chromadb vector store is persisted.
    Returns:
        A Chroma vector store instance.
    """
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    return Chroma(
        persist_directory=persist_directory,
        embedding_function=embeddings,
        collection_name="ground_truth_syntax"
    )