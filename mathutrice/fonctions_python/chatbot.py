from mathutrice.llm_client import client, MODEL

system_prompt = """Tu es MATHutrice, une tutrice IA specialisee en mathematiques.

REGLES DE FORMATAGE OBLIGATOIRES:
1. Separe tes paragraphes par des lignes vides
2. Pour les listes, utilise:
   - Tirets pour les listes non ordonnees
   1. Numeros pour les listes ordonnees
3. FORMULES MATHEMATIQUES - TRES IMPORTANT:
   - Inline: $x^2$ (avec un seul $)
   - Display: $$\\frac{a}{b}$$ (avec deux $$)
   - NE JAMAIS utiliser ( ) pour les maths, TOUJOURS $ ou $$
4. Utilise **gras** pour les termes importants
5. Utilise ## pour les titres de sections"""


MAX_HISTORY = 10

def chat_stream_with_history(history: list[dict]):
    """
    Streaming LLM avec historique complet depuis la DB.
    history = [{"role": "user"|"assistant", "content": "..."}]
    """

    # On ne garde que les 10 derniers
    recent = history[-MAX_HISTORY:]
    messages = [{"role": "system", "content": system_prompt}] + history

    full_response = ""

    try:
        stream = client.chat.completions.create(
            model=MODEL, messages=messages, stream=True
        )

        for event in stream:
            if event.choices and len(event.choices) > 0:
                delta = event.choices[0].delta
                if delta.content:
                    chunk = delta.content
                    full_response += chunk
                    yield chunk

    except Exception as e:
        yield f"Erreur: {str(e)}"

    return full_response


# ------------------------------------------------------------------
# Fonctions legacy (conservées pour compatibilité)
# ------------------------------------------------------------------

messages_history = [{"role": "system", "content": system_prompt}]


def chat(user_input: str) -> str:
    """Version non-streaming du chat"""
    messages_history.append({"role": "user", "content": user_input})
    try:
        response = client.chat.completions.create(
            model=MODEL, messages=messages_history
        )
        reply = response.choices[0].message.content
        messages_history.append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        messages_history.pop()
        return f"Erreur: {str(e)}"


def chat_stream(user_input: str):
    """Version legacy — conservée pour compatibilité"""
    messages_history.append({"role": "user", "content": user_input})
    full_response = ""

    try:
        stream = client.chat.completions.create(
            model=MODEL, messages=messages_history, stream=True
        )

        for event in stream:
            if event.choices and len(event.choices) > 0:
                delta = event.choices[0].delta
                if delta.content:
                    chunk = delta.content
                    full_response += chunk
                    yield chunk

        messages_history.append({"role": "assistant", "content": full_response})

    except Exception as e:
        messages_history.pop()
        yield f"Erreur: {str(e)}"


def reset_conversation():
    """Reinitialise l'historique legacy"""
    global messages_history
    messages_history = [{"role": "system", "content": system_prompt}]