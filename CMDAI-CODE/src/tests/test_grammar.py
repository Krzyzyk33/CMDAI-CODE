import os
import sys
import json
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from main import load_state
from llama import LlamaModel
from tools import TOOLS_DEFINITIONS

PROMPTS = [
    "Wylistuj mi zawartość katalogu D:\\testy",
    "Stwórz plik test.txt i wpisz w nim 'hello'",
    "Odpal komendę ping google.com",
    "Przeszukaj pliki w projekcie używając grep pod kątem słowa 'def stream_chat'",
    "Utwórz katalog src/tests komendą bash",
    "Odczytaj zawartość pliku src/main.py",
    "Zmodyfikuj plik config.json zamieniając 'a' na 'b'",
    "Pokaż mi logi z git status w katalogu głównym",
    "Zapisz do pliku test_output.log wynik komendy ls",
    "Napisz skrypt python, który wypisze hello world i zapisz go jako hello.py"
]

@pytest.fixture(scope="module")
def model():
    state = load_state()
    model_path = state.get("active_api_model", {}).get("name", "")
    if not model_path:
        model_path = state.get("model_path", "")
        
    if not model_path or not os.path.exists(model_path):
        pytest.skip("Brak skonfigurowanego lokalnego modelu do testów.")
        
    n_gpu_layers = state.get("n_gpu_layers", -1)
    return LlamaModel(model_path, n_gpu_layers=n_gpu_layers, n_ctx=2048)

def evaluate_run(model, grammar_path):
    success_count = 0
    
    for prompt in PROMPTS:
        messages = [
            {"role": "system", "content": "You are an AI coding assistant. You must respond with a tool call in JSON format to complete the user request."},
            {"role": "user", "content": prompt}
        ]
        
        try:
            full_content = ""
            for chunk_content, _, tool_calls in model.stream_chat(messages, tools=TOOLS_DEFINITIONS, grammar_path=grammar_path):
                if chunk_content:
                    full_content += chunk_content
                    
            try:
                json_str = full_content.strip()
                if not json_str.startswith("{"):
                    start = json_str.find("{")
                    if start != -1:
                        json_str = json_str[start:]
                
                parsed = json.loads(json_str)
                if "name" in parsed and "arguments" in parsed:
                    success_count += 1
            except Exception:
                pass
        except Exception as e:
            print(f"Błąd podczas inferencji: {e}")
            
    return success_count

def test_without_grammar(model):
    print("\n--- Test BASELINE (Bez GBNF) ---")
    success = evaluate_run(model, None)
    print(f"Sukcesy: {success} / {len(PROMPTS)}")
    
def test_with_grammar(model):
    print("\n--- Test GBNF (Z Gramatyką) ---")
    grammar_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "grammars", "tool_call.gbnf"))
    if not os.path.exists(grammar_path):
        pytest.skip("Plik tool_call.gbnf nie istnieje.")
        
    success = evaluate_run(model, grammar_path)
    print(f"Sukcesy: {success} / {len(PROMPTS)}")
