"""LangGraph Workflow untuk VTEKI_Chatbot.
Mengatur percabangan cerdas antara respons otomatis AI (Gemini) dan eskalasi ke Manusia (Agen CS / Admin).
"""

from typing import TypedDict, List, Dict, Any
import re
from langgraph.graph import StateGraph, END


class AgentState(TypedDict):
    message: str
    case_id: str
    history: List[Dict[str, Any]]
    hits: List[Dict[str, Any]]
    reply: str
    status: str
    intent: str
    priority: str
    risk: int
    route: str


def retriever_node(state: AgentState, retrieve_fn) -> Dict[str, Any]:
    """Node 1: Mengambil artikel relevan dari Knowledge Base (RAG)."""
    hits = retrieve_fn(state["message"])
    return {"hits": hits}


def classifier_node(state: AgentState) -> Dict[str, Any]:
    """Node 2: Mengklasifikasikan pesan berdasarkan kata kunci, risiko, dan ketersediaan KB."""
    msg = state["message"].lower()

    # Deteksi keinginan berbicara langsung dengan Admin / Petugas / Manusia
    target_person = r"(admin|agen|cs|manusia|orang|petugas|staf|operator|live agent)"
    talk_to_admin = bool(
        re.search(
            r"\b(bicara|hubungkan|sambungkan|chat|ngomong|kontak|panggil|tanya)\b.*?\b" + target_person + r"\b",
            msg,
        )
        or re.search(
            r"\b(mau|minta|butuh|perlu|ke)\b.*?\b(admin|live agent|operator)\b",
            msg,
        )
    )

    high_risk = bool(
        re.search(
            r"\b(refund|pengembalian dana|blokir|block|ubah data|ganti nomor|transfer uang)\b",
            msg,
        )
    )
    complaint = bool(
        re.search(r"komplain|terpotong|dua kali|ganda|kecewa|gagal tapi saldo", msg)
    )
    hits = state.get("hits", [])

    if talk_to_admin:
        return {
            "route": "talk_to_admin",
            "intent": "Eskalasi Admin",
            "priority": "Tinggi",
            "risk": 0,
            "status": "Menunggu agen",
        }
    elif high_risk:
        return {
            "route": "admin_approval",
            "intent": "Permintaan Layanan",
            "priority": "Tinggi",
            "risk": 1,
            "status": "Menunggu persetujuan",
        }
    elif complaint:
        return {
            "route": "cs_agent",
            "intent": "Komplain",
            "priority": "Tinggi",
            "risk": 0,
            "status": "Menunggu agen",
        }
    elif hits:
        return {
            "route": "gemini_ai",
            "intent": "Inquiry",
            "priority": "Normal",
            "risk": 0,
            "status": "Selesai otomatis",
        }
    else:
        return {
            "route": "cs_agent",
            "intent": "Inquiry",
            "priority": "Normal",
            "risk": 0,
            "status": "Menunggu agen",
        }


def gemini_node(state: AgentState, gemini_fn) -> Dict[str, Any]:
    """Node 3A: AI Gemini menjawab pertanyaan umum berdasarkan dokumen Knowledge Base."""
    answer = gemini_fn(state["message"], state["history"], state["hits"])
    # Jaminan Sitasi: Pastikan sitasi rujukan dokumen knowledge base selalu tercantum
    if state.get("hits"):
        has_kbid = any(f"KB-{h['id']}" in answer for h in state["hits"])
        has_source_word = "sumber:" in answer.lower() or "referensi:" in answer.lower()
        if not (has_kbid and has_source_word):
            citations = " · ".join([f"[{h['title']} (KB-{h['id']})]" for h in state["hits"]])
            answer += f"\n\n📚 Sumber Rujukan: {citations}"
    return {"reply": answer}


def cs_agent_escalation_node(state: AgentState, gemini_fn) -> Dict[str, Any]:
    """Node 3B: Mengarahkan kasus ke antrean Agen CS manusia karena komplain atau dokumen tidak ada."""
    case_id = state["case_id"]
    if state["hits"]:
        # Ada artikel relevan, tapi bernada komplain: berikan jawaban awal + teruskan ke CS
        answer = gemini_fn(state["message"], state["history"], state["hits"])
        answer += f"\n\nTiket {case_id} telah dicatat untuk ditindaklanjuti agen customer service."
    else:
        # Informasi tidak ditemukan di Knowledge Base
        answer = f"Maaf, informasi tersebut tidak tersedia dalam basis data kami. Tiket {case_id} telah diteruskan ke agen customer service kami."
    return {"reply": answer}


def admin_approval_escalation_node(state: AgentState, gemini_fn) -> Dict[str, Any]:
    """Node 3C: Mengarahkan tindakan sensitif (refund/blokir/dll) ke persetujuan Admin/Supervisor."""
    # Dapatkan jawaban faktual/prosedur jika ada di KB
    answer = gemini_fn(state["message"], state["history"], state["hits"])
    answer += "\n\nCatatan: Permintaan ini berisiko tinggi dan perlu ditinjau agen/supervisor. Belum ada transaksi atau perubahan akun yang dijalankan."
    return {"reply": answer}


def talk_to_admin_node(state: AgentState) -> Dict[str, Any]:
    """Node 3D: Merespons permintaan langsung untuk berbicara dengan admin dan menerbitkan tiket."""
    case_id = state["case_id"]
    reply = (
        f"Baik, permintaan Anda untuk berbicara dengan Admin telah kami terima.\n\n"
        f"Tiket Pengaduan: [{case_id}] telah dibuat dan langsung diteruskan ke antrean Admin/Customer Service kami.\n\n"
        f"Mohon menunggu sejenak, petugas kami akan segera bergabung dan membalas percakapan Anda di ruang chat ini."
    )
    return {"reply": reply}


def route_decision(state: AgentState) -> str:
    """Conditional Edge: Memilih node berikutnya berdasarkan hasil klasifikasi."""
    return state.get("route", "gemini_ai")


def build_vteki_graph(retrieve_fn, gemini_fn):
    """Membangun dan mengompilasi StateGraph LangGraph untuk alur VTEKI."""
    workflow = StateGraph(AgentState)

    # Tambahkan nodes
    workflow.add_node("retriever", lambda s: retriever_node(s, retrieve_fn))
    workflow.add_node("classifier", classifier_node)
    workflow.add_node("gemini_ai", lambda s: gemini_node(s, gemini_fn))
    workflow.add_node("cs_agent", lambda s: cs_agent_escalation_node(s, gemini_fn))
    workflow.add_node("admin_approval", lambda s: admin_approval_escalation_node(s, gemini_fn))
    workflow.add_node("talk_to_admin", talk_to_admin_node)

    # Tentukan Entry Point & Alur Edges
    workflow.set_entry_point("retriever")
    workflow.add_edge("retriever", "classifier")

    # Conditional Edges dari Classifier
    workflow.add_conditional_edges(
        "classifier",
        route_decision,
        {
            "gemini_ai": "gemini_ai",
            "cs_agent": "cs_agent",
            "admin_approval": "admin_approval",
            "talk_to_admin": "talk_to_admin",
        },
    )

    # Hubungkan ke Titik Akhir (END)
    workflow.add_edge("gemini_ai", END)
    workflow.add_edge("cs_agent", END)
    workflow.add_edge("admin_approval", END)
    workflow.add_edge("talk_to_admin", END)

    return workflow.compile()
