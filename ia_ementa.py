"""Gera ementa e tópicos de aula com IA (Gemini, OpenAI ou Groq)."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


def _extrair_json(texto: str) -> dict[str, Any]:
    texto = (texto or "").strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```(?:json)?\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto)
    return json.loads(texto)


def _chamar_gemini(prompt: str, chave: str) -> str:
    modelos = [
        os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        "gemini-1.5-flash",
    ]
    ultimo_erro = None
    for modelo in modelos:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{modelo}:generateContent?key={chave}"
        )
        corpo = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.4,
                "responseMimeType": "application/json",
            },
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(corpo).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                dados = json.loads(resp.read().decode("utf-8"))
            return dados["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as exc:
            ultimo_erro = exc
            continue
    raise RuntimeError(f"Gemini falhou: {ultimo_erro}")


def _chamar_openai_compativel(prompt: str, chave: str, base: str, modelo: str) -> str:
    url = base.rstrip("/") + "/chat/completions"
    corpo = {
        "model": modelo,
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "Você responde apenas JSON válido, sem markdown.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(corpo).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {chave}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        dados = json.loads(resp.read().decode("utf-8"))
    return dados["choices"][0]["message"]["content"]


def provedor_ia() -> str:
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("GROQ_API_KEY"):
        return "groq"
    return ""


def gerar_proposta(disciplina: dict[str, Any]) -> dict[str, Any]:
    """Devolve ementa, objetivos e um tópico por encontro de aula."""
    n = max(1, int(disciplina.get("encontros") or 0))
    ja = disciplina.get("conteudos_lancados") or []
    ja_txt = "\n".join(f"- {x}" for x in ja) if ja else "Nenhum conteúdo lançado ainda."
    nome = disciplina.get("nome_disciplina") or disciplina.get("nome") or ""
    curso = disciplina.get("curso") or "curso do IFCE"
    ch = disciplina.get("carga_horaria") or ""
    nivel = disciplina.get("nivel") or ""

    prompt = f"""
Você é docente do Instituto Federal do Ceará (IFCE) elaborando a ementa e o plano de aulas.

Disciplina: {nome}
Curso: {curso}
{("Nível/turno: " + nivel) if nivel else ""}
Carga horária: {ch}
Quantidade de encontros de aula no semestre: {n}

Conteúdos já lançados no diário (mantenha coerência com eles):
{ja_txt}

Tarefa:
1. Escreva uma ementa em 1 parágrafo, no estilo de PPC de Instituto Federal.
2. Liste 3 a 5 objetivos de aprendizagem.
3. Crie EXATAMENTE {n} tópicos de aula, um por encontro, em ordem pedagógica do semestre.
   Cada tópico deve ter 1 ou 2 palavras no máximo (ex.: frontend1, frontend2, sql1, ed3).
   Nada de frase, ementa longa ou detalhe técnico. Sem numeração extra, sem markdown.
   Se já houver conteúdos lançados, mantenha o estilo curto e a sequência.

Responda somente JSON neste formato:
{{
  "ementa": "parágrafo",
  "objetivos": ["...", "..."],
  "topicos": ["...", "..."]
}}
""".strip()

    provedor = provedor_ia()
    if provedor == "gemini":
        bruto = _chamar_gemini(prompt, os.environ["GEMINI_API_KEY"])
    elif provedor == "openai":
        bruto = _chamar_openai_compativel(
            prompt,
            os.environ["OPENAI_API_KEY"],
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        )
    elif provedor == "groq":
        bruto = _chamar_openai_compativel(
            prompt,
            os.environ["GROQ_API_KEY"],
            "https://api.groq.com/openai/v1",
            os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        )
    else:
        raise RuntimeError(
            "Nenhuma chave de IA configurada. Defina GEMINI_API_KEY (gratuita), "
            "OPENAI_API_KEY ou GROQ_API_KEY no arquivo .env."
        )

    dados = _extrair_json(bruto)
    topicos = [str(t).strip() for t in (dados.get("topicos") or []) if str(t).strip()]
    if len(topicos) < n:
        base = topicos[-1] if topicos else f"Continuidade dos conteúdos de {nome}."
        while len(topicos) < n:
            topicos.append(f"{base} Aprofundamento e exercícios.")
    dados["topicos"] = topicos[:n]
    dados["ementa"] = str(dados.get("ementa") or "").strip()
    dados["objetivos"] = [str(o).strip() for o in (dados.get("objetivos") or []) if str(o).strip()]
    dados["provedor"] = provedor
    return dados
