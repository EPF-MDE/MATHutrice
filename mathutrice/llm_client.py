"""
llm_client.py — Client LLM partagé (API compatible OpenAI)

Point d'entrée unique vers le LLM. Le endpoint, la clé et le modèle viennent
uniquement des settings, sans valeur par défaut dans le code :
  - LLM_BASE_URL : endpoint compatible OpenAI
  - LLM_API_KEY  : clé de ce endpoint
  - LLM_MODEL    : modèle servi par ce endpoint
"""

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_missing = [
    name
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
    if not os.getenv(name)
]

if _missing:
    raise ValueError(f"{', '.join(_missing)} missing")

MODEL = os.environ["LLM_MODEL"]

client = OpenAI(
    base_url=os.environ["LLM_BASE_URL"],
    api_key=os.environ["LLM_API_KEY"],
)
