from langchain_core.tools import tool
from utils import load_vector_store


def get_rag_tool():
    chroma_db = load_vector_store()
    @tool
    def search_pine_syntax(query: str):
        """Search the Pine syntax reference database to find the correct Pine syntax for a legacy template variable or function.
        
        Args:
            query: The legacy variable or function name to find the Pine syntax equivalent for.
        """

        results = chroma_db.similarity_search(query, k=5)
        return "\n\n---\n\n".join([doc.page_content for doc in results])
    return search_pine_syntax