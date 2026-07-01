# Local AI Speech-to-Speech Assistant: Project Blueprint
**Date:** July 1, 2026
**Target Architecture:** Apple Silicon (Mac M1) - Unified Memory Optimized
**Focus:** AI Engineering & Agentic System Design

## 1. Project Objective and Mission
**Mission:** To build a fully local, zero-cloud, low-latency conversational AI agent. The system must operate as a seamless speech-to-speech interface, utilizing a **Hybrid RAG Memory System** that respects explicit user commands while intelligently and autonomously persisting critical context over time. 

**Core Philosophies:**
- **Absolute Privacy:** All inference, embeddings, and transcription must happen locally on the hardware. No API calls to external cloud providers.
- **Compute Efficiency:** Tailor all heavy computational loads (STT and LLM inference) to utilize Apple's MLX framework and Metal Performance Shaders (MPS).
- **Agentic Autonomy:** Move beyond simple chatbots by implementing tool-calling capabilities, allowing the AI to manage its own long-term memory dynamically.

---

## 2. The 2026 Tech Stack
This stack is explicitly curated to avoid CUDA/NVIDIA bottlenecks and maximize the Mac M1 architecture.

### The Brain & Inference
- **LLM Runner:** [Ollama](https://ollama.com/) (Localhost API at `11434`).
- **Conversational Model:** `llama3.2:3b` (or equivalent lightweight Qwen model optimized for tool-calling and rapid Q&A).
- **Embedding Model:** `nomic-embed-text` (via Ollama) for generating high-quality vector representations locally.

### The Memory (RAG)
- **Vector Database:** `chromadb` (Running in persistent local mode, saving to `./vault_db`). Replaces the inefficient `.txt` parsing method for instant O(1) semantic retrieval.

### The Audio Pipeline (Ears & Mouth)
- **Speech-to-Text (STT):** `mlx-whisper` using `whisper-tiny` or `whisper-base`. Runs on the Mac GPU via Apple's MLX framework for sub-second transcription.
- **Audio Capture:** `sounddevice` and `numpy` for handling microphone streams and Voice Activity Detection (VAD).
- **Text-to-Speech (TTS):** macOS native `NSSpeechSynthesizer` (via `os.system("say")` or `subprocess`) for the MVP phase to ensure zero-latency setup. *(See Future Enhancements for the upgrade path).*

---

## 3. Architecture Logic: The Hybrid Router
The core loop continuously listens (via spacebar toggle or VAD), transcribes the audio, and routes the string through a dual-layered cognitive pipeline:

### Layer A: The Manual Override (Explicit Command)
If the transcribed text `lower().startswith("insert info")`:
1. **Extract:** Slice the string to remove the trigger phrase.
2. **Embed:** Pass the payload directly to `nomic-embed-text`.
3. **Store:** Upsert the resulting vector and text document into ChromaDB.
4. **Acknowledge:** Trigger TTS to say "Information secured."
5. *Bypass the conversational LLM entirely to save compute and time.*

### Layer B: The Agentic Loop (Autonomous Tool Calling)
If the transcribed text does **not** contain the explicit command:
1. **Recall:** Query ChromaDB with the user's string. Retrieve the top 3 (K=3) relevant memory chunks.
2. **Contextualize:** Inject these memories into the `llama3.2:3b` system prompt as `<context>`.
3. **Evaluate & Act (Tool Calling):** Pass the prompt to the LLM with a predefined JSON tool schema: `save_to_memory(fact: str)`.
   - *System Instruction:* "If the user mentions a new, persistent fact (e.g., 'I am moving', 'My flight is at 5 PM'), invoke the `save_to_memory` tool. Otherwise, respond conversationally."
4. **Execute:** If the tool is invoked by the LLM, the Python backend executes the ChromaDB upsert natively.
5. **Respond:** Stream the LLM's final response text back to the TTS engine.

---

## 4. Implementation Phasing & Guidelines
**To the AI Coder:** Follow this sequence strictly when scaffolding the repository.

- **Phase 1: Environment & Core I/O**
  - Set up `requirements.txt` (`mlx-whisper`, `chromadb`, `sounddevice`, `ollama`, `numpy`).
  - Build `audio_handler.py` to manage clean microphone recording and basic macOS TTS output. Test transcription latency.

- **Phase 2: The Memory Manager (`memory_manager.py`)**
  - Initialize the persistent ChromaDB client.
  - Create standard functions: `upsert_memory(text)` and `query_memory(text, k=3)`.
  - Connect the Ollama `nomic-embed-text` endpoint as the embedding function for ChromaDB.

- **Phase 3: The LLM Router (`llm_router.py`)**
  - Implement the "Hybrid Router" logic.
  - Format the tool-calling schema exactly as required by the Ollama API Python client.
  - Ensure tool execution handles exceptions gracefully (e.g., if the LLM hallucinates a parameter).

- **Phase 4: The Main Loop (`main.py`)**
  - Tie all modules together in a `while True:` loop.

---

## 5. Future Enhancements (Open Possibilities)
Once the baseline blueprint is stable, consider these modular upgrades:

1. **TTS Upgrade:** Swap the robotic macOS voice for **MeloTTS** or **Supertonic 3** (running via ONNX runtime) for emotive, ultra-realistic voice cloning locally.
2. **Vision Integration:** Integrate `llava` via Ollama. Modify the main loop to capture a webcam frame, allowing the agent to answer questions about the user's physical environment.
3. **Multi-Agent Handoff:** Expand the tool-calling schema so the primary assistant can route complex coding or math queries to specialized local models (e.g., `deepseek-coder`).
4. **Continuous VAD:** Replace the push-to-talk mechanism with Silero VAD for continuous, natural wake-word detection.
