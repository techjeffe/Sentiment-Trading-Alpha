"""
Ollama Client for Scoring
Integrates with STA's existing Ollama setup.
"""

import os
import json
import requests
from typing import Dict, List, Optional, Any


def get_ollama_url() -> str:
    """Get Ollama URL from environment or default."""
    return os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")


def get_ollama_model() -> str:
    """Get Ollama model from environment or default."""
    return os.getenv("OLLAMA_MODEL", "llama3.2:3b")


def call_ollama(system_prompt: str, user_message: str, temperature: float = 0.3) -> Optional[str]:
    """
    Call Ollama API for scoring.
    
    Args:
        system_prompt: System prompt for the AI
        user_message: User message (JSON string)
        temperature: Temperature for generation
        
    Returns:
        AI response text or None if failed
    """
    ollama_url = get_ollama_url()
    model = get_ollama_model()
    
    payload = {
        "model": model,
        "system": system_prompt,
        "prompt": user_message,
        "temperature": temperature,
        "stream": False,
        "format": "json"  # Request JSON response
    }
    
    try:
        response = requests.post(ollama_url, json=payload, timeout=60)
        response.raise_for_status()
        
        result = response.json()
        return result.get("response", "")
        
    except requests.RequestException as e:
        print(f"Ollama API error: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"Ollama response parse error: {e}")
        return None


def score_with_ollama(symbol_summaries: List[Dict]) -> Optional[Dict[str, Any]]:
    """
    Score symbols using Ollama.
    
    Args:
        symbol_summaries: List of symbol summary dicts
        
    Returns:
        Parsed JSON response or None
    """
    from services.scoring.advanced_scorer import build_scoring_prompt
    
    system_prompt = build_scoring_prompt(symbol_summaries)
    user_message = json.dumps(symbol_summaries, indent=2)
    
    print(f"Calling Ollama for scoring {len(symbol_summaries)} symbols...")
    
    response_text = call_ollama(system_prompt, user_message, temperature=0.3)
    
    if not response_text:
        return None
    
    try:
        # Parse JSON response
        result = json.loads(response_text)
        return result
    except json.JSONDecodeError as e:
        print(f"Failed to parse Ollama response as JSON: {e}")
        print(f"Response text: {response_text[:500]}")
        return None


if __name__ == "__main__":
    # Test Ollama connection
    print("Testing Ollama connection...")
    print(f"Ollama URL: {get_ollama_url()}")
    print(f"Model: {get_ollama_model()}\n")
    
    # Simple test
    test_prompt = "Say 'Ollama is working' in JSON format: {\"status\": \"...\"}"
    response = call_ollama("You are a helpful assistant.", test_prompt)
    
    if response:
        print(f"Ollama response: {response}")
    else:
        print("Ollama call failed")
